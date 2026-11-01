"""Friday's own calls through Exotel: the dialer, the stream and who answers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import websockets
import websockets.sync.client as ws_client
from pipecat.serializers.exotel import ExotelFrameSerializer

from friday_services.calls.bot import CallHelpers, ask_friday_handler, build_call_pipeline
from friday_services.calls.exotel import (
    ExotelDialer,
    ExotelError,
    custom_parameters,
    stream_url,
)
from friday_services.calls.prompts import owner_inbound_prompt, reception_prompt
from friday_services.calls.store import CallStore
from friday_services.config import Settings
from friday_services.phone_in import caller_digits

from .conftest import FAKE_ENV
from .mcp_client import McpClient

KEY = "exotel-stream-key-0123456789"
EXOTEL_ENV = {
    **{name: value for name, value in FAKE_ENV.items() if not name.startswith("TWILIO_")},
    "EXOTEL_API_KEY": "exo-key",
    "EXOTEL_API_TOKEN": "exo-token",
    "EXOTEL_ACCOUNT_SID": "fridayindia1",
    "EXOTEL_CALLER_ID": "08012345678",
    "EXOTEL_APP_ID": "123456",
    "EXOTEL_STREAM_KEY": KEY,
    "FRIDAY_OWNER_PHONE": "+919800000001",
}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # Overrides conftest's Twilio settings, so `server` runs with Exotel.
    return Settings.from_env(EXOTEL_ENV, data_dir=tmp_path)


def exotel_start(key: str, call_sid: str, caller: str) -> list[str]:
    return [
        json.dumps({"event": "connected"}),
        json.dumps(
            {
                "event": "start",
                "sequence_number": 1,
                "stream_sid": "ST1",
                "start": {
                    "stream_sid": "ST1",
                    "call_sid": call_sid,
                    "account_sid": "fridayindia1",
                    "from": caller,
                    "to": "08012345678",
                    "custom_parameters": {"key": key},
                    "media_format": {"encoding": "raw", "sample_rate": "8000"},
                },
            }
        ),
    ]


def stream(server, key: str, call_sid: str, caller: str) -> int | None:
    """Plays Exotel's opening messages; the close code if Friday refused."""
    url = server.url.replace("http://", "ws://") + "/exotel/stream"
    with ws_client.connect(url) as socket:
        for message in exotel_start(key, call_sid, caller):
            socket.send(message)
        try:
            socket.recv(timeout=5)
        except websockets.exceptions.ConnectionClosed as closed:
            return closed.rcvd.code if closed.rcvd else None
    return None


def test_exotel_is_chosen_once_its_key_is_set(settings):
    assert settings.call_provider == "exotel"
    assert settings.own_calls_ready
    twilio = Settings.from_env({**EXOTEL_ENV, "FRIDAY_CALL_PROVIDER": "twilio"})
    assert twilio.call_provider == "twilio" and not twilio.own_calls_ready


def test_voicebot_url_and_custom_parameters(settings):
    assert stream_url(settings) == f"wss://friday.example.test/exotel/stream?key={KEY}"
    assert custom_parameters({"key": "a"}) == {"key": "a"}
    assert custom_parameters("?key=a&x=1") == {"key": "a", "x": "1"}
    assert custom_parameters(None) == {}


def test_indian_numbers_with_a_trunk_zero_match():
    assert caller_digits("09800000001") == caller_digits("+91 98000 00001") == "919800000001"


async def test_dialer_connects_the_number_to_fridays_call_flow(settings):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = httpx.QueryParams(request.content.decode())
        return httpx.Response(200, json={"Call": {"Sid": "exo-sid-1", "Status": "in-progress"}})

    record = CallStore().create(phone="+919812345678", goal="Book", language="en-IN", context="")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sid = await ExotelDialer(settings, client).place(record)
    assert sid == "exo-sid-1"
    assert seen["url"] == ("https://api.in.exotel.com/v1/Accounts/fridayindia1/Calls/connect.json")
    assert seen["auth"].startswith("Basic ")
    body = seen["body"]
    assert body["From"] == "+919812345678" and body["CallerId"] == "08012345678"
    assert body["Url"] == "http://my.exotel.com/fridayindia1/exoml/start_voice/123456"
    assert body["StatusCallback"] == (
        f"https://friday.example.test/exotel/status/{record.id}?key={KEY}"
    )
    assert body["CustomField"] == record.id


async def test_dialer_reports_exotel_refusals(settings):
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            403, json={"RestException": {"Status": 403, "Message": "Number not whitelisted"}}
        )
    )
    record = CallStore().create(phone="+919812345678", goal="Book", language="en-IN", context="")
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ExotelError, match="not whitelisted"):
            await ExotelDialer(settings, client).place(record)


def test_store_matches_exotel_audio_to_the_call_friday_placed():
    store = CallStore()
    record = store.create(phone="+919812345678", goal="Book", language="en-IN", context="")
    record.call_sid = "exo-1"
    assert store.waiting_for_audio("exo-1") is record
    # Exotel's stream may carry another leg's SID: the one ringing call still matches.
    assert store.waiting_for_audio("other") is record
    record.stream_started = True
    assert store.waiting_for_audio("exo-1") is None


