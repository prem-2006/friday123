"""Friday's everyday helpers: timers, reminders and deadlines, notes, modes,
and small PC facts (battery, lock, screenshots), plus weather and directions.

Reminders are kept in data/reminders.json and spoken by Friday when they come
due (through the voice screen or the notch, or Windows' own voice when neither
is open). Deadlines are also announced 24 hours, 3 hours, 1 hour and 15
minutes before. Ideas from the ARRC "J" kit, rebuilt for Friday.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from friday_services.usage import counted

DEADLINE_STAGES = [24 * 60, 3 * 60, 60, 15]  # minutes before the due time


class OrganizerError(ValueError):
    pass


def parse_when(text: str) -> datetime:
    """A local date and time like 2026-10-24T17:00. A time with a zone (such as
    UTC) is converted to this PC's local time, not just stripped of its zone."""
    try:
        when = datetime.fromisoformat(text.strip())
    except ValueError as error:
        raise OrganizerError("Give the time as a date and time, e.g. 2026-10-24T17:00.") from error
    if when.tzinfo is not None:
        when = when.astimezone()
    return when.replace(tzinfo=None)


def spoken_time(when: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now()
    clock = when.strftime("%I:%M %p").lstrip("0").replace(":00 ", " ")
    if when.date() == now.date():
        return f"today at {clock}"
    if when.date() == (now + timedelta(days=1)).date():
        return f"tomorrow at {clock}"
    return f"{when.strftime('%A %d %B')} at {clock}"


@dataclass
class Reminder:
    id: str
    kind: str  # timer | reminder | deadline
    text: str
    due: float  # epoch seconds
    # Minutes before `due` still to announce (deadlines), then the due time itself.
    stages: list[int] = field(default_factory=list)
    done: bool = False
    # contact_call: who to phone and what to say (phone, name, goal, opening).
    extra: dict = field(default_factory=dict)


@dataclass
class Due:
    kind: str  # timer | reminder | deadline | call | contact_call
    text: str
    minutes_left: int  # 0 when it is due now
    extra: dict = field(default_factory=dict)


class Reminders:
    def __init__(self, path: Path, clock=time.time):
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()

    def _load(self) -> list[Reminder]:
        try:
            return [Reminder(**item) for item in json.loads(self._path.read_text("utf-8"))]
        except (OSError, ValueError, TypeError):
            return []

    def _save(self, items: list[Reminder]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps([asdict(item) for item in items], indent=2), "utf-8")

    def add(self, kind: str, text: str, due: datetime, extra: dict | None = None) -> Reminder:
        when = due.timestamp()
        if when <= self._clock():
            raise OrganizerError("That time has already passed.")
        stages = []
        if kind == "deadline":
            minutes_left = (when - self._clock()) / 60
            stages = [m for m in DEADLINE_STAGES if m < minutes_left]
        item = Reminder(uuid.uuid4().hex[:8], kind, text.strip(), when, stages, extra=extra or {})
        with self._lock:
            items = self._load()
            items.append(item)
            self._save(items)
        return item

    def upcoming(self) -> list[Reminder]:
        return sorted((r for r in self._load() if not r.done), key=lambda r: r.due)

    def finish(self, text: str) -> Reminder | None:
        """Marks the reminder or deadline whose text matches as done."""
        wanted = text.casefold().strip()
        with self._lock:
            items = self._load()
            match = next(
                (r for r in items if not r.done and wanted and wanted in r.text.casefold()), None
            )
            if match:
                match.done = True
                self._save(items)
        return match

    def finish_id(self, reminder_id: str) -> Reminder | None:
        with self._lock:
            items = self._load()
            match = next((r for r in items if r.id == reminder_id and not r.done), None)
            if match:
                match.done = True
                self._save(items)
        return match

    def due_now(self) -> list[Due]:
        """What to say now; each announcement is said once."""
        now = self._clock()
        said: list[Due] = []
        with self._lock:
            items = self._load()
            changed = False
            for item in items:
                if item.done:
                    continue
                while item.stages and now >= item.due - item.stages[0] * 60:
                    minutes = item.stages.pop(0)
                    left = (
                        f"{minutes // 60} hours"
                        if minutes >= 120
                        else ("1 hour" if minutes == 60 else f"{minutes} minutes")
                    )
                    said.append(Due(item.kind, f"Reminder: {item.text} is due in {left}.", minutes))
                    changed = True
                if now >= item.due:
                    item.done = True
                    changed = True
                    said.append(
                        Due(
                            item.kind,
                            {
                                "timer": f"Your timer is done: {item.text}.",
                                "deadline": f"{item.text} is due now.",
                                "call": item.text,
                                "contact_call": item.text,
                            }.get(item.kind, f"Reminder: {item.text}."),
                            0,
                            item.extra,
                        )
                    )
            if changed:
                self._save(items)
        return said


class Notes:
    def __init__(self, path: Path):
        self.path = path

    def add(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%d %b %Y, %I:%M %p")
        with self.path.open("a", encoding="utf-8") as file:
            file.write(f"[{stamp}] {text.strip()}\n")

    def latest(self, count: int = 3) -> list[str]:
        try:
            lines = [line.strip() for line in self.path.read_text("utf-8").splitlines()]
        except OSError:
            return []
        return [line.split("] ", 1)[-1] for line in lines if line][-count:][::-1]


# Modes: one phrase opens a set of sites (in Friday's Chrome) and apps.
# data/modes.json can replace or add to these.
DEFAULT_MODES: dict[str, dict[str, Any]] = {
    "work": {
        "say": "Work mode: Gmail, your calendar and VS Code.",
        "sites": ["https://mail.google.com/", "https://calendar.google.com/"],
        "apps": ["Visual Studio Code"],
    },
    "study": {
        "say": "Study mode: focus music and a fresh Notepad.",
        "sites": [],
        "music": "lofi study music",
        "apps": ["Notepad"],
    },
    "chill": {
        "say": "Chill mode: some music for you.",
        "sites": [],
        "music": "arijit singh best songs",
    },
    "news": {
        "say": "Here is today's news.",
        "sites": ["https://news.google.com/home?hl=en-IN&gl=IN&ceid=IN:en"],
    },
}


def load_modes(path: Path) -> dict[str, dict[str, Any]]:
    modes = dict(DEFAULT_MODES)
    try:
        modes.update(json.loads(path.read_text("utf-8")))
    except (OSError, ValueError):
        pass
    return modes


def battery() -> dict[str, Any]:
    """Battery level and whether it is charging (Windows)."""
    import ctypes
    from ctypes import wintypes

    class Status(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", wintypes.BYTE),
            ("BatteryFlag", wintypes.BYTE),
            ("BatteryLifePercent", wintypes.BYTE),
            ("SystemStatusFlag", wintypes.BYTE),
            ("BatteryLifeTime", wintypes.DWORD),
            ("BatteryFullLifeTime", wintypes.DWORD),
        ]

    status = Status()
    ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status))
    percent = status.BatteryLifePercent
    return {
        "percent": None if percent == 255 else int(percent),
        "charging": status.ACLineStatus == 1,
    }


