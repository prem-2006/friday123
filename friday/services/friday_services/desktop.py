"""Windows desktop apps for the Operator Dot, through UI Automation.

Apps are listed from the Start menu and opened by name. A window is described
as numbered controls that the model acts on by number. WhatsApp windows are
off limits here: messages and calls go through the WhatsApp tools, which always
ask the owner first.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

INTERACTIVE = {
    "Button",
    "Edit",
    "MenuItem",
    "ListItem",
    "TabItem",
    "CheckBox",
    "RadioButton",
    "ComboBox",
    "Hyperlink",
    "TreeItem",
    "Document",
    "SplitButton",
}
GUARDED_WINDOWS = ("whatsapp",)
SPECIAL_KEYS = {
    "enter": "ENTER",
    "return": "ENTER",
    "tab": "TAB",
    "escape": "ESC",
    "esc": "ESC",
    "backspace": "BACKSPACE",
    "delete": "DELETE",
    "space": "SPACE",
    "up": "UP",
    "down": "DOWN",
    "left": "LEFT",
    "right": "RIGHT",
    "home": "HOME",
    "end": "END",
    "pageup": "PGUP",
    "pagedown": "PGDN",
    **{f"f{n}": f"F{n}" for n in range(1, 13)},
}
MODIFIERS = {"ctrl": "^", "control": "^", "shift": "+", "alt": "%"}


class DesktopError(RuntimeError):
    pass


def to_send_keys(combo: str) -> str:
    """'Ctrl+Shift+N' -> '^+n', 'Enter' -> '{ENTER}' (pywinauto key syntax)."""
    combo = combo.strip()
    plus_key = len(combo) > 1 and combo.endswith("++")  # e.g. Ctrl++ (zoom in)
    parts = [part.strip() for part in combo.split("+") if part.strip()]
    if plus_key:
        parts.append("+")
    if not parts:
        raise DesktopError("Say which key to press, e.g. Enter or Ctrl+N.")
    *mods, key = parts
    prefix = ""
    for mod in mods:
        if mod.lower() not in MODIFIERS:
            raise DesktopError(f"Unknown modifier key: {mod}.")
        prefix += MODIFIERS[mod.lower()]
    name = key.lower()
    if name in SPECIAL_KEYS:
        return prefix + "{" + SPECIAL_KEYS[name] + "}"
    if len(key) == 1:
        return prefix + ("{" + key + "}" if key in "{}[]()+^%~" else key.lower())
    raise DesktopError(f"Unknown key: {key}.")


def escape_text(text: str) -> str:
    """Make free text safe for pywinauto send_keys."""
    return "".join("{" + c + "}" if c in "{}[]()+^%~" else c for c in text)


def start_menu_apps() -> list[dict[str, str]]:
    out = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout
    data = json.loads(out or "[]")
    rows = data if isinstance(data, list) else [data]
    return [{"name": row["Name"], "app_id": row["AppID"]} for row in rows if row.get("Name")]


def visible_window_titles() -> list[str]:
    """Titles of visible top-level windows, straight from Win32: about 2 ms,
    where walking UI Automation takes most of a second."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    titles: list[str] = []

    def add(hwnd, _param):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                title = buffer.value.strip()
                if title and title not in titles:
                    titles.append(title)
        return True

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(add)
    user32.EnumWindows(callback, 0)
    return titles


# Browser processes, and the word their own windows' titles end with.
BROWSER_TITLES = {
    "chrome": "google chrome",
    "msedge": "edge",
    "firefox": "firefox",
    "brave": "brave",
    "opera": "opera",
    "vivaldi": "vivaldi",
}


