"""YouTube's player keys in the YouTube window used last (media.py)."""

from __future__ import annotations

from friday_services.media import OwnerMedia, youtube_window

APP = {"hwnd": 1, "title": "YouTube - Lambiya Judaiyan", "proc": "chrome"}
TAB = {"hwnd": 2, "title": "lofi - YouTube - Google Chrome", "proc": "chrome"}
NOTEPAD = {"hwnd": 3, "title": "notes.txt - Notepad", "proc": "notepad"}
NOTCH = {"hwnd": 4, "title": "Friday", "proc": "electron"}


def test_picks_the_youtube_window_used_last():
    assert youtube_window([NOTEPAD, TAB, APP]) == TAB
    assert youtube_window([APP, TAB]) == APP
    assert youtube_window([NOTEPAD]) is None
    # A notepad window titled "YouTube" is not YouTube.
    assert youtube_window([{**NOTEPAD, "title": "YouTube - notes"}]) is None


def test_in_front_only_when_the_owner_is_on_youtube():
    assert youtube_window([NOTCH, APP, NOTEPAD], in_front=True) == APP
    assert youtube_window([NOTEPAD, APP], in_front=True) is None


def test_presses_youtube_keys_in_that_window():
    pressed, focused = [], []
    media = OwnerMedia(
        None,
        windows=lambda: [],
        send_keys=lambda keys, **_: pressed.append(keys),
        focus_window=focused.append,
    )
    assert media.youtube("fullscreen", APP) == {"title": "Lambiya Judaiyan", "app": "YouTube"}
    media.youtube("exit_fullscreen", TAB)
    media.youtube("forward", APP, 30)
    assert focused == [1, 2, 1]
    assert pressed == ["f", "{ESC}", "l", "l", "l"]
    assert media.youtube("captions", TAB)["title"] == "lofi"
