"""The owner phones Friday's number: Sarvam's agent asks /phone/ask. Nothing
here reaches Sarvam, ngrok or the app; the app's routes are faked."""

from __future__ import annotations

import asyncio
import hashlib
import json

import httpx
import pytest

from friday_services.config import Settings
from friday_services.main import create_app
from friday_services.phone_in import PhoneDesk, caller_name, latest_request, pending_approval
from tests.conftest import FAKE_ENV, FakeBrowser, FakeWhatsAppUI

PHONE_TOKEN = "phone-token-0123456789"
OWNER = "+919876543210"


def test_latest_request_takes_what_the_caller_said_after_the_agent():
    turns = [
        {"role": "agent", "text": "Hi Prem, what can I do?"},
        {"role": "user", "text": "Set a timer"},
        {"role": "agent", "text": "For how long?"},
        {"role": "user", "text": "ten"},
        {"role": "user", "text": "minutes"},
    ]
    assert latest_request(turns) == "ten minutes"
    assert latest_request(json.dumps(turns)) == "ten minutes"
    assert latest_request("agent: Hello\nuser: open YouTube") == "open YouTube"
    assert latest_request(None) == ""


def approval_turn() -> list[dict]:
    return [
        {
            "id": "a1",
            "role": "assistant",
            "content": "",
            "toolCalls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "gmail_send", "arguments": "{}"},
                }
            ],
        },
        {
            "id": "t1",
            "role": "tool",
            "toolCallId": "c1",
            "content": json.dumps({"status": "approval_required", "approvalId": "ap-1"}),
        },
        {
            "id": "a2",
            "role": "assistant",
            "content": "",
            "toolCalls": [
                {
                    "id": "c2",
                    "type": "function",
                    "function": {
                        "name": "request_connection_action",
                        "arguments": json.dumps(
                            {"approvalId": "made-up", "summary": "Send the email to Ravi."}
                        ),
                    },
                }
            ],
        },
    ]


def test_pending_approval_uses_the_saved_approval():
    found = pending_approval([{"id": "u", "role": "user", "content": "x"}, *approval_turn()])
    assert found == {"toolCallId": "c2", "approvalId": "ap-1", "summary": "Send the email to Ravi."}


class FakeApp:
    """The app's run and approve routes."""

    def __init__(self, turns: list[list[dict]], delay: float = 0):
        self.turns = turns
        self.delay = delay
        self.runs: list[list[dict]] = []
        self.callers: list[str] = []
        self.approved: list[dict] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path == "/api/friday/phone/run":
            self.runs.append(body["messages"])
            self.callers.append(body["caller"])
            await asyncio.sleep(self.delay)
            return httpx.Response(200, json={"threadId": "th-1", "newMessages": self.turns.pop(0)})
        if request.url.path == "/api/conversations/th-1/connection-actions":
            self.approved.append(body)
            return httpx.Response(200, json={"isError": False, "text": "Sent."})
        return httpx.Response(404, json={"error": "Not found."})


def desk_for(app: FakeApp, settings: Settings, reply_within: float = 5) -> PhoneDesk:
    client = httpx.AsyncClient(transport=httpx.MockTransport(app))
    return PhoneDesk(settings, client=client, reply_within=reply_within)


async def test_a_request_runs_friday_and_returns_its_reply(settings):
    app = FakeApp([[{"id": "r", "role": "assistant", "content": "Timer set for 10 minutes."}]])
    desk = desk_for(app, settings)
    assert await desk.ask("call-1", "set a timer for 10 minutes") == "Timer set for 10 minutes."
    assert app.runs[0][-1]["content"] == "set a timer for 10 minutes"
    assert app.callers == ["owner"]


async def test_a_call_friday_placed_starts_with_why(settings):
    app = FakeApp([[{"id": "r", "role": "assistant", "content": "Prem says call him."}]])
    desk = desk_for(app, settings)
    said = await desk.ask("call-2", "haan bolo", owner=False, context="Remind Didi to call Prem.")
    assert said == "Prem says call him."
    assert app.callers == ["client"]
    first = app.runs[0][-1]["content"]
    assert first.startswith("(Note: you placed this call. Remind Didi to call Prem.)")
    assert first.endswith("haan bolo")


async def test_a_client_never_gets_an_approval_turn(settings):
    app = FakeApp([approval_turn(), [{"id": "r", "role": "assistant", "content": "Okay."}]])
    desk = desk_for(app, settings)
    await desk.ask("call-3", "send an email", owner=False)
    # A "yes" from a client is just words to Reception, never an approval.
    assert await desk.ask("call-3", "yes", owner=False) == "Okay."
    assert app.approved == []


async def test_a_yes_on_the_call_approves_and_finishes(settings):
    app = FakeApp(
        [approval_turn(), [{"id": "r", "role": "assistant", "content": "Sent it to Ravi."}]]
    )
    desk = desk_for(app, settings)
    asked = await desk.ask("call-1", "email Ravi that I'm late")
    assert asked == "I need your OK: Send the email to Ravi. Shall I go ahead?"
    assert "yes or no" in await desk.ask("call-1", "hmm what")
    assert await desk.ask("call-1", "haan bhej do") == "Sent it to Ravi."
    assert app.approved == [{"toolCallId": "c2", "approvalId": "ap-1"}]
    answer = json.loads(app.runs[1][-1]["content"])
    assert app.runs[1][-1]["toolCallId"] == "c2" and answer["approved"] is True


