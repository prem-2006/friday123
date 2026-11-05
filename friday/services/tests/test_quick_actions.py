"""Friday's own one-step actions: each says what happened in friday_say, which
Friday speaks without another model call."""

from __future__ import annotations

import httpx
import pytest

from friday_services.screen import ScreenError, describe_screen, snap_rect

from .conftest import TOKEN
from .mcp_client import McpClient

AUTH = {"Authorization": f"Bearer {TOKEN}"}


def quick(server) -> McpClient:
    mcp = McpClient(f"{server.url}/quick/mcp", TOKEN)
    mcp.initialize()
    return mcp


def test_quick_tools_and_which_only_look(server):
    tools = quick(server).tools()
    assert set(tools) == {
        "open_app",
        "close_app",
        "youtube_control",
        "write_in_app",
        "clear_in_app",
        "press_keys_in_app",
        "media_control",
        "find_files",
        "open_file",
        "schedule_meeting",
        "send_email",
        "open_website",
        "play_youtube",
        "web_search",
        "play_shown_video",
        "look_at_screen",
        "snap_window",
        "watch_window",
        "stop_watching",
        "set_timer",
        "add_reminder",
        "add_deadline",
        "list_reminders",
        "finish_reminder",
        "take_note",
        "read_notes",
        "show_notes",
        "run_mode",
        "focus_session",
        "system_status",
        "lock_screen",
        "take_screenshot",
        "daily_briefing",
        "weather",
        "directions",
        "draft_email",
        "call_me",
        "set_volume",
    }
    assert tools["look_at_screen"]["annotations"]["readOnlyHint"] is True
    assert tools["play_youtube"]["annotations"]["readOnlyHint"] is False


def test_open_play_and_search_say_what_happened(server):
    client = quick(server)
    opened = client.call("open_app", {"name": "notepad"})["data"]
    assert opened["friday_say"] == "Notepad is open."
    assert server.launched == ["notepad"]
    site = client.call("open_website", {"url": "example.test"})["data"]
    assert site["friday_say"] == "Opening Example."
    played = client.call("play_youtube", {"query": "lofi"})["data"]
    # Short: only the start of the title, and not always the same words.
    assert "Lo-fi beats to study" in played["friday_say"]
    assert "YouTube" not in played["friday_say"]
    found = client.call("web_search", {"site": "youtube", "query": "lofi"})["data"]
    shown = client.call("play_shown_video", {"n": 2})["data"]
    assert "Rain sounds" in shown["friday_say"]
    assert server.browser.log[-1] == ("play_shown", 2)
    assert found["friday_say"] == (
        "Top results on YouTube: Lo-fi beats to study to; Lo-fi channel; Rain sounds."
    )
    feed = httpx.get(f"{server.url}/activity", headers=AUTH).json()
    events = feed["events"] if isinstance(feed, dict) else feed
    assert {e["actor"] for e in events} == {"friday"}


def test_look_and_snap(server):
    client = quick(server)
    seen = client.call("look_at_screen", {"question": "What is open?"})["data"]
    assert seen["friday_say"] == "A code editor with a Python file open."
    assert server.looked == ["What is open?"]
    placed = client.call("snap_window", {"window": "notepad", "position": "top-left"})["data"]
    assert placed["friday_say"] == "Untitled - Notepad is on the top left."
    assert server.snapped == [("notepad", "top-left")]
    other = client.call(
        "snap_window", {"window": "notepad", "position": "maximize", "screen": "monitor"}
    )["data"]
    assert other["friday_say"] == "Untitled - Notepad is maximized on the monitor."
    assert server.snapped[-1] == ("notepad", "maximize", "monitor")
    bad = client.call("snap_window", {"window": "notepad", "position": "middle"})
    assert bad["isError"]


def test_stop_switch_blocks_quick_actions(server):
    httpx.post(f"{server.url}/activity/stop", headers=AUTH)
    result = quick(server).call("open_app", {"name": "notepad"})
    assert result["isError"] and "pressed stop" in result["text"]
    assert server.launched == []


def test_snap_rect_halves_and_quarters():
    work = (0, 0, 1920, 1040)
    assert snap_rect(work, "left") == (0, 0, 960, 1040)
    assert snap_rect(work, "right") == (960, 0, 960, 1040)
    assert snap_rect(work, "bottom-right") == (960, 520, 960, 520)
    assert snap_rect((1920, 0, 3840, 1080), "top-left") == (1920, 0, 960, 540)
    with pytest.raises(ScreenError):
        snap_rect(work, "middle")


