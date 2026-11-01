"""The owner phones Friday's Sarvam number and Friday does what they ask.

Sarvam's agent answers the call and hears the owner. For each request it
calls its API tool `ask_friday`, which reaches POST /phone/ask here through
the ngrok tunnel. This runs the request through the Friday Dot exactly like
the voice screen does (the app's /api/friday/voice/run) and returns the
sentence the agent should say.

Only the owner's own number gets anything done. Something that needs the
owner's OK (an email, a WhatsApp message) is read back on the call, and a
yes on the same call approves it. A request that takes longer than Sarvam
waits for a tool keeps running; the next question gets its answer.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from friday_services.config import Settings

APPROVAL_TOOL = "request_connection_action"
# Sarvam waits at most 30 s for a tool; answer before that.
REPLY_WITHIN_SECONDS = 22.0
KEEP_CALLS_SECONDS = 2 * 60 * 60
KEEP_MESSAGES = 30

YES = re.compile(
    r"\b(yes|yeah|yep|ok(ay)?|sure|go ahead|do it|send it|haan?|ha+n|ji|theek hai|thik hai|"
    r"kar do|karo|bhej do|chalo)\b",
    re.IGNORECASE,
)
NO = re.compile(r"\b(no|nope|don'?t|cancel|stop|nahi+n?|nahi|mat|ruko|rehne do)\b", re.IGNORECASE)


def caller_digits(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("0"):
        # Exotel writes Indian numbers with a trunk 0, e.g. 09812345678.
        digits = digits[1:]
    return "91" + digits if len(digits) == 10 else digits


def number_code(digits: str) -> str:
    """Sarvam's "User Identifier": SHA-256 of the number as +<digits>."""
    return hashlib.sha256(f"+{digits}".encode()).hexdigest()


def same_caller(caller: str, digits: str) -> bool:
    """Whether Sarvam's caller value (the number, or its code) is this number."""
    if not digits:
        return False
    caller = (caller or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{64}", caller):
        return hmac.compare_digest(caller.lower(), number_code(digits))
    return caller_digits(caller) == digits


def caller_name(caller: str, contacts: list[dict[str, Any]]) -> str:
    """A contact's name for the caller, else their number, else unknown."""
    for contact in contacts:
        if same_caller(caller, caller_digits(str(contact.get("phone") or ""))):
            return str(contact.get("name") or "a contact")
    digits = caller_digits(caller) if not re.fullmatch(r"[0-9a-fA-F]{64}", caller or "") else ""
    return f"+{digits}" if digits else "an unknown caller"


def latest_request(transcript: Any) -> str:
    """What the caller said last, from Sarvam's call transcript (a list of
    turns or plain 'role: text' lines)."""
    if isinstance(transcript, str):
        text = transcript.strip()
        try:
            transcript = json.loads(text)
        except ValueError:
            said: list[str] = []
            for line in text.splitlines():
                who, _, rest = line.partition(":")
                if rest and who.strip().lower() in ("user", "caller", "customer", "human"):
                    said.append(rest.strip())
                elif rest and who.strip().lower() in ("agent", "bot", "assistant"):
                    said = []
            return " ".join(said) or (text.splitlines()[-1] if text else "")
    if isinstance(transcript, list):
        said = []
        for turn in transcript:
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("role") or turn.get("speaker") or "").lower()
            words = str(turn.get("text") or turn.get("content") or turn.get("message") or "")
            if role in ("user", "caller", "customer", "human"):
                said.append(words.strip())
            elif role in ("agent", "bot", "assistant"):
                said = []
        return " ".join(part for part in said if part)
    return ""


def pending_approval(messages: list[dict[str, Any]]) -> dict[str, str] | None:
    """An OK Friday asked for in the latest turn that has no answer yet (the
    voice screen's pendingApprovalOf)."""
    answered = {m.get("toolCallId") for m in messages if m.get("role") == "tool"}
    saved: list[str] = []
    for message in reversed(messages):
        if message.get("role") == "user":
            break
        if message.get("role") == "tool" and isinstance(message.get("content"), str):
            try:
                result = json.loads(message["content"])
            except ValueError:
                continue
            if isinstance(result, dict) and result.get("status") == "approval_required":
                if isinstance(result.get("approvalId"), str):
                    saved.insert(0, result["approvalId"])
    for message in reversed(messages):
        if message.get("role") == "user":
            return None
        if message.get("role") != "assistant":
            continue
        for call in message.get("toolCalls") or []:
            function = call.get("function") or {}
            if function.get("name") != APPROVAL_TOOL or call.get("id") in answered:
                continue
            try:
                args = json.loads(function.get("arguments") or "{}")
            except ValueError:
                return None
            approval = args.get("approvalId") if args.get("approvalId") in saved else None
            approval = approval or (saved[-1] if saved else None)
            if not approval:
                return None
            return {
                "toolCallId": call["id"],
                "approvalId": approval,
                "summary": str(args.get("summary") or "this action"),
            }
    return None


def reply_of(new_messages: list[dict[str, Any]]) -> str:
    for message in reversed(new_messages):
        if message.get("role") == "assistant" and isinstance(message.get("content"), str):
            if message["content"].strip():
                return message["content"].strip()
    return ""


