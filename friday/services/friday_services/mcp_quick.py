"""MCP tools for Friday itself: one-step actions without handing work to a helper.

Handing a task to Navigator or Operator costs two or three extra model calls
(5 to 8 seconds). Opening an app or a site, playing a video, a quick search,
looking at the screen and arranging windows need none of that, so Friday does
them directly. Each result carries "friday_say": the sentence Friday speaks,
which also ends the turn without another model call (see
src/friday/server/quick-finish.ts).
"""

from __future__ import annotations

import asyncio
import random
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote_plus

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from friday_services import winctl
from friday_services.activity import ActivityFeed
from friday_services.browser import BrowserError, OnScreenBrowser, first_video, nth_video
from friday_services.desktop import DesktopApps, DesktopError
from friday_services.files import FilesError, find_files, open_file
from friday_services.media import MediaError, OwnerMedia
from friday_services.meetings import MeetingError, calendar_event_url
from friday_services.organizer import (
    Notes,
    OrganizerError,
    Reminders,
    battery,
    gmail_compose_url,
    load_modes,
    lock_screen,
    maps_directions_url,
    parse_when,
    spoken_time,
    status_sentence,
    take_screenshot,
    weather,
)
from friday_services.screen import POSITIONS, Position, Screen, ScreenError
from friday_services.volume import Volume, VolumeError, volume_sentence
from friday_services.watch import Until, Watches
from friday_services.worker import Worker

ACT = ToolAnnotations(read_only_hint=False, destructive_hint=False)
SENDS = ToolAnnotations(read_only_hint=False, destructive_hint=True)
LOOK = ToolAnnotations(read_only_hint=True)
FAILURES = (
    BrowserError,
    DesktopError,
    ScreenError,
    FilesError,
    MediaError,
    VolumeError,
    MeetingError,
    OrganizerError,
)
MediaAction = Literal[
    "pause",
    "play",
    "stop",
    "next",
    "previous",
    "rewind",
    "forward",
]
MEDIA_SAID = {
    "pause": "Paused.",
    "play": "Playing.",
    "stop": "Stopped.",
    "rewind": "Gone back.",
    "forward": "Skipped ahead.",
    "next": "Next one.",
    "previous": "Going back.",
}
IN_BROWSER = {
    "directions",
    "draft_email",
    "open_website",
    "play_youtube",
    "browser_search",
    "send_email",
    "schedule_meeting",
}


# What Friday says when a video starts: short, and not the same every time.
PLAYING = ["Playing {}.", "Here's {}.", "{}, coming up.", "Okay, {}.", "Chal raha hai: {}."]
_TITLE_CUTS = re.compile(r"\s+[|•·–—-]\s+|\s*[(\[]|\s+(?:ft\.?|feat\.?|official)\b", re.I)


# What Friday calls a site out loud: the name, never the address.
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


def youtube_query(title: str) -> str:
    """A YouTube window's title -> the search its results page shows:
    "(2) lofi - YouTube - Google Chrome" -> "lofi"; "" for the home page."""
    shown = re.sub(r"^YouTube - ", "", title)  # the YouTube app: "YouTube - <page>"
    shown = re.sub(r"^\(\d+\)\s*", "", shown)
    shown = re.split(r"\s+-\s+YouTube\b", shown, maxsplit=1, flags=re.I)[0].strip()
    return "" if shown.casefold() == "youtube" else shown


def site_name(url: str) -> str:
    """ "https://mail.google.com/mail/u/0" -> "Gmail"; "irctc.co.in" -> "IRCTC"."""
    from urllib.parse import urlparse

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


def video_name(title: str, words: int = 4) -> str:
    """The short part of a video title: "Kesariya - Full Song | Brahmastra (4K)" -> "Kesariya"."""
    first = _TITLE_CUTS.split(title.replace(" - YouTube", "").strip(), maxsplit=1)[0].strip()
    parts = (first or title).split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def playing_sentence(title: str, choose=random.choice) -> str:
    name = video_name(title) if title.strip() else ""
    return choose(PLAYING).format(name) if name else "Playing it."


def _short(title: str, limit: int = 70) -> str:
    title = " ".join(title.split())
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


