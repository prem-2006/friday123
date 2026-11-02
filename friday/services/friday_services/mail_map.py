"""Friday's background map of the owner's Gmail: what is unread, what is new
since the owner last asked, and the full text of a message to summarise.

When Gmail is connected through Composio (the main page's Connections card),
the mail is read there: the app server asks Composio's Gmail tool for unread
inbox mail, full text included. Otherwise it comes through Friday's own
Chrome, signed in to Gmail once, as plain requests with no page shown: Gmail's private inbox feed (unread mail:
sender, subject, preview, time) and a message's print view (its full text).
Nothing is opened in front of the owner, and nothing is ever sent, archived
or deleted from here. The map is kept in data/mail-map.json.
"""

from __future__ import annotations

import html
import json
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

FEED = "https://mail.google.com/mail/feed/atom"
ATOM = "{http://purl.org/atom/ns#}"
PRINT_VIEW = "https://mail.google.com/mail/u/0/?ui=2&view=pt&search=all&th={id}"
MAX_TEXT = 6000

Fetch = Callable[[str], tuple[int, str]]
# Composio's answer: {"connected": False}, or {"connected": True, "count": n,
# "mails": [{id, sender, email, subject, preview, when, text}]}.
Composio = Callable[[], dict]


class MailError(RuntimeError):
    pass


@dataclass
class Mail:
    id: str  # Gmail's message id (hex), from the feed's link
    sender: str
    email: str
    subject: str
    preview: str
    when: str  # ISO time

    def line(self) -> str:
        """ "Ravi: Project plan" """
        return f"{self.sender or self.email}: {self.subject or '(no subject)'}"


def parse_feed(xml: str) -> tuple[int, list[Mail]]:
    """Gmail's inbox feed -> (unread count, unread mail, newest first)."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as error:
        raise MailError("Gmail's feed could not be read.") from error
    count = int((root.findtext(f"{ATOM}fullcount") or "0").strip() or 0)
    found: list[Mail] = []
    for entry in root.findall(f"{ATOM}entry"):
        link = entry.find(f"{ATOM}link")
        href = link.get("href", "") if link is not None else ""
        ids = parse_qs(urlparse(href).query).get("message_id") or []
        author = entry.find(f"{ATOM}author")
        found.append(
            Mail(
                id=ids[0] if ids else (entry.findtext(f"{ATOM}id") or "").rsplit(":", 1)[-1],
                sender=(author.findtext(f"{ATOM}name") if author is not None else "") or "",
                email=(author.findtext(f"{ATOM}email") if author is not None else "") or "",
                subject=(entry.findtext(f"{ATOM}title") or "").strip(),
                preview=html.unescape(entry.findtext(f"{ATOM}summary") or "").strip(),
                when=(entry.findtext(f"{ATOM}issued") or entry.findtext(f"{ATOM}modified") or ""),
            )
        )
    found.sort(key=lambda mail: mail.when, reverse=True)
    return count, found


_SCRIPTS = re.compile(r"<(script|style|head)\b.*?</\1>", re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")
_BREAKS = re.compile(r"<\s*(br|/p|/div|/tr|/li)\b[^>]*>", re.I)


def print_view_text(page: str) -> str:
    """The readable text of a message's print view."""
    text = _BREAKS.sub("\n", _SCRIPTS.sub(" ", page))
    text = html.unescape(_TAGS.sub(" ", text))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)[:MAX_TEXT]