ENV = {"EVON_BASE_URL": "https://model.test/v1/", "EVON_API_KEY": "k", "EVON_MODEL": "m"}


def test_describe_screen_sends_the_picture_and_reports_limits():
    sent = {}

    def answer(request: httpx.Request) -> httpx.Response:
        sent["url"] = str(request.url)
        sent["body"] = request.read().decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": " A  browser. "}}]})

    text = describe_screen("aGk=", "What?", ENV, transport=httpx.MockTransport(answer))
    assert text == "A browser."
    assert sent["url"] == "https://model.test/v1/chat/completions"
    assert "data:image/jpeg;base64,aGk=" in sent["body"]
    limited = httpx.MockTransport(lambda request: httpx.Response(429))
    with pytest.raises(ScreenError, match="rate limiting"):
        describe_screen("aGk=", "What?", ENV, transport=limited)
    with pytest.raises(ScreenError, match="EVON_API_KEY"):
        describe_screen("aGk=", "What?", {})


def test_write_in_app_types_into_the_app(server):
    client = quick(server)
    written = client.call("write_in_app", {"app": "notepad", "text": "Milk\nEggs"})["data"]
    assert written["friday_say"] == "Done, it is written in Notepad."
    assert server.launched == ["notepad"]  # it was not open yet
    assert server.typed == ["Milk", "{ENTER}", "Eggs"]


def test_send_email_is_marked_as_one_that_sends(server):
    tool = quick(server).tools()["send_email"]
    assert tool["annotations"]["readOnlyHint"] is False
    assert tool["annotations"]["destructiveHint"] is True
    sent = quick(server).call("send_email", {})["data"]
    assert sent["friday_say"] == "The email is sent."
    assert ("send_email",) in server.browser.log


def test_main_page_google_and_shortcut_routes(server):
    assert httpx.get(f"{server.url}/account/google").status_code == 401
    assert httpx.get(f"{server.url}/account/google", headers=AUTH).json() == {
        "signed_in": True,
        "email": "prem@example.com",
    }
    connect = httpx.post(f"{server.url}/account/google/connect", headers=AUTH)
    assert connect.status_code == 200 and ("connect_google", None) in server.browser.log
    # With the owner's Gmail from "Your details", sign-in starts with it filled in.
    httpx.post(
        f"{server.url}/account/google/connect",
        headers=AUTH,
        json={"email": "prem@gmail.com"},
    )
    assert ("connect_google", "prem@gmail.com") in server.browser.log
    bad = httpx.post(
        f"{server.url}/account/google/connect", headers=AUTH, json={"email": "not an email"}
    )
    assert bad.status_code == 400
    opened = httpx.post(
        f"{server.url}/browser/open", headers=AUTH, json={"url": "https://mail.google.com/"}
    )
    assert opened.json() == {"url": "https://example.test/", "title": "Example"}
    bad = httpx.post(f"{server.url}/browser/open", headers=AUTH, json={"url": "file:///c:/"})
    assert bad.status_code == 400


def test_media_control_uses_the_youtube_tab_or_else_the_media_keys(server):
    client = quick(server)
    said = client.call("media_control", {"action": "pause"})["data"]["friday_say"]
    assert said == "Paused." and server.media_keys == ["pause"]  # no YouTube tab
    server.browser.youtube_open = True
    paused = client.call("media_control", {"action": "pause"})["data"]
    assert paused["title"] == "Lo-fi beats" and paused["paused"] is True
    assert server.media_keys == ["pause"]  # the tab handled it


def test_clear_and_keys_in_apps(server):
    client = quick(server)
    assert client.call("clear_in_app", {"app": "notepad"})["data"]["friday_say"] == (
        "Notepad is cleared."
    )
    client.call("press_keys_in_app", {"app": "notepad", "keys": ["Ctrl+S"]})
    client.call("write_in_app", {"app": "notepad", "text": "Eggs", "replace": True})
    assert server.typed == ["^a", "{DELETE}", "^s", "^a", "Eggs"]


