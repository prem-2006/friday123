"""Web addresses as Friday says them: the site's name, never the whole URL."""

from __future__ import annotations

import re
from urllib.parse import urlparse

SITE_NAMES = {
    "mail.google.com": "Gmail",
    "gmail.com": "Gmail",
    "calendar.google.com": "Google Calendar",
    "drive.google.com": "Google Drive",
    "docs.google.com": "Google Docs",
    "sheets.google.com": "Google Sheets",
    "maps.google.com": "Google Maps",
    "google.com": "Google",
    "youtube.com": "YouTube",
    "music.youtube.com": "YouTube Music",
    "web.whatsapp.com": "WhatsApp",
    "instagram.com": "Instagram",
    "facebook.com": "Facebook",
    "linkedin.com": "LinkedIn",
    "github.com": "GitHub",
    "chatgpt.com": "ChatGPT",
    "claude.ai": "Claude",
    "irctc.co.in": "IRCTC",
    "amazon.in": "Amazon",
    "flipkart.com": "Flipkart",
    "netflix.com": "Netflix",
    "spotify.com": "Spotify",
}

# A full address, or a bare one like mail.google.com (not an email address or a file).
_ADDRESS = re.compile(
    r"https?://\S+"
    r"|(?<![@\w.-])(?:[a-z0-9-]+\.)+(?:com|in|org|net|io|ai|app|dev|co)(?:\.[a-z]{2})?(?:/\S*)?(?![\w@-])",
    re.I,
)


def site_name(url: str) -> str:
    """ "https://mail.google.com/mail/u/0" -> "Gmail"; "irctc.co.in" -> "IRCTC"."""
    host = urlparse(url if "://" in url else f"https://{url}").hostname or url
    host = host.lower().removeprefix("www.").removeprefix("m.")
    if host in SITE_NAMES:
        return SITE_NAMES[host]
    parts = host.split(".")
    for i in range(len(parts) - 1):  # "in.linkedin.com" -> "linkedin.com"
        if ".".join(parts[i:]) in SITE_NAMES:
            return SITE_NAMES[".".join(parts[i:])]
    main = (
        parts[-3]
        if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "gov", "ac")
        else parts[0]
        if len(parts) < 2
        else parts[-2]
    )
    return main.upper() if len(main) <= 4 else main.capitalize()


def name_urls(text: str) -> str:
    """ "Opened https://mail.google.com." -> "Opened Gmail." """

    def named(match: re.Match[str]) -> str:
        address = match[0]
        url = address.rstrip(".,;:!?)…")
        return site_name(url) + address[len(url) :]

    return _ADDRESS.sub(named, text)
