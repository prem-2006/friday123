"""What the helper Dots are doing right now, streamed to Friday's voice screen,
plus the owner's stop switch for every on-screen action."""

from __future__ import annotations

import asyncio
import itertools
import time
from collections import deque
from dataclasses import asdict, dataclass, field

from mcp.server.mcpserver.exceptions import ToolError

# How long a stop holds at most (see ActivityFeed.stopped).
STOP_HOLDS = 15.0


@dataclass
class Activity:
    id: int
    actor: str  # navigator | operator | friday
    action: str  # e.g. browser_open
    detail: str  # one short human sentence
    state: str = "running"  # running | done | failed
    image: str | None = None  # base64 JPEG of the screen after the action
    at: float = field(default_factory=time.time)

    def public(self) -> dict:
        return asdict(self)


class ActivityFeed:
    def __init__(self, keep: int = 200):
        self._events: deque[Activity] = deque(maxlen=keep)
        self._listeners: set[asyncio.Queue[Activity]] = set()
        # Voice windows (the voice screen, the desktop notch), oldest first.
        # Caps Lock goes to one only, so one request never runs twice: the
        # newest notch when one is open (a voice page left open in a browser
        # must not take it over whenever its stream reconnects), else the
        # newest voice window. Letting go goes where pressing went.
        self._voices: list[asyncio.Queue[Activity]] = []
        self._notches: set[asyncio.Queue[Activity]] = set()
        self._holder: asyncio.Queue[Activity] | None = None
        self._ids = itertools.count(1)
        self._stopped_at: float | None = None

    def windows(self) -> dict[str, int]:
        """How many voice windows and notches are connected (for Caps Lock)."""
        return {"voice_windows": len(self._voices), "notches": len(self._notches)}

    def emit(self, actor: str, action: str, detail: str, **fields) -> Activity:
        event = Activity(next(self._ids), actor, action, detail, **fields)
        self._events.append(event)
        for queue in list(self._listeners):
            queue.put_nowait(event)
        return event

    def signal(self, action: str, detail: str = "") -> None:
        """Tell the voice screen to do something (e.g. ptt_down, wake). Not kept
        in the history, so a page that connects later never replays it."""
        event = Activity(next(self._ids), "control", action, detail, state="done")
        if action == "notch_toggle":
            # Only the notch acts on it, wherever it is in the list.
            for queue in self._voices:
                queue.put_nowait(event)
        elif action == "ptt_up" and self._holder in self._voices:
            self._holder.put_nowait(event)
            self._holder = None
        elif self._voices:
            notches = [queue for queue in self._voices if queue in self._notches]
            target = (notches or self._voices)[-1]
            if action == "ptt_down":
                self._holder = target
            target.put_nowait(event)

    @property
    def watched(self) -> bool:
        """Whether a voice window is connected right now."""
        return bool(self._voices)

    def recent(self, limit: int = 30) -> list[dict]:
        return [event.public() for event in list(self._events)[-limit:]]

    def listen(self, voice: bool = False, notch: bool = False) -> asyncio.Queue[Activity]:
        queue: asyncio.Queue[Activity] = asyncio.Queue()
        self._listeners.add(queue)
        if voice or notch:
            self._voices.append(queue)
        if notch:
            self._notches.add(queue)
        return queue

    def unlisten(self, queue: asyncio.Queue[Activity]) -> None:
        self._listeners.discard(queue)
        self._notches.discard(queue)
        if queue in self._voices:
            self._voices.remove(queue)

    @property
    def stopped(self) -> bool:
        """A stop ends what is running; it never outlasts STOP_HOLDS seconds,
        so a lost "resume" (the voice screen reloaded, the app restarted)
        cannot leave Friday refusing everything."""
        if self._stopped_at is not None and time.monotonic() - self._stopped_at > STOP_HOLDS:
            self._stopped_at = None
        return self._stopped_at is not None

    def stop(self) -> None:
        self._stopped_at = time.monotonic()
        self.emit("friday", "stop", "You stopped all on-screen actions.", state="done")

    def resume(self) -> None:
        self._stopped_at = None
        self.emit("friday", "resume", "On-screen actions are allowed again.", state="done")

    def guard(self) -> None:
        if self.stopped:
            raise ToolError(
                "The owner pressed stop. Do nothing more on screen and tell the owner what was done so far."
            )
