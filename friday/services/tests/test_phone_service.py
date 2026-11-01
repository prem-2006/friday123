"""The running service: MCP tools, Twilio webhooks and the audio socket."""

from __future__ import annotations

import json

import httpx
import websockets
import websockets.sync.client as ws_client

from friday_services.calls.twilio import signature

from .conftest import TOKEN
from .mcp_client import McpClient


def phone(server, token: str | None = TOKEN) -> McpClient:
    client = McpClient(f"{server.url}/phone/mcp", token)
    return client


def twilio_post(server, path: str, params: dict, *, sign: bool = True) -> httpx.Response:
    url = server.settings.public_url + path
    headers = (
        {"X-Twilio-Signature": signature("twilio-test-auth-token", url, params)} if sign else {}
    )
    return httpx.post(f"{server.url}{path}", data=params, headers=headers)


def test_health_reports_what_is_configured(server):
    body = httpx.get(f"{server.url}/health").json()
    assert body["calls"] == {"ready": True, "missing": [], "provider": "twilio"}


def test_mcp_requires_the_bearer_token(server):
    assert phone(server, token=None).raw("tools/list").status_code == 401
    assert phone(server, token="wrong-token-wrong-token-1").raw("tools/list").status_code == 401


def test_phone_tools_and_approval_hints(server):
    client = phone(server)
    assert client.initialize()["serverInfo"]["name"] == "friday-phone"
    tools = client.tools()
    assert set(tools) == {"start_call", "get_call_result", "schedule_call"}
    # OpenDots runs read-only tools directly and asks the owner before the rest.
    assert tools["start_call"]["annotations"]["readOnlyHint"] is False
    assert tools["get_call_result"]["annotations"]["readOnlyHint"] is True
    schema = tools["start_call"]["inputSchema"]
    assert set(schema["required"]) == {"phone", "goal"}
    assert schema["properties"]["language"]["default"] == "en-IN"


def test_start_call_rejects_bad_input(server):
    client = phone(server)
    bad_number = client.call("start_call", {"phone": "98123", "goal": "Book a check-up"})
    assert bad_number["isError"] and "international number" in bad_number["text"]
    bad_language = client.call(
        "start_call", {"phone": "+919812345678", "goal": "Book a check-up", "language": "fr-FR"}
    )
    assert bad_language["isError"] and "Unsupported language" in bad_language["text"]
    assert server.dialer.placed == []


def test_a_call_nobody_answers(server):
    client = phone(server)
    started = client.call("start_call", {"phone": "+919812345678", "goal": "Book a check-up"})
    assert not started["isError"], started["text"]
    call_id = started["data"]["call_id"]
    assert started["data"]["status"] == "in_progress"
    assert started["data"]["stage"] == "ringing"

    busy = client.call("start_call", {"phone": "+919800000000", "goal": "Another call"})
    assert busy["isError"] and "still in progress" in busy["text"]

    assert (
        twilio_post(
            server, f"/twilio/status/{call_id}", {"CallStatus": "ringing"}, sign=False
        ).status_code
        == 403
    )
    assert (
        twilio_post(server, f"/twilio/status/{call_id}", {"CallStatus": "no-answer"}).status_code
        == 204
    )
    result = client.call("get_call_result", {"call_id": call_id, "wait_seconds": 1})["data"]
    assert result["status"] == "no_answer" and result["summary"] == "Nobody answered."


def test_an_answered_call_streams_audio_to_friday(server):
    client = phone(server)
    call_id = client.call(
        "start_call", {"phone": "+919812345678", "goal": "Book a check-up", "language": "hi-IN"}
    )["data"]["call_id"]
    record = server.app.state.calls.get(call_id)

    twiml = twilio_post(server, f"/twilio/voice/{call_id}", {"CallStatus": "in-progress"})
    assert twiml.status_code == 200 and "wss://friday.example.test/twilio/stream" in twiml.text

    ws_url = server.url.replace("http://", "ws://") + "/twilio/stream"

    def twilio_start(token: str) -> list[str]:
        return [
            json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}),
            json.dumps(
                {
                    "event": "start",
                    "streamSid": "MZ123",
                    "start": {
                        "streamSid": "MZ123",
                        "callSid": "CA123",
                        "accountSid": "AC1",
                        "customParameters": {"call_id": call_id, "token": token},
                    },
                }
            ),
        ]

    # A stream without the call's secret is refused.
    with ws_client.connect(ws_url) as socket:
        for message in twilio_start("forged"):
            socket.send(message)
        try:
            socket.recv(timeout=5)
        except websockets.exceptions.ConnectionClosed as closed:
            assert closed.rcvd.code == 1008
    assert server.call.runs == []

    with ws_client.connect(ws_url) as socket:
        for message in twilio_start(record.stream_token):
            socket.send(message)
        try:
            socket.recv(timeout=5)
        except websockets.exceptions.ConnectionClosed:
            pass
    assert server.call.runs[0]["stream_sid"] == "MZ123"
    assert server.call.runs[0]["call_sid"] == "CA123"
    assert server.call.runs[0]["provider"] == "twilio"

    result = client.call("get_call_result", {"call_id": call_id, "wait_seconds": 1})["data"]
    assert result["status"] == "answered" and result["outcome"] == "completed"
    assert result["transcript"] == ["Friday: Hello, this is Friday."]
