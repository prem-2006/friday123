"""Phone-call building blocks, without any network."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from friday_services.calls.bot import build_call_pipeline, end_call_handler
from friday_services.calls.prompts import phone_prompt
from friday_services.calls.store import CallStore
from friday_services.calls.stt import GNANI_FRAME_BYTES, FramedGnaniSTTService
from friday_services.calls.twilio import (
    TwilioDialer,
    TwilioError,
    signature,
    stream_twiml,
    valid_signature,
)


class FakeGnaniSocket:
    def __init__(self):
        self.sent: list[bytes] = []

    async def send(self, data: bytes):
        self.sent.append(data)


async def test_stt_regroups_twilio_packets_into_gnani_frames():
    stt = FramedGnaniSTTService(api_key="fake", sample_rate=8000)
    stt._ws = FakeGnaniSocket()  # skip the real connection
    for _ in range(10):  # ten 20 ms Twilio packets at 8 kHz = 3,200 bytes
        async for _frame in stt.run_stt(b"\x01\x02" * 160):
            pass
    assert [len(frame) for frame in stt._ws.sent] == [GNANI_FRAME_BYTES] * 3
    assert len(stt._pending) == 3200 - 3 * GNANI_FRAME_BYTES


def test_twilio_signature_matches_an_independent_computation():
    import base64
    import hashlib
    import hmac

    url = "https://friday.example.test/twilio/status/call_1"
    params = {"CallStatus": "ringing", "CallSid": "CA1", "AccountSid": "AC1"}
    payload = url + "AccountSidAC1CallSidCA1CallStatusringing"
    expected = base64.b64encode(
        hmac.new(b"secret", payload.encode(), hashlib.sha1).digest()
    ).decode()
    assert signature("secret", url, params) == expected
    assert valid_signature("secret", url, params, expected)
    assert not valid_signature("secret", url, {**params, "CallStatus": "busy"}, expected)
    assert not valid_signature("", url, params, expected)
    assert not valid_signature("secret", url, params, None)


def test_twiml_streams_to_the_public_websocket_with_call_secrets(settings):
    store = CallStore()
    record = store.create(phone="+919812345678", goal="Book", language="en-IN", context="")
    twiml = stream_twiml(settings, record)
    assert '<Stream url="wss://friday.example.test/twilio/stream">' in twiml
    assert f'<Parameter name="call_id" value="{record.id}"/>' in twiml
    assert f'<Parameter name="token" value="{record.stream_token}"/>' in twiml


async def test_dialer_sends_the_expected_twilio_request(settings):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.content.decode()
        return httpx.Response(201, json={"sid": "CA123"})

    store = CallStore()
    record = store.create(phone="+919812345678", goal="Book", language="en-IN", context="")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sid = await TwilioDialer(settings, client).place(record)
    assert sid == "CA123"
    assert seen["url"].endswith("/Accounts/AC00000000000000000000000000000000/Calls.json")
    assert seen["auth"].startswith("Basic ")
    body = httpx.QueryParams(seen["body"])
    assert body["To"] == "+919812345678"
    assert body["Url"] == f"https://friday.example.test/twilio/voice/{record.id}"
    assert body.get_list("StatusCallbackEvent") == ["initiated", "ringing", "answered", "completed"]


async def test_dialer_reports_twilio_refusals(settings):
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(400, json={"message": "The number is unverified."})
    )
    store = CallStore()
    record = store.create(phone="+919812345678", goal="Book", language="en-IN", context="")
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(TwilioError, match="unverified"):
            await TwilioDialer(settings, client).place(record)


async def test_store_finishes_once_and_wakes_waiters(tmp_path):
    store = CallStore(tmp_path)
    record = store.create(phone="+919812345678", goal="Book", language="en-IN", context="")
    store.set_stage(record.id, "ringing")
    early = await store.wait(record.id, 0.05)
    assert early.result()["status"] == "in_progress"
    assert early.result()["stage"] == "ringing"

    waiter = asyncio.create_task(store.wait(record.id, 5))
    await asyncio.sleep(0.05)
    store.finish(record.id, "answered", outcome="completed", summary="Booked.")
    store.finish(record.id, "failed", error="too late")  # ignored
    done = await waiter
    assert done.status == "answered" and done.error is None
    saved = (tmp_path / "calls" / f"{record.id}.json").read_text(encoding="utf-8")
    assert "Booked." in saved and record.stream_token not in saved


async def test_end_call_records_the_outcome_and_ends_the_pipeline():
    store = CallStore()
    record = store.create(phone="+919812345678", goal="Book", language="en-IN", context="")
    ended = asyncio.Event()
    pushed, results = [], []

    async def result_callback(result, **_kwargs):
        results.append(result)

    async def push_frame(frame, direction):
        pushed.append((type(frame).__name__, direction.name))

    params = SimpleNamespace(
        arguments={
            "outcome": "completed",
            "summary": "Booked for 7 PM.",
            "details": {"confirmed_time": "7 PM"},
        },
        result_callback=result_callback,
        llm=SimpleNamespace(push_frame=push_frame),
    )
    await end_call_handler(store, record, ended)(params)
    assert record.status == "answered" and record.outcome == "completed"
    assert record.details == {"confirmed_time": "7 PM"}
    assert ended.is_set() and results == [{"ok": True}]
    assert pushed == [("EndWorkerFrame", "UPSTREAM")]


def test_phone_prompt_discloses_ai_and_uses_india_time(settings):
    store = CallStore()
    record = store.create(
        phone="+919812345678",
        goal="Book a dental check-up tomorrow at 6 PM",
        language="hi-IN",
        context="7 PM is also fine.",
    )
    prompt = phone_prompt(record, "Asha", "Asia/Kolkata", datetime(2026, 10, 22, 7, 33, tzinfo=UTC))
    assert "Friday, an AI assistant calling for Asha" in prompt
    assert "Book a dental check-up tomorrow at 6 PM" in prompt
    assert "speak Hindi" in prompt and "7 PM is also fine." in prompt
    assert "Never agree to pay" in prompt
    assert "01:03 PM (Asia/Kolkata)" in prompt


async def test_call_pipeline_builds_with_gnani_and_evon(settings):
    store = CallStore()
    record = store.create(phone="+919812345678", goal="Book", language="ta-IN", context="")
    call = build_call_pipeline(
        object(),
        stream_sid="MZ1",
        call_sid="CA1",
        record=record,
        store=store,
        settings=settings,
    )
    names = [type(p).__name__ for p in call.pipeline.processors]
    expected = [
        "FastAPIWebsocketInputTransport",
        "FramedGnaniSTTService",
        "LLMUserAggregator",
        "OpenAILLMService",
        "GnaniTTSService",
        "FastAPIWebsocketOutputTransport",
        "LLMAssistantAggregator",
    ]
    start = names.index(expected[0])
    assert names[start : start + len(expected)] == expected
