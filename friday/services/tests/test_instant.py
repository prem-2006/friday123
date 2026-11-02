"""Instant commands and learned shortcuts (instant.py), with pretend tools."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from friday_services.instant import Instant, gist, plain

from .conftest import TOKEN

AUTH = {"Authorization": f"Bearer {TOKEN}"}
APPS = [
    "Notepad",
    "Google Chrome",
    "Paint",
    "Spotify",
    "Visual Studio Code",
    "ChatGPT Classic",
    "YouTube",
]


@pytest.fixture
def friday(tmp_path):
    ran: list[tuple[str, dict]] = []

    async def run(tool, args):
        ran.append((tool, args))
        if args.get("window") == "Ghost":
            raise RuntimeError("No open window called 'Ghost'.")
        return {"friday_say": f"{tool} done."}

    instant = Instant(tmp_path / "shortcuts.json", lambda: APPS, run)
    instant.ran = ran
    return instant


def ask(instant, text):
    instant.ran.clear()
    return asyncio.run(instant.handle(text))


def test_plain_and_gist_drop_politeness_and_filler():
    assert plain("Hey Friday, can you open Spotify please?") == "open spotify"
    assert sorted(gist("um, open the notepad")) == sorted(gist("notepad open"))


def test_simple_asks_run_without_the_model(friday):
    assert ask(friday, "Open Notepad")["calls"] == [
        {"tool": "open_app", "args": {"name": "Notepad"}}
    ]
    assert ask(friday, "chrome kholo")["calls"][0]["args"] == {"name": "Google Chrome"}
    assert ask(friday, "open github.com")["calls"][0] == {
        "tool": "open_website",
        "args": {"url": "github.com"},
    }
    assert ask(friday, "search youtube for lofi beats")["calls"][0]["args"] == {
        "site": "youtube",
        "query": "lofi beats",
    }
    assert ask(friday, "arijit singh bajao")["calls"][0] == {
        "tool": "play_youtube",
        "args": {"query": "arijit singh"},
    }
    assert ask(friday, "volume 40")["calls"][0]["args"] == {"level": 40}
    assert ask(friday, "pause")["calls"][0]["args"] == {"action": "pause"}


def test_window_moves_across_screens(friday):
    done = ask(friday, "Chrome left, Notepad right")
    assert [c["args"] for c in done["calls"]] == [
        {"window": "Google Chrome", "position": "left", "screen": "current"},
        {"window": "Notepad", "position": "right", "screen": "current"},
    ]
    done = ask(friday, "full screen chrome on my big screen")
    assert done["calls"][0]["args"] == {
        "window": "Google Chrome",
        "position": "maximize",
        "screen": "monitor",
    }
    opened = ask(friday, "open notepad on the left")
    assert [c["tool"] for c in opened["calls"]] == ["open_app", "snap_window"]


def test_unclear_or_risky_asks_go_to_the_model(friday):
    for text in (
        "right",
        "what's on my monitor",
        "open this",
        "send a message to Priya",
        "search amazon for shoes and find the cheapest",
        "play something",
        "open the thing",
    ):
        assert ask(friday, text) is None, text
    assert friday.ran == []


def test_learned_turns_replay_in_any_order_and_for_another_app(friday):
    calls = [{"tool": "snap_window", "args": {"window": "Notepad", "position": "top-left"}}]
    assert friday.learn("put notepad top left", calls, "Notepad is on the top left.") is True
    assert friday.learn("put notepad top left", calls, "Notepad is on the top left.") is False
    again = ask(friday, "top left notepad put")
    assert again["shortcut"] == "put notepad top left"
    paint = ask(friday, "put paint top left")
    assert paint["calls"][0]["args"]["window"] == "Paint"
    assert paint["say"] == "Paint is on the top left."


def test_only_safe_successful_turns_are_learned(friday):
    send = [{"tool": "whatsapp_send_message", "args": {"to": "Priya"}}]
    assert friday.learn("message priya hi", send, "Sent.") is None
    open_it = [{"tool": "open_app", "args": {"name": "Paint"}}]
    assert friday.learn("open this", open_it, "Opened.") is None
    assert friday.learn("open my drawing app", open_it, "Sorry, that failed.") is None
    assert friday.shortcuts() == []


def test_voice_shortcuts_are_named_and_kept(friday):
    done = ask(friday, "When I say work mode, open Spotify")
    assert done["learned"] == "work mode"
    assert "Say “work mode” any time." in done["say"]
    assert ask(friday, "work mode")["calls"] == [{"tool": "open_app", "args": {"name": "Spotify"}}]
    # Several steps the model did are saved under the name too.
    steps = [
        {"tool": "open_app", "args": {"name": "Notepad"}},
        {"tool": "play_youtube", "args": {"query": "lofi"}},
    ]
    assert friday.learn("When I say study time, open notepad and play lofi", steps, "Done.")
    assert [c["tool"] for c in ask(friday, "study time")["calls"]] == ["open_app", "play_youtube"]
    assert {s["words"] for s in friday.shortcuts() if s["mine"]} == {"work mode", "study time"}
    assert ask(friday, "forget work mode")["say"] == "Forgot “work mode”."
    assert ask(friday, "work mode") is None


def test_a_failed_first_step_hands_over_to_the_model(friday):
    calls = [{"tool": "snap_window", "args": {"window": "Ghost", "position": "left"}}]
    friday.learn("put ghost left", calls, "Ghost is on the left.")
    assert ask(friday, "put ghost left") is None


def test_learning_can_be_switched_off(tmp_path):
    async def run(tool, args):
        return {"friday_say": "ok"}

    off = Instant(tmp_path / "s.json", lambda: APPS, run, learning=lambda: False)
    assert off.learn("open paint", [{"tool": "open_app", "args": {"name": "Paint"}}], "Ok.") is None
    assert asyncio.run(off.handle("When I say art, open paint")) is None


def test_instant_route_runs_friday_quick_tools(server):
    done = httpx.post(f"{server.url}/instant", json={"text": "open notepad"}, headers=AUTH).json()
    assert done["handled"] and done["say"] == "Notepad is open."
    assert server.launched == ["notepad"]
    nothing = httpx.post(
        f"{server.url}/instant", json={"text": "what should I eat"}, headers=AUTH
    ).json()
    assert nothing == {"handled": False}
    learned = httpx.post(
        f"{server.url}/instant/learn",
        json={
            "text": "notepad in the corner",
            "calls": [
                {"tool": "snap_window", "args": {"window": "notepad", "position": "top-left"}}
            ],
            "reply": "Notepad is on the top left.",
        },
        headers=AUTH,
    ).json()
    assert learned == {"learned": True, "new": True}
    again = httpx.post(
        f"{server.url}/instant", json={"text": "notepad in the corner"}, headers=AUTH
    ).json()
    assert again["handled"] and server.snapped[-1] == ("notepad", "top-left")
    shortcuts = httpx.get(f"{server.url}/instant/shortcuts", headers=AUTH).json()
    assert [s["words"] for s in shortcuts["shortcuts"]] == ["notepad in the corner"]
    assert httpx.post(f"{server.url}/instant", json={"text": "x"}).status_code == 401


def test_time_date_and_battery_questions_get_only_that(friday):
    asks = {
        "what is the time": ("time", False),
        "time batao": ("time", True),
        "abhi kitne baje hain": ("time", True),
        "aaj kya date hai": ("date", True),
        "is my laptop charging": ("battery", False),
        "laptop charge ho raha hai kya": ("battery", True),
    }
    for text, (wanted, hinglish) in asks.items():
        done = ask(friday, text)
        assert done["calls"] == [
            {"tool": "system_status", "args": {"ask": wanted, "hinglish": hinglish}}
        ], text
    for text in ("set a timer for 5 minutes", "remind me at 5 pm"):
        assert all(c["tool"] != "system_status" for c in (ask(friday, text) or {}).get("calls", []))


def test_email_summaries_always_go_to_the_background_summary(friday):
    for text, args in (
        ("summarise my gmail", {"which": 0, "about": "", "hinglish": False}),
        ("read my latest email", {"which": 1, "about": "", "hinglish": False}),
        ("summarise the mail from ravi", {"which": 0, "about": "ravi", "hinglish": False}),
        ("mere mail padh ke batao", {"which": 0, "about": "", "hinglish": True}),
    ):
        assert ask(friday, text)["calls"] == [{"tool": "gmail_summary", "args": args}], text
    assert ask(friday, "any new email")["calls"] == [{"tool": "gmail_new", "args": {}}]
    assert ask(friday, "reply to the mail from ravi") is None  # the Mail helper drafts it


def test_youtube_search_and_videos_on_screen(friday):
    # No site named: the tool picks YouTube when the owner's browser shows it.
    assert ask(friday, "search for lofi beats")["calls"][0] == {
        "tool": "web_search",
        "args": {"query": "lofi beats"},
    }
    assert ask(friday, "google cricket score")["calls"][0]["args"]["site"] == "google"
    assert ask(friday, "search lofi on yt")["calls"][0]["args"]["site"] == "youtube"
    for said, n in [
        ("play first video", 1),
        ("Play the first video.", 1),
        ("play the second one", 2),
        ("third video", 3),
        ("play the video which is visible on the screen", 1),
        ("play the video on my screen", 1),
        ("play this video", 1),
        ("pehla video chalao", 1),
    ]:
        assert ask(friday, said)["calls"] == [{"tool": "play_shown_video", "args": {"n": n}}], said
    # A song whose name only starts like one still plays as a song.
    assert ask(friday, "play first class")["calls"][0]["tool"] == "play_youtube"


def test_every_instant_answer_shows_on_the_task_toast(server):
    def events():
        feed = httpx.get(f"{server.url}/activity", headers=AUTH).json()
        return feed["events"] if isinstance(feed, dict) else feed

    before = len(events())
    done = httpx.post(f"{server.url}/instant", json={"text": "what time is it"}, headers=AUTH).json()
    assert done["handled"]
    new = events()[before:]
    assert [(e["action"], e["state"]) for e in new] == [("instant", "done")]
    assert new[0]["detail"] == done["say"]
    # A tool that shows its own steps is not shown twice.
    before = len(events())
    httpx.post(f"{server.url}/instant", json={"text": "open notepad"}, headers=AUTH)
    assert "instant" not in [e["action"] for e in events()[before:]]


def test_apps_close_completely_and_open_by_their_short_name(friday):
    for said in ("close chrome", "Chrome band karo", "chrome ko band kar do", "quit spotify"):
        call = ask(friday, said)["calls"][0]
        assert call["tool"] == "close_app", said
    assert ask(friday, "close chrome")["calls"][0]["args"] == {"name": "Google Chrome"}
    assert ask(friday, "close the door") is None
    # "ChatGPT" is the ChatGPT Classic desktop app, not the website.
    assert ask(friday, "chatgpt kholo")["calls"][0] == {
        "tool": "open_app",
        "args": {"name": "ChatGPT Classic"},
    }
    assert ask(friday, "open youtube")["calls"][0]["args"] == {"name": "YouTube"}
    # "google" is never Google Chrome.
    assert friday.app_named("google") is None


def test_in_chrome_means_the_browser_not_the_app(friday):
    for said in ("open youtube in chrome", "chrome mein youtube kholo", "youtube chrome me kholo"):
        assert ask(friday, said)["calls"][0] == {
            "tool": "open_website",
            "args": {"url": "youtube.com", "in_browser": True},
        }, said


def test_youtube_pe_is_where_to_play_not_what(friday):
    for said in ("lofi beats youtube pe chalao", "youtube pe lofi beats chalao"):
        assert ask(friday, said)["calls"][0] == {
            "tool": "play_youtube",
            "args": {"query": "lofi beats"},
        }, said
    for said, n in (("youtube pe pehla video play karo", 1), ("youtube pe doosra video chalao", 2)):
        assert ask(friday, said)["calls"][0] == {"tool": "play_shown_video", "args": {"n": n}}, said


def test_youtube_player_asks_go_to_youtube(friday):
    for said, action in [
        ("full screen", "fullscreen"),
        ("video ko full screen karo", "fullscreen"),
        ("exit full screen", "exit_fullscreen"),
        ("full screen band karo", "exit_fullscreen"),
        ("captions on", "captions"),
        ("theater mode", "theater"),
        ("video mute karo", "mute"),
        ("next video", "next"),
        ("agla video", "next"),
        ("youtube speed badhao", "faster"),
    ]:
        call = ask(friday, said)["calls"][0]
        assert call["tool"] == "youtube_control", said
        assert call["args"]["action"] == action, said
    # Without "video" or "YouTube": only while YouTube is in front.
    assert ask(friday, "full screen")["calls"][0]["args"]["in_front"] is True
    assert ask(friday, "video full screen")["calls"][0]["args"]["in_front"] is False
    # Someone else's: the PC's volume, a window move, the next song.
    for said in ["mute", "full screen chrome", "next song", "maximize notepad"]:
        done = ask(friday, said)
        assert not done or done["calls"][0]["tool"] != "youtube_control", said


def test_full_screen_off_youtube_is_not_a_video_ask(tmp_path):
    ran: list = []

    async def run(tool, args):
        ran.append(tool)
        if tool == "youtube_control" and args.get("in_front"):
            return {"skipped": True, "friday_say": ""}
        return {"friday_say": "ok"}

    instant = Instant(tmp_path / "s.json", lambda: APPS, run)
    assert asyncio.run(instant.handle("full screen")) is None  # the model decides
    assert ran == ["youtube_control"]


def test_a_number_on_the_page_plays_that_video_there(friday):
    for said, n in [
        ("play the third video", 3),
        ("3rd number ka play karo", 3),
        ("teesra wala chalao", 3),
        ("number 4 video", 4),
        ("play 2nd one", 2),
        ("youtube pe pehla video play karo", 1),
    ]:
        call = ask(friday, said)["calls"][0]
        assert call == {"tool": "play_shown_video", "args": {"n": n}}, said
    for said in ["third", "volume 3", "play 3 idiots trailer"]:
        done = ask(friday, said)
        assert not done or done["calls"][0]["tool"] != "play_shown_video", said
