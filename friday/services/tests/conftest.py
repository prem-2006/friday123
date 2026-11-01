"""Shared fixtures. Nothing here reaches Twilio, Gnani, Evon or WhatsApp."""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn

from friday_services.calls.store import CallRecord, CallStore
from friday_services.config import Settings
from friday_services.desktop import DesktopApps
from friday_services.main import create_app

TOKEN = "test-token-0123456789abcdef"
FAKE_ENV = {
    "FRIDAY_SERVICES_TOKEN": TOKEN,
    "FRIDAY_OWNER_NAME": "Asha",
    "GNANI_API_KEY": "fake-gnani",
    "EVON_BASE_URL": "http://127.0.0.1:9/v1",
    "EVON_MODEL": "gnani-evon-v3.3",
    "TWILIO_ACCOUNT_SID": "AC00000000000000000000000000000000",
    "TWILIO_AUTH_TOKEN": "twilio-test-auth-token",
    "TWILIO_FROM_NUMBER": "+15550001111",
    "PUBLIC_URL": "https://friday.example.test",
    "TIMEZONE": "Asia/Kolkata",
}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.from_env(FAKE_ENV, data_dir=tmp_path)


class FakeDialer:
    def __init__(self):
        self.placed: list[CallRecord] = []
        self.error: Exception | None = None

    async def place(self, record: CallRecord) -> str:
        if self.error:
            raise self.error
        self.placed.append(record)
        return f"CA{len(self.placed):032d}"


class FakeCall:
    """Stands in for the Pipecat pipeline: records the call and ends it."""

    def __init__(self):
        self.runs: list[dict] = []

    async def __call__(self, websocket, *, record, store: CallStore, **kwargs):
        self.runs.append({"record": record, **kwargs})
        record.stream_started = True
        store.add_turn(record.id, "friday", "Hello, this is Friday.")
        store.finish(record.id, "answered", outcome="completed", summary="Booked for 6 PM.")
        await websocket.close()


class FakeWhatsAppUI:
    """A pretend WhatsApp window: records what Friday did, sends nothing."""

    def __init__(self, contacts=("Priya Sharma", "Priya Office", "Rahul")):
        self.contacts = list(contacts)
        self.chat: str | None = None
        self.log: list[tuple] = []
        self.misdirect = False  # open the wrong chat, to test the safety check

    def focus(self):
        self.log.append(("focus",))

    def open_phone(self, digits):
        self.log.append(("open_phone", digits))
        self.chat = f"+{digits}"

    def search(self, query):
        self.log.append(("search", query))
        return [c for c in self.contacts if query.casefold() in c.casefold()]

    def open_result(self, name):
        self.log.append(("open_result", name))
        self.chat = "Someone Else" if self.misdirect else name

    def current_chat(self):
        return self.chat

    def write(self, text):
        self.log.append(("write", self.chat, text))

    def press_enter(self):
        self.log.append(("enter", self.chat))

    def start_call(self, video):
        self.log.append(("call", self.chat, video))


class FakeBrowser:
    def __init__(self):
        self.log: list[tuple] = []

    def _page(self, title="Example"):
        return {"url": "https://example.test/", "title": title, "elements": '[1] link "More"'}

    def open(self, url):
        self.log.append(("open", url))
        return self._page()

    def snapshot(self):
        return self._page()

    def click(self, ref):
        self.log.append(("click", ref))
        return self._page("Clicked")

    def type(self, ref, text, press_enter):
        self.log.append(("type", ref, text, press_enter))
        return self._page()

    def press(self, key):
        return self._page()

    def scroll(self, direction):
        return self._page()

    def read(self):
        return {"url": "https://example.test/", "title": "Example", "text": "Hello"}

    def back(self):
        return self._page()

    def screenshot(self):
        return "aW1hZ2U="

    def search(self, site, query):
        self.log.append(("search", site, query))
        return {
            "site": "YouTube" if site == "youtube" else "Google",
            "query": query,
            "results": [
                {"title": "Lo-fi beats to study to", "url": "https://yt.test/1", "kind": "video"},
                {"title": "Lo-fi channel", "url": "https://yt.test/@lofi", "kind": "channel"},
                {"title": "Rain sounds", "url": "https://yt.test/2", "kind": "video"},
                {"title": "Short clip", "url": "https://yt.test/s", "kind": "short"},
            ],
        }

    def play_youtube(self, query):
        self.log.append(("play", query))
        return {"title": "Lo-fi beats to study to", "url": "https://yt.test/1"}

    def play_shown(self, n):
        self.log.append(("play_shown", n))
        return {"title": "Rain sounds", "url": "https://yt.test/2"}

    def warm(self):
        pass

    def google_account(self):
        return {"signed_in": True, "email": "prem@example.com"}

    def connect_google(self, email=None):
        self.log.append(("connect_google", email))
        return {"url": "https://accounts.google.com/", "title": "Sign in"}

    youtube_open = False

    def youtube_control(self, action):
        self.log.append(("youtube", action))
        if not self.youtube_open:
            return None
        return {"title": "Lo-fi beats", "paused": action == "pause", "volume": 80, "muted": False}

    def schedule_meeting(self, url, invite):
        self.log.append(("meeting", url, invite))
        return {"saved": True, "url": "https://calendar.google.com/calendar/r"}

    def send_open_email(self):
        self.log.append(("send_email",))
        return {"sent": True}

    def cdp_url(self):
        return "http://127.0.0.1:9333"

    def close(self):
        pass


