"""What is playing, and the browser the owner used last.

Pause, play, next, previous, rewind and forward go through Windows' own media
controls, which reach whatever is playing (YouTube in any browser, Spotify,
the YouTube app): the session Windows shows in its media flyout, which is the
one the owner touched last. When a control is not offered there (YouTube's
next on a single video, say), YouTube's own keys are pressed in the browser
window that shows it.

Songs and sites open in the browser the owner used last (the frontmost
browser window), not in a new window: a song replaces the video in the tab
when that tab is YouTube, otherwise it opens in a new tab of that window.
Friday's own Chrome (its profile folder) is driven through Playwright instead.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

BROWSERS = {
    "chrome.exe": "Chrome",
    "msedge.exe": "Edge",
    "firefox.exe": "Firefox",
    "brave.exe": "Brave",
    "opera.exe": "Opera",
    "vivaldi.exe": "Vivaldi",
}
# YouTube's own keys, for a browser window showing a video.
YOUTUBE_KEYS = {
    "pause": "k",
    "play": "k",
    "stop": "k",
    "next": "+n",
    "previous": "+p",
    "rewind": "j",  # 10 s back
    "forward": "l",  # 10 s ahead
}
SECONDS = 10
# The rest of YouTube's player keys (youtube_control): full screen, captions…
YOUTUBE_PLAYER_KEYS = {
    **YOUTUBE_KEYS,
    "fullscreen": "f",
    "exit_fullscreen": "{ESC}",
    "theater": "t",
    "captions": "c",
    "mute": "m",
    "unmute": "m",
    "faster": "+.",
    "slower": "+,",
}


def shows_youtube(window: dict[str, Any]) -> bool:
    """A window showing YouTube: the YouTube app ("YouTube - <video>") or a
    browser tab ("<video> - YouTube - Google Chrome")."""
    title = window.get("title") or ""
    return f"{window.get('proc', '')}.exe".casefold() in BROWSERS and (
        title == "YouTube" or title.startswith("YouTube - ") or " - YouTube" in title
    )


def youtube_window(windows: list[dict[str, Any]], in_front: bool = False) -> dict[str, Any] | None:
    """The YouTube window to act in, the one used last. in_front: only when it
    is the owner's window in front (Friday's own windows aside), so "full
    screen" means the video only while the owner is on YouTube."""
    for window in windows:
        if window.get("proc", "").casefold() == "electron" or (
            window.get("title") or ""
        ).startswith("Friday"):
            continue  # the notch, the toast, the voice screen
        if shows_youtube(window):
            return window
        if in_front:
            return None
    return None


class MediaError(RuntimeError):
    pass


@dataclass(frozen=True)
class BrowserWindow:
    hwnd: int
    title: str
    name: str  # Chrome, Edge, …
    friday: bool  # Friday's own Chrome (its profile folder)

    @property
    def on_youtube(self) -> bool:
        return "youtube" in self.title.casefold()


def browser_windows(friday_profile: Path | None = None) -> list[BrowserWindow]:
    """Visible browser windows, the one used last first (Win32 lists windows
    front to back)."""
    import ctypes
    from ctypes import wintypes

    import psutil

    user32 = ctypes.windll.user32
    profile = str(friday_profile).casefold() if friday_profile else ""
    found: list[BrowserWindow] = []
    names: dict[int, tuple[str, bool]] = {}

    def add(hwnd, _param):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):  # owned popups
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in names:
            try:
                process = psutil.Process(pid.value)
                exe = process.name().casefold()
                mine = (
                    bool(profile)
                    and exe in BROWSERS
                    and profile in " ".join(process.cmdline()).casefold()
                )
            except (psutil.Error, OSError):
                exe, mine = "", False
            names[pid.value] = (exe, mine)
        exe, mine = names[pid.value]
        if exe in BROWSERS:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value
            # A browser window's title ends with the browser's name; an
            # installed site (an app window) has only the site's own title.
            if mine or BROWSERS[exe].casefold() in title.casefold():
                found.append(BrowserWindow(int(hwnd), title, BROWSERS[exe], mine))
        return True

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(add)
    user32.EnumWindows(callback, 0)
    return found


async def _session():
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Manager,
    )

    manager = await Manager.request_async()
    return manager.get_current_session()