def build_quick_server(
    browser: OnScreenBrowser,
    browser_worker: Worker,
    desktop: DesktopApps,
    desktop_worker: Worker,
    feed: ActivityFeed,
    look: Callable[[str], str],
    snap: Callable[[str, str, str], dict[str, Any]],
    media_key: Callable[[str], None],
    file_roots: Callable[[], list[Path]],
    open_path: Callable[[str], None],
    reminders: Reminders,
    notes: Notes,
    data_dir: Path,
    call_owner: Callable[[str], Awaitable[dict]],
    timezone: str = "Asia/Kolkata",
    owner_media: OwnerMedia | None = None,
    volume: Volume | None = None,
    watches: Watches | None = None,
    chrome: Callable[[], Any] | None = None,
    mail_map: Any = None,
    summarise: Callable[[list[dict], bool], Awaitable[str | None]] | None = None,
) -> MCPServer:
    volume = volume or Volume()
    server = MCPServer(
        name="friday-quick",
        instructions=(
            "Friday's own one-step actions on the owner's PC. Each result's friday_say is "
            "spoken to the owner as is."
        ),
    )

    async def act(action: str, detail: str, run, *args) -> Any:
        feed.guard()
        feed.emit("friday", action, detail)
        try:
            result = await run(*args)
        except FAILURES as error:
            feed.emit("friday", action, str(error), state="failed")
            raise ToolError(str(error)) from error
        except Exception as error:
            feed.emit("friday", action, f"{detail} did not work.", state="failed")
            raise ToolError(f"That did not work: {error}") from error
        # The voice screen shows the page for browser actions.
        image = await browser_worker.run(browser.screenshot) if action in IN_BROWSER else ""
        feed.emit("friday", action, f"{detail} Done.", state="done", image=image or None)
        return result

    def in_browser(fn):
        return lambda *args: browser_worker.run(fn, *args)

    @server.tool(
        name="open_app",
        description="Open a Windows app by name, e.g. Notepad, Calculator, Spotify, VS Code.",
        annotations=ACT,
    )
    async def open_app(name: Annotated[str, Field(min_length=1, max_length=80)]) -> dict:
        opened = await act(
            "open_app",
            f"Opening {name}…",
            lambda: desktop_worker.run(desktop.open, name),
        )
        if opened.get("already_open"):
            return {**opened, "friday_say": f"{opened['opened']} was already open; it is in front now."}
        return {**opened, "friday_say": f"{opened['opened']} is open."}

    @server.tool(
        name="close_app",
        description=(
            "Close a Windows app completely, as its X button does: every window of it, "
            "e.g. 'close Chrome', 'Spotify band karo'. The app may still ask to save work."
        ),
        annotations=ACT,
    )
    async def close_app(name: Annotated[str, Field(min_length=1, max_length=80)]) -> dict:
        closed = await act(
            "close_app", f"Closing {name}…", lambda: desktop_worker.run(desktop.close, name)
        )
        say = (
            f"{closed['closed']} is closed."
            if closed["windows"]
            else f"{closed['closed']} is not open."
        )
        return {**closed, "friday_say": say}

    @server.tool(
        name="write_in_app",
        description=(
            "Type text into a Windows app, e.g. a shopping list into Notepad. Uses the "
            "app's open window, or opens the app first. Put each line on its own line."
        ),
        annotations=ACT,
    )
    async def write_in_app(
        app: Annotated[str, Field(min_length=1, max_length=80)],
        text: Annotated[str, Field(min_length=1, max_length=4000)],
        replace: Annotated[
            bool, Field(description="True to replace everything already in the app with text.")
        ] = False,
    ) -> dict:
        written = await act(
            "write_in_app",
            f"Writing in {app}…",
            lambda: desktop_worker.run(desktop.write, app, text, replace),
        )
        return {**written, "friday_say": f"Done, it is written in {written['app']}."}

    @server.tool(
        name="clear_in_app",
        description="Remove all the text in an app's window, e.g. clear Notepad.",
        annotations=ACT,
    )
    async def clear_in_app(app: Annotated[str, Field(min_length=1, max_length=80)]) -> dict:
        cleared = await act(
            "clear_in_app", f"Clearing {app}…", lambda: desktop_worker.run(desktop.clear, app)
        )
        return {**cleared, "friday_say": f"{cleared['app']} is cleared."}

    @server.tool(
        name="press_keys_in_app",
        description=(
            "Press keys in an app's window: Ctrl+S to save, Ctrl+Z to undo, Ctrl+Y to redo, "
            "Enter, Escape, Ctrl+N for new and so on."
        ),
        annotations=ACT,
    )
    async def press_keys_in_app(
        app: Annotated[str, Field(min_length=1, max_length=80)],
        keys: Annotated[list[str], Field(min_length=1, max_length=10)],
    ) -> dict:
        pressed = await act(
            "press_keys_in_app",
            f"Pressing {', '.join(keys)} in {app}.",
            lambda: desktop_worker.run(desktop.keys_in, app, keys),
        )
        return {**pressed, "friday_say": "Done."}

    @server.tool(
        name="media_control",
        description=(
            "Control what is playing, in any browser or player (YouTube, Spotify): pause, "
            "play, stop, next, previous, rewind or forward (seconds, default 10). 'Stop', "
            "'ruko' or 'band karo' about music means stop. For loudness use set_volume."
        ),
        annotations=ACT,
    )
    async def media_control(
        action: MediaAction, seconds: Annotated[int, Field(ge=1, le=600)] = 10
    ) -> dict:
        async def run():
            if owner_media:
                done = await desktop_worker.run(owner_media.control, action, seconds)
                if done is not None:
                    return done
            if action in ("rewind", "forward"):
                raise DesktopError("Nothing is playing that I can move.")
            video = await browser_worker.run(
                browser.youtube_control, "pause" if action == "stop" else action
            )
            if video is not None:
                return video
            await desktop_worker.run(media_key, "pause" if action == "stop" else action)
            return {"media_key": action}

        done = await act("media_control", f"Media: {action.replace('_', ' ')}.", run)
        say = MEDIA_SAID[action]
        if action in ("rewind", "forward"):
            say = f"{'Back' if action == 'rewind' else 'Ahead'} {seconds} seconds."
        return {**done, "friday_say": say}

    @server.tool(
        name="set_volume",
        description=(
            "Set the PC's speaker volume. level sets an exact number: 'volume 97', 'set it "
            "to 40'. change moves it: 'louder' or 'volume badhao' +10, 'much louder' +25, "
            "'quieter' -10, 'increase by 20' +20, 'decrease by 30' -30. mute true or false "
            "mutes or unmutes. With nothing given, says the current volume."
        ),
        annotations=ACT,
    )
    async def set_volume(
        level: Annotated[int | None, Field(ge=0, le=100)] = None,
        change: Annotated[int | None, Field(ge=-100, le=100)] = None,
        mute: bool | None = None,
    ) -> dict:
        if level is not None:
            detail = f"Volume to {level}."
        elif change is not None:
            detail = f"Volume {change:+d}."
        elif mute is not None:
            detail = "Muting." if mute else "Unmuting."
        else:
            detail = "Checking the volume."
        state = await act(
            "set_volume", detail, lambda: desktop_worker.run(volume.set, level, change, mute)
        )
        return {**state, "friday_say": volume_sentence(state)}

    async def signed_in_chrome():
        """The owner's own Chrome profile (their Gmail), when there is one."""
        if chrome is None:
            return None
        try:
            return await asyncio.to_thread(chrome)
        except Exception:
            return None

    async def in_youtube_app() -> bool:
        """Whether the owner has YouTube installed as an app (then YouTube plays there)."""
        return bool(await desktop_worker.run(desktop.installed_site, "YouTube"))

    async def owner_browser(youtube: bool = False):
        """The browser the owner used last (for a song: the one already showing
        YouTube), unless that is Friday's own Chrome."""
        if not owner_media:
            return None
        pick = owner_media.for_youtube if youtube else owner_media.last_browser
        window = await desktop_worker.run(pick)
        return window if window and not window.friday else None

    @server.tool(
        name="find_files",
        description=(
            "Find files on the owner's PC by name (Desktop, Documents, Downloads, Pictures, "
            "Music, Videos), newest first."
        ),
        annotations=LOOK,
    )
    async def find_files_tool(query: Annotated[str, Field(min_length=1, max_length=120)]) -> dict:
        found = await act(
            "find_files",
            f'Looking for "{query}" in your files.',
            lambda: asyncio.to_thread(find_files, query, file_roots()),
        )
        names = [item["name"] for item in found[:3]]
        say = (
            f"I found {len(found)}: " + "; ".join(names) + "."
            if found
            else f'No file named like "{query}" in your folders.'
        )
        return {"files": found, "friday_say": say}

    @server.tool(
        name="open_file",
        description="Open a file that find_files found, by its full path, in its usual app.",
        annotations=ACT,
    )
    async def open_file_tool(path: Annotated[str, Field(min_length=3, max_length=600)]) -> dict:
        opened = await act(
            "open_file",
            "Opening the file…",
            lambda: asyncio.to_thread(open_file, path, file_roots(), open_path),
        )
        return {**opened, "friday_say": f"Opened {opened['opened']}."}

    def time_of(text: str):
        try:
            return parse_when(text)
        except OrganizerError as error:
            raise ToolError(str(error)) from error

    @server.tool(
        name="set_timer",
        description="Set a timer; Friday says out loud when it is done.",
        annotations=ACT,
    )
    async def set_timer(
        minutes: Annotated[float, Field(gt=0, le=720)],
        label: Annotated[str, Field(max_length=120)] = "",
    ) -> dict:
        due = datetime.now() + timedelta(minutes=minutes)
        name = label or f"{minutes:g} minutes"
        await act(
            "set_timer",
            f"Timer: {name}.",
            lambda: asyncio.to_thread(reminders.add, "timer", name, due),
        )
        span = f"{minutes:g} minute{'s' if minutes != 1 else ''}"
        return {"friday_say": f"Timer set for {span}{f' for {label}' if label else ''}."}

    @server.tool(
        name="add_reminder",
        description=(
            "Remind the owner of something at a time; Friday says it out loud then. "
            "when is the owner's local time in India with no time zone, e.g. 2026-10-24T17:00."
        ),
        annotations=ACT,
    )
    async def add_reminder(
        text: Annotated[str, Field(min_length=1, max_length=200)],
        when: Annotated[str, Field(min_length=10, max_length=25)],
    ) -> dict:
        due = time_of(when)
        await act(
            "add_reminder",
            f"Reminder: {text}.",
            lambda: asyncio.to_thread(reminders.add, "reminder", text, due),
        )
        return {"friday_say": f"Okay, I'll remind you {spoken_time(due)}."}

    @server.tool(
        name="add_deadline",
        description=(
            "Save a deadline (an assignment, a bill, a project). Friday reminds the owner "
            "24 hours, 3 hours, 1 hour and 15 minutes before, and when it is due. due is "
            "the owner's local time in India with no time zone, e.g. 2026-10-24T17:00."
        ),
        annotations=ACT,
    )
    async def add_deadline(
        title: Annotated[str, Field(min_length=1, max_length=200)],
        due: Annotated[str, Field(min_length=10, max_length=25)],
    ) -> dict:
        moment = time_of(due)
        await act(
            "add_deadline",
            f"Deadline: {title}.",
            lambda: asyncio.to_thread(reminders.add, "deadline", title, moment),
        )
        return {
            "friday_say": f"Saved. {title} is due {spoken_time(moment)}; I'll remind you before."
        }

    @server.tool(
        name="list_reminders",
        description="Say what timers, reminders and deadlines are coming up.",
        annotations=LOOK,
    )
    async def list_reminders() -> dict:
        items = await asyncio.to_thread(reminders.upcoming)
        listed = [
            {
                "text": item.text,
                "kind": item.kind,
                "due": datetime.fromtimestamp(item.due).isoformat(),
            }
            for item in items
        ]
        if not items:
            say = "Nothing is coming up."
        else:
            first = "; ".join(
                f"{item.text} {spoken_time(datetime.fromtimestamp(item.due))}" for item in items[:3]
            )
            say = f"You have {len(items)} coming up: {first}."
        return {"items": listed, "friday_say": say}

    @server.tool(
        name="finish_reminder",
        description="Mark a deadline as done or cancel a reminder or timer, by its name.",
        annotations=ACT,
    )
    async def finish_reminder(title: Annotated[str, Field(min_length=1, max_length=200)]) -> dict:
        done = await asyncio.to_thread(reminders.finish, title)
        if not done:
            raise ToolError(f"Nothing called {title} is coming up.")
        feed.emit("friday", "finish_reminder", f"Done: {done.text}.", state="done")
        return {"friday_say": f"Done, {done.text} is off the list."}

    @server.tool(
        name="call_me",
        description=(
            'Phone the owner\'s own mobile, for "call me" or when the owner names '
            'themself ("call Prem"); no OK needed. Now, or at a time to remind them (when is the '
            "owner's local time in India with no time zone, e.g. 2026-10-24T17:00). Friday "
            "says why it is calling and talks briefly. message is the reminder in plain "
            "words, e.g. 'time to have your dinner'. Always use this tool when asked, "
            "even if an earlier call failed: the owner may have fixed it since."
        ),
        annotations=ACT,
    )
    async def call_me(
        message: Annotated[str, Field(min_length=3, max_length=300)],
        when: Annotated[str, Field(max_length=25)] = "",
    ) -> dict:
        if when:
            due = time_of(when)
            await act(
                "call_me",
                f"Calling you {spoken_time(due)}: {message}",
                lambda: asyncio.to_thread(reminders.add, "call", message, due),
            )
            return {"friday_say": f"Okay, I'll call you {spoken_time(due)}."}
        feed.guard()
        placed = await call_owner(message)
        if not placed["called"]:
            raise ToolError(placed["why"])
        feed.emit("friday", "call_me", f"Calling your phone: {message}", state="done")
        return {"friday_say": "Calling your phone now."}

    @server.tool(name="take_note", description="Save a note the owner dictates.", annotations=ACT)
    async def take_note(text: Annotated[str, Field(min_length=1, max_length=2000)]) -> dict:
        await act("take_note", "Saving a note.", lambda: asyncio.to_thread(notes.add, text))
        return {"friday_say": "Noted."}

    @server.tool(name="read_notes", description="Read the latest notes aloud.", annotations=LOOK)
    async def read_notes(count: Annotated[int, Field(ge=1, le=10)] = 3) -> dict:
        latest = await asyncio.to_thread(notes.latest, count)
        say = (
            ("Your latest notes: " + "; ".join(latest) + ".")
            if latest
            else "You have no notes yet."
        )
        return {"notes": latest, "friday_say": say}

    @server.tool(name="show_notes", description="Open the notes file on screen.", annotations=ACT)
    async def show_notes() -> dict:
        if not notes.path.exists():
            raise ToolError("You have no notes yet.")
        await act(
            "show_notes",
            "Opening your notes.",
            lambda: asyncio.to_thread(open_path, str(notes.path)),
        )
        return {"friday_say": "Your notes are on screen."}

    @server.tool(
        name="run_mode",
        description=(
            "Switch the PC into a mode that opens its sites, apps and music: work, study, "
            "chill or news (more can be added in data/modes.json)."
        ),
        annotations=ACT,
    )
    async def run_mode(mode: Annotated[str, Field(min_length=1, max_length=40)]) -> dict:
        modes = load_modes(data_dir / "modes.json")
        chosen = modes.get(mode.strip().lower())
        if not chosen:
            raise ToolError(f"No {mode} mode. Modes: {', '.join(modes)}.")

        async def run():
            for app in chosen.get("apps", []):
                try:
                    await desktop_worker.run(desktop.open, app)
                except DesktopError:
                    pass  # not installed here; the rest still opens
            if chosen.get("sites"):
                await browser_worker.run(browser.open_tabs, chosen["sites"])
            if chosen.get("music"):
                await browser_worker.run(browser.play_youtube, chosen["music"])
            return {"mode": mode}

        await act("run_mode", f"{mode.title()} mode.", run)
        return {"friday_say": chosen.get("say", f"{mode.title()} mode is on.")}

    @server.tool(
        name="focus_session",
        description=(
            "Start a focus session: opens the topic's file if there is one, plays focus music "
            "and says when the time is up."
        ),
        annotations=ACT,
    )
    async def focus_session(
        minutes: Annotated[int, Field(ge=1, le=180)] = 25,
        topic: Annotated[str, Field(max_length=120)] = "",
    ) -> dict:
        async def run():
            opened = ""
            if topic:
                found = await asyncio.to_thread(find_files, topic, file_roots(), 1)
                if found:
                    await asyncio.to_thread(open_file, found[0]["path"], file_roots(), open_path)
                    opened = found[0]["name"]
            await browser_worker.run(browser.play_youtube, "lofi focus music")
            await asyncio.to_thread(
                reminders.add,
                "timer",
                f"{minutes} minute focus session{f' on {topic}' if topic else ''}; take a short break",
                datetime.now() + timedelta(minutes=minutes),
            )
            return {"opened": opened}

        done = await act("focus_session", f"Focus session: {minutes} minutes.", run)
        extra = f" I opened {done['opened']}." if done.get("opened") else ""
        return {"friday_say": f"Focus for {minutes} minutes.{extra} I'll tell you when time is up."}

    @server.tool(
        name="system_status",
        description=(
            "Answer exactly what the owner asked about the PC, and nothing else. ask: "
            "battery for 'is it charging?', 'battery kitni hai?' (says charging or not and "
            "the percent); time for 'what time is it?'; date for 'what's the date?'; all "
            "only when they ask for everything. hinglish: true when the owner spoke Hindi "
            "or Hinglish."
        ),
        annotations=LOOK,
    )
    async def system_status(
        # No default: the model must say what was asked, so "what time is it?"
        # never gets the date and battery as well.
        ask: Literal["battery", "time", "date", "all"],
        hinglish: bool = False,
    ) -> dict:
        power = await asyncio.to_thread(battery)
        return {**power, "friday_say": status_sentence(power, ask=ask, hinglish=hinglish)}

    @server.tool(
        name="lock_screen",
        description="Lock the PC. The owner approves it first.",
        annotations=SENDS,
    )
    async def lock_screen_tool() -> dict:
        await act("lock_screen", "Locking the PC.", lambda: asyncio.to_thread(lock_screen))
        return {"friday_say": "Locked."}

    @server.tool(
        name="take_screenshot",
        description=(
            "Take a screenshot of the whole screen and save it where Windows saves its "
            "own (Pictures, Screenshots), on the owner's PC."
        ),
        annotations=ACT,
    )
    async def take_screenshot_tool() -> dict:
        path = await act(
            "take_screenshot",
            "Taking a screenshot.",
            lambda: asyncio.to_thread(take_screenshot),
        )
        return {
            "path": str(path),
            "friday_say": "Done, it's in your Screenshots folder.",
        }

    @server.tool(
        name="weather",
        description="Today's weather for a city, spoken.",
        annotations=LOOK,
    )
    async def weather_tool(city: Annotated[str, Field(min_length=2, max_length=80)]) -> dict:
        said = await act("weather", f"Weather in {city}.", lambda: asyncio.to_thread(weather, city))
        return {"friday_say": said}

    @server.tool(
        name="directions",
        description="Show Google Maps directions to a place in Friday's Chrome.",
        annotations=ACT,
    )
    async def directions(
        destination: Annotated[str, Field(min_length=2, max_length=200)],
        origin: Annotated[str, Field(max_length=200)] = "",
    ) -> dict:
        url = maps_directions_url(destination, origin)
        await act("directions", f"Directions to {destination}.", in_browser(browser.open), url)
        return {"friday_say": f"Directions to {destination} are on screen."}

    @server.tool(
        name="draft_email",
        description=(
            "Open a Gmail draft with these details in Friday's Chrome, not sent. Read it back "
            "to the owner; send it with send_email after their OK."
        ),
        annotations=ACT,
    )
    async def draft_email(
        to: Annotated[str, Field(max_length=300)],
        subject: Annotated[str, Field(max_length=300)],
        body: Annotated[str, Field(max_length=5000)],
    ) -> dict:
        url = gmail_compose_url(to, subject, body)
        await act("draft_email", "Writing the email draft.", in_browser(browser.open), url)
        return {"friday_say": "The draft is ready in Gmail. Shall I send it?"}

    @server.tool(
        name="schedule_meeting",
        description=(
            "Add an event to the owner's Google Calendar in Friday's Chrome, inviting guests "
            "by email. start is the owner's local time in India with no time zone, e.g. 2026-10-24T22:00 for 10 PM. The owner "
            "approves every meeting before it is saved and invitations go out."
        ),
        annotations=SENDS,
    )
    async def schedule_meeting(
        title: Annotated[str, Field(min_length=1, max_length=200)],
        start: Annotated[str, Field(min_length=10, max_length=25)],
        minutes: Annotated[int, Field(ge=5, le=1440)] = 30,
        guests: Annotated[list[str], Field(max_length=20)] = [],  # noqa: B006
        details: Annotated[str, Field(max_length=2000)] = "",
    ) -> dict:
        try:
            url = calendar_event_url(title, start, minutes, guests, details, timezone)
        except MeetingError as error:
            raise ToolError(str(error)) from error
        saved = await act(
            "schedule_meeting",
            f"Scheduling {title}…",
            in_browser(browser.schedule_meeting),
            url,
            bool(guests),
        )
        say = (
            f"{title} is on your calendar"
            + (
                f", and {len(guests)} invitation{'s' if len(guests) > 1 else ''} sent."
                if guests
                else "."
            )
            if saved.get("saved")
            else "I filled in the meeting, but Calendar did not confirm it was saved."
        )
        return {**saved, "friday_say": say}

    @server.tool(
        name="open_website",
        description=(
            "Open a website in a new tab of the browser the owner used last, e.g. gmail.com "
            "or irctc.co.in. When the site is installed as a desktop app (ChatGPT, YouTube, "
            "WhatsApp) that app opens instead, unless in_browser is true: only when the owner "
            "asks for the browser ('in Chrome')."
        ),
        annotations=ACT,
    )
    async def open_website(
        url: Annotated[str, Field(min_length=3, max_length=500)],
        in_browser: bool = False,
    ) -> dict:
        if not in_browser and owner_media is not None and not re.search(r"\.[a-z]+/\S", url):
            # A site's front page, and the owner has its app: the app, not a tab.
            app = await desktop_worker.run(desktop.app_for_site, site_name(url))
            if app:
                return await open_app(app)
        profile = await signed_in_chrome()
        if profile:
            full = url if "://" in url else f"https://{url}"
            await act(
                "open_in_browser",
                f"Opening {site_name(url)} in your Chrome.",
                lambda: asyncio.to_thread(profile.open, full),
            )
            return {"url": full, "browser": "Chrome", "friday_say": f"Opening {site_name(url)}."}
        window = await owner_browser()
        if window:
            full = url if "://" in url else f"https://{url}"
            await act(
                "open_in_browser",
                f"Opening {site_name(url)} in {window.name}.",
                lambda: desktop_worker.run(owner_media.open_url, full, window, False),
            )
            return {"url": full, "browser": window.name, "friday_say": f"Opening {site_name(url)}."}
        if owner_media is not None:
            # No browser of the owner's is open: their default browser, not Friday's own.
            full = url if "://" in url else f"https://{url}"
            await act(
                "open_in_browser",
                f"Opening {site_name(url)}.",
                lambda: asyncio.to_thread(open_path, full),
            )
            return {"url": full, "friday_say": f"Opening {site_name(url)}."}
        page = await act(
            "open_website",
            f"Opening {site_name(url)}.",
            lambda: browser_worker.run(browser.open, url),  # (in_browser is the argument here)
        )
        return {
            "url": page["url"],
            "title": page["title"],
            "friday_say": f"Opening {site_name(url)}.",
        }

    @server.tool(
        name="play_youtube",
        description=(
            "Play a song, video or topic on YouTube in the browser the owner used last: in "
            "the same tab when it already shows YouTube."
        ),
        annotations=ACT,
    )
    async def play_youtube(query: Annotated[str, Field(min_length=1, max_length=200)]) -> dict:
        if owner_media is not None and await in_youtube_app():
            # The owner's YouTube app: its own search, then a click on the video.
            found = await asyncio.to_thread(first_video, query)
            searched["youtube"] = query
            done = await act(
                "play_in_app",
                f'Playing "{query}" in the YouTube app.',
                lambda: desktop_worker.run(
                    desktop.search_and_click, "YouTube", query, found[1] if found else ""
                ),
            )
            title = found[1] if found else query
            say = (
                playing_sentence(title)
                if done["clicked"]
                else f'Here are the YouTube results for "{query}"; say which one to play.'
            )
            return {"title": title, "app": "YouTube", "friday_say": say}
        window = await owner_browser(youtube=True)
        profile = await signed_in_chrome()
        if profile and not (window and window.on_youtube):
            # A new song where nothing plays yet: a tab in the owner's signed-in Chrome.
            found = await asyncio.to_thread(first_video, query)
            url = (
                f"https://www.youtube.com/watch?v={found[0]}"
                if found
                else f"https://www.youtube.com/results?search_query={quote_plus(query)}"
            )
            await act(
                "play_in_browser",
                f'Playing "{query}" in your Chrome.',
                lambda: asyncio.to_thread(profile.open, url),
            )
            title = found[1] if found else query
            return {
                "title": title,
                "url": url,
                "browser": "Chrome",
                "friday_say": playing_sentence(title),
            }
        if window:
            found = await asyncio.to_thread(first_video, query)
            url = (
                f"https://www.youtube.com/watch?v={found[0]}"
                if found
                else f"https://www.youtube.com/results?search_query={quote_plus(query)}"
            )
            title = found[1] if found else query
            await act(
                "play_in_browser",
                f'Playing "{query}" in {window.name}.',
                lambda: desktop_worker.run(owner_media.open_url, url, window, window.on_youtube),
            )
            return {
                "title": title,
                "url": url,
                "browser": window.name,
                "friday_say": playing_sentence(title),
            }
        if owner_media is not None:
            # No browser of the owner's is open: play it in their default browser.
            found = await asyncio.to_thread(first_video, query)
            url = (
                f"https://www.youtube.com/watch?v={found[0]}"
                if found
                else f"https://www.youtube.com/results?search_query={quote_plus(query)}"
            )
            await act(
                "play_in_browser", f'Playing "{query}".', lambda: asyncio.to_thread(open_path, url)
            )
            title = found[1] if found else query
            return {"title": title, "url": url, "friday_say": playing_sentence(title)}
        video = await act(
            "play_youtube",
            f'Playing "{query}" on YouTube.',
            in_browser(browser.play_youtube),
            query,
        )
        return {**video, "friday_say": playing_sentence(video["title"])}

    # The last YouTube search Friday ran, to tell its results page from a video's.
    searched = {"youtube": ""}

    @server.tool(
        name="play_shown_video",
        description=(
            "Play a video from the YouTube results the owner is looking at: 'play the first "
            "video', 'play the second one', 'play the video on the screen'. n is its place "
            "in the results, 1 for the first."
        ),
        annotations=ACT,
    )
    async def play_shown_video(n: Annotated[int, Field(ge=1, le=10)] = 1) -> dict:
        window = await owner_browser(youtube=True)
        app_window = (
            await desktop_worker.run(desktop.site_window, "YouTube") if owner_media else None
        )
        if app_window and window and window.on_youtube:
            # YouTube in the app and in a browser tab: the one used last.
            order = await asyncio.to_thread(lambda: [w["hwnd"] for w in winctl.windows()])
            if window.hwnd in order and app_window["hwnd"] in order:
                if order.index(window.hwnd) < order.index(app_window["hwnd"]):
                    app_window = None
        if app_window:
            # The YouTube app's results ("lofi - YouTube"): click the video itself.
            query = youtube_query(app_window["title"])
            if not query and n == 1:
                # The home page: its first video.
                title = await act(
                    "play_in_app",
                    "Playing the first video in the YouTube app.",
                    lambda: desktop_worker.run(desktop.click_first_video, app_window["hwnd"]),
                )
                if not title:
                    raise ToolError("I could not see a video in the YouTube app.")
                return {"title": title, "app": "YouTube", "friday_say": playing_sentence(title)}
            if not query:
                raise ToolError("I can only pick from search results. Tell me what to search.")
            found = await asyncio.to_thread(nth_video, query, n)
            if not found:
                raise ToolError(f'I could not find video {n} for "{query}".')
            done = await act(
                "play_in_app",
                f"Playing video {n} in the YouTube app.",
                lambda: desktop_worker.run(desktop.search_and_click, "YouTube", "", found[1]),
            )
            if not done["clicked"]:
                raise ToolError(f"I could not see video {n} in the YouTube app.")
            return {"title": found[1], "app": "YouTube", "friday_say": playing_sentence(found[1])}
        if window and window.on_youtube:
            query = youtube_query(window.title)
            if not query:
                raise ToolError("I can only pick from search results. Tell me what to search.")
            found = await asyncio.to_thread(nth_video, query, n)
            if not found:
                raise ToolError(f'I could not find video {n} for "{query}".')
            url = f"https://www.youtube.com/watch?v={found[0]}"
            await act(
                "play_in_browser",
                f"Playing video {n} in {window.name}.",
                lambda: desktop_worker.run(owner_media.open_url, url, window, True),
            )
            return {
                "title": found[1],
                "url": url,
                "browser": window.name,
                "friday_say": playing_sentence(found[1]),
            }
        if owner_media is not None and searched["youtube"]:
            # The results Friday opened, in a window it cannot see right now.
            found = await asyncio.to_thread(nth_video, searched["youtube"], n)
            if found:
                url = f"https://www.youtube.com/watch?v={found[0]}"
                profile = await signed_in_chrome()
                await act(
                    "play_in_browser",
                    f"Playing video {n}.",
                    lambda: asyncio.to_thread(profile.open if profile else open_path, url),
                )
                return {"title": found[1], "url": url, "friday_say": playing_sentence(found[1])}
        if owner_media is not None:
            raise ToolError("YouTube is not open. Tell me what to play.")
        video = await act(
            "play_youtube", f"Playing video {n} on YouTube.", in_browser(browser.play_shown), n
        )
        return {**video, "friday_say": playing_sentence(video["title"])}

    @server.tool(
        name="web_search",
        description=(
            "Search YouTube or Google in the browser the owner used last. "
            "Use youtube for videos, channels, songs, tutorials or anything said to be on "
            "YouTube; google for everything else. Leave site out when the owner names no "
            "site: YouTube if their browser is showing it, else Google."
        ),
        annotations=ACT,
    )
    async def web_search(
        query: Annotated[str, Field(min_length=1, max_length=300)],
        site: Literal["youtube", "google"] | None = None,
    ) -> dict:
        # A window already showing YouTube takes YouTube searches, in its own tab.
        youtube = await owner_browser(youtube=True)
        if site is None:
            site = "youtube" if youtube and youtube.on_youtube else "google"
        if site == "youtube":
            searched["youtube"] = query
        if site == "youtube" and owner_media is not None and await in_youtube_app():
            await act(
                "search_in_app",
                f'Searching YouTube for "{query}" in the YouTube app.',
                lambda: desktop_worker.run(desktop.search_and_click, "YouTube", query),
            )
            return {"app": "YouTube", "friday_say": "Here are the YouTube results."}
        window = youtube if site == "youtube" else await owner_browser()
        name = "YouTube" if site == "youtube" else "Google"
        profile = await signed_in_chrome()
        if profile and not (site == "youtube" and window and window.on_youtube):
            url = (
                f"https://www.youtube.com/results?search_query={quote_plus(query)}"
                if site == "youtube"
                else f"https://www.google.com/search?q={quote_plus(query)}"
            )
            await act(
                "search_in_browser",
                f'Searching {name} for "{query}" in your Chrome.',
                lambda: asyncio.to_thread(profile.open, url),
            )
            return {"url": url, "browser": "Chrome", "friday_say": f"Here are the {name} results."}
        if window:
            url = (
                f"https://www.youtube.com/results?search_query={quote_plus(query)}"
                if site == "youtube"
                else f"https://www.google.com/search?q={quote_plus(query)}"
            )
            # A search page already open in that tab is reused, else a new tab.
            same_tab = (
                window.on_youtube
                if site == "youtube"
                else "google search" in (window.title.casefold())
            )
            await act(
                "search_in_browser",
                f'Searching {name} for "{query}" in {window.name}.',
                lambda: desktop_worker.run(owner_media.open_url, url, window, same_tab),
            )
            return {
                "url": url,
                "browser": window.name,
                "friday_say": f"Here are the {name} results for {_short(query, 60)}.",
            }
        if owner_media is not None:
            url = (
                f"https://www.youtube.com/results?search_query={quote_plus(query)}"
                if site == "youtube"
                else f"https://www.google.com/search?q={quote_plus(query)}"
            )
            await act(
                "search_in_browser",
                f'Searching {name} for "{query}".',
                lambda: asyncio.to_thread(open_path, url),
            )
            return {"url": url, "friday_say": f"Here are the {name} results."}
        found = await act(
            "browser_search",
            f'Searching {name} for "{query}".',
            in_browser(browser.search),
            site,
            query,
        )
        top = [_short(result["title"], 60) for result in found["results"][:3]]
        say = (
            f"Top results on {found['site']}: " + "; ".join(top) + "."
            if top
            else f"The results are on screen in {found['site']}."
        )
        return {"results": found["results"][:5], "friday_say": say}

    @server.tool(
        name="send_email",
        description=(
            "Send the email draft that is open in Gmail in the on-screen browser. First have "
            "Navigator prepare the draft and read it back to the owner; the owner approves "
            "every send."
        ),
        annotations=SENDS,
    )
    async def send_email() -> dict:
        sent = await act("send_email", "Sending the email…", in_browser(browser.send_open_email))
        say = (
            "The email is sent."
            if sent.get("sent")
            else "I clicked Send; Gmail has not confirmed yet."
        )
        return {**sent, "friday_say": say}

    @server.tool(
        name="look_at_screen",
        description=(
            "Look at the owner's screen and answer a question about it, e.g. "
            "'What is on my screen?' or 'What does this error say?'"
        ),
        annotations=LOOK,
    )
    async def look_at_screen(
        question: Annotated[str, Field(max_length=300)] = "What is on the screen?",
    ) -> dict:
        answer = await act(
            "look_at_screen", "Looking at your screen…", lambda: asyncio.to_thread(look, question)
        )
        return {"answer": answer, "friday_say": answer}

    @server.tool(
        name="snap_window",
        description=(
            "Move an open window: a half or quarter of a screen, the center, maximize or "
            f"minimize. Positions: {', '.join(POSITIONS)}. screen: laptop (the built-in "
            "panel), monitor (the external display) or current. The window is found by its "
            "app ('chrome', 'notepad'), process or part of its title."
        ),
        annotations=ACT,
    )
    async def snap_window(
        window: Annotated[
            str, Field(min_length=1, max_length=120, description="App name or part of its title")
        ],
        position: Position,
        screen: Screen = "current",
    ) -> dict:
        placed = await act(
            "snap_window",
            f"Moving {window} to the {position}.",
            lambda: asyncio.to_thread(snap, window, position, screen),
        )
        where = {"maximize": "maximized", "minimize": "minimized", "center": "centered"}.get(
            position, f"on the {position.replace('-', ' ')}"
        )
        on = f" on the {screen}" if screen != "current" else ""
        name = placed.get("app") or placed.get("window") or window
        return {**placed, "friday_say": f"{_short(name, 40)} is {where}{on}."}

    if mail_map is not None:
        from friday_services.mail_map import MailError, new_mail_sentence

        @server.tool(
            name="gmail_new",
            description=(
                "What is new in the owner's Gmail: 'any new email?', 'what are my new mails', "
                "'naye mail aaye?'. Checks in the background (nothing opens on screen) and says "
                "who wrote about what since the owner last asked."
            ),
            annotations=LOOK,
        )
        async def gmail_new() -> dict:
            def run():
                try:
                    return mail_map.new()
                except MailError as error:
                    raise OrganizerError(str(error)) from error

            count, mails, fresh = await act(
                "gmail_new", "Checking your Gmail.", lambda: browser_worker.run(run)
            )
            return {
                "unread": count,
                "new": [mail.line() for mail in fresh],
                "friday_say": new_mail_sentence(count, fresh),
            }

        @server.tool(
            name="gmail_summary",
            description=(
                "Summarise the owner's Gmail, in the background (nothing opens on screen): "
                "'summarise my Gmail', 'summary of my emails', 'what do my new mails say', "
                "'read my latest email' (which 1), 'the mail from Ravi' (about 'ravi'). "
                "which 0 = the newest unread emails together. Use this for every email "
                "summary; never open Gmail or hand summaries to a helper. hinglish: true "
                "when the owner spoke Hindi or Hinglish."
            ),
            annotations=LOOK,
        )
        async def gmail_summary(
            which: Annotated[int, Field(ge=0, le=20)] = 0,
            about: Annotated[str, Field(max_length=80)] = "",
            hinglish: bool = False,
        ) -> dict:
            def gather():
                try:
                    if which or about.strip():
                        picked = [mail_map.find(which or 1, about)]
                    else:
                        _count, mails = mail_map.refresh()
                        if not mails:
                            raise MailError("You have no unread mail.")
                        picked = mails[:5]
                    # The newest in full, the rest by their preview: quick, and enough.
                    return [
                        {
                            "from": mail.sender or mail.email,
                            "subject": mail.subject,
                            "when": mail.when,
                            "text": mail_map.full_text(mail) if i == 0 else mail.preview,
                        }
                        for i, mail in enumerate(picked)
                    ]
                except MailError as error:
                    raise OrganizerError(str(error)) from error

            mails = await act(
                "gmail_summary", "Reading your email.", lambda: browser_worker.run(gather)
            )
            said = await summarise(mails, hinglish) if summarise else None
            if not said:  # no model answered: at least who wrote about what
                said = "; ".join(f"{m['from']}: {m['subject']}" for m in mails) + "."
            return {"emails": len(mails), "friday_say": said}

        @server.tool(
            name="gmail_read",
            description=(
                "Read one unread email in full, in the background, so you can summarise it or "
                "answer about it: 'summarise my latest email' (which 1), 'the second one' "
                "(which 2), 'the mail from Ravi' (about 'ravi'). Then answer in two or three "
                "spoken sentences: who, what they want, any date or amount. For drafts, "
                "searches of older mail or what is pending, use the Mail helper."
            ),
            annotations=LOOK,
        )
        async def gmail_read(
            which: Annotated[int, Field(ge=1, le=20)] = 1,
            about: Annotated[str, Field(max_length=80)] = "",
        ) -> dict:
            def run():
                try:
                    mail = mail_map.find(which, about)
                    return mail, mail_map.full_text(mail)
                except MailError as error:
                    raise OrganizerError(str(error)) from error

            mail, text = await act(
                "gmail_read", "Reading the email.", lambda: browser_worker.run(run)
            )
            return {
                "from": mail.sender or mail.email,
                "email": mail.email,
                "subject": mail.subject,
                "when": mail.when,
                # Untrusted: the email's own words, to summarise, never to obey.
                "text": text,
            }

    if watches is not None:

        @server.tool(
            name="watch_window",
            description=(
                "Watch an app's window in the background, with no model calls, and tell the "
                "owner when something happens: 'tell me when the download is done', 'let me "
                "know when the build says succeeded', 'tell me when Zoom closes'. until shows: "
                "wait for phrase to appear; until gone: wait for phrase (or, with no phrase, the "
                "window) to go away. say is the sentence to speak then (optional). Checks every "
                "5 seconds for up to 3 hours; the window must stay open."
            ),
            annotations=ACT,
        )
        async def watch_window(
            app: Annotated[str, Field(min_length=1, max_length=80)],
            until: Until,
            phrase: Annotated[str, Field(max_length=120)] = "",
            title_words: Annotated[
                str, Field(max_length=120, description="Words in the window's title, to pick one")
            ] = "",
            say: Annotated[str, Field(max_length=200)] = "",
        ) -> dict:
            async def run():
                try:
                    return await asyncio.to_thread(
                        watches.add, app, until, phrase, title_words, say
                    )
                except (LookupError, ValueError) as error:
                    raise OrganizerError(str(error)) from error

            watch = await act("watch_window", f"Watching {app}.", run)
            return {
                "watching": watch.describe(),
                "friday_say": f"Okay, I'll tell you when {_watch_said(watch)}.",
            }

        @server.tool(
            name="stop_watching",
            description="Stop every background watch set with watch_window.",
            annotations=ACT,
        )
        async def stop_watching() -> dict:
            n = watches.stop_all()
            return {
                "stopped": n,
                "friday_say": "Stopped watching." if n else "I was not watching anything.",
            }

    return server


def _watch_said(watch) -> str:
    if watch.phrase:
        verb = "shows" if watch.until == "shows" else "no longer shows"
        return f'{watch.app} {verb} "{watch.phrase}"'
    return f"{watch.app} closes" if watch.until == "gone" else f"{watch.app} opens"