class FakeBrowserAgent:
    """Stands in for browser-use: reports two steps and an answer."""

    def __init__(self):
        self.tasks: list[tuple] = []
        self.error: Exception | None = None

    async def run(self, task, cdp_url, max_steps, on_step, stopped):
        self.tasks.append((task, cdp_url, max_steps))
        if self.error:
            raise self.error
        await on_step({"type": "step", "n": 1, "goal": "Open the Python docs."})
        await on_step({"type": "step", "n": 2, "goal": "Read the release notes."})
        return {
            "type": "done",
            "ok": True,
            "result": "Python 3.14 is the latest.",
            "urls": ["https://docs.python.org/"],
            "steps": 2,
        }


class PretendWindow:
    def __init__(self, title: str):
        self.title = title

    def window_text(self):
        return self.title

    def is_visible(self):
        return True

    def set_focus(self):
        pass


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def server(settings: Settings, tmp_path: Path):
    dialer, call = FakeDialer(), FakeCall()
    browser, whatsapp, agent = FakeBrowser(), FakeWhatsAppUI(), FakeBrowserAgent()
    launched, looked, snapped = [], [], []
    typed: list[str] = []
    notepad = PretendWindow("Untitled - Notepad")
    # Everything is pretend: tests never see or type into real windows.
    desktop = DesktopApps(
        list_apps=lambda: [{"name": "Notepad", "app_id": "notepad"}],
        launch=launched.append,
        desktop=SimpleNamespace(windows=lambda: [notepad] if launched else []),
        send_keys=lambda keys, **_: typed.append(keys),
    )

    def look(question):
        looked.append(question)
        return "A code editor with a Python file open."

    def snap(window, position, screen="current"):
        snapped.append((window, position) if screen == "current" else (window, position, screen))
        return {"window": "Untitled - Notepad", "position": position}

    media_keys: list[str] = []
    opened_files: list[str] = []
    folder = tmp_path / "Documents"
    folder.mkdir()
    (folder / "Resume 2026.pdf").write_text("pdf")
    (folder / "notes.txt").write_text("hi")
    (folder / "setup resume.exe").write_text("program")
    app = create_app(
        settings,
        media_key=media_keys.append,
        speak=lambda text: None,
        file_roots=lambda: [folder],
        open_path=opened_files.append,
        dialer=dialer,
        call_runner=call,
        browser=browser,
        browser_agent=agent,
        desktop=desktop,
        look=look,
        snap=snap,
        whatsapp_ui=whatsapp,
    )
    port = free_port()
    uv = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not uv.started and time.time() < deadline:
        time.sleep(0.05)
    assert uv.started, "test server did not start"
    yield SimpleNamespace(
        url=f"http://127.0.0.1:{port}",
        app=app,
        dialer=dialer,
        call=call,
        settings=settings,
        browser=browser,
        browser_agent=agent,
        whatsapp=whatsapp,
        launched=launched,
        typed=typed,
        media_keys=media_keys,
        opened_files=opened_files,
        folder=folder,
        looked=looked,
        snapped=snapped,
    )
    uv.should_exit = True
    thread.join(10)
