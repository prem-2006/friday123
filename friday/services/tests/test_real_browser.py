"""The real on-screen browser code driving Chrome (headless here) through the
demo calendar, the same way the Navigator Dot would."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from friday_services import browser as browser_module
from friday_services.browser import BrowserError, OnScreenBrowser

CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
pytestmark = pytest.mark.skipif(
    not CHROME.exists() and not shutil.which("chrome"), reason="Chrome is not installed"
)


def ref_of(elements: str, label: str) -> int:
    for line in elements.splitlines():
        if f'"{label}' in line:
            return int(line[1 : line.index("]")])
    raise AssertionError(f"{label!r} not in:\n{elements}")


def test_fills_in_the_calendar_like_navigator(server, tmp_path):
    browser = OnScreenBrowser(tmp_path / "profile", headless=True)
    try:
        page = browser.open(f"{server.url}/calendar")
        assert page["title"] == "Friday demo calendar"
        page = browser.type(ref_of(page["elements"], "Title"), "Dentist", press_enter=False)
        page = browser.type(ref_of(page["elements"], "Date"), "2026-10-23", press_enter=False)
        page = browser.type(ref_of(page["elements"], "Time"), "18:00", press_enter=False)
        page = browser.click(ref_of(page["elements"], "Add event"))
        assert "Added: Dentist on 2026-10-23 at 18:00." in browser.read()["text"]
        assert browser.screenshot()  # the owner's live view gets a picture
        with pytest.raises(BrowserError, match="No element"):
            browser.click(999)
    finally:
        browser.close()


FAKE_HOME = """<!doctype html><title>YouTube</title>
<form action="/results"><input name="search_query" aria-label="Search"></form>"""
FAKE_RESULTS = """<!doctype html><title>results - YouTube</title><body>
<a href="/shorts/s1">Top 5 Python channels #shorts</a>
<a href="/watch?v=v1">Watch</a>
<a href="/watch?v=v1" title="Python full course for beginners">Python full course for beginners</a>
<a href="/@coreyms">Corey Schafer</a>
<a href="/watch?v=v2">12:34</a>
</body>"""


def test_search_types_in_youtubes_box_and_ranks_results(tmp_path):
    browser = OnScreenBrowser(tmp_path / "profile", headless=True)
    try:
        browser._page()  # start Chrome so a pretend YouTube can be served offline

        def serve(route):
            html = FAKE_RESULTS if "/results" in route.request.url else FAKE_HOME
            route.fulfill(status=200, content_type="text/html", body=html)

        browser._context.route("https://www.youtube.com/**", serve)
        found = browser.search("youtube", "best python channel")
        assert found["site"] == "YouTube"
        assert "search_query=best+python+channel" in found["url"]
        assert [(r["kind"], r["title"]) for r in found["results"]] == [
            ("channel", "Corey Schafer"),
            ("video", "Python full course for beginners"),
            ("short", "Top 5 Python channels #shorts"),
        ]
        with pytest.raises(BrowserError, match="YouTube or Google"):
            browser.search("bing", "x")
    finally:
        browser.close()


def test_play_youtube_goes_straight_to_the_video_it_looked_up(tmp_path, monkeypatch):
    monkeypatch.setattr(
        browser_module, "first_video", lambda query: ("v1", "Python full course for beginners")
    )
    browser = OnScreenBrowser(tmp_path / "profile", headless=True)
    try:
        browser._page()
        opened: list[str] = []

        def serve(route):
            opened.append(route.request.url)
            html = "<!doctype html><title>Python full course - YouTube</title>"
            route.fulfill(status=200, content_type="text/html", body=html)

        browser._context.route("https://www.youtube.com/**", serve)
        played = browser.play_youtube("python course")
        assert played == {
            "title": "Python full course for beginners",
            "url": "https://www.youtube.com/watch?v=v1",
        }
        # The lookup already found it: no results page in the window.
        assert [u.split("?")[0] for u in opened] == ["https://www.youtube.com/watch"]
    finally:
        browser.close()


def test_play_youtube_uses_the_results_page_when_the_lookup_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_module, "first_video", lambda query: None)
    browser = OnScreenBrowser(tmp_path / "profile", headless=True)
    try:
        browser._page()
        opened: list[str] = []

        def serve(route):
            url = route.request.url
            opened.append(url)
            if "/watch" in url:
                html = "<!doctype html><title>Python full course - YouTube</title>"
            else:
                html = FAKE_RESULTS
            route.fulfill(status=200, content_type="text/html", body=html)

        browser._context.route("https://www.youtube.com/**", serve)
        played = browser.play_youtube("python course")
        assert played == {
            "title": "Python full course for beginners",
            "url": "https://www.youtube.com/watch?v=v1",
        }
        # Straight to the results page, then the video: no home page, no Short.
        assert [u.split("?")[0] for u in opened] == [
            "https://www.youtube.com/results",
            "https://www.youtube.com/watch",
        ]
    finally:
        browser.close()


FAKE_GMAIL = """<!doctype html><title>Inbox - Gmail</title><body>
<div role="dialog">
  <input aria-label="To recipients" value="priya@example.com">
  <div role="button" aria-label="Send (Ctrl-Enter)" tabindex="0"
       onclick="document.body.insertAdjacentHTML('beforeend', '<span>Message sent</span>')">Send</div>
</div></body>"""


def test_only_friday_can_send_an_email_and_only_through_send_email(tmp_path):
    browser = OnScreenBrowser(tmp_path / "profile", headless=True)
    try:
        browser._page()
        browser._context.route(
            "https://mail.google.com/**",
            lambda route: route.fulfill(status=200, content_type="text/html", body=FAKE_GMAIL),
        )
        page = browser.open("https://mail.google.com/mail/u/0/")
        # Navigator's own tools refuse to send: the owner must approve first.
        with pytest.raises(BrowserError, match="owner's OK"):
            browser.click(ref_of(page["elements"], "Send"))
        with pytest.raises(BrowserError, match="owner's OK"):
            browser.press("Control+Enter")
        assert "Message sent" not in browser.read()["text"]
        # Friday's send_email (behind an approval card) clicks Send.
        assert browser.send_open_email() == {"sent": True}
        assert "Message sent" in browser.read()["text"]
        assert browser.google_account() == {"signed_in": False, "email": None}
    finally:
        browser.close()


def test_send_needs_an_open_gmail_draft(tmp_path):
    from friday_services.browser import is_send_label

    assert is_send_label("Send (Ctrl-Enter)") and is_send_label("send")
    assert not is_send_label("Sender details") and not is_send_label("Resend code")
    browser = OnScreenBrowser(tmp_path / "profile", headless=True)
    try:
        browser._page().set_content("<title>Elsewhere</title>")
        with pytest.raises(BrowserError, match="No Gmail draft"):
            browser.send_open_email()
    finally:
        browser.close()


def test_sign_in_uses_the_owners_gmail_and_reads_which_account_is_in():
    from urllib.parse import parse_qs, urlparse

    from friday_services.browser import email_from_feed, sign_in_url

    query = parse_qs(urlparse(sign_in_url("prem@gmail.com")).query)
    assert query == {"continue": ["https://mail.google.com/"], "Email": ["prem@gmail.com"]}
    assert "Email" not in sign_in_url()
    feed = "<feed><title>Gmail - Inbox for Prem.Singh@gmail.com</title><entry>...</entry></feed>"
    assert email_from_feed(feed) == "prem.singh@gmail.com"
    assert email_from_feed("<feed><title>Gmail</title></feed>") is None