async def _control_session(action: str, seconds: int) -> dict[str, Any] | None:
    """Act on the media session Windows shows; None if nothing is playing or
    the control is not offered."""
    session = await _session()
    if session is None:
        return None
    controls = session.get_playback_info().controls
    done = False
    if action in ("pause", "stop") and controls.is_pause_enabled:
        done = await session.try_pause_async()
    elif action == "play" and controls.is_play_enabled:
        done = await session.try_play_async()
    elif action == "next" and controls.is_next_enabled:
        done = await session.try_skip_next_async()
    elif action == "previous" and controls.is_previous_enabled:
        done = await session.try_skip_previous_async()
    elif action in ("rewind", "forward") and controls.is_playback_position_enabled:
        timeline = session.get_timeline_properties()
        position = timeline.position
        playing = session.get_playback_info().playback_status == 4  # PLAYING
        if playing and timeline.last_updated_time:
            position += datetime.now(UTC) - timeline.last_updated_time
        step = timedelta(seconds=seconds if action == "forward" else -seconds)
        target = max(timedelta(0), min(position + step, timeline.end_time or position + step))
        done = await session.try_change_playback_position_async(
            int(target.total_seconds() * 10_000_000)
        )
    elif action in ("pause", "stop", "play"):
        # Already in that state (pause offered only while playing, and back).
        done = True
    if not done:
        return None
    properties = await session.try_get_media_properties_async()
    return {"title": properties.title or "", "app": session.source_app_user_model_id}


def control_session(action: str, seconds: int = SECONDS) -> dict[str, Any] | None:
    try:
        return asyncio.run(_control_session(action, seconds))
    except Exception:
        return None  # no media session support: fall back to keys


def focus(hwnd: int) -> None:
    from pywinauto import Desktop

    window = Desktop(backend="win32").window(handle=hwnd)
    if window.is_minimized():
        window.restore()
    window.set_focus()
    time.sleep(0.25)


class OwnerMedia:
    """The owner's own browser and players (see the module docstring)."""

    def __init__(
        self,
        friday_profile: Path | None,
        windows: Callable[[], list[BrowserWindow]] | None = None,
        session: Callable[[str, int], dict[str, Any] | None] = control_session,
        send_keys: Callable[..., None] | None = None,
        focus_window: Callable[[int], None] = focus,
    ):
        self._windows = windows or (lambda: browser_windows(friday_profile))
        self._session = session
        self._send_keys = send_keys
        self._focus = focus_window

    def _keys(self, keys: str, **kwargs) -> None:
        if self._send_keys is None:
            from pywinauto.keyboard import send_keys

            self._send_keys = send_keys
        self._send_keys(keys, **kwargs)

    def last_browser(self) -> BrowserWindow | None:
        found = self._windows()
        return found[0] if found else None

    def for_youtube(self) -> BrowserWindow | None:
        """The window already showing YouTube (a new song replaces the old one
        there), else the browser used last."""
        found = self._windows()
        return next((w for w in found if w.on_youtube), found[0] if found else None)

    def control(self, action: str, seconds: int = SECONDS) -> dict[str, Any] | None:
        """Pause, play, stop, next, previous, rewind or forward what is playing.
        None when nothing could be reached this way."""
        done = self._session(action, seconds)
        if done is not None:
            return done
        if action not in YOUTUBE_KEYS:
            return None
        window = next((w for w in self._windows() if w.on_youtube), None)
        if window is None:
            return None
        self._focus(window.hwnd)
        presses = max(1, round(seconds / SECONDS)) if action in ("rewind", "forward") else 1
        for _ in range(presses):
            self._keys(YOUTUBE_KEYS[action])
        return {"title": window.title.split(" - YouTube")[0], "app": window.name}

    def youtube(
        self, action: str, window: dict[str, Any], seconds: int = SECONDS
    ) -> dict[str, Any]:
        """Press YouTube's own key for it in that YouTube window (app or tab)."""
        self._focus(window["hwnd"])
        presses = max(1, round(seconds / SECONDS)) if action in ("rewind", "forward") else 1
        for _ in range(presses):
            self._keys(YOUTUBE_PLAYER_KEYS[action])
        title = re.sub(r"^YouTube - |\s*- YouTube.*$|^\(\d+\)\s*", "", window["title"])
        return {"title": title, "app": "YouTube"}

    def open_url(self, url: str, window: BrowserWindow, same_tab: bool) -> None:
        """Open a page in that browser window: in its current tab, or a new one."""
        from friday_services.desktop import escape_text

        self._focus(window.hwnd)
        self._keys("^l" if same_tab else "^t")
        time.sleep(0.15)
        self._keys(escape_text(url), with_spaces=True, pause=0.002)
        self._keys("{ENTER}")