def status_sentence(
    power: dict[str, Any],
    now: datetime | None = None,
    ask: str = "all",
    hinglish: bool = False,
) -> str:
    """Only what was asked: battery ("is it charging?"), time, date, or all of it."""
    now = now or datetime.now()
    clock = now.strftime("%I:%M %p").lstrip("0")
    day = f"{now.strftime('%A')}, {now.day} {now.strftime('%B')}"
    if ask == "time":
        return f"Abhi {clock} ho rahe hain." if hinglish else f"It's {clock}."
    if ask == "date":
        return f"Aaj {day} hai." if hinglish else f"Today is {day}."
    if ask == "battery":
        return battery_sentence(power, hinglish)
    said = f"It's {clock} on {day}."
    if power.get("percent") is not None:
        said += f" Battery is at {power['percent']} percent" + (
            ", charging." if power.get("charging") else "."
        )
    return said


def battery_sentence(power: dict[str, Any], hinglish: bool = False) -> str:
    """A straight answer to "is the laptop charging?", with the level."""
    percent, charging = power.get("percent"), bool(power.get("charging"))
    if percent is None:
        return (
            "Is PC mein battery nahi hai, yeh seedha power par chal raha hai."
            if hinglish
            else "This PC has no battery; it runs on mains power."
        )
    if charging and percent >= 100:
        return (
            "Haan, charger laga hai aur battery full hai."
            if hinglish
            else "Yes, it's plugged in and fully charged."
        )
    if hinglish:
        return (
            f"Haan, laptop charge ho raha hai, abhi {percent} percent hai."
            if charging
            else f"Nahi, laptop charge nahi ho raha, abhi {percent} percent hai."
        )
    return (
        f"Yes, the laptop is charging, at {percent} percent."
        if charging
        else f"No, it's not charging. Battery is at {percent} percent."
    )


