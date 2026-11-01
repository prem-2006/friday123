"""In-memory call records, saved to disk when a call ends."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

FINAL_STATUSES = frozenset({"answered", "no_answer", "failed"})


@dataclass
class CallRecord:
    phone: str
    goal: str
    language: str
    context: str
    # business: Friday calls someone for the owner; owner: Friday calls the owner;
    # owner_in: the owner calls Friday; reception: anyone else calls Friday.
    kind: str = "business"
    id: str = field(default_factory=lambda: f"call_{uuid.uuid4().hex[:12]}")
    # queued -> ringing -> in_progress -> answered | no_answer | failed
    status: str = "queued"
    outcome: str | None = None
    summary: str | None = None
    details: dict[str, str] = field(default_factory=dict)
    transcript: list[dict[str, str]] = field(default_factory=list)
    latency_ms: list[int] = field(default_factory=list)
    error: str | None = None
    # The provider's id for the call (Twilio's or Exotel's CallSid).
    call_sid: str | None = None
    stream_started: bool = False
    # Proves an audio stream belongs to this call; never returned to the model.
    stream_token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    created_at: float = field(default_factory=time.time)
    ended_at: float | None = None

    @property
    def final(self) -> bool:
        return self.status in FINAL_STATUSES

    def result(self) -> dict:
        """What Friday's planner sees."""
        latency = (
            {
                "turns": len(self.latency_ms),
                "average_ms": round(sum(self.latency_ms) / len(self.latency_ms)),
                "max_ms": max(self.latency_ms),
            }
            if self.latency_ms
            else None
        )
        return {
            "call_id": self.id,
            "phone": self.phone,
            "status": self.status if self.final else "in_progress",
            "stage": self.status,
            "outcome": self.outcome,
            "summary": self.summary,
            "details": self.details,
            "transcript": [
                f"{'Friday' if line['speaker'] == 'friday' else 'Them'}: {line['text']}"
                for line in self.transcript
            ],
            "duration_seconds": round((self.ended_at or time.time()) - self.created_at),
            "latency": latency,
            "error": self.error,
        }


class CallStore:
    def __init__(self, data_dir: Path | None = None):
        self._calls: dict[str, CallRecord] = {}
        self._finished: dict[str, asyncio.Event] = {}
        self._dir = data_dir / "calls" if data_dir else None

    def create(
        self, *, phone: str, goal: str, language: str, context: str, kind: str = "business"
    ) -> CallRecord:
        record = CallRecord(phone=phone, goal=goal, language=language, context=context, kind=kind)
        self._calls[record.id] = record
        self._finished[record.id] = asyncio.Event()
        return record

    def get(self, call_id: str) -> CallRecord | None:
        return self._calls.get(call_id)

    def active(self) -> CallRecord | None:
        return next((call for call in self._calls.values() if not call.final), None)

    def waiting_for_audio(self, call_sid: str) -> CallRecord | None:
        """The call Friday placed whose audio is just arriving: by the
        provider's call SID, else the one placed call still ringing."""
        waiting = [
            call
            for call in self._calls.values()
            if not call.final and not call.stream_started and call.kind in ("business", "owner")
        ]
        by_sid = next((call for call in waiting if call_sid and call.call_sid == call_sid), None)
        return by_sid or (waiting[0] if len(waiting) == 1 else None)

    def set_stage(self, call_id: str, status: str) -> None:
        record = self._calls[call_id]
        if not record.final:
            record.status = status

    def add_turn(self, call_id: str, speaker: str, text: str) -> None:
        text = (text or "").strip()
        if text:
            self._calls[call_id].transcript.append({"speaker": speaker, "text": text})

    def add_latency(self, call_id: str, milliseconds: int) -> None:
        self._calls[call_id].latency_ms.append(milliseconds)

    def finish(
        self,
        call_id: str,
        status: str,
        *,
        outcome: str | None = None,
        summary: str | None = None,
        details: dict[str, str] | None = None,
        error: str | None = None,
    ) -> CallRecord:
        """Record the final result once; later calls change nothing."""
        if status not in FINAL_STATUSES:
            raise ValueError(f"{status!r} is not a final call status")
        record = self._calls[call_id]
        if record.final:
            return record
        record.status = status
        record.outcome = outcome or record.outcome
        record.summary = summary or record.summary
        record.details = details or record.details
        record.error = error or record.error
        record.ended_at = time.time()
        self._save(record)
        self._finished[call_id].set()
        return record

    async def wait(self, call_id: str, timeout: float) -> CallRecord:
        """Return when the call has a final result, or after `timeout` seconds."""
        try:
            await asyncio.wait_for(self._finished[call_id].wait(), timeout)
        except TimeoutError:
            pass
        return self._calls[call_id]

    def _save(self, record: CallRecord) -> None:
        if not self._dir:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        data = asdict(record)
        data.pop("stream_token")
        (self._dir / f"{record.id}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