class MailMap:
    def __init__(
        self,
        fetch: Fetch,
        path: Path,
        clock: Callable[[], float] = time.time,
        composio: Composio | None = None,
        composio_latest: Composio | None = None,
    ):
        self._fetch = fetch
        self.path = path
        self._clock = clock
        self._composio = composio
        self._composio_latest = composio_latest
        self._texts: dict[str, str] = {}  # full text Composio already gave

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")

    def _from_composio(self, read: Composio | None = None) -> tuple[int, list[Mail]] | None:
        """Mail through Composio (unread unless told otherwise); None when Gmail
        is not connected there."""
        read = read or self._composio
        if read is None:
            return None
        answer = read()
        if not answer.get("connected"):
            return None
        mails = []
        for item in answer.get("mails") or []:
            mail = Mail(
                **{field: str(item.get(field) or "") for field in Mail.__dataclass_fields__}
            )
            mail.preview = html.unescape(mail.preview)
            if item.get("text"):
                self._texts[mail.id] = str(item["text"])
            mails.append(mail)
        return int(answer.get("count") or len(mails)), mails

    def _from_chrome(self) -> tuple[int, list[Mail]]:
        status, body = self._fetch(FEED)
        if status in (401, 403) or "<feed" not in body:
            raise MailError(
                "Gmail is not connected. On the main page, Connections, press Connect "
                "next to Gmail (or Sign in once, for Friday's Chrome)."
            )
        if status >= 400:
            raise MailError(f"Gmail did not answer (error {status}).")
        return parse_feed(body)

    def refresh(self) -> tuple[int, list[Mail]]:
        count, mails = self._from_composio() or self._from_chrome()
        data = self._load()
        data.update(
            checked_at=self._clock(),
            unread=count,
            mails=[asdict(mail) for mail in mails],
        )
        self._save(data)
        return count, mails

    def new(self) -> tuple[int, list[Mail], list[Mail]]:
        """-> (unread count, unread mail, the part not told to the owner yet).
        What is returned as new counts as told from now on."""
        count, mails = self.refresh()
        data = self._load()
        told = set(data.get("told", []))
        fresh = [mail for mail in mails if mail.id not in told]
        data["told"] = sorted(told | {mail.id for mail in mails})[-500:]
        self._save(data)
        return count, mails, fresh

    def inbox(self) -> list[Mail] | None:
        """The inbox from the top, as Gmail shows it (Primary, read or unread);
        None when only the unread feed can be read."""
        found = self._from_composio(self._composio_latest) if self._composio_latest else None
        return found[1] if found else None

    def find(self, which: int = 1, about: str = "") -> Mail:
        """The Nth mail from the top of the inbox (1 = latest), or the newest
        whose sender or subject mentions `about`."""
        if about.strip():
            words = about.casefold().split()
            _count, unread = self.refresh()
            for mail in unread + (self.inbox() or []):
                haystack = f"{mail.sender} {mail.email} {mail.subject}".casefold()
                if all(word in haystack for word in words):
                    return mail
            raise MailError(f'No recent mail about "{about}".')
        mails = self.inbox()
        if mails is None:
            _count, mails = self.refresh()
        if not mails:
            raise MailError("Your inbox has no mail to read.")
        if not 1 <= which <= len(mails):
            raise MailError(f"There are only {len(mails)} recent emails.")
        return mails[which - 1]

    def full_text(self, mail: Mail) -> str:
        """The message's text (from Composio, or the print view); the preview
        if that fails."""
        if self._texts.get(mail.id):
            return self._texts[mail.id][:MAX_TEXT]
        try:
            status, page = self._fetch(PRINT_VIEW.format(id=mail.id))
        except Exception:
            status, page = 0, ""
        text = print_view_text(page) if status == 200 else ""
        if mail.subject and mail.subject.casefold()[:20] not in text.casefold():
            return mail.preview  # not the message: keep to what the feed said
        return text


def composio_reader(
    app_url: str, token: str = "", timeout: float = 45, which: str = "unread"
) -> Composio:
    """Asks Friday's app server for the mail Composio sees (unread, or latest:
    the top of the inbox). If the app server is down or Composio fails, answers
    "not connected", so the mail is read through Friday's Chrome instead."""
    import httpx

    def read() -> dict:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            response = httpx.get(
                f"{app_url.rstrip('/')}/api/friday/apps/gmail/{which}",
                headers=headers,
                timeout=timeout,
            )
            return response.json() if response.status_code == 200 else {"connected": False}
        except (httpx.HTTPError, ValueError):
            return {"connected": False}

    return read


def new_mail_sentence(count: int, fresh: list[Mail], most: int = 4) -> str:
    if not count:
        return "No unread mail."
    if not fresh:
        return f"Nothing new since I last told you. {count} unread in total."
    names = "; ".join(mail.line() for mail in fresh[:most])
    more = f", and {len(fresh) - most} more" if len(fresh) > most else ""
    total = f" {count} unread in total." if count > len(fresh) else ""
    return f"{len(fresh)} new: {names}{more}.{total}"
