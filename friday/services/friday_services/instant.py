"""Instant commands and learned shortcuts: Friday acts with no model call.

The idea and most of the rules come from Atticus (SaarthurR/atticus-mac and
its Windows port FindlayHi/atticus-windows, both MIT):

- Simple asks skip the AI: "open Notepad", "Chrome on the left, Notepad on the
  right", "search YouTube for lofi", "play Arijit Singh", "volume 40".
- Learned shortcuts: when a model turn did an action with Friday's own quick
  tools, the exact tool calls are saved under the words that asked for it. The
  same ask later replays them with no model, said in any order or about
  another app ("put notepad left" learned, "put paint left" replays for Paint).
- Voice shortcuts: "When I say focus mode, close Slack and open Spotify" saves
  the words; "focus mode" then does it all.

Atticus replays shell commands; Friday replays its own quick tools (see
mcp_quick.py), so every step still goes through the stop switch and activity
feed. Anything that sends, buys, calls or writes a reminder is never learned.
Hindi and Hinglish asks ("notepad kholo", "arijit singh bajao") work too.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

# Tools whose calls can be replayed later with no model. Nothing here sends,
# pays, calls someone or saves something said at that moment.
REPLAYABLE = {
    "open_app",
    "open_website",
    "play_youtube",
    "web_search",
    "snap_window",
    "media_control",
    "set_volume",
    "press_keys_in_app",
    "open_file",
    "run_mode",
}
MAX_STEPS = 6
FORGET_AFTER_DAYS = 30

# "close this" depends on the moment: never learned or replayed.
DEICTIC = re.compile(r"\b(this|that|these|those|it|here|there|them|him|her|yeh|woh|isko|usko)\b")
NAMED = re.compile(
    r"(?:from now on,? )?when(?:ever)? i say "
    r"(?:[\"“']([^\"”']{2,40})[\"”']|([^,:;\"“]{2,40}?)\s*(?:[,:;]|\b(?:do|then|can you|please|you)\b))",
    re.I,
)
MORE = re.compile(r"\b(and|then|after that|also|aur|phir)\b", re.I)
FAILED = re.compile(
    r"\b(can't|cannot|couldn't|could not|unable|not able|failed|not done|didn't work|did not work|"
    r"no app called|no open window|sorry)\b",
    re.I,
)
STOP = set(
    "a an the to of for on in at my me i you your please can could would will just "
    "is are be it its with from by up".split()
)
FILLER = set(
    "um uh umm hmm like so bro yeah actually okay ok hey wait new again also now then "
    "quick quickly real friday zara jaldi".split()
)
THANKS = {"thanks", "thank you", "thank you so much", "shukriya", "dhanyavaad", "dhanyavad"}
# Spoken name -> the Start-menu name it usually means.
ALIASES = {
    "chrome": "Google Chrome",
    "google chrome": "Google Chrome",
    "edge": "Microsoft Edge",
    "vs code": "Visual Studio Code",
    "vscode": "Visual Studio Code",
    "code": "Visual Studio Code",
    "word": "Word",
    "excel": "Excel",
    "powerpoint": "PowerPoint",
    "explorer": "File Explorer",
    "file explorer": "File Explorer",
    "files": "File Explorer",
    "terminal": "Terminal",
    "cmd": "Command Prompt",
    "command prompt": "Command Prompt",
    "calculator": "Calculator",
    "settings": "Settings",
    "notepad": "Notepad",
    "paint": "Paint",
    "spotify": "Spotify",
    "whatsapp": "WhatsApp",
    "teams": "Microsoft Teams",
    "outlook": "Outlook",
}
# Ways to say a screen or a size, in any order: "full screen chrome on my big screen".
MOVE_WORDS = [
    (r"\b(?:full ?screen|maximi[sz]ed?|bada karo)\b", "full"),
    (r"\b(?:minimi[sz]ed?|chhota karo|hide)\b", "minimize"),
    (
        r"\b(?:(?:big|bigger|large|larger|external|second) (?:screen|monitor|display)|external|monitor|display)\b",
        "monitor",
    ),
    (
        r"\b(?:laptop|built ?in|(?:small|smaller|little) (?:screen|display))(?: (?:screen|display))?\b",
        "laptop",
    ),
    (r"\b(?:top left|upper left)\b", "top-left"),
    (r"\b(?:top right|upper right)\b", "top-right"),
    (r"\b(?:bottom left|lower left)\b", "bottom-left"),
    (r"\b(?:bottom right|lower right)\b", "bottom-right"),
    (r"\b(?:baayen|baaye|bayen|left side)\b", "left"),
    (r"\b(?:daayen|daaye|dayen|right side)\b", "right"),
]
PLACES = r"top-left|top-right|bottom-left|bottom-right|left|right|full|center|middle|minimize"
OPEN_WORDS = r"open|launch|start|pull up|bring up|focus(?: on)?|switch to|go to"
HINGLISH = (
    r"\b(hai|hain|ho raha|ho rahi|kya|kitna|kitni|kitne|kab|abhi|nahi|hua|batao|bata|"
    r"bolo|baje|samay|waqt|aaj|tareekh|tarikh|laptop ka)\b"
)
HINDI_OPEN = r"kholo|khol do|kholiye|kholna|open karo|open kar do|chalu karo|start karo"
HINDI_CLOSE = r"band karo|band kar do|bandh karo|bandh kar do|close karo|close kar do|hata do"
# "in Chrome": the owner wants the browser, not the site's app.
IN_BROWSER = (
    r"(?:(.+?) (?:in|on) (?:the )?(?:google )?(?:chrome|browser)"
    r"|(?:google )?(?:chrome|browser) (?:mein|me|main|pe|par) (.+)"
    r"|(.+?) (?:google )?(?:chrome|browser) (?:mein|me|main|pe|par))"
)
HINDI_PLAY = r"bajao|baja do|play karo|play kar do|laga do|lagao|chalao|chala do"
HINDI_SEARCH = r"search karo|search kar do|dhundo|dhoondo|google karo"
# "the second video": its place in the results on screen.
ORDINALS = {
    "first": 1, "1st": 1, "top": 1, "pehla": 1, "pehli": 1, "pehle": 1,
    "second": 2, "2nd": 2, "doosra": 2, "dusra": 2, "doosri": 2, "dusri": 2,
    "third": 3, "3rd": 3, "teesra": 3, "teesri": 3,
    "fourth": 4, "4th": 4, "fifth": 5, "5th": 5,
}  # fmt: skip
FILE_EXT = re.compile(
    r"\.(pdf|docx?|xlsx?|pptx?|txt|csv|png|jpe?g|zip|mp[34]|mov|md|py|java|exe)$", re.I
)

RunTool = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def site_url(name: str) -> str | None:
    """A spoken site name -> its address: "youtube" -> "youtube.com"."""
    from friday_services.mcp_quick import SITE_NAMES

    name = name.strip().removeprefix("the ")
    if re.fullmatch(r"[\w-]+(\.[\w-]+)+", name.replace(" dot ", ".")):
        return name.replace(" dot ", ".")
    want = _key(name)
    for host, shown in SITE_NAMES.items():
        if _key(shown) == want:
            return host
    return None


def plain(text: str) -> str:
    """Spoken request -> its core words: "Hey Friday, can you open Spotify please?" -> "open spotify"."""
    t = re.sub(r"[,;!?।]|:(?!//)|(?<!\w)\.|\.(?!\w)", "", text.lower().strip(" .!?")).strip()
    t = re.sub(r"^(hey |hi |ok |okay )?(friday,? )?", "", t)
    t = re.sub(r"^(please |can you |could you |would you |zara |jaldi )+", "", t)
    t = re.sub(r"^(also |and |now |then )+", "", re.sub(r"\s+(please|na|plz)$", "", t))
    return re.sub(r"\s+", " ", t).strip()


def gist(text: str) -> list[str]:
    """The words that matter, in order: "um, can you make a new Zoom meeting?" == "start a zoom meeting"."""
    return [
        {"create": "make", "start": "make", "launch": "open"}.get(w, w)
        for w in re.findall(r"[a-z0-9'.-]+", plain(text))
        if w not in STOP | FILLER
    ]


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


class Instant:
    def __init__(
        self,
        path: Path,
        app_names: Callable[[], list[str]],
        run_tool: RunTool,
        learning: Callable[[], bool] = lambda: True,
    ):
        self.path = path
        self._app_names = app_names
        self._run = run_tool
        self._learning = learning
        self._last_app = ""
        self._last_app_at = 0.0

    # ---------- the learned shortcuts file ----------

    def skills(self) -> dict[str, dict[str, Any]]:
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(saved, dict):
            return {}
        return {
            task: entry
            for task, entry in saved.items()
            if isinstance(entry, dict)
            and isinstance(entry.get("reply"), str)
            and isinstance(entry.get("calls"), list)
            and all(
                isinstance(c, dict)
                and c.get("tool") in REPLAYABLE
                and isinstance(c.get("args"), dict)
                for c in entry["calls"]
            )
        }

    def _save(self, skills: dict[str, dict[str, Any]]) -> None:
        """Write learned tasks; one unused for 30 days goes (shortcuts the owner named stay)."""
        old = time.strftime("%Y-%m-%d", time.localtime(time.time() - FORGET_AFTER_DAYS * 86400))
        skills = {
            k: v
            for k, v in skills.items()
            if v.get("mine") or max(v.get("used", ""), v.get("at", "")) >= old
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.path.parent, delete=False, suffix=".tmp"
        ) as f:
            json.dump(skills, f, indent=1, ensure_ascii=False)
        os.replace(f.name, self.path)  # a cancelled write cannot truncate the learned tasks

    def shortcuts(self) -> list[dict[str, Any]]:
        return [
            {"words": k, "steps": [c["tool"] for c in v["calls"]], "mine": bool(v.get("mine"))}
            for k, v in sorted(self.skills().items())
        ]

    def forget(self, words: str) -> bool:
        skills, k = self.skills(), plain(words)
        hit = k if k in skills else next((s for s in skills if gist(s) == gist(k)), None)
        if hit is None:
            return False
        skills.pop(hit)
        self._save(skills)
        return True

    def learn(self, text: str, calls: list[dict[str, Any]], reply: str) -> bool | None:
        """Save the tool calls a turn made, if every one can be replayed.
        -> True: a new or changed shortcut; False: already known; None: not learned."""
        if not self._learning():
            return None
        mine = False
        named = NAMED.match(text.strip())
        if named:  # "When I say focus mode, …": saved under "focus mode"
            text, mine = (named[1] or named[2]).strip(), True
        k = plain(text)
        calls = [
            {"tool": c["tool"], "args": dict(c.get("args") or {})}
            for c in calls
            if isinstance(c, dict) and isinstance(c.get("tool"), str)
        ]
        if (
            not k
            or not calls
            or len(calls) > MAX_STEPS
            or DEICTIC.search(k)
            or FAILED.search(reply or "")
            or any(c["tool"] not in REPLAYABLE for c in calls)
        ):
            return None
        skills = self.skills()
        before = skills.get(k, {})
        new = before.get("calls") != calls
        skills[k] = {
            "calls": calls,
            "reply": reply.strip() or "Done.",
            "at": time.strftime("%Y-%m-%d"),
            **({"mine": True} if mine or before.get("mine") else {}),
        }
        self._save(skills)
        return new

    # ---------- matching ----------

    def app_named(self, words: str) -> str | None:
        """Spoken name -> a Start-menu app's name, or None. Whole-name matches only,
        so "chrome history" is not Chrome."""
        w = re.sub(r"\s+(app|application)$", "", words.lower().strip(" .!?"))
        w = w.removeprefix("the ").removeprefix("my ")
        try:
            names = self._app_names()
        except Exception:
            return None
        if w in ALIASES:
            want = ALIASES[w].lower()
            hit = next((n for n in names if n.lower() == want), None) or next(
                (n for n in names if n.lower().startswith(want)), None
            )
            if hit:
                return hit
        for test in (
            lambda n: _key(n) == _key(w),
            lambda n: _key(n.split()[-1]) == _key(w),
            lambda n: _key(re.sub(r"\s*\(.*\)$", "", n)) == _key(w),
            lambda n: n.lower().startswith(w + " ")
            and not re.search(r"chrome|edge|firefox|brave|opera", n.lower()),
        ):
            hit = next((n for n in names if test(n)), None)
            if hit:
                return hit
        return None

    def _app_in(self, words: list[str]) -> tuple[list[str], str] | None:
        """The one app named in these words -> (the words with it as '{app}', the app)."""
        for n in (3, 2, 1):
            for i in range(len(words) - n + 1):
                app = self.app_named(" ".join(words[i : i + n]))
                if app:
                    return words[:i] + ["{app}"] + words[i + n :], app
        return None

    def _like(self, g: list[str], skills: dict) -> tuple[str | None, tuple[str, str] | None]:
        """A learned task said another way: the same words in any order, or the
        same words about another app."""
        same = next((k for k in skills if sorted(gist(k)) == sorted(g)), None)
        if same:
            return same, None
        new = self._app_in(g)
        if new:
            for k, entry in skills.items():
                words = gist(k)
                old = len(words) == len(g) and self._app_in(words)
                if (
                    old
                    and old[1] != new[1]
                    and sorted(old[0]) == sorted(new[0])
                    and swapped(entry["calls"], old[1], new[1])
                ):
                    return k, (old[1], new[1])
        return None, None

    # ---------- running ----------

    async def _call(self, tool: str, args: dict[str, Any], done: list) -> dict[str, Any]:
        result = await self._run(tool, args)
        done.append({"tool": tool, "args": args})
        return result

    async def _replay(self, k: str, swap: tuple[str, str] | None) -> dict[str, Any] | None:
        skills = self.skills()
        entry = skills.get(k)
        calls = swapped(entry["calls"], *swap) if entry and swap else (entry or {}).get("calls")
        if not calls:
            return None
        done: list = []
        for call in calls:
            try:
                await self._call(call["tool"], call["args"], done)
            except Exception:
                if not done:
                    return None  # nothing happened yet: the model takes over
                return {"say": "I could not finish that shortcut.", "calls": done, "failed": True}
        if entry.get("used") != time.strftime("%Y-%m-%d"):  # used today: the 30-day clock restarts
            skills[k]["used"] = time.strftime("%Y-%m-%d")
            self._save(skills)
        reply = entry["reply"]
        if swap:
            reply = reply.replace(swap[0], swap[1])
        return {"say": reply, "calls": done, "shortcut": k}

    async def handle(self, text: str) -> dict[str, Any] | None:
        """Run a simple ask at once. -> {say, calls, …}, or None to hand it to the model."""
        named = NAMED.match(text.strip())
        if named:
            if not self._learning():
                return None
            name, action = (named[1] or named[2]).strip(), text[named.end() :].strip(" ,:;")
            if not action or NAMED.match(action):
                return None
            done = await self.handle(action)
            if not done or done.get("failed") or not done.get("calls"):
                return None  # the model does it; the turn's tool calls are learned under the name
            if self.learn(text, done["calls"], done["say"]) is None:
                return done
            return {**done, "say": f"{done['say']} Say “{name}” any time.", "learned": name}

        t = plain(text)
        if not t:
            return None
        if t in THANKS:
            return {"say": "Anytime.", "calls": []}
        m = re.fullmatch(r"(?:forget|delete|remove) (?:the )?(?:shortcut )?[\"“']?(.+?)[\"”']?", t)
        if m and self.forget(m[1]):
            return {"say": f"Forgot “{m[1]}”.", "calls": []}
        if re.fullmatch(
            r"(?:what|which) (?:shortcuts|voice shortcuts) do you know|list (?:my )?shortcuts", t
        ):
            names = [s["words"] for s in self.shortcuts()]
            return {
                "say": ("I know: " + "; ".join(names[:8]) + ".")
                if names
                else "No shortcuts yet. Say: when I say focus mode, open Spotify.",
                "calls": [],
            }

        if self._learning():
            skills, g = self.skills(), gist(t)
            if t in skills:
                k, swap = t, None
            elif DEICTIC.search(t) or not g:
                k, swap = None, None
            else:
                k, swap = self._like(g, skills)
            if k:
                replayed = await self._replay(k, swap)
                if replayed:
                    return replayed

        for rule in (
            self._mail,
            self._status,
            self._media,
            self._volume,
            self._search,
            self._shown,
            self._play,
            self._close,
            self._open,
            self._moves,
        ):
            done = await rule(t, text)
            if done:
                return done
        return None

    # ---------- the instant rules ----------

    async def _mail(self, t: str, _text: str):
        """ "Any new email?", "what are my new mails", "naye mail aaye": the
        background mail map, no model. Reading or summarising one goes to it."""
        if len(t.split()) > 9 or MORE.search(t):
            return None
        if not re.search(r"\b(e?-?mails?|gmails?|inbox)\b", t):
            return None
        if re.search(r"\b(reply|draft|send|bhejo|likho|compose|delete|archive)\b", t):
            return None  # writing mail: the Mail helper, with the owner's OK
        done: list = []
        if re.search(r"\b(summar\w*|read|padh\w*|kya likha|bata\w*|what do|what does)\b", t):
            # A summary, always in the background (never a Gmail tab).
            one = re.search(
                r"\b(latest|last|recent|newest|top|pehla|aakhri)\b", t
            ) and not re.search(r"\b(e?mails|gmails|emails)\b", t)
            sender = re.search(r"\bfrom ([a-z][\w .]{1,30}?)$", t)
            args = {
                "which": 1 if one else 0,
                "about": sender[1].strip() if sender else "",
                "hinglish": bool(re.search(HINGLISH, t)),
            }
            result = await self._call("gmail_summary", args, done)
            return {"say": result.get("friday_say") or "Done.", "calls": done}
        if not re.search(r"\b(new|naye?|nayi|any|koi|unread|check|aaye?|aayi)\b", t):
            return None
        result = await self._call("gmail_new", {}, done)
        return {"say": result.get("friday_say") or "Done.", "calls": done}

    async def _status(self, t: str, _text: str):
        """ "Is the laptop charging?", "battery kitni hai", "time kya hua": only that."""
        if re.search(r"\b(charg\w*|battery|batt?ery)\b", t):
            ask = "battery"
        elif re.search(r"\b(time|baje|samay|waqt|clock)\b", t) and not re.search(
            r"\b(timer|set|remind|alarm|meeting|schedule|call|times)\b", t
        ):
            ask = "time"  # "what is the time", "abhi kitne baje hain", "time batao"
        elif re.search(r"\b(date|tareekh|tarikh|tithi|what day|kaunsa din|kya din)\b", t):
            ask = "date"
        else:
            return None
        if MORE.search(t) or len(t.split()) > 9:
            return None  # "check the battery and then…" is more than one ask
        hinglish = bool(re.search(HINGLISH, t))
        done: list = []
        result = await self._call("system_status", {"ask": ask, "hinglish": hinglish}, done)
        return {"say": result.get("friday_say") or "Done.", "calls": done}

    async def _media(self, t: str, _text: str):
        action = {
            "pause": "pause",
            "pause music": "pause",
            "pause the music": "pause",
            "pause the video": "pause",
            "resume": "play",
            "resume music": "play",
            "next song": "next",
            "next track": "next",
            "skip": "next",
            "skip song": "next",
            "agla gaana": "next",
            "previous song": "previous",
            "previous track": "previous",
            "pichla gaana": "previous",
        }.get(t)
        if not action:
            return None
        done: list = []
        result = await self._call("media_control", {"action": action}, done)
        return {"say": result.get("friday_say") or "Done.", "calls": done}

    async def _volume(self, t: str, _text: str):
        args: dict[str, Any] | None = None
        if m := re.fullmatch(r"(?:set )?(?:the )?volume (?:to |at )?(\d{1,3})(?: percent)?", t):
            args = {"level": min(100, int(m[1]))}
        elif re.fullmatch(r"(?:volume up|louder|turn it up|awaaz badhao|volume badhao)", t):
            args = {"change": 10}
        elif re.fullmatch(
            r"(?:volume down|quieter|softer|turn it down|awaaz kam karo|volume kam karo)", t
        ):
            args = {"change": -10}
        elif t in ("mute", "mute it", "mute the sound"):
            args = {"mute": True}
        elif t in ("unmute", "unmute it"):
            args = {"mute": False}
        if args is None:
            return None
        done: list = []
        result = await self._call("set_volume", args, done)
        return {"say": result.get("friday_say") or "Done.", "calls": done}

    async def _search(self, t: str, _text: str):
        m = re.fullmatch(
            r"(?:search(?: up)?|google|look up)(?: (?:on |in )?(google|youtube|yt|the web|online))?"
            r"(?: for)? (.+?)(?: (?:on|in) (google|chrome|edge|youtube|yt|the web))?",
            t,
        )
        if not m:
            hindi = re.fullmatch(
                rf"(?:(youtube|yt|google) (?:pe|par) )?(.+?) (?:{HINDI_SEARCH})", t
            )
            m = hindi and (hindi[1], hindi[2], None)
            if not m:
                return None
        site_word, query, end_site = (m[1], m[2], m[3]) if not isinstance(m, tuple) else m
        # "search amazon for X and find the best one" is two steps; "search this" needs the screen.
        if (
            MORE.search(query)
            or re.search(r"\b(this|that|it|these|my)\b", query)
            or re.search(r"\b(for|on|in|the|a|an|and|to|of|some)$", query)
        ):
            return None
        said = {site_word, end_site} - {None}
        args = {"query": query}
        if said & {"youtube", "yt"}:
            args["site"] = "youtube"
        elif said or t.startswith("google") or "google karo" in t:
            args["site"] = "google"
        # No site named: the tool searches YouTube when the owner is on it, else Google.
        done: list = []
        result = await self._call("web_search", args, done)
        return {"say": result.get("friday_say") or f"Searched {query}.", "calls": done}

    async def _shown(self, t: str, _text: str):
        """ "Play the first video", "second one", "play the video on the screen",
        "pehla video chalao": a video from the YouTube results on screen."""
        place = "|".join(ORDINALS)
        t = re.sub(r"^(?:youtube|yt) (?:pe|par|mein|me|main|on) |^on (?:youtube|yt),? ", "", t)
        m = re.fullmatch(
            rf"(?:(?:play|open|click(?: on)?|start) )?(?:the )?({place}) (?:video|result|one|song|link)"
            r"(?: (?:on|in) (?:the |my )?(?:screen|page|list|results|youtube))?"
            rf"(?: (?:{HINDI_PLAY}|play|karo|kholo))?",
            t,
        )
        if m:
            n = ORDINALS[m[1]]
        elif re.fullmatch(
            r"(?:play|open|click(?: on)?|start) (?:the |this |that )?(?:video|one)"
            r"(?: (?:which|that) (?:is|'s))?(?: (?:visible|shown|showing|open))?"
            r" (?:on|in) (?:the |my )?(?:screen|page)"
            r"|(?:play|open|click(?: on)?) (?:this|that) (?:video|one)",
            t,
        ):
            n = 1
        else:
            return None
        done: list = []
        result = await self._call("play_shown_video", {"n": n}, done)
        return {"say": result.get("friday_say") or "Playing it.", "calls": done}

    async def _play(self, t: str, _text: str):
        m = re.fullmatch(
            r"play (?:me )?(?:the song |the video |a video of )?(.+?)(?: on youtube)?", t
        )
        query = m[1] if m else None
        if not query:
            hindi = re.fullmatch(rf"(.+?) (?:{HINDI_PLAY})", t)
            query = hindi[1] if hindi else None
        if (
            not query
            or DEICTIC.search(query)
            or MORE.search(query)
            or query in ("music", "song", "a song", "some music", "gaana", "gaane", "something")
        ):
            return None  # "play" alone or "play something" needs the model
        query = re.sub(r"^(koi |ek )?(gaana|song) ", "", query)
        # "lofi youtube pe chalao", "youtube pe lofi chalao": YouTube is where, not what.
        query = re.sub(r"^(?:youtube|yt) (?:pe|par|mein|me|main|se) ", "", query)
        query = re.sub(r" (?:youtube|yt) (?:pe|par|mein|me|main|se)$| (?:on|in) (?:youtube|yt)$", "", query)
        if not query.strip():
            return None
        done: list = []
        result = await self._call("play_youtube", {"query": query}, done)
        return {"say": result.get("friday_say") or f"Playing {query}.", "calls": done}

    async def _close(self, t: str, _text: str):
        m = re.fullmatch(r"(?:close|quit|exit|shut down|shut) (.+?)(?: completely| fully)?", t)
        if not m:
            m = re.fullmatch(rf"(.+?) (?:ko )?(?:poora |pura )?(?:{HINDI_CLOSE})", t)
        if not m or MORE.search(m[1]) or DEICTIC.search(m[1]):
            return None
        app = self.app_named(m[1])
        if not app:
            return None
        done: list = []
        result = await self._call("close_app", {"name": app}, done)
        return {"say": result.get("friday_say") or f"{app} is closed.", "calls": done}

    async def _open(self, t: str, text: str):
        m = re.fullmatch(
            rf"(?:{OPEN_WORDS})\s+(.+?)(?:\s+(?:on|to|in|at)\s+(?:the\s+|my\s+)?({PLACES}|laptop|monitor)"
            r"(?:\s+(?:side|half|screen))?)?",
            t,
        )
        if not m:
            hindi = re.fullmatch(rf"(.+?) (?:{HINDI_OPEN})", t)
            if not hindi:
                return None
            target, where = hindi[1], None
        else:
            target, where = m[1], m[2]
        if MORE.search(target) or DEICTIC.search(target):
            return None
        done: list = []
        asked = re.fullmatch(IN_BROWSER, target)
        if asked:
            name = next(part for part in asked.groups() if part)
            url = site_url(name)
            if not url:
                return None
            result = await self._call("open_website", {"url": url, "in_browser": True}, done)
            return {"say": result.get("friday_say") or f"Opened {name}.", "calls": done}
        site = target.replace(" dot ", ".")
        site = next((w.rstrip(".,!") for w in text.split() if plain(w) == target), site)
        if re.fullmatch(r"(?:https?://)?[\w-]+(\.[\w-]+)+(/\S*)?", site) and not FILE_EXT.search(
            site
        ):
            result = await self._call("open_website", {"url": site}, done)
            return {"say": result.get("friday_say") or f"Opened {site}.", "calls": done}
        app = self.app_named(target)
        if not app:
            return None
        result = await self._call("open_app", {"name": app}, done)
        self._remember_app(app)
        if not where:
            return {"say": result.get("friday_say") or f"{app} is open.", "calls": done}
        position, screen = _where(where)
        placed = await self._call(
            "snap_window", {"window": app, "position": position, "screen": screen}, done
        )
        return {"say": placed.get("friday_say") or f"{app} is open.", "calls": done}

    async def _moves(self, t: str, text: str):
        parts = [
            plain(p)
            for p in re.split(r"\s*(?:[,;]|\band then\b|\band\b|\bthen\b|\baur\b)\s*", text)
            if plain(p)
        ]
        if (
            len(parts) > 1
        ):  # "Chrome left, Notepad right": every part a move, or the model does it all
            moves = [
                self._move_target(
                    p if re.match(r"(snap|move|put|throw|send|make)\b", p) else "put " + p
                )
                for p in parts
            ]
            if all(moves):
                done: list = []
                said = []
                for app, position, screen in moves:
                    try:
                        await self._call(
                            "snap_window",
                            {"window": app, "position": position, "screen": screen},
                            done,
                        )
                    except Exception:
                        return (
                            None
                            if not done
                            else {"say": "I moved only some of them.", "calls": done}
                        )
                    said.append(_said_move(app, position, screen))
                self._remember_app(moves[-1][0])
                return {"say": " ".join(said), "calls": done}
        move = self._move_target(t)
        if not move:
            return None
        app, position, screen = move
        done = []
        result = await self._call(
            "snap_window", {"window": app, "position": position, "screen": screen}, done
        )
        self._remember_app(app)
        return {"say": result.get("friday_say") or _said_move(app, position, screen), "calls": done}

    def _remember_app(self, app: str) -> None:
        self._last_app, self._last_app_at = app, time.monotonic()

    def _move_target(self, t: str) -> tuple[str, str, str] | None:
        """ "put chrome on the left of the monitor" -> (app, position, screen), or None.
        Everything but the app, one place and one screen must be filler, so
        "what's on my monitor" stays with the model."""
        for pattern, word in MOVE_WORDS:
            t = re.sub(pattern, word, t)
        where = re.findall(rf"(?<![\w-])({PLACES})(?![\w-])", t)
        screen = re.findall(r"\b(laptop|monitor)\b", t)
        if len(where) > 1 or len(screen) > 1 or not (where or screen):
            return None
        who = re.sub(
            rf"(?<![\w-])(?:snap|move|put|throw|send|make|{PLACES}|laptop|monitor|to|on|onto|the|my|of|"
            r"and|side|half|screen|window|par|pe|ko|karo|kar do)(?![\w-])",
            " ",
            t,
        )
        who = re.sub(r"\s+", " ", who).strip()
        if not who:
            return None  # "right" alone is agreement, not a move
        if who in ("it", "that", "this"):  # "move it left" right after opening something
            if not self._last_app or time.monotonic() - self._last_app_at > 600:
                return None
            app = self._last_app
        else:
            app = self.app_named(who)
            if not app:
                return None
        position, screen_name = _where(where[0] if where else "center")
        return app, position, screen[0] if screen else screen_name


def _where(word: str) -> tuple[str, str]:
    """A spoken place -> (position for snap_window, screen)."""
    if word in ("laptop", "monitor"):
        return "maximize", word
    return {"full": "maximize", "middle": "center"}.get(word, word), "current"


def _said_move(app: str, position: str, screen: str) -> str:
    where = {"maximize": "maximized", "minimize": "minimized", "center": "centered"}.get(
        position, f"on the {position.replace('-', ' ')}"
    )
    return f"{app} is {where}" + (f" on the {screen}." if screen != "current" else ".")


def swapped(calls: list[dict[str, Any]], old: str, new: str) -> list[dict[str, Any]] | None:
    """The calls with app old -> new, or None when old is only part of a longer text."""
    out = []
    for call in calls:
        args = {}
        for name, value in call["args"].items():
            if isinstance(value, str) and value.lower() == old.lower():
                value = new
            elif isinstance(value, str) and old.lower() in value.lower():
                return None
            args[name] = value
        out.append({"tool": call["tool"], "args": args})
    return out if any(new in call["args"].values() for call in out) else None
