"""Friday's daily briefing: "aaj ka briefing do" says the date, the weather, what
is due today and how much mail is unread, in one go. Pure text, so it can be
tested; each part is left out when it could not be found.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from friday_services.config import REPO_ROOT
from friday_services.organizer import Reminder


def owner_city(profile_path: Path | None = None) -> str:
    """The city on Friday's main page (Your details), if any."""
    path = profile_path or REPO_ROOT / "data" / "friday-profile.json"
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("city") or "").strip()
    except (OSError, ValueError, AttributeError):
        return ""


def _time(when: datetime) -> str:
    return when.strftime("%I:%M %p").lstrip("0")


def _joined(items: list[str], word: str) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} {word} {items[-1]}"


def _greeting(hour: int) -> str:
    if hour < 12:
        return "Good morning"
    return "Good afternoon" if hour < 17 else "Good evening"


def briefing_sentence(
    now: datetime,
    items: list[Reminder],
    weather: str = "",
    unread: int | None = None,
    hinglish: bool = False,
) -> str:
    """What Friday says. Things due later today, or else the next thing due."""
    today = [
        (when, item)
        for item in items
        if (when := datetime.fromtimestamp(item.due)).date() == now.date() and when >= now
    ]
    later = [
        (when, item)
        for item in items
        if (when := datetime.fromtimestamp(item.due)).date() > now.date()
    ]
    named = [f"{item.text} at {_time(when)}" for when, item in today[:3]]
    parts = [f"{_greeting(now.hour)}. Today is {now:%A}, {now.day} {now:%B}."]
    if weather:
        parts.append(weather.strip())
    if hinglish:
        if named:
            parts.append(f"Aaj ke liye: {_joined(named, 'aur')}.")
            if len(today) > 3:
                parts.append(f"{len(today) - 3} aur kaam bhi hain.")
        elif later:
            when, item = later[0]
            parts.append(f"Aaj kuch due nahi hai. Agla kaam {item.text}, {when:%A} ko.")
        else:
            parts.append("Aaj koi reminder nahi hai.")
        if unread:
            parts.append(f"{unread} unread email hain.")
        elif unread == 0:
            parts.append("Koi unread email nahi hai.")
    else:
        if named:
            parts.append(f"Today you have {_joined(named, 'and')}.")
            if len(today) > 3:
                parts.append(f"There are {len(today) - 3} more after that.")
        elif later:
            when, item = later[0]
            parts.append(f"Nothing is due today. Next is {item.text}, on {when:%A}.")
        else:
            parts.append("You have no reminders today.")
        if unread:
            parts.append(f"You have {unread} unread {'email' if unread == 1 else 'emails'}.")
        elif unread == 0:
            parts.append("You have no unread email.")
    return " ".join(parts)
