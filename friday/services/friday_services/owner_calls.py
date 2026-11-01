"""Friday phones its owner: for deadlines, and when asked ("call me at 5").

With Exotel set up (EXOTEL_*), Friday's own pipeline (calls/bot.py) makes
the call from its Indian number, with the owner prompt and ask_friday, so
the owner can get things done on the call. Otherwise a voice agent places
it: Sarvam (calls/sarvam.py, SARVAM_*) first, else Gnani's Inya
(calls/inya.py, INYA_API_KEY). Without either, the Twilio + Gnani pipeline. Calls are limited so a mistake can never ring the owner
again and again.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from friday_services.calls.inya import InyaDialer
from friday_services.calls.prompts import gnani_owner_prompt
from friday_services.calls.store import CallStore
from friday_services.config import REPO_ROOT, Settings

MIN_GAP_SECONDS = 10 * 60  # at most one call every 10 minutes
MAX_PER_DAY = 8


def owner_phone(settings: Settings, profile_path: Path | None = None) -> str | None:
    """FRIDAY_OWNER_PHONE, else the phone in the main page's Your details,
    in international format (+91 added to a 10-digit Indian number)."""
    raw = settings.get("FRIDAY_OWNER_PHONE")
    if not raw:
        try:
            path = profile_path or REPO_ROOT / "data" / "friday-profile.json"
            raw = str(json.loads(path.read_text("utf-8")).get("phone") or "")
        except (OSError, ValueError):
            raw = ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        digits = "91" + digits
    return f"+{digits}" if 11 <= len(digits) <= 15 else None


class OwnerCaller:
    def __init__(
        self,
        settings: Settings,
        store: CallStore,
        dialer: Any,
        clock=time.time,
        gnani: InyaDialer | None = None,
        sarvam: Any = None,
    ):
        self._settings = settings
        self._store = store
        self._dialer = dialer
        self._clock = clock
        self._gnani = gnani
        self._sarvam = sarvam
        self._calls: list[float] = []

    @property
    def agent(self) -> Any:
        """The voice agent that places calls to the owner, if one is set up
        and Friday's own Exotel pipeline is not."""
        if self._settings.call_provider == "exotel" and self._settings.own_calls_ready:
            return None
        for agent in (self._sarvam, self._gnani):
            if agent and agent.ready:
                return agent
        return None

    @property
    def via(self) -> str:
        agent = self.agent
        if agent is None:
            return self._settings.call_provider
        return "sarvam" if agent is self._sarvam else "gnani"

    def missing(self) -> list[str]:
        missing = [] if self.agent else self._settings.missing(self._settings.call_settings)
        if not owner_phone(self._settings):
            missing.append("your phone number (Your details, or FRIDAY_OWNER_PHONE)")
        return missing

    async def call(
        self, reason: str, urgent: bool = False, updates: str = "", opening: str = ""
    ) -> dict[str, Any]:
        """Rings the owner now. opening: the exact first words ("Hi Prem, you
        have a reminder: ..."); else the model says the reason nicely.
        Returns what happened, never raises for limits."""
        missing = self.missing()
        if missing:
            return {"called": False, "why": "Phone calls are not set up: " + ", ".join(missing)}
        now = self._clock()
        self._calls = [t for t in self._calls if now - t < 24 * 3600]
        if len(self._calls) >= MAX_PER_DAY:
            return {"called": False, "why": "I already called you 8 times today."}
        if self._calls and now - self._calls[-1] < MIN_GAP_SECONDS and not urgent:
            return {"called": False, "why": "I called you less than 10 minutes ago."}
        if self._store.active():
            return {"called": False, "why": "Another call is in progress."}
        if self.agent:
            return await self._call_with_agent(reason, updates, now, opening)
        record = self._store.create(
            phone=owner_phone(self._settings) or "",
            goal=reason,
            language=self._settings.get("FRIDAY_CALL_LANGUAGE", "en-IN"),
            context=updates,
            kind="owner",
        )
        try:
            record.call_sid = await self._dialer.place(record)
        except Exception as error:
            self._store.finish(record.id, "failed", error=str(error))
            return {"called": False, "why": f"The call could not be placed: {error}"}
        self._store.set_stage(record.id, "ringing")
        self._calls.append(now)
        return {"called": True, "call_id": record.id}

    async def _call_with_agent(
        self, reason: str, updates: str, now: float, opening: str = ""
    ) -> dict[str, Any]:
        agent = self.agent
        note = reason + (f"\nThe updates:\n{updates}" if updates else "")
        greeting = opening or await (self._gnani or InyaDialer(self._settings)).greeting(note)
        prompt = gnani_owner_prompt(
            self._settings.owner_name, greeting, reason, updates, self._settings.timezone
        )
        try:
            extra = {"note": reason, "updates": updates} if agent is self._sarvam else {}
            await agent.call(
                phone=owner_phone(self._settings) or "",
                greeting=greeting,
                prompt=prompt,
                reference=f"friday-{int(now)}",
                **extra,
            )
        except Exception as error:
            return {"called": False, "why": f"The call could not be placed: {error}"}
        self._calls.append(now)
        return {"called": True, "via": self.via}