def app_window_belongs(app: dict[str, str], window: dict[str, Any]) -> bool:
    """Whether an open window ({title, proc, app}, from winctl) is this
    Start-menu app's. A site installed as an app (YouTube from Chrome) runs in
    the browser's process, titled with the site only ("lofi - YouTube"); the
    browser's own windows end with its name ("… - Google Chrome")."""
    from friday_services.winctl import key, matches

    name, proc, title = app["name"], window["proc"].lower(), window["title"].casefold()
    browser = BROWSER_TITLES.get(proc)
    browser_window = bool(browser) and title.endswith(browser)
    if "_crx_" in app["app_id"] or "_pwa" in app["app_id"].lower():
        return bool(browser) and not browser_window and name.casefold() in title
    if browser:  # "Google Chrome" is the browser's own windows, not its sites
        return browser_window and matches(window, name)
    # "ChatGPT Classic" runs as ChatGPT.exe: its first word names the process.
    return matches(window, name) or key(name.split()[0]) in (key(proc), key(window["app"]))


def open_windows_of(app: dict[str, str]) -> list[dict[str, Any]]:
    """The app's open windows, the one used last first."""
    from friday_services import winctl

    return [w for w in winctl.windows() if app_window_belongs(app, w)]


def bring_window(hwnd: int) -> None:
    from friday_services import winctl

    winctl.bring(hwnd)


def close_window(hwnd: int) -> None:
    """What the window's X does: the app may still ask to save first."""
    import ctypes

    ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE


# Windows media keys: they control whatever is playing (YouTube app, Spotify, …).
MEDIA_KEYS = {
    "play": 0xB3,  # play/pause toggles
    "pause": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "volume_up": 0xAF,
    "volume_down": 0xAE,
    "mute": 0xAD,
}


def press_media_key(action: str) -> None:
    import ctypes

    vk = MEDIA_KEYS[action]
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP


def launch_app(app_id: str) -> None:
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"])


@dataclass
class Control:
    ref: int
    kind: str
    name: str
    wrapper: Any


VIDEO_LINK = re.compile(r"\d+ (?:seconds?|minutes?|hours?)\W*$|^mix [-–]", re.I)
VIDEO_LENGTH = re.compile(r"(?:^|\s+)\d+ (?:seconds?|minutes?|hours?)[\s,\w]*$")


def shown_videos(links) -> list[tuple[Any, str]]:
    """(link, title) of the videos among a page's links, in page order, each
    once (a thumbnail and its title link are the same video)."""
    seen: set[str] = set()
    videos = []
    for link, text in links:
        text = " ".join(text.split())
        if not VIDEO_LINK.search(text):
            continue
        name = VIDEO_LENGTH.sub("", text).strip()
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        videos.append((link, name))
    return videos


def pick_video(videos: list[tuple[Any, str]], n: int = 1, title: str = ""):
    """The video called `title` (its start, or a part of it), else the nth."""
    if title:
        want = " ".join(title.casefold().split())
        return next((v for v in videos if v[1].casefold().startswith(want[:40])), None) or next(
            (v for v in videos if want[:40] in v[1].casefold()), None
        )
    return videos[n - 1] if 0 < n <= len(videos) else None