def test_finds_and_opens_files_but_never_programs(server):
    client = quick(server)
    found = client.call("find_files", {"query": "resume"})["data"]
    names = {item["name"] for item in found["files"]}
    assert names == {"Resume 2026.pdf", "setup resume.exe"}
    pdf = next(item["path"] for item in found["files"] if item["name"].endswith(".pdf"))
    assert client.call("open_file", {"path": pdf})["data"]["friday_say"] == (
        "Opened Resume 2026.pdf."
    )
    exe = str(server.folder / "setup resume.exe")
    refused = client.call("open_file", {"path": exe})
    assert refused["isError"] and "program" in refused["text"]
    outside = client.call("open_file", {"path": "C:/Windows/notepad.exe"})
    assert outside["isError"] and "your own folders" in outside["text"]
    assert server.opened_files == [pdf]


def test_meetings_are_marked_as_sending_and_filled_in(server):
    client = quick(server)
    tool = client.tools()["schedule_meeting"]
    assert tool["annotations"]["destructiveHint"] is True
    done = client.call(
        "schedule_meeting",
        {
            "title": "Project sync",
            "start": "2026-10-24T15:00",
            "minutes": 45,
            "guests": ["ruchita@example.com"],
        },
    )["data"]
    assert done["friday_say"] == "Project sync is on your calendar, and 1 invitation sent."
    _, url, invite = next(entry for entry in server.browser.log if entry[0] == "meeting")
    assert invite is True
    assert "dates=20261024T150000%2F20261024T154500" in url
    assert "add=ruchita%40example.com" in url and "ctz=Asia%2FKolkata" in url
    bad = client.call("schedule_meeting", {"title": "x", "start": "tomorrow 3pm"})
    assert bad["isError"] and "date and time" in bad["text"]


def test_video_replies_keep_only_the_short_part_of_the_title():
    from friday_services.mcp_quick import playing_sentence, video_name

    assert video_name("Kesariya - Full Song | Brahmastra | Ranbir (4K)") == "Kesariya"
    assert video_name("Tum Hi Ho ft. Arijit Singh") == "Tum Hi Ho"
    assert video_name("Arijit Singh Best Songs 2026 Jukebox") == "Arijit Singh Best Songs…"
    assert playing_sentence("Kesariya (Official Video)", lambda options: options[0]) == (
        "Playing Kesariya."
    )
    assert playing_sentence("") == "Playing it."


def test_status_answers_only_what_was_asked():
    from datetime import datetime

    from friday_services.organizer import status_sentence

    now = datetime(2026, 10, 30, 8, 23)
    charging, unplugged = {"percent": 52, "charging": True}, {"percent": 52, "charging": False}
    assert (
        status_sentence(charging, now, "battery") == "Yes, the laptop is charging, at 52 percent."
    )
    assert status_sentence(unplugged, now, "battery") == (
        "No, it's not charging. Battery is at 52 percent."
    )
    assert status_sentence(charging, now, "battery", hinglish=True) == (
        "Haan, laptop charge ho raha hai, abhi 52 percent hai."
    )
    assert status_sentence(unplugged, now, "battery", hinglish=True) == (
        "Nahi, laptop charge nahi ho raha, abhi 52 percent hai."
    )
    assert status_sentence({"percent": 100, "charging": True}, now, "battery") == (
        "Yes, it's plugged in and fully charged."
    )
    assert status_sentence(charging, now, "time") == "It's 8:23 AM."
    assert status_sentence(charging, now, "date", hinglish=True) == "Aaj Friday, 30 October hai."
    assert "8:23 AM" in status_sentence(charging, now)


def test_sites_are_called_by_name_never_by_address():
    from friday_services.mcp_quick import site_name
    from friday_services.sites import name_urls

    assert name_urls("Opened https://mail.google.com.") == "Opened Gmail."
    assert name_urls("Opening mail.google.com/mail/u/0 now") == "Opening Gmail now"
    assert (
        name_urls("Ask ravi@example.com about report.pdf")
        == "Ask ravi@example.com about report.pdf"
    )

    assert site_name("https://mail.google.com/mail/u/0") == "Gmail"
    assert site_name("https://www.youtube.com/watch?v=abc") == "YouTube"
    assert site_name("irctc.co.in") == "IRCTC"
    assert site_name("https://in.linkedin.com/in/prem") == "LinkedIn"
    assert site_name("bbc.co.uk") == "BBC"