@dataclass
class PhoneCall:
    owner: bool = True  # the owner (Friday itself) or anyone else (Reception)
    messages: list[dict[str, Any]] = field(default_factory=list)
    thread_id: str | None = None
    working: asyncio.Task | None = None
    waiting_reply: str = ""
    seen: float = field(default_factory=time.time)


class PhoneDesk:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        reply_within: float = REPLY_WITHIN_SECONDS,
    ):
        self._settings = settings
        self._client = client
        self._reply_within = reply_within
        self._calls: dict[str, PhoneCall] = {}

    @property
    def app_url(self) -> str:
        return self._settings.get("FRIDAY_APP_URL", "http://127.0.0.1:4310").rstrip("/")

    async def _post(self, path: str, body: dict[str, Any], timeout: float = 300) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._settings.get("OWNER_TOKEN"):
            headers["Authorization"] = f"Bearer {self._settings.get('OWNER_TOKEN')}"
        client = self._client or httpx.AsyncClient(timeout=timeout)
        try:
            response = await client.post(f"{self.app_url}/api{path}", json=body, headers=headers)
        finally:
            if not self._client:
                await client.aclose()
        data = response.json() if response.content else {}
        if response.status_code >= 400:
            raise RuntimeError(data.get("error") or f"HTTP {response.status_code}")
        return data

    def _call(self, call_id: str, owner: bool) -> PhoneCall:
        now = time.time()
        for key in [k for k, c in self._calls.items() if now - c.seen > KEEP_CALLS_SECONDS]:
            del self._calls[key]
        key = f"{'owner' if owner else 'client'}:{call_id or 'call'}"
        call = self._calls.setdefault(key, PhoneCall(owner=owner))
        call.seen = now
        return call

    async def _run(self, call: PhoneCall) -> str:
        """One turn of Friday on the app; the sentence to say afterwards."""
        start = 0
        recent = call.messages[-KEEP_MESSAGES:]
        while start < len(recent) and recent[start].get("role") != "user":
            start += 1
        ran = await self._post(
            "/friday/phone/run",
            {"messages": recent[start:] or recent, "caller": "owner" if call.owner else "client"},
        )
        call.thread_id = ran.get("threadId") or call.thread_id
        new = ran.get("newMessages") or []
        call.messages.extend(new)
        waiting = pending_approval(call.messages)
        if waiting:
            return f"I need your OK: {waiting['summary'].rstrip('. ')}. Shall I go ahead?"
        return reply_of(new) or "Done."

    async def _decide(self, call: PhoneCall, waiting: dict[str, str], approve: bool) -> str:
        answer: dict[str, Any] = {
            "approved": False,
            "message": "The owner declined this action. Do not perform it or try another way.",
        }
        if approve:
            try:
                result = await self._post(
                    f"/conversations/{call.thread_id}/connection-actions",
                    {"toolCallId": waiting["toolCallId"], "approvalId": waiting["approvalId"]},
                )
                answer = {"approved": True, **result}
            except Exception as error:
                answer = {"approved": False, "message": f"This could not run: {error}"}
        call.messages.append(
            {
                "id": str(uuid.uuid4()),
                "role": "tool",
                "toolCallId": waiting["toolCallId"],
                "content": json.dumps(answer),
            }
        )
        return await self._run(call)

    async def ask(self, call_id: str, request: str, owner: bool = True, context: str = "") -> str:
        """What to say back to the caller. owner: the owner's own number (the
        Friday Dot, which can do anything) or anyone else (Reception).
        context: why Friday placed this call, when it did."""
        call = self._call(call_id, owner)
        request = request.strip()
        if call.working and not call.working.done():
            return "I'm still working on your last request. Ask me again in a moment."
        if call.working:
            # The previous request finished after Sarvam stopped waiting.
            finished, call.working = call.working, None
            try:
                said = finished.result()
            except Exception as error:
                said = f"That did not work: {error}"
            if not request or re.search(r"\b(done|ho gaya|hua|status|update)\b", request, re.I):
                return said
            call.waiting_reply = said
        if not request:
            return "Sorry, I didn't catch that. Please say it again."
        waiting = pending_approval(call.messages) if call.owner else None
        if waiting and call.thread_id:
            if YES.search(request) and not NO.search(request):
                job = self._decide(call, waiting, True)
            elif NO.search(request):
                job = self._decide(call, waiting, False)
            else:
                return f"I still need your OK: {waiting['summary'].rstrip('. ')}. Please say yes or no."
        else:
            if context and not call.messages:
                # The first turn says why Friday called, so the Dot stays on it.
                request = f"(Note: you placed this call. {context.strip()})\n{request}"
            call.messages.append({"id": str(uuid.uuid4()), "role": "user", "content": request})
            job = self._run(call)
        task = asyncio.ensure_future(job)
        try:
            said = await asyncio.wait_for(asyncio.shield(task), self._reply_within)
        except TimeoutError:
            call.working = task
            return "I'm on it. This will take a little while; ask me if it's done in a minute."
        except Exception as error:
            return f"Sorry, that did not work: {error}"
        earlier, call.waiting_reply = call.waiting_reply, ""
        return f"{earlier} {said}".strip() if earlier else said
