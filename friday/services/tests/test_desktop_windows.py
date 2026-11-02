"""Which open windows belong to an app, and opening or closing apps with them."""

from __future__ import annotations

from types import SimpleNamespace

from friday_services.desktop import DesktopApps, app_window_belongs

YOUTUBE = {"name": "YouTube", "app_id": "Chrome._crx_agimnkijcaahngcdmfeangaknmldooml"}
CHROME = {"name": "Google Chrome", "app_id": "Chrome"}
CHATGPT = {"name": "ChatGPT Classic", "app_id": "OpenAI.ChatGPT-Desktop_2p2nqsd0c76g0!ChatGPT"}


def win(title, proc, app, hwnd=1):
    return {"hwnd": hwnd, "title": title, "proc": proc, "app": app}


def test_an_installed_site_is_not_its_browser():
    app_window = win("lofi beats - YouTube", "chrome", "Google Chrome")
    tab = win("lofi beats - YouTube - Google Chrome", "chrome", "Google Chrome")
    assert app_window_belongs(YOUTUBE, app_window)
    assert not app_window_belongs(YOUTUBE, tab)
    assert app_window_belongs(CHROME, tab)
    assert not app_window_belongs(CHROME, app_window)
    assert app_window_belongs(CHATGPT, win("ChatGPT", "ChatGPT", "ChatGPT"))
    assert not app_window_belongs(
        CHATGPT, win("ChatGPT - Google Chrome", "chrome", "Google Chrome")
    )


def apps(windows):
    launched, brought, closed = [], [], []
    desktop = DesktopApps(
        list_apps=lambda: [YOUTUBE, CHROME, CHATGPT, {"name": "Notepad", "app_id": "np"}],
        launch=launched.append,
        desktop=SimpleNamespace(windows=lambda: []),
        app_windows=lambda app: [w for w in windows if app_window_belongs(app, w)],
        bring=brought.append,
        close=closed.append,
    )
    return desktop, launched, brought, closed


def test_an_open_app_comes_to_the_front_instead_of_a_second_copy():
    desktop, launched, brought, _ = apps([win("YouTube", "chrome", "Google Chrome", hwnd=7)])
    assert desktop.open("YouTube") == {
        "opened": "YouTube",
        "window": "YouTube",
        "already_open": True,
    }
    assert brought == [7] and launched == []


def test_closing_an_app_closes_every_window_of_it():
    windows = [
        win("Gmail - Google Chrome", "chrome", "Google Chrome", hwnd=1),
        win("YouTube", "chrome", "Google Chrome", hwnd=2),
        win("Docs - Google Chrome", "chrome", "Google Chrome", hwnd=3),
    ]
    desktop, _, _, closed = apps(windows)
    assert desktop.close("Google Chrome") == {"closed": "Google Chrome", "windows": 2}
    assert closed == [1, 3]  # the YouTube app is not Chrome's
    assert desktop.close("Notepad") == {"closed": "Notepad", "windows": 0}


def test_a_site_opens_in_its_app_when_there_is_one():
    desktop, *_ = apps([])
    assert desktop.app_for_site("ChatGPT") == "ChatGPT Classic"
    assert desktop.app_for_site("YouTube") == "YouTube"
    assert desktop.app_for_site("Google") is None  # not Google Chrome
    assert desktop.installed_site("YouTube") == YOUTUBE
    assert desktop.installed_site("Notepad") is None


def test_the_search_a_youtube_window_shows():
    from friday_services.mcp_quick import youtube_query

    assert youtube_query("YouTube - lofi beats - YouTube") == "lofi beats"  # the app
    assert youtube_query("(2) lofi - YouTube - Google Chrome") == "lofi"  # a tab
    assert youtube_query("YouTube - (10) YouTube") == ""  # the app's home page


def test_videos_on_the_page_are_picked_as_shown_not_searched():
    from friday_services.desktop import pick_video, shown_videos

    links = [
        ("a", "Home"),
        ("b", "4 minutes, 20 seconds"),  # a thumbnail: no title
        ("c", "Jo Tum Mere Ho 4 minutes, 20 seconds"),
        ("d", "Jo Tum Mere Ho 4 minutes, 20 seconds"),  # its title link: the same video
        ("e", "Mix – Lofi songs"),
        ("f", "O Re Piya Lofi | Slowed + Reverb 3 minutes, 5 seconds"),
    ]
    videos = shown_videos(links)
    assert [name for _, name in videos] == [
        "Jo Tum Mere Ho",
        "Mix – Lofi songs",
        "O Re Piya Lofi | Slowed + Reverb",
    ]
    assert pick_video(videos, 3)[0] == "f"
    assert pick_video(videos, 9) is None
    assert pick_video(videos, title="O Re Piya Lofi")[0] == "f"
    assert pick_video(videos, title="slowed + reverb")[0] == "f"
    assert pick_video(videos, title="Kesariya") is None