def speak_with_windows(text: str) -> None:
    """Says something with Windows' own voice, when no Friday screen is open."""
    import os
    import subprocess

    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Add-Type -AssemblyName System.Speech; "
            "(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak($env:FRIDAY_SAY)",
        ],
        env={**os.environ, "FRIDAY_SAY": text},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def lock_screen() -> None:
    import ctypes

    ctypes.windll.user32.LockWorkStation()


# Windows' own folders (SHGetKnownFolderPath): where Win + PrintScreen saves,
# and Pictures, which OneDrive or the owner may have moved.
SCREENSHOTS_FOLDER = "{b7bede81-df94-4682-a7d8-57a52620b86f}"
PICTURES_FOLDER = "{33E28130-4E1E-4676-835A-98395C3BC3BB}"


def known_folder(guid: str) -> Path | None:
    import ctypes
    import uuid as _uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    raw = _uuid.UUID(guid).bytes_le
    folder_id = GUID.from_buffer_copy(raw)
    found = ctypes.c_wchar_p()
    try:
        shell = ctypes.windll.shell32
        if shell.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(found)):
            return None
        path = Path(found.value)
        ctypes.windll.ole32.CoTaskMemFree(found)
        return path
    except (AttributeError, OSError):
        return None


def screenshots_folder() -> Path:
    """Where Windows saves screenshots: the Screenshots folder in Pictures, wherever that is."""
    shots = known_folder(SCREENSHOTS_FOLDER)
    if shots:
        return shots
    pictures = known_folder(PICTURES_FOLDER) or Path.home() / "Pictures"
    return pictures / "Screenshots"


def take_screenshot(folder: Path | None = None) -> Path:
    from PIL import ImageGrab

    folder = folder or screenshots_folder()
    folder.mkdir(parents=True, exist_ok=True)
    # Named like Windows' own: "Screenshot 2026-10-30 083512.png".
    path = folder / f"Screenshot {datetime.now().strftime('%Y-%m-%d %H%M%S')}.png"
    ImageGrab.grab(all_screens=True).save(path)
    return path


def maps_directions_url(destination: str, origin: str = "") -> str:
    query = {"api": "1", "destination": destination}
    if origin:
        query["origin"] = origin
    return "https://www.google.com/maps/dir/?" + urlencode(query)


def gmail_compose_url(to: str, subject: str, body: str, account: str | None = None) -> str:
    query = {"view": "cm", "fs": "1", "to": to, "su": subject, "body": body}
    if account:
        query["authuser"] = account
    return "https://mail.google.com/mail/?" + urlencode(query)


# Open-Meteo weather codes, in words.
WEATHER_WORDS = {
    0: "clear sky",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "foggy",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    80: "rain showers",
    81: "rain showers",
    82: "heavy rain showers",
    95: "thunderstorms",
    96: "thunderstorms with hail",
    99: "thunderstorms with hail",
}


@counted("Tools", "Open-Meteo", "weather")
def weather(city: str, transport: httpx.BaseTransport | None = None) -> str:
    """Today's weather for a city, as one spoken sentence (Open-Meteo, no key)."""
    with httpx.Client(transport=transport, timeout=10) as client:
        found = client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1},
        ).json()
        places = found.get("results") or []
        if not places:
            raise OrganizerError(f"I could not find a place called {city}.")
        place = places[0]
        forecast = client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,weather_code",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": 1,
            },
        ).json()
    now = forecast["current"]
    day = forecast["daily"]
    words = WEATHER_WORDS.get(int(now["weather_code"]), "mixed weather")
    rain = (day.get("precipitation_probability_max") or [None])[0]
    said = (
        f"{place['name']}: {round(now['temperature_2m'])} degrees and {words} now, "
        f"between {round(day['temperature_2m_min'][0])} and {round(day['temperature_2m_max'][0])} today"
    )
    return said + (f", {rain} percent chance of rain." if rain is not None else ".")