async def test_a_no_declines_without_running_the_action(settings):
    app = FakeApp([approval_turn(), [{"id": "r", "role": "assistant", "content": "Okay."}]])
    desk = desk_for(app, settings)
    await desk.ask("call-1", "email Ravi")
    assert await desk.ask("call-1", "no, don't") == "Okay."
    assert app.approved == []
    assert json.loads(app.runs[1][-1]["content"])["approved"] is False


async def test_a_slow_request_keeps_running_and_answers_later(settings):
    app = FakeApp([[{"id": "r", "role": "assistant", "content": "Booked for 6 PM."}]], delay=0.3)
    desk = desk_for(app, settings, reply_within=0.05)
    assert "I'm on it" in await desk.ask("call-1", "book a table")
    assert "still working" in await desk.ask("call-1", "is it done?")
    await asyncio.sleep(0.4)
    assert await desk.ask("call-1", "is it done?") == "Booked for 6 PM."


class FakeDesk:
    def __init__(self):
        self.asked: list[tuple] = []

    async def ask(self, call_id: str, request: str, owner: bool = True, context: str = "") -> str:
        self.asked.append((call_id, request, owner, context))
        return "Done, Prem." if owner else "Hi, I'm Friday, Asha's assistant. Can I take a message?"


@pytest.fixture
def phone_app(tmp_path):
    settings = Settings.from_env(
        {**FAKE_ENV, "FRIDAY_PHONE_TOKEN": PHONE_TOKEN, "FRIDAY_OWNER_PHONE": OWNER},
        data_dir=tmp_path,
    )
    desk = FakeDesk()
    app = create_app(settings, browser=FakeBrowser(), whatsapp_ui=FakeWhatsAppUI(), phone_desk=desk)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    client.notes = tmp_path / "notes.txt"
    return client, desk


THROUGH_NGROK = {"x-friday-tunnel": "1"}


async def test_phone_ask_does_what_the_owner_says(phone_app):
    client, desk = phone_app
    response = await client.post(
        "/phone/ask",
        headers={"Authorization": f"Bearer {PHONE_TOKEN}", **THROUGH_NGROK},
        json={
            "caller": "9876543210",
            "call_id": "int-7",
            "transcript": [{"role": "user", "text": "open YouTube"}],
        },
    )
    assert response.json() == {"reply": "Done, Prem."}
    assert desk.asked == [("int-7", "open YouTube", True, "")]


async def test_the_owner_is_known_by_sarvams_caller_code_too(phone_app):
    client, desk = phone_app
    code = hashlib.sha256(OWNER.encode()).hexdigest()  # Sarvam's User Identifier
    response = await client.post(
        "/phone/ask",
        headers={"Authorization": f"Bearer {PHONE_TOKEN}"},
        json={"caller": code, "call_id": "int-8", "request": "battery?", "reason": "Dinner time"},
    )
    assert response.json() == {"reply": "Done, Prem."}
    assert desk.asked == [("int-8", "battery?", True, "Dinner time")]


async def test_other_callers_are_clients_whose_message_is_saved(phone_app):
    client, desk = phone_app
    wrong = await client.post(
        "/phone/ask", headers={"Authorization": "Bearer nope"}, json={"caller": OWNER}
    )
    assert wrong.status_code == 401
    stranger = await client.post(
        "/phone/ask",
        headers={"Authorization": f"Bearer {PHONE_TOKEN}"},
        json={"caller": "+911111111111", "request": "read my email"},
    )
    assert stranger.json()["reply"].endswith("Can I take a message?")
    assert desk.asked == [("", "read my email", False, "")]  # Reception, not Friday
    assert "Phone message from +911111111111: read my email" in client.notes.read_text("utf-8")


def test_a_contact_is_named_by_number_or_caller_code():
    contacts = [{"name": "Didi", "phone": "+91 98765 00000"}]
    code = hashlib.sha256(b"+919876500000").hexdigest()
    assert caller_name(code, contacts) == "Didi"
    assert caller_name("9876500000", contacts) == "Didi"
    assert caller_name("+911111111111", contacts) == "+911111111111"
    assert (
        caller_name(hashlib.sha256(b"+910000000000").hexdigest(), contacts) == "an unknown caller"
    )


async def test_through_ngrok_only_the_call_routes_answer(phone_app):
    client, _ = phone_app
    assert (await client.get("/health", headers=THROUGH_NGROK)).status_code == 404
    assert (await client.get("/health")).status_code == 200


async def test_the_phone_token_is_accepted_however_the_agent_sends_it(phone_app):
    client, _desk = phone_app
    body = {"caller": OWNER, "request": "battery?"}
    for headers, params in (
        ({"Authorization": PHONE_TOKEN}, {}),
        ({"Authorization": f"Token {PHONE_TOKEN}"}, {}),
        ({"x-api-key": PHONE_TOKEN}, {}),
        ({}, {"token": PHONE_TOKEN}),
    ):
        response = await client.post("/phone/ask", headers=headers, params=params, json=body)
        assert response.status_code == 200, headers or params
    for headers in ({}, {"Authorization": "Bearer nope"}, {"x-api-key": "nope"}):
        assert (await client.post("/phone/ask", headers=headers, json=body)).status_code == 401
