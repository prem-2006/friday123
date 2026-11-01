"""What Friday says when woken ("Hey Friday" or a double clap): hello, and an
offer that fits what is on screen right now.

It reads only the window in front (its app and title, from Windows), so the
greeting is instant and costs no model call. Nothing is sent anywhere; the
sentence is spoken on this PC.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable

# (pattern on "app — title", offer). The first match wins.
OFFERS: list[tuple[str, str]] = [
    (
        r"leetcode|hackerrank|geeksforgeeks|codeforces",
        "Want me to explain this problem or help solve it?",
    ),
    (r"youtube", "Want me to play something else, or pause this?"),
    (r"whatsapp", "Want me to send a message to someone?"),
    (r"gmail|outlook|mail", "Want me to draft a reply or find an email?"),
    (r"visual studio code|pycharm|intellij|\.py\b|\.ts\b|\.js\b", "Need help with this code?"),
    (r"stack overflow|github", "Want me to dig into this for you?"),
    (r"word|google docs|notepad|\.docx|\.txt", "Want me to write or tidy something here?"),
    (r"excel|google sheets|\.xlsx|\.csv", "Want help with this sheet?"),
    (r"powerpoint|google slides", "Want help with these slides?"),
    (r"zoom|google meet|microsoft teams", "Want me to take notes or set a reminder?"),
    (r"spotify", "Want me to change the song?"),
    (r"amazon|flipkart|myntra", "Want me to compare prices for this?"),
    (r"maps", "Want directions somewhere?"),
    (r"chrome|edge|firefox|brave|opera", "Want me to read this page or search something?"),
]
HELLOS = ["Hi", "Hey", "Hello", "Haan, boliye"]
GENERIC = "What can I do for you?"
# Windows that are Friday itself, or no app at all.
SELF = re.compile(r"\b(friday|electron|program manager|task switching|search)\b", re.I)


def short_title(title: str, app: str) -> str:
    """The part of a title worth saying: "Two Sum - LeetCode - Google Chrome" -> "Two Sum"."""
    parts = [p.strip() for p in re.split(r"\s[-–—|]\s", title) if p.strip()]
    parts = [p for p in parts if p.casefold() != app.casefold() and not SELF.search(p)]
    words = (parts[0] if parts else "").split()
    if not words:
        return ""
    return " ".join(words[:8]) + ("…" if len(words) > 8 else "")


def screen_greeting(
    app: str, title: str, owner: str = "", choose: Callable[[list[str]], str] = random.choice
) -> str:
    hello = choose(HELLOS) + (f" {owner.split()[0]}" if owner.strip() else "") + "!"
    if not app or SELF.search(app):
        return f"{hello} {GENERIC}"
    seen = f"{app} — {title}".casefold()
    offer = next((text for pattern, text in OFFERS if re.search(pattern, seen)), None)
    what = short_title(title, app)
    where = f"You're on {what} in {app}." if what else f"You're in {app}."
    return f"{hello} {where} {offer or GENERIC}"
