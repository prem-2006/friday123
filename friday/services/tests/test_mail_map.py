"""The background mail map, with a pretend Gmail."""

from __future__ import annotations

import pytest

from friday_services.mail_map import FEED, MailError, MailMap, new_mail_sentence, parse_feed

FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed version="0.3" xmlns="http://purl.org/atom/ns#">
<title>Gmail - Inbox for prem@gmail.com</title>
<fullcount>2</fullcount>
<entry>
<title>Project plan</title>
<summary>Can you approve the plan by Friday?</summary>
<link rel="alternate" href="https://mail.google.com/mail?account_id=prem@gmail.com&amp;message_id=18fa1&amp;view=conv&amp;extsrc=atom" type="text/html" />
<issued>2026-11-01T09:00:00Z</issued>
<author><name>Ravi</name><email>ravi@example.com</email></author>
</entry>
<entry>
<title>Your order has shipped</title>
<summary>Arriving Tuesday</summary>
<link rel="alternate" href="https://mail.google.com/mail?message_id=18fa0&amp;view=conv" type="text/html" />
<issued>2026-10-31T18:00:00Z</issued>
<author><name>Amazon</name><email>ship@amazon.in</email></author>
</entry>
</feed>"""

PRINT_PAGE = """<html><head><style>b{}</style></head><body>
<b>Project plan</b><br>Hi Prem,<br><br>Can you approve the plan by Friday? Budget is &#8377;50,000.
<script>ignore()</script></body></html>"""


def gmail(feed=FEED_XML, status=200, page=PRINT_PAGE):
    asked: list[str] = []

    def fetch(url):
        asked.append(url)
        return (status, feed) if url == FEED else (200, page)

    fetch.asked = asked
    return fetch


def test_the_feed_gives_unread_mail_newest_first():
    count, mails = parse_feed(FEED_XML)
    assert count == 2
    assert [m.id for m in mails] == ["18fa1", "18fa0"]
    assert mails[0].line() == "Ravi: Project plan"
    assert mails[0].preview == "Can you approve the plan by Friday?"


def test_new_means_not_told_yet(tmp_path):
    mail = MailMap(gmail(), tmp_path / "map.json")
    count, _all, fresh = mail.new()
    assert count == 2 and len(fresh) == 2
    assert new_mail_sentence(count, fresh).startswith("2 new: Ravi: Project plan; Amazon:")
    _count, _all, fresh = mail.new()
    assert fresh == []
    assert new_mail_sentence(2, []) == "Nothing new since I last told you. 2 unread in total."
    assert new_mail_sentence(0, []) == "No unread mail."


def test_reading_one_mail_gives_its_full_text(tmp_path):
    fetch = gmail()
    mail = MailMap(fetch, tmp_path / "map.json")
    latest = mail.find(1)
    text = mail.full_text(latest)
    assert "approve the plan by Friday" in text and "₹50,000" in text
    assert "ignore()" not in text
    assert "th=18fa1" in fetch.asked[-1]
    assert mail.find(about="amazon").subject == "Your order has shipped"
    with pytest.raises(MailError):
        mail.find(about="nobody")


def test_a_page_that_is_not_the_mail_falls_back_to_the_preview(tmp_path):
    mail = MailMap(gmail(page="<html>Sign in</html>"), tmp_path / "map.json")
    assert mail.full_text(mail.find(1)) == "Can you approve the plan by Friday?"


def test_not_signed_in_says_what_to_do(tmp_path):
    mail = MailMap(gmail(feed="<html>login</html>", status=200), tmp_path / "map.json")
    with pytest.raises(MailError, match="Connect"):
        mail.new()


def test_reads_through_composio_when_gmail_is_connected_there(tmp_path):
    def chrome(url):
        raise AssertionError("Chrome should not be asked")

    answer = {
        "connected": True,
        "count": 3,
        "mails": [
            {
                "id": "c1",
                "sender": "Ravi",
                "email": "ravi@example.com",
                "subject": "Project plan",
                "preview": "Can you approve",
                "when": "2026-11-01T09:00:00Z",
                "text": "Hi Prem, can you approve the plan by Friday?",
            }
        ],
    }
    mails = MailMap(chrome, tmp_path / "map.json", composio=lambda: answer)
    count, found = mails.refresh()
    assert count == 3 and found[0].line() == "Ravi: Project plan"
    assert mails.full_text(found[0]) == "Hi Prem, can you approve the plan by Friday?"
    assert [m.id for m in mails.new()[2]] == ["c1"]


def test_falls_back_to_chrome_when_composio_has_no_gmail(tmp_path):
    fetch = gmail()
    mails = MailMap(fetch, tmp_path / "map.json", composio=lambda: {"connected": False})
    count, found = mails.refresh()
    assert count == 2 and found[0].subject == "Project plan"
    assert fetch.asked == [FEED]


def test_composio_reader_says_not_connected_when_the_app_is_down():
    from friday_services.mail_map import composio_reader

    assert composio_reader("http://127.0.0.1:9", timeout=2)() == {"connected": False}