class DesktopApps:
    """Use only from one COM thread (see worker.Worker)."""

    def __init__(
        self,
        list_apps: Callable[[], list[dict[str, str]]] = start_menu_apps,
        launch: Callable[[str], None] = launch_app,
        desktop: Any = None,
        send_keys: Callable[..., None] | None = None,
        window_titles: Callable[[], list[str]] | None = None,
        app_windows: Callable[[dict[str, str]], list[dict[str, Any]]] | None = None,
        bring: Callable[[int], None] | None = None,
        close: Callable[[int], None] | None = None,
    ):
        self._list_apps = list_apps
        # Tests that pass a fake desktop read its windows instead.
        self._titles = window_titles or (visible_window_titles if desktop is None else None)
        real = desktop is None
        self._app_windows = app_windows or (open_windows_of if real else lambda app: [])
        self._bring = bring or (bring_window if real else lambda hwnd: None)
        self._close = close or (close_window if real else lambda hwnd: None)
        self._launch = launch
        self._desktop = desktop
        self._send_keys = send_keys
        self._apps: list[dict[str, str]] | None = None
        self._controls: dict[int, Control] = {}
        self._window: Any = None

    def _uia(self):
        if self._desktop is None:
            from pywinauto import Desktop

            self._desktop = Desktop(backend="uia")
        return self._desktop

    def _keys(self, keys: str, **kwargs) -> None:
        if self._send_keys is None:
            from pywinauto.keyboard import send_keys

            self._send_keys = send_keys
        self._send_keys(keys, **kwargs)

    def warm(self) -> None:
        """Load the Start menu list ahead of the first request (about 2 s)."""
        if self._apps is None:
            self._apps = self._list_apps()

    def apps(self, query: str = "") -> list[str]:
        if self._apps is None:
            self._apps = self._list_apps()
        names = sorted({app["name"] for app in self._apps})
        return [name for name in names if query.casefold() in name.casefold()][:60]

    def names(self) -> list[str]:
        """Every Start-menu app's name (for instant commands)."""
        if self._apps is None:
            self._apps = self._list_apps()
        return sorted({app["name"] for app in self._apps})

    def _find_app(self, name: str) -> dict[str, str]:
        if self._apps is None:
            self._apps = self._list_apps()
        wanted = name.casefold().strip()
        exact = [app for app in self._apps if app["name"].casefold() == wanted]
        partial = [app for app in self._apps if wanted in app["name"].casefold()]
        matches = exact or partial
        if not matches:
            raise DesktopError(f"No app called {name!r}. Use list_apps to see what is installed.")
        if not exact and len({app["name"] for app in partial}) > 1:
            options = ", ".join(sorted({app["name"] for app in partial})[:6])
            raise DesktopError(f"Several apps match {name!r}: {options}. Say which one.")
        return matches[0]

    def windows(self) -> list[str]:
        if self._titles:
            return self._titles()
        titles = []
        for window in self._uia().windows():
            title = (window.window_text() or "").strip()
            if title and window.is_visible() and title not in titles:
                titles.append(title)
        return titles

    def _window_named(self, title: str):
        wanted = title.casefold().strip()
        windows = [w for w in self._uia().windows() if w.is_visible() and w.window_text()]
        exact = [w for w in windows if w.window_text().casefold() == wanted]
        partial = [w for w in windows if wanted in w.window_text().casefold()]
        window = (exact or partial or [None])[0]
        if window is None:
            raise DesktopError(f"No open window called {title!r}. Use list_windows.")
        if any(guard in window.window_text().casefold() for guard in GUARDED_WINDOWS):
            raise DesktopError(
                "WhatsApp is controlled only through the WhatsApp tools, which ask the owner first."
            )
        return window

    def open(self, name: str, wait_seconds: float = 12) -> dict:
        # Opening any app is fine, WhatsApp included; typing into WhatsApp is
        # not (messages go through the WhatsApp tools, which ask first).
        app = self._find_app(name)
        # Already open: that window comes to the front, no second copy.
        already = self._app_windows(app)
        if already:
            self._bring(already[0]["hwnd"])
            return {"opened": app["name"], "window": already[0]["title"], "already_open": True}
        before = set(self.windows())
        self._launch(app["app_id"])
        deadline = time.time() + wait_seconds
        first_word = app["name"].split()[0].casefold()
        while time.time() < deadline:
            time.sleep(0.2)
            titles = self.windows()
            new = [t for t in titles if t not in before]
            match = [t for t in titles if first_word in t.casefold()]
            if new or match:
                return {"opened": app["name"], "window": (new or match)[0]}
        return {"opened": app["name"], "window": None, "note": "Started; no new window seen yet."}

    def close(self, name: str) -> dict:
        """Close every window of the app, as its X would."""
        app = self._find_app(name)
        windows = self._app_windows(app)
        for window in windows:
            self._close(window["hwnd"])
        return {"closed": app["name"], "windows": len(windows)}

    def app_for_site(self, site: str) -> str | None:
        """The installed app for a site, if the owner has one: "ChatGPT" ->
        "ChatGPT Classic", "YouTube" -> "YouTube". Never a browser."""
        want = site.casefold().strip()
        names = self.names()
        for test in (lambda c: c == want, lambda c: c.startswith(want + " ")):
            for name in names:
                c = name.casefold()
                if test(c) and not any(b in c for b in BROWSER_TITLES.values()):
                    return name
        return None

    def installed_site(self, name: str) -> dict[str, str] | None:
        """A site installed as an app from the browser (YouTube), if it is."""
        if self._apps is None:
            self._apps = self._list_apps()
        return next(
            (
                app
                for app in self._apps
                if app["name"].casefold() == name.casefold() and "_crx_" in app["app_id"]
            ),
            None,
        )

    def site_window(self, name: str) -> dict[str, Any] | None:
        """The installed site's open window ({hwnd, title}), or None."""
        app = self.installed_site(name)
        windows = self._app_windows(app) if app else []
        return windows[0] if windows else None

    def search_and_click(
        self, name: str, query: str = "", link: str = "", wait_seconds: float = 10
    ) -> dict | None:
        """In an installed site's own window (opened, or brought to the front):
        search with the site's search box ("/" on YouTube), then click the link
        whose text starts with `link`. None when the site is not installed."""
        app = self.installed_site(name)
        if not app:
            return None
        window = self.site_window(name)
        if window is None:
            self.open(app["name"])
            deadline = time.time() + wait_seconds
            while window is None and time.time() < deadline:
                time.sleep(0.4)
                window = self.site_window(name)
            if window is None:
                raise DesktopError(f"{app['name']} did not open a window.")
            time.sleep(2.5)  # the page loads its search box
        self._bring(window["hwnd"])
        time.sleep(0.3)
        if query:
            box = self._search_box(window["hwnd"])
            if box is None:
                raise DesktopError(f"I could not find the search box in {app['name']}.")
            box.set_focus()
            time.sleep(0.2)
            self._keys("^a")
            self._keys(escape_text(query), with_spaces=True, pause=0.002)
            self._keys("{ENTER}")
        clicked = self.click_link(window["hwnd"], link, wait_seconds) if link else False
        return {"app": app["name"], "searched": bool(query), "clicked": clicked}

    def _search_box(self, hwnd: int, wait_seconds: float = 6):
        """The page's search box (YouTube's is a combo box in the page)."""
        window = self._uia().window(handle=hwnd)
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            for page in window.descendants(control_type="Document"):
                boxes = page.descendants(control_type="ComboBox")
                if boxes:
                    return boxes[0]
            time.sleep(0.4)
        return None

    def click_first_video(self, hwnd: int, wait_seconds: float = 6) -> str:
        """Click the first video on the page; its title, or ""."""
        return self.click_shown_video(hwnd, 1, "", wait_seconds)

    def click_shown_video(
        self, hwnd: int, n: int = 1, title: str = "", wait_seconds: float = 6
    ) -> str:
        """Click a video on the page as it is (no new search): the nth one, or
        the one whose title starts with `title`. Videos are the links whose text
        ends with their length ("… 4 hours, 20 minutes") or mixes. Its title, or ""."""
        window = self._uia().window(handle=hwnd)
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            videos = shown_videos(
                (link, link.window_text() or "")
                for link in window.descendants(control_type="Hyperlink")
            )
            pick = pick_video(videos, n, title)
            if pick:
                link, name = pick
                try:
                    link.invoke()
                except Exception:
                    link.click_input()
                return name
            time.sleep(0.5)
        return ""

    def click_link(self, hwnd: int, starts: str, wait_seconds: float = 10) -> bool:
        """Click (through UI Automation, the mouse stays put) the first link in
        the window whose text starts with `starts`, once the page shows it."""
        want = " ".join(starts.casefold().split())[:50]
        window = self._uia().window(handle=hwnd)
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            for link in window.descendants(control_type="Hyperlink"):
                text = " ".join((link.window_text() or "").casefold().split())
                if want and text.startswith(want):
                    try:
                        link.invoke()
                    except Exception:
                        link.click_input()
                    return True
            time.sleep(0.5)
        return False

    def _focus_app(self, name: str):
        """The app's window, in front and taking the keyboard; opens the app
        first if it has no window. Never WhatsApp: its tools ask first."""
        if any(guard in name.casefold() for guard in GUARDED_WINDOWS):
            raise DesktopError("Use the WhatsApp tools for WhatsApp; they ask the owner first.")
        app = self._find_app(name)
        first_word = app["name"].split()[0].casefold()
        title = next((t for t in self.windows() if first_word in t.casefold()), None)
        opened = title is None
        if opened:
            title = self.open(name)["window"]
            if not title:
                raise DesktopError(f"{app['name']} has not opened a window yet. Try again.")
        window = self._window_named(title)
        try:
            window.set_focus()
        except Exception:
            pass
        time.sleep(0.6 if opened else 0.2)  # let a new window take the keyboard
        return app, window, opened

    def write(self, name: str, text: str, replace: bool = False) -> dict:
        """Type text into an app: into its open window, or open it first.

        Lines become separate lines (Enter between them). The text goes where
        the app's cursor is, as if the owner typed it; replace=True first
        selects everything, so the new text takes the old text's place."""
        app, window, opened = self._focus_app(name)
        if replace:
            self._keys("^a")
        lines = text.replace("\r\n", "\n").split("\n")
        for index, line in enumerate(lines):
            if line:
                self._keys(escape_text(line), with_spaces=True, pause=0.005)
            if index < len(lines) - 1:
                self._keys("{ENTER}")
        return {"app": app["name"], "window": window.window_text(), "opened": opened}

    def clear(self, name: str) -> dict:
        """Removes all the text in an app's window (select all, delete)."""
        app, window, _opened = self._focus_app(name)
        self._keys("^a")
        self._keys("{DELETE}")
        return {"app": app["name"], "window": window.window_text()}

    def keys_in(self, name: str, combos: list[str]) -> dict:
        """Presses keys in an app's window, e.g. Ctrl+S to save, Ctrl+Z to undo."""
        app, window, _opened = self._focus_app(name)
        for combo in combos:
            self._keys(to_send_keys(combo))
            time.sleep(0.15)
        return {"app": app["name"], "window": window.window_text(), "pressed": combos}

    def snapshot(self, title: str) -> dict:
        window = self._window_named(title)
        try:
            window.set_focus()
        except Exception:
            pass
        self._window = window
        self._controls = {}
        lines = []
        for element in window.descendants():
            info = element.element_info
            if info.control_type not in INTERACTIVE:
                continue
            try:
                if not element.is_visible():
                    continue
            except Exception:
                continue
            ref = len(self._controls) + 1
            name = " ".join((info.name or "").split())[:80]
            self._controls[ref] = Control(ref, info.control_type, name, element)
            lines.append(f'[{ref}] {info.control_type} "{name}"')
            if ref >= 150:
                break
        return {"window": window.window_text(), "controls": "\n".join(lines)}

    def _control(self, ref: int) -> Control:
        if ref not in self._controls:
            raise DesktopError(f"No control [{ref}]. Take a new snapshot of the window first.")
        return self._controls[ref]

    def click(self, ref: int) -> dict:
        control = self._control(ref)
        control.wrapper.click_input()
        time.sleep(0.6)
        return {"clicked": f'{control.kind} "{control.name}"'}

    def type(self, ref: int, text: str, press_enter: bool = False) -> dict:
        control = self._control(ref)
        try:
            secret = bool(control.wrapper.element_info.element.CurrentIsPassword)
        except Exception:
            secret = False
        if secret:
            raise DesktopError("That is a password field. Ask the owner to type it themselves.")
        control.wrapper.click_input()
        self._keys(escape_text(text), with_spaces=True, with_newlines=False, pause=0.01)
        if press_enter:
            self._keys("{ENTER}")
        return {"typed_into": f'{control.kind} "{control.name}"', "pressed_enter": press_enter}

    def press(self, combo: str) -> dict:
        if self._window is not None and any(
            guard in (self._window.window_text() or "").casefold() for guard in GUARDED_WINDOWS
        ):
            raise DesktopError("Use the WhatsApp tools for WhatsApp.")
        self._keys(to_send_keys(combo))
        return {"pressed": combo}

    def screenshot(self) -> str:
        """JPEG of the window last worked on, for the owner's live view."""
        if self._window is None:
            return ""
        try:
            import base64
            import io

            image = self._window.capture_as_image()
            if image is None:
                return ""
            image.thumbnail((1280, 800))
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=45)
            return base64.b64encode(buffer.getvalue()).decode()
        except Exception:
            return ""