def test_health_reports_exotel(server):
    body = httpx.get(f"{server.url}/health").json()
    assert body["calls"] == {"ready": True, "missing": [], "provider": "exotel"}


def test_a_placed_call_streams_through_exotel(server):
    client = McpClient(f"{server.url}/phone/mcp", FAKE_ENV["FRIDAY_SERVICES_TOKEN"])
    started = client.call("start_call", {"phone": "+919812345678", "goal": "Book a check-up"})
    assert not started["isError"], started["text"]
    call_id = started["data"]["call_id"]
    assert started["data"]["stage"] == "ringing"
    record = server.app.state.calls.get(call_id)

    # Without the stream key Friday hangs up on the audio.
    assert stream(server, "forged", record.call_sid, "09812345678") == 1008
    assert server.call.runs == []

    stream(server, KEY, record.call_sid, "09812345678")
    run = server.call.runs[0]
    assert run["record"] is record and run["provider"] == "exotel"
    assert run["stream_sid"] == "ST1" and run["call_sid"] == record.call_sid
    assert isinstance(run["helpers"], CallHelpers)
    result = client.call("get_call_result", {"call_id": call_id, "wait_seconds": 1})["data"]
    assert result["status"] == "answered"


def test_status_callbacks_need_the_key(server):
    client = McpClient(f"{server.url}/phone/mcp", FAKE_ENV["FRIDAY_SERVICES_TOKEN"])
    call_id = client.call("start_call", {"phone": "+919812345678", "goal": "Book a check-up"})[
        "data"
    ]["call_id"]
    path = f"{server.url}/exotel/status/{call_id}"
    assert httpx.post(path, params={"key": "wrong"}, data={"Status": "busy"}).status_code == 403
    assert httpx.post(path, params={"key": KEY}, data={"Status": "no-answer"}).status_code == 204
    result = client.call("get_call_result", {"call_id": call_id, "wait_seconds": 1})["data"]
    assert result["status"] == "no_answer" and result["summary"] == "Nobody answered."


def test_the_owner_calling_gets_friday_and_others_get_reception(server):
    stream(server, KEY, "in-1", "09800000001")
    stream(server, KEY, "in-2", "09812345678")
    owner_call, other_call = (run["record"] for run in server.call.runs)
    assert owner_call.kind == "owner_in" and "Coming up:" in owner_call.context
    assert other_call.kind == "reception" and other_call.phone == "09812345678"


def tools_of(call) -> set[str]:
    llm = next(p for p in call.pipeline.processors if type(p).__name__ == "OpenAILLMService")
    return set(llm._functions)


async def noop_ask(record, request):
    return "Done."


async def noop_take(record, name, message):
    return None


@pytest.mark.parametrize(
    ("kind", "tools"),
    [
        ("business", {"end_call"}),
        ("owner", {"end_call", "ask_friday"}),
        ("owner_in", {"end_call", "ask_friday"}),
        ("reception", {"end_call", "take_message"}),
    ],
)
async def test_each_kind_of_call_gets_its_tools(settings, kind, tools):
    store = CallStore()
    record = store.create(
        phone="+919812345678", goal="Book", language="hi-IN", context="", kind=kind
    )
    call = build_call_pipeline(
        object(),
        stream_sid="ST1",
        call_sid="exo-1",
        record=record,
        store=store,
        settings=settings,
        provider="exotel",
        helpers=CallHelpers(ask=noop_ask, take_message=noop_take),
    )
    assert tools_of(call) == tools
    assert isinstance(call.transport._params.serializer, ExotelFrameSerializer)


async def test_ask_friday_says_what_friday_did_or_why_it_failed():
    record = CallStore().create(phone="+91", goal="", language="en-IN", context="", kind="owner_in")
    asked, said = [], []

    async def ask(call, request):
        asked.append((call.id, request))
        if "fail" in request:
            raise RuntimeError("WhatsApp is closed")
        return "Sent it to Didi."

    async def result_callback(result):
        said.append(result)

    handler = ask_friday_handler(record, ask)
    await handler(
        SimpleNamespace(arguments={"request": "Didi ko bolo"}, result_callback=result_callback)
    )
    await handler(
        SimpleNamespace(arguments={"request": "fail please"}, result_callback=result_callback)
    )
    assert asked == [(record.id, "Didi ko bolo"), (record.id, "fail please")]
    assert said == [
        {"reply": "Sent it to Didi."},
        {"reply": "Sorry, that did not work: WhatsApp is closed"},
    ]


def test_prompts_for_calls_to_friday(settings):
    store = CallStore()
    owner_in = store.create(phone="+91", goal="", language="en-IN", context="", kind="owner_in")
    assert "ask_friday" in owner_inbound_prompt(owner_in, "Asha", "Asia/Kolkata")
    assert "cannot do tasks" not in owner_inbound_prompt(owner_in, "Asha", "Asia/Kolkata")
    caller = store.create(phone="+91", goal="", language="en-IN", context="Ravi", kind="reception")
    prompt = reception_prompt(caller, "Asha", "Asia/Kolkata")
    assert "The caller is Ravi; they are not Asha." in prompt
    assert "take_message" in prompt and "share nothing about Asha" in prompt
