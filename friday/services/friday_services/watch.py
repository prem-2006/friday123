"""Watch for me: "tell me when the download is done".

From Atticus (SaarthurR/atticus-mac, FindlayHi/atticus-windows; MIT): set up
once, then check a window every few seconds on this PC with no model calls,
so waiting is free. Here the window's text comes from its title and Windows UI
Automation instead of OCR. When the condition is met Friday says so (on the
voice screen, or with Windows' voice).

A window closing counts as "gone"; a minimized window still has its text.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

Until = Literal["gone", "shows"]
EVERY_SECONDS = 5.0
GIVE_UP_SECONDS = 3 * 3600
MAX_WATCHES = 5


@dataclass
class Watch:
    id: int
    app: str
    until: Until
    phrase: str
    title_words: str
    say: str
    started: float = field(default_factory=time.monotonic)

    def describe(self) -> str:
        what = f'"{self.phrase}"' if self.phrase else "its window"
        verb = "appears" if self.until == "shows" else "goes away"
        return f"{self.app}: until {what} {verb}"


def window_text(app: str, title_words: str = "") -> str | None:
    """The text a person sees in the app's front window (title and controls),
    or None when no such window is open."""
    from friday_services import winctl

    found = winctl.find(app, title_words)
    if not found:
        return None
    hwnd = found[0]["hwnd"]
    parts = [found[0]["title"]]
    try:
        import comtypes
        from pywinauto import Desktop

        comtypes.CoInitialize()
        window = Desktop(backend="uia").window(handle=hwnd)
        for i, item in enumerate(window.descendants()):
            if i >= 400:
                break
            text = (item.window_text() or "").strip()
            if text:
                parts.append(text)
    except Exception:
        pass  # the title alone still answers "is it open?"
    return "\n".join(parts)


class Watches:
    def __init__(
        self,
        announce: Callable[[str], None],
        read: Callable[[str, str], str | None] = window_text,
        every: float = EVERY_SECONDS,
        give_up: float = GIVE_UP_SECONDS,
    ):
        self._announce = announce
        self._read = read
        self._every = every
        self._give_up = give_up
        self._ids = itertools.count(1)
        self._watches: dict[int, Watch] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{"id": w.id, "watching": w.describe()} for w in self._watches.values()]

    def add(
        self, app: str, until: Until, phrase: str = "", title_words: str = "", say: str = ""
    ) -> Watch:
        if until not in ("gone", "shows"):
            raise ValueError("until must be gone or shows")
        now = self._read(app, title_words)
        if now is None and until == "gone" and not phrase:
            raise LookupError(f"No open window called {app!r} to watch.")
        if phrase and now is not None and self._met(until, phrase, now):
            raise ValueError(
                f'"{phrase}" is {"already" if until == "shows" else "not"} on screen in {app}.'
            )
        with self._lock:
            if len(self._watches) >= MAX_WATCHES:
                raise ValueError(f"I can watch at most {MAX_WATCHES} things at once.")
            watch = Watch(next(self._ids), app, until, phrase.strip(), title_words.strip(), say)
            self._watches[watch.id] = watch
            if (
                self._thread is None
            ):  # under the lock, so a thread that is just ending cannot miss it
                self._thread = threading.Thread(target=self._loop, name="friday-watch", daemon=True)
                self._thread.start()
        return watch

    def stop_all(self) -> int:
        with self._lock:
            n = len(self._watches)
            self._watches.clear()
        self._wake.set()
        return n

    @staticmethod
    def _met(until: Until, phrase: str, text: str | None) -> bool:
        if not phrase:
            return (text is None) if until == "gone" else (text is not None)
        present = text is not None and phrase.casefold() in text.casefold()
        return present if until == "shows" else not present

    def check(self) -> None:
        """One round over every watch (the thread calls this; tests call it directly)."""
        with self._lock:
            watches = list(self._watches.values())
        for watch in watches:
            try:
                text = self._read(watch.app, watch.title_words)
            except Exception:
                continue
            fired = self._met(watch.until, watch.phrase, text)
            expired = time.monotonic() - watch.started > self._give_up
            if not (fired or expired):
                continue
            with self._lock:
                if self._watches.pop(watch.id, None) is None:
                    continue
            if fired:
                self._announce(watch.say or _done_sentence(watch))
            else:
                self._announce(f"I stopped watching {watch.app}; nothing changed for three hours.")

    def _loop(self) -> None:
        while True:
            with self._lock:
                if not self._watches:
                    self._thread = None
                    return
            self.check()
            self._wake.wait(self._every)
            self._wake.clear()


def _done_sentence(watch: Watch) -> str:
    if watch.phrase:
        verb = "now shows" if watch.until == "shows" else "no longer shows"
        return f'{watch.app} {verb} "{watch.phrase}".'
    return f"{watch.app} has closed." if watch.until == "gone" else f"{watch.app} is open."
