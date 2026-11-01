"""The owner's own Chrome, signed in to their Gmail: where Friday opens sites,
searches and music.

Chrome keeps one profile per account; its "Local State" file says which
profile folder belongs to which email. Friday opens pages with
`chrome.exe --profile-directory=<that folder> <url>`, which adds a tab to that
profile's window (opening one if needed), so the page has the owner's logins.
The email is the one on Friday's main page (Your details), or
FRIDAY_CHROME_EMAIL; FRIDAY_CHROME_PROFILE names a profile folder directly.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from friday_services.config import REPO_ROOT, Settings


@dataclass(frozen=True)
class ChromeProfile:
    exe: Path
    folder: str  # "Default", "Profile 4", ...
    email: str

    def open(self, url: str) -> None:
        subprocess.Popen(
            [str(self.exe), f"--profile-directory={self.folder}", url],
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            close_fds=True,
        )


def chrome_exe() -> Path | None:
    for base in (
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ):
        exe = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
        if base and exe.exists():
            return exe
    return None


def chrome_accounts(user_data: Path | None = None) -> list[dict[str, str]]:
    """Every Chrome profile signed in to a Google account: folder, email, name."""
    user_data = (
        user_data or Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    )
    try:
        state = json.loads((user_data / "Local State").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    cache = state.get("profile", {}).get("info_cache", {})
    return [
        {"folder": folder, "email": str(info.get("user_name")), "name": str(info.get("name") or "")}
        for folder, info in cache.items()
        if info.get("user_name")
    ]


def chrome_profiles(user_data: Path | None = None) -> dict[str, str]:
    """Profile folder -> the email signed in there."""
    user_data = (
        user_data or Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    )
    try:
        state = json.loads((user_data / "Local State").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    cache = state.get("profile", {}).get("info_cache", {})
    return {folder: str(info.get("user_name") or "") for folder, info in cache.items()}


def owner_email(settings: Settings, profile_path: Path | None = None) -> str:
    if settings.get("FRIDAY_CHROME_EMAIL"):
        return settings.get("FRIDAY_CHROME_EMAIL").strip()
    path = profile_path or REPO_ROOT / "data" / "friday-profile.json"
    try:
        details = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(details, dict):
        return ""
    # The Google account chosen for Friday (main page), else the owner's email.
    return str(details.get("friday_google") or details.get("email") or "").strip()


def owner_chrome(
    settings: Settings,
    profiles: dict[str, str] | None = None,
    exe: Path | None = None,
    profile_path: Path | None = None,
) -> ChromeProfile | None:
    """The owner's signed-in Chrome profile, or None (no Chrome, or no profile
    for their email): then Friday uses the browser they used last."""
    exe = exe or chrome_exe()
    if exe is None:
        return None
    profiles = chrome_profiles() if profiles is None else profiles
    wanted = settings.get("FRIDAY_CHROME_PROFILE").strip()
    if wanted and wanted in profiles:
        return ChromeProfile(exe, wanted, profiles[wanted])
    email = owner_email(settings, profile_path).casefold()
    if not email:
        return None
    folder = next((f for f, mail in profiles.items() if mail.casefold() == email), None)
    return ChromeProfile(exe, folder, profiles[folder]) if folder else None
