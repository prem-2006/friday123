"""Friday services: MCP tools for the on-screen browser, desktop apps and
WhatsApp, a live activity feed for the voice screen, the demo calendar, and
Friday's own phone calls (Exotel, or Twilio) and Sarvam's ask_friday."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import weakref
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import StreamingResponse
from pipecat.runner.utils import parse_telephony_websocket

from friday_services.activity import ActivityFeed
from friday_services.auth import BearerAuth
from friday_services.browser import BrowserError, OnScreenBrowser
from friday_services.browser_agent import BrowserAgent, agent_from_settings
from friday_services.calendar.page import add_calendar_page
from friday_services.calendar.store import CalendarStore
from friday_services.calls.bot import CallHelpers, run_call
from friday_services.calls.exotel import NOT_CONNECTED as EXOTEL_NOT_CONNECTED
from friday_services.calls.exotel import ExotelDialer, custom_parameters, valid_key
from friday_services.calls.exotel import stream_url as exotel_stream_url
from friday_services.calls.inya import InyaDialer
from friday_services.calls.sarvam import SarvamDialer, recent_conversations
from friday_services.calls.store import CallRecord, CallStore
from friday_services.calls.twilio import (
    NOT_CONNECTED,
    TwilioDialer,
    point_number_at,
    stream_twiml,
    valid_signature,
)
from friday_services.config import REPO_ROOT, Settings
from friday_services.desktop import DesktopApps, press_media_key
from friday_services.files import owner_folders
from friday_services.instant import Instant
from friday_services.mail_map import MailMap, composio_reader
from friday_services.mail_summary import summarise_mail
from friday_services.mcp_browser import build_browser_server
from friday_services.mcp_calendar import build_calendar_server
from friday_services.mcp_desktop import build_desktop_server, build_whatsapp_server
from friday_services.mcp_phone import Dialer, build_phone_server, call_someone, place_own_call
from friday_services.mcp_quick import build_quick_server
from friday_services.media import OwnerMedia
from friday_services.organizer import (
    Notes,
    OrganizerError,
    Reminders,
    parse_when,
    speak_with_windows,
    spoken_time,
)
from friday_services.owner_calls import OwnerCaller, owner_phone
from friday_services.owner_chrome import owner_chrome
from friday_services.phone_in import (
    PhoneDesk,
    caller_digits,
    caller_name,
    latest_request,
    same_caller,
)
from friday_services.screen import capture_screen, describe_screen, snap_window
from friday_services.tunnel import NgrokTunnel, QuickTunnel, TunnelGuard
from friday_services.usage import timed, usage
from friday_services.watch import Watches
from friday_services.whatsapp import DesktopWhatsAppUI, WhatsApp, WhatsAppUI
from friday_services.worker import Worker, com_thread

CallRunner = Callable[..., Awaitable[None]]
# What Sarvam's agent is told when the caller is not the owner.
CLIENT_REPLY = (
    "This caller is not {owner}, so treat them as a client. Do not do any task for them "
    "and share nothing about {owner}: no schedule, plans, contacts, location, numbers or "
    "anything personal. Say you are Friday, {owner}'s AI assistant; answer only general "
    "questions politely, and offer to pass on a message. Their words have been saved "
    "for {owner}."
)


def create_app(
    settings: Settings | None = None,
    *,
    dialer: Dialer | None = None,
    call_runner: CallRunner = run_call,
    browser: OnScreenBrowser | None = None,
    browser_agent: BrowserAgent | None = None,
    desktop: DesktopApps | None = None,
    look: Callable[[str], str] | None = None,
    snap: Callable[[str, str, str], dict] | None = None,
    media_key: Callable[[str], None] | None = None,
    file_roots: Callable[[], list] | None = None,
    open_path: Callable[[str], None] | None = None,
    wake: bool = False,
    tunnel: bool = False,
    speak: Callable[[str], None] | None = None,
    whatsapp_ui: WhatsAppUI | None = None,
    phone_desk: PhoneDesk | None = None,
    owner_media: OwnerMedia | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    if len(settings.services_token) < 24:
        raise RuntimeError(
            "Set FRIDAY_SERVICES_TOKEN in .env to a random value of 24+ characters. "
            "OpenDots sends it as the bearer token for Friday's MCP connections."
        )
    store = CallStore(settings.data_dir)
    calendar = CalendarStore(settings.data_dir / "calendar.json")
    feed = ActivityFeed()
    if owner_media is None and browser is None:
        # The owner's own browser and players (not with a pretend browser in tests).
        owner_media = OwnerMedia(settings.data_dir / "browser-profile")
    browser = browser or OnScreenBrowser(
        settings.data_dir / "browser-profile",
        cdp_port=settings.browser_cdp_port,
        chrome_path=settings.get("FRIDAY_CHROME_PATH") or None,
    )
    browser_agent = browser_agent or agent_from_settings(
        settings.browser_agent_dir,
        settings.data_dir,
        settings.hermes_agent_dir,
        settings.get("FRIDAY_BROWSER_AGENT", "auto").lower(),
    )
    browser_worker = Worker("friday-browser")
    desktop_worker = Worker("friday-desktop", initializer=com_thread)
    desktop = desktop or DesktopApps()
    dialer = dialer or (
        ExotelDialer(settings) if settings.call_provider == "exotel" else TwilioDialer(settings)
    )
    owner_caller = OwnerCaller(
        settings, store, dialer, gnani=InyaDialer(settings), sarvam=SarvamDialer(settings)
    )
    ringing_back: set[asyncio.Task] = set()
    desk = phone_desk or PhoneDesk(settings)
    usage.configure(settings.data_dir / "usage.json", settings.timezone)
    reminders = Reminders(settings.data_dir / "reminders.json")
    notes = Notes(settings.data_dir / "notes.txt")
    whatsapp = WhatsApp(whatsapp_ui or DesktopWhatsAppUI(), settings.owner_name)
    # Watches run on their own thread; what they say goes back through the event loop.
    loop_ref: dict[str, asyncio.AbstractEventLoop] = {}

    def announce_later(text: str) -> None:
        if "loop" in loop_ref:
            loop_ref["loop"].call_soon_threadsafe(announce, text)

    watches = Watches(announce_later)
    # The "Hey Friday" listener, once started (for /wake/ready).
    wake_ref: dict[str, Any] = {}
    servers = {
        "/browser": build_browser_server(browser, browser_worker, feed, browser_agent),
        "/desktop": build_desktop_server(desktop, desktop_worker, feed),
        "/whatsapp": build_whatsapp_server(whatsapp, desktop_worker, feed),
        "/quick": build_quick_server(
            browser,
            browser_worker,
            desktop,
            desktop_worker,
            feed,
            look=look
            or (lambda question: describe_screen(capture_screen(), question, settings.values)),
            snap=snap or snap_window,
            media_key=media_key or press_media_key,
            file_roots=file_roots or owner_folders,
            open_path=open_path or os.startfile,
            reminders=reminders,
            notes=notes,
            call_owner=owner_caller.call,
            data_dir=settings.data_dir,
            timezone=settings.timezone,
            owner_media=owner_media,
            watches=watches,
            # Sites, searches and songs open in the owner's signed-in Chrome
            # (not in tests, which have a pretend browser).
            chrome=(lambda: owner_chrome(settings)) if owner_media is not None else None,
            # Gmail in the background: through Composio when Gmail is
            # connected there, else Friday's Chrome (not in tests).
            mail_map=(
                MailMap(
                    browser.background_get,
                    settings.data_dir / "mail-map.json",
                    composio=composio_reader(
                        settings.get("FRIDAY_APP_URL", "http://127.0.0.1:4310"),
                        settings.get("OWNER_TOKEN", ""),
                    ),
                )
                if owner_media is not None
                else None
            ),
            summarise=lambda mails, hinglish: summarise_mail(settings, mails, hinglish),
        ),
        "/calendar": build_calendar_server(calendar, settings.calendar_url),
        "/phone": build_phone_server(
            store, dialer, settings, sarvam=SarvamDialer(settings), reminders=reminders
        ),
    }
    mcp_apps = {
        path: (
            server,
            server.streamable_http_app(
                streamable_http_path="/mcp", stateless_http=True, json_response=True
            ),
        )
        for path, server in servers.items()
    }

    # Instant commands and learned shortcuts (instant.py): Friday's own quick
    # tools, called here with no model in between.
    quick_server = servers["/quick"]

    async def run_quick(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        result = await quick_server.call_tool(tool, args)
        if getattr(result, "is_error", False):
            text = " ".join(getattr(block, "text", "") for block in result.content)
            raise RuntimeError(text or f"{tool} did not work.")
        data = getattr(result, "structured_content", None)
        if isinstance(data, dict) and set(data) == {"result"} and isinstance(data["result"], dict):
            data = data["result"]
        if not isinstance(data, dict):
            try:
                data = json.loads(result.content[0].text)
            except (AttributeError, IndexError, TypeError, ValueError):
                data = {}
        return data if isinstance(data, dict) else {}

    instant = Instant(
        settings.data_dir / "friday-shortcuts.json",
        desktop.names,
        run_quick,
        learning=lambda: settings.get("FRIDAY_LEARN", "true").lower() != "false",
    )

    # Timers, reminders and deadlines: Friday says them when they come due, on
    # the voice screen or the notch, or with Windows' voice if neither is open.
    def announce(text: str) -> None:
        feed.emit("friday", "reminder", text, state="done")
        if feed.watched:
            feed.signal("announce", text)
        else:
            (speak or speak_with_windows)(text)

    # Friday also phones the owner: for a "call me" request, and for deadlines
    # an hour and 15 minutes before and when due (FRIDAY_CALL_DEADLINES=false
    # turns that off).
    def should_call(due) -> bool:
        if due.kind == "call":
            return True
        if due.kind == "reminder":
            # A reminder rings the owner when it comes due (FRIDAY_CALL_REMINDERS=false: only spoken).
            calls_for_reminders = settings.get("FRIDAY_CALL_REMINDERS", "true").lower() != "false"
            return calls_for_reminders and due.minutes_left == 0
        calls_for_deadlines = settings.get("FRIDAY_CALL_DEADLINES", "true").lower() != "false"
        return due.kind == "deadline" and calls_for_deadlines and due.minutes_left <= 60

    def call_opening(due) -> str:
        """The first words when the owner picks up: straight to the point."""
        first = (settings.owner_name or "").split()
        hello = f"Hi {first[0]}" if first else "Hi"
        if due.kind == "reminder":
            what = due.text.removeprefix("Reminder: ").rstrip(". ")
            return f"{hello}, you have a reminder: {what}."
        if due.kind == "deadline":
            return f"{hello}, about your deadline: {due.text}"
        return ""

    async def place_contact_call(due) -> None:
        """A call to someone else that was scheduled for now."""
        who = due.extra.get("name") or due.extra.get("phone")
        goal = due.extra.get("goal") or due.text
        opening = due.extra.get("opening") or ""
        try:
            if settings.own_calls_ready:
                record = await place_own_call(
                    store, dialer, due.extra["phone"], goal, opening_line=opening
                )
                if record.final:
                    raise RuntimeError(record.error or "the call failed")
            else:
                await call_someone(
                    SarvamDialer(settings), settings, due.extra["phone"], goal, opening
                )
            feed.emit("friday", "schedule_call", f"Calling {who} now.", state="done")
        except Exception as error:
            feed.emit("friday", "schedule_call", f"Could not call {who}: {error}", state="failed")

    async def reminder_clock() -> None:
        while True:
            try:
                for due in await asyncio.to_thread(reminders.due_now):
                    if due.kind == "contact_call":
                        await place_contact_call(due)
                        continue
                    announce(f"Calling your phone: {due.text}" if due.kind == "call" else due.text)
                    if should_call(due):
                        placed = await owner_caller.call(
                            due.text, urgent=due.minutes_left == 0, opening=call_opening(due)
                        )
                        if not placed["called"]:
                            feed.emit("friday", "call_owner", placed["why"], state="failed")
            except Exception:
                pass  # a bad reminders file must not stop the clock
            await asyncio.sleep(5)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with AsyncExitStack() as stack:
            loop_ref["loop"] = asyncio.get_running_loop()
            stack.callback(watches.stop_all)
            for server, _asgi in mcp_apps.values():
                await stack.enter_async_context(server.session_manager.run())
            # The first "open an app" should not wait for the Start menu list.
            warming = asyncio.ensure_future(desktop_worker.run(desktop.warm))
            warming.add_done_callback(lambda task: task.cancelled() or task.exception())
            if wake:
                stack.callback(start_wake(settings, feed, asyncio.get_running_loop(), wake_ref))
            clock = asyncio.create_task(reminder_clock(), name="friday-reminders")
            stack.callback(clock.cancel)
            if tunnel and settings.get("NGROK_AUTHTOKEN") and settings.get("NGROK_DOMAIN"):
                # A fixed address: Sarvam's agent reaches /phone/ask through it,
                # and Twilio uses it too instead of a quick tunnel.
                fixed = NgrokTunnel(
                    settings.port, settings.get("NGROK_AUTHTOKEN"), settings.get("NGROK_DOMAIN")
                )
                try:
                    url = await fixed.start()
                    settings.values["FRIDAY_NGROK_URL"] = url
                    stack.push_async_callback(fixed.stop)
                    if not settings.get("PUBLIC_URL"):
                        settings.values["PUBLIC_URL"] = url
                        if settings.call_provider == "twilio" and settings.get(
                            "TWILIO_ACCOUNT_SID"
                        ):
                            print(f"Phone calls: {point_number_at(settings, url)}")
                    if settings.call_provider == "exotel":
                        # Exotel's flow has this address typed in, so it must not change.
                        print(f"Phone: Exotel Voicebot URL: {exotel_stream_url(settings)}")
                except Exception as error:
                    print(f"Phone: the ngrok tunnel did not start: {error}")
            if (
                tunnel
                and not settings.get("PUBLIC_URL")
                and settings.get("FRIDAY_TUNNEL", "cloudflared") != "off"
                # Only Twilio can use a changing address; Exotel needs NGROK_DOMAIN.
                and settings.call_provider == "twilio"
                and settings.get("TWILIO_ACCOUNT_SID")
            ):
                # Twilio reaches the call routes through a free quick tunnel;
                # its address is passed with each call.
                def tunnel_ready(url: str) -> None:
                    settings.values["PUBLIC_URL"] = url
                    try:
                        print(f"Phone calls: {point_number_at(settings, url)}")
                    except Exception as error:
                        print(f"Phone calls: could not point the Twilio number here: {error}")

                quick = QuickTunnel(settings.port, tunnel_ready)
                if await asyncio.to_thread(quick.start):
                    stack.callback(quick.stop)
            # Starting Chrome takes about five seconds; do it before the first request.
            if settings.browser_prewarm:
                opening = asyncio.ensure_future(browser_worker.run(browser.warm))
                opening.add_done_callback(lambda task: task.cancelled() or task.exception())
            yield
        for task in list(live["tasks"]):
            task.cancel()
        if live["handler"]:
            await live["handler"].close()
        try:
            await asyncio.wait_for(browser_worker.run(browser.close), 10)
        except Exception:
            pass
        browser_worker.close()
        desktop_worker.close()

    app = FastAPI(title="Friday services", lifespan=lifespan)
    # Through the public tunnel only the Twilio call routes are reachable.
    app.add_middleware(TunnelGuard)
    app.state.settings = settings
    app.state.calls = store
    app.state.calendar = calendar
    app.state.activity = feed

    def owner(request: Request) -> None:
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied, f"Bearer {settings.services_token}"):
            raise HTTPException(status_code=401, detail="Missing or wrong bearer token.")

    @app.get("/activity", dependencies=[Depends(owner)])
    async def activity() -> dict[str, Any]:
        return {"stopped": feed.stopped, "events": feed.recent()}

    @app.get("/activity/stream", dependencies=[Depends(owner)])
    async def activity_stream(request: Request, voice: str = "") -> StreamingResponse:
        # voice=1: a voice window (gets Caps Lock); voice=notch: the desktop notch.
        queue = feed.listen(voice=voice in ("1", "true"), notch=voice == "notch")

        async def events():
            try:
                yield ": connected\n\n"
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(queue.get(), 15)
                        yield f"data: {json.dumps(event.public())}\n\n"
                    except TimeoutError:
                        yield ": keep-alive\n\n"
            finally:
                feed.unlisten(queue)

        return StreamingResponse(events(), media_type="text/event-stream")

    # The main page: Friday's Google account and one-click shortcuts.
    async def in_browser(fn, *args):
        try:
            return await browser_worker.run(fn, *args)
        except BrowserError as error:
            raise HTTPException(409, str(error)) from error
        except Exception as error:
            raise HTTPException(503, f"Friday's browser is not available: {error}") from error

    @app.get("/account/google", dependencies=[Depends(owner)])
    async def google_status() -> dict:
        return await in_browser(browser.google_account)

    @app.post("/account/google/connect", dependencies=[Depends(owner)])
    async def google_connect(request: Request) -> dict:
        body = await request.json() if await request.body() else {}
        email = str((body or {}).get("email") or "").strip() or None
        if email and ("@" not in email or len(email) > 120):
            raise HTTPException(400, "That does not look like an email address.")
        feed.emit(
            "friday", "google_sign_in", "Showing Google sign-in. Type your password yourself."
        )
        return await in_browser(browser.connect_google, email)

    # The Google accounts signed in to the owner's own Chrome, and the one
    # Friday uses (main page: Google account).
    @app.get("/account/chrome", dependencies=[Depends(owner)])
    async def chrome_account() -> dict:
        from friday_services.owner_chrome import chrome_accounts

        chosen = await asyncio.to_thread(owner_chrome, settings)
        return {
            "accounts": await asyncio.to_thread(chrome_accounts),
            "using": {"folder": chosen.folder, "email": chosen.email} if chosen else None,
        }

    # A page in the owner's signed-in Chrome (Gmail, an app's sign-in link).
    @app.post("/browser/open-mine", dependencies=[Depends(owner)])
    async def browser_open_mine(request: Request) -> dict:
        body = await request.json()
        url = str((body or {}).get("url") or "")
        if not url.startswith(("https://", "http://")):
            raise HTTPException(400, "Send a web address starting with https://.")
        chosen = await asyncio.to_thread(owner_chrome, settings)
        if chosen:
            await asyncio.to_thread(chosen.open, url)
            return {"opened": url, "in": f"Chrome ({chosen.email})"}
        await asyncio.to_thread(os.startfile, url)
        return {"opened": url, "in": "your default browser"}

    @app.post("/browser/open", dependencies=[Depends(owner)])
    async def browser_open(request: Request) -> dict:
        body = await request.json()
        url = str((body or {}).get("url") or "")
        if not url.startswith(("https://", "http://")):
            raise HTTPException(400, "Send a web address starting with https://.")
        feed.emit("friday", "browser_open", f"Opening {url}.")
        page = await in_browser(browser.open, url)
        return {"url": page["url"], "title": page["title"]}

    live: dict[str, Any] = {"handler": None, "tasks": set()}

    @app.post("/voice/offer", dependencies=[Depends(owner)])
    async def voice_offer(request: Request) -> dict:
        """WebRTC offer from the voice screen: starts a live voice pipeline."""
        from pipecat.transports.smallwebrtc.request_handler import (
            SmallWebRTCRequest,
            SmallWebRTCRequestHandler,
        )

        from friday_services.live_voice import LiveVoiceSession
        from friday_services.voices import DEFAULT_LANGUAGE, voice_for

        api_key = settings.get("GNANI_API_KEY")
        if not api_key:
            raise HTTPException(503, "Set GNANI_API_KEY in .env for Friday's live voice.")
        body = await request.json()
        if not isinstance(body, dict) or "sdp" not in body or "type" not in body:
            raise HTTPException(400, "Send a WebRTC offer (sdp and type).")
        wanted = (body.get("request_data") or {}).get("language") or DEFAULT_LANGUAGE
        try:
            voice = voice_for(wanted)
        except ValueError:
            voice = voice_for(DEFAULT_LANGUAGE)
        live["handler"] = live["handler"] or SmallWebRTCRequestHandler()

        async def started(connection) -> None:
            session = LiveVoiceSession(connection, api_key, voice.code, voice.voice)
            # The window's mic also feeds "Hey Friday" and the clap detector.
            ears = wake_ref.get("ears")
            if ears is not None:
                session.gate.tap = lambda pcm, rate: ears.from_live(session, pcm, rate)
            wake_ref.setdefault("gates", weakref.WeakSet()).add(session.gate)
            task = asyncio.create_task(session.run(), name="friday-live-voice")
            live["tasks"].add(task)
            task.add_done_callback(live["tasks"].discard)

        return await live["handler"].handle_web_request(
            SmallWebRTCRequest(sdp=body["sdp"], type=body["type"], pc_id=body.get("pc_id")),
            started,
        )

    # The main page's reminders panel.
    @app.get("/calls/recent", dependencies=[Depends(owner)])
    async def calls_recent() -> list[dict]:
        """Friday's last Sarvam calls and what was said, for the main page."""
        if not SarvamDialer(settings).ready:
            return []
        names: dict[str, str] = {}
        try:
            contacts = json.loads((REPO_ROOT / "data" / "friday-contacts.json").read_text("utf-8"))
            names = {
                re.sub(r"\D", "", str(c.get("phone", ""))): str(c.get("name")) for c in contacts
            }
        except (OSError, ValueError, AttributeError):
            pass
        mine = re.sub(r"\D", "", owner_phone(settings) or "")
        if mine:
            names[mine] = "You"
        try:
            return await recent_conversations(settings, names)
        except Exception as error:
            raise HTTPException(502, f"Could not read calls from Sarvam: {error}") from error

    @app.get("/reminders", dependencies=[Depends(owner)])
    async def reminders_list() -> list[dict]:
        return [
            {"id": r.id, "kind": r.kind, "text": r.text, "due": r.due}
            for r in await asyncio.to_thread(reminders.upcoming)
        ]

    @app.post("/reminders", dependencies=[Depends(owner)])
    async def reminders_add(request: Request) -> dict:
        body = await request.json()
        kind = str((body or {}).get("kind") or "reminder")
        text = str((body or {}).get("text") or "").strip()
        if kind not in ("reminder", "deadline", "timer") or not text or len(text) > 200:
            raise HTTPException(400, "Give a reminder or deadline with a short title.")
        try:
            item = await asyncio.to_thread(
                reminders.add, kind, text, parse_when(str(body.get("when") or ""))
            )
        except OrganizerError as error:
            raise HTTPException(400, str(error)) from error
        return {"id": item.id, "kind": item.kind, "text": item.text, "due": item.due}

    @app.post("/reminders/{reminder_id}/done", dependencies=[Depends(owner)])
    async def reminders_done(reminder_id: str) -> dict:
        return {"done": bool(await asyncio.to_thread(reminders.finish_id, reminder_id))}

    @app.post("/activity/stop", dependencies=[Depends(owner)])
    async def activity_stop() -> dict[str, bool]:
        feed.stop()
        return {"stopped": True}

    @app.post("/activity/resume", dependencies=[Depends(owner)])
    async def activity_resume() -> dict[str, bool]:
        feed.resume()
        return {"stopped": False}

    # Instant commands: the app server asks here first; "handled": false sends
    # the ask to the Friday Dot as usual.
    @app.post("/instant", dependencies=[Depends(owner)])
    async def instant_run(request: Request) -> dict[str, Any]:
        body = await request.json()
        text = str(body.get("text") or "")[:500]
        if not text.strip() or feed.stopped:
            return {"handled": False}
        last = (feed.recent(1) or [{"id": 0}])[-1]["id"]
        try:
            done = await instant.handle(text)
        except Exception:
            done = None  # a quick tool refused: the model sorts it out
        if not done:
            return {"handled": False}
        # The task toast shows every request: one answered by a tool that
        # showed no step of its own (battery, time, volume) shows what was said.
        shown = any(e["id"] > last and e["actor"] != "control" for e in feed.recent(20))
        if done.get("say") and not shown:
            feed.emit(
                "friday", "instant", done["say"], state="failed" if done.get("failed") else "done"
            )
        return {"handled": True, **done}

    @app.post("/instant/learn", dependencies=[Depends(owner)])
    async def instant_learn(request: Request) -> dict[str, Any]:
        body = await request.json()
        calls = body.get("calls") if isinstance(body.get("calls"), list) else []
        learned = await asyncio.to_thread(
            instant.learn, str(body.get("text") or "")[:500], calls, str(body.get("reply") or "")
        )
        return {"learned": learned is not None, "new": bool(learned)}

    @app.get("/instant/shortcuts", dependencies=[Depends(owner)])
    async def instant_shortcuts() -> dict[str, Any]:
        return {"shortcuts": instant.shortcuts()}

    @app.post("/instant/forget", dependencies=[Depends(owner)])
    async def instant_forget(request: Request) -> dict[str, bool]:
        body = await request.json()
        return {"forgot": await asyncio.to_thread(instant.forget, str(body.get("words") or ""))}

    # The voice screen has spoken its wake greeting: listen now.
    @app.post("/wake/ready", dependencies=[Depends(owner)])
    async def wake_ready() -> dict[str, bool]:
        listener = wake_ref.get("listener")
        if listener:
            listener.trigger()
        return {"listening": bool(listener)}

    # "Stop" / "Hey Friday" on the voice screen: while stopped, a double clap
    # does not wake Friday (a clap-like noise would undo the stop).
    @app.post("/wake/stopped", dependencies=[Depends(owner)])
    async def wake_stopped(request: Request) -> dict[str, bool]:
        body = await request.json()
        wake_ref["stopped"] = bool(isinstance(body, dict) and body.get("stopped"))
        return {"stopped": wake_ref["stopped"]}

    # What the wake word and the clap detector heard lately: for finding out
    # why a "Hey Friday" or a double clap did not work.
    @app.get("/wake/status", dependencies=[Depends(owner)])
    async def wake_status() -> dict[str, Any]:
        listener, claps = wake_ref.get("listener"), wake_ref.get("claps")
        return {
            "wake_word": settings.wake_word,
            "model_loaded": bool(listener and listener._recognizer is not None),
            "hey_friday": list(listener.history) if listener else [],
            "clap_level": settings.clap_level,
            "loudest_recently": round(claps.loudest, 3) if claps else None,
            "claps": list(claps.history) if claps else [],
        }

    @app.get("/watches", dependencies=[Depends(owner)])
    async def watch_list() -> dict[str, Any]:
        return {"watches": watches.list()}

    # Before the MCP mounts, so /calendar stays the web page and only
    # /calendar/mcp goes to the tools.
    add_calendar_page(app, calendar)

    async def twilio_params(request: Request) -> dict[str, str]:
        """Read a Twilio webhook and reject it unless Twilio signed it."""
        params = {key: str(value) for key, value in (await request.form()).items()}
        url = settings.public_url + request.url.path
        if request.url.query:
            url += f"?{request.url.query}"
        if not valid_signature(
            settings.get("TWILIO_AUTH_TOKEN"),
            url,
            params,
            request.headers.get("x-twilio-signature"),
        ):
            raise HTTPException(status_code=403, detail="Invalid Twilio signature.")
        return params

    @app.get("/health")
    async def health() -> dict[str, Any]:
        calls_missing = settings.missing(settings.call_settings)
        return {
            "ok": True,
            "calls": {
                "ready": not calls_missing,
                "missing": calls_missing,
                "provider": settings.call_provider,
            },
            "owner_calls": {
                "via": owner_caller.via,
                "missing": owner_caller.missing(),
            },
            "calendar": {"ready": True, "missing": [], "browser_url": settings.calendar_url},
        }

    @app.get("/usage", dependencies=[Depends(owner)])
    async def usage_now() -> dict[str, Any]:
        """Today's counts for every outside API, and what is connected now,
        for the Admin page."""
        return {
            **usage.snapshot(),
            "live": {
                **feed.windows(),
                "ngrok_url": settings.get("FRIDAY_NGROK_URL") or None,
                "ngrok_configured": bool(
                    settings.get("NGROK_AUTHTOKEN") and settings.get("NGROK_DOMAIN")
                ),
                "phone_token": bool(settings.get("FRIDAY_PHONE_TOKEN")),
                "sarvam_ready": SarvamDialer(settings).ready,
                "inya_ready": bool(settings.get("INYA_API_KEY")),
                "twilio_on": bool(settings.get("TWILIO_ACCOUNT_SID")),
                "owner_calls_via": owner_caller.via,
                "owner_phone": bool(owner_phone(settings)),
            },
        }

    def load_contacts() -> list[dict[str, Any]]:
        try:
            contacts = json.loads((REPO_ROOT / "data" / "friday-contacts.json").read_text("utf-8"))
        except (OSError, ValueError):
            return []
        return contacts if isinstance(contacts, list) else []

    def owner_updates() -> str:
        """What is coming up and what Friday did today, for the owner's calls."""
        upcoming = [
            f"- {item.text} ({item.kind}), {spoken_time(datetime.fromtimestamp(item.due))}"
            for item in reminders.upcoming()[:6]
        ]
        today = [
            f"- {event['detail']}"
            for event in feed.recent(60)
            if event["state"] == "done" and event["actor"] != "control"
        ][-8:]
        context = "Coming up:\n" + ("\n".join(upcoming) or "- nothing")
        if today:
            context += "\nDone today:\n" + "\n".join(today)
        return context

    # What Friday's own calls reach: the Friday Dot for the owner, notes for others.
    async def ask_on_call(record: CallRecord, request: str) -> str:
        feed.emit("friday", "call_in", f"On the phone: {request}", state="running")
        why = record.goal if record.kind == "owner" else ""
        with timed("Phone calls", settings.call_provider.capitalize(), "Friday's calls · tasks"):
            reply = await desk.ask(record.id, request, owner=True, context=why)
        feed.emit("friday", "call_in", f"Told you on the phone: {reply}", state="done")
        return reply

    async def take_message(record: CallRecord, name: str, message: str) -> None:
        who = name.strip() or record.context or record.phone
        await asyncio.to_thread(notes.add, f"Phone message from {who}: {message}")
        feed.emit("friday", "call_in", f"{who} on the phone: {message}", state="done")

    call_helpers = CallHelpers(ask=ask_on_call, take_message=take_message)

    def answer_incoming(caller: str) -> CallRecord | None:
        """A call to Friday's number: the owner gets Friday, anyone else
        Reception. None when Friday is already on a call."""
        if store.active():
            return None
        language = settings.get("FRIDAY_CALL_LANGUAGE", "en-IN")
        if same_caller(caller, caller_digits(owner_phone(settings) or "")):
            feed.emit("friday", "call_in", "You phoned Friday.", state="done")
            return store.create(
                phone=caller,
                goal="The owner called Friday.",
                language=language,
                context=owner_updates(),
                kind="owner_in",
            )
        who = caller_name(caller, load_contacts())
        feed.emit("friday", "call_in", f"{who} phoned Friday.", state="done")
        return store.create(
            phone=caller,
            goal=f"Take a message for {settings.owner_name}.",
            language=language,
            context=who,
            kind="reception",
        )

    @app.post("/phone/ask")
    async def phone_ask(request: Request) -> dict[str, str]:
        """Sarvam's agent asks Friday to do what the owner said on a call to
        Friday's number (its API tool ask_friday)."""
        token = settings.get("FRIDAY_PHONE_TOKEN")
        if not token or not phone_token_ok(request, token):
            # What came, never the secret: tells a missing header from a wrong token.
            auth = request.headers.get("authorization", "")
            print(
                "Phone: ask_friday refused. Authorization header "
                + (f"'{auth.split()[0]} …' ({len(auth)} chars)" if auth else "missing")
                + "; set the tool's bearer token to FRIDAY_PHONE_TOKEN from .env."
            )
            raise HTTPException(status_code=401, detail="Missing or wrong bearer token.")
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        mine = caller_digits(owner_phone(settings) or "")
        caller = str(body.get("caller") or "")
        said = str(body.get("request") or "").strip() or latest_request(body.get("transcript"))
        call_id = str(body.get("call_id") or "")
        # Why Friday placed this call, when it did (Sarvam's reason variable).
        context = str(body.get("reason") or "").strip()
        if context in ("none", "@reason") or "{{" in context:
            context = ""
        if not same_caller(caller, mine):
            # Anyone else gets Reception: no tools and nothing private about
            # the owner. Every word they say is saved for the owner.
            who = caller_name(caller, load_contacts())
            if said:
                await asyncio.to_thread(notes.add, f"Phone message from {who}: {said}")
                feed.emit("friday", "call_in", f"{who} on the phone: {said}", state="done")
            try:
                with timed("Phone calls", "Sarvam", "calls to Friday · others (Reception)"):
                    reply = await desk.ask(call_id, said, owner=False, context=context)
            except Exception:
                reply = ""
            if not reply or reply.startswith("Sorry, that did not work"):
                return {"reply": CLIENT_REPLY.format(owner=settings.owner_name)}
            return {"reply": reply}
        feed.emit(
            "friday", "call_in", f"On the phone: {said or '(nothing heard)'}", state="running"
        )
        with timed("Phone calls", "Sarvam", "calls to Friday · you (Friday)"):
            reply = await desk.ask(call_id, said, owner=True, context=context)
        feed.emit("friday", "call_in", f"Told you on the phone: {reply}", state="done")
        return {"reply": reply}

    @app.post("/twilio/voice/{call_id}")
    async def twilio_voice(call_id: str, request: Request) -> Response:
        await twilio_params(request)
        record = store.get(call_id)
        if not record:
            raise HTTPException(status_code=404, detail="Unknown call.")
        return Response(stream_twiml(settings, record), media_type="application/xml")

    @app.post("/twilio/incoming")
    async def twilio_incoming(request: Request) -> Response:
        """The owner phoned Friday's number: Friday answers with their updates
        (with Gnani set up: lets it ring out and calls back)."""
        params = await twilio_params(request)
        caller = re.sub(r"\D", "", params.get("From", ""))
        mine = re.sub(r"\D", "", owner_phone(settings) or "")
        say = '<?xml version="1.0" encoding="UTF-8"?><Response><Say>{}</Say><Hangup/></Response>'
        if not mine or caller != mine:
            # Only the owner may talk to Friday by phone.
            return Response(
                say.format("Sorry, Friday only takes calls from its owner."),
                media_type="application/xml",
            )
        if store.active():
            return Response(
                say.format("Friday is on another call. Please try again in a minute."),
                media_type="application/xml",
            )
        context = owner_updates()
        if owner_caller.agent:
            # A missed call is enough: Friday does not answer (so the call costs
            # nothing and no trial notice plays) and Gnani rings back with the
            # updates.
            async def ring_back() -> None:
                await asyncio.sleep(3)
                placed = await owner_caller.call(
                    "You gave me a missed call, so here are your updates.",
                    urgent=True,
                    updates=context,
                )
                if not placed["called"]:
                    feed.emit("friday", "call_owner", placed["why"], state="failed")

            task = asyncio.create_task(ring_back(), name="friday-ring-back")
            ringing_back.add(task)
            task.add_done_callback(ringing_back.discard)
            feed.emit("friday", "call_in", "Missed call from you: calling you back.", state="done")
            return Response(
                '<?xml version="1.0" encoding="UTF-8"?><Response><Reject reason="busy"/></Response>',
                media_type="application/xml",
            )
        record = store.create(
            phone=params.get("From", ""),
            goal="The owner called for an update.",
            language=settings.get("FRIDAY_CALL_LANGUAGE", "en-IN"),
            context=context,
            kind="owner_in",
        )
        record.call_sid = params.get("CallSid")
        feed.emit("friday", "call_in", "You phoned Friday.", state="done")
        return Response(stream_twiml(settings, record), media_type="application/xml")

    @app.post("/twilio/status/{call_id}")
    async def twilio_status(call_id: str, request: Request) -> Response:
        params = await twilio_params(request)
        record = store.get(call_id)
        if not record:
            raise HTTPException(status_code=404, detail="Unknown call.")
        status = params.get("CallStatus", "")
        if status == "ringing":
            store.set_stage(call_id, "ringing")
        elif status in NOT_CONNECTED:
            store.finish(
                call_id,
                NOT_CONNECTED[status],
                summary="Nobody answered." if NOT_CONNECTED[status] == "no_answer" else None,
                error=None
                if NOT_CONNECTED[status] == "no_answer"
                else f"Twilio reported {status}.",
            )
        elif status == "completed" and not record.stream_started:
            store.finish(
                call_id,
                "failed",
                error="The call connected but its audio never reached Friday. Check PUBLIC_URL and the tunnel.",
            )
        return Response(status_code=204)

    @app.websocket("/twilio/stream")
    async def twilio_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        provider, call_data = await parse_telephony_websocket(websocket)
        custom = call_data.get("body") or {}
        record = store.get(str(custom.get("call_id", "")))
        if (
            provider != "twilio"
            or not record
            or record.stream_started
            or not hmac.compare_digest(str(custom.get("token", "")), record.stream_token)
        ):
            await websocket.close(code=1008)
            return
        await call_runner(
            websocket,
            stream_sid=call_data["stream_id"],
            call_sid=call_data["call_id"],
            record=record,
            store=store,
            settings=settings,
            provider="twilio",
            helpers=call_helpers,
        )

    @app.post("/exotel/status/{call_id}")
    async def exotel_status(call_id: str, request: Request, key: str = "") -> Response:
        """Exotel's StatusCallback for a call Friday placed."""
        if not valid_key(settings, key):
            raise HTTPException(status_code=403, detail="Wrong Exotel key.")
        record = store.get(call_id)
        if not record:
            raise HTTPException(status_code=404, detail="Unknown call.")
        if "json" in request.headers.get("content-type", ""):
            params = await request.json()
            params = params if isinstance(params, dict) else {}
        else:
            params = {name: str(value) for name, value in (await request.form()).items()}
        status = str(params.get("Status") or params.get("CallStatus") or "").lower()
        if status in EXOTEL_NOT_CONNECTED:
            outcome = EXOTEL_NOT_CONNECTED[status]
            store.finish(
                call_id,
                outcome,
                summary="Nobody answered." if outcome == "no_answer" else None,
                error=None if outcome == "no_answer" else f"Exotel reported {status}.",
            )
        elif status == "completed" and not record.stream_started:
            store.finish(
                call_id,
                "failed",
                error=(
                    "The call connected but its audio never reached Friday. Check the "
                    "Voicebot applet's URL in the Exotel call flow, and the ngrok tunnel."
                ),
            )
        return Response(status_code=204)

    @app.websocket("/exotel/stream")
    async def exotel_stream(websocket: WebSocket) -> None:
        """Exotel's Voicebot applet: a call Friday placed, or one to its number."""
        await websocket.accept()
        provider, call_data = await parse_telephony_websocket(websocket)
        custom = custom_parameters(call_data.get("custom_parameters"))
        if provider != "exotel" or not valid_key(settings, custom.get("key")):
            await websocket.close(code=1008)
            return
        call_sid = str(call_data.get("call_id") or "")
        record = store.waiting_for_audio(call_sid) or answer_incoming(
            str(call_data.get("from") or "")
        )
        if not record or record.stream_started:
            await websocket.close(code=1008)
            return
        record.call_sid = record.call_sid or call_sid
        await call_runner(
            websocket,
            stream_sid=call_data["stream_id"],
            call_sid=call_sid,
            record=record,
            store=store,
            settings=settings,
            provider="exotel",
            helpers=call_helpers,
        )

    for path, (_server, asgi) in mcp_apps.items():
        app.mount(path, BearerAuth(asgi, settings.services_token))
    return app


def phone_token_ok(request: Request, token: str) -> bool:
    """The phone agent's tool proves it is Friday's: the token as a bearer
    token, a bare Authorization value, an x-api-key header or ?token=."""
    auth = request.headers.get("authorization", "").strip()
    offered = [
        auth.split(None, 1)[1].strip() if auth.lower().startswith(("bearer ", "token ")) else auth,
        request.headers.get("x-api-key", "").strip(),
        request.query_params.get("token", "").strip(),
    ]
    return any(value and hmac.compare_digest(value, token) for value in offered)


def start_wake(
    settings: Settings, feed: ActivityFeed, loop, wake_ref: dict | None = None
) -> Callable[[], None]:
    """Caps Lock push-to-talk, double-clap wake and "Hey Friday"; returns how to stop them."""
    import logging
    import threading
    import webbrowser

    from friday_services.greeting import screen_greeting
    from friday_services.wake import (
        ClapCounter,
        Ears,
        Loudness,
        Microphone,
        PushToTalkKey,
        WakeWord,
        ensure_wake_model,
        pcm16,
    )

    log = logging.getLogger("friday.wake")
    stops: list[Callable[[], None]] = []
    mic_users: list[Callable] = []

    def signal(action: str) -> None:
        loop.call_soon_threadsafe(feed.signal, action)

    def clapped() -> None:
        # The same as "Hey Friday": a greeting, then listening. Not after the
        # owner said "stop": then only "Hey Friday" brings Friday back.
        if wake_ref is not None and wake_ref.get("stopped"):
            print("Wake: double clap ignored (Friday was told to stop)")
            return
        listener.wake()

    def greet() -> bool:
        """On waking: no voice screen yet, so bring it up and listen at once;
        else Friday says hello about what is on screen, and the screen asks
        for listening (/wake/ready) once the greeting is spoken."""
        if not feed.watched:
            webbrowser.open(settings.stage_url)
            return False
        try:
            from friday_services import winctl

            app, title, _ = winctl.front()
        except Exception:
            app, title = "", ""
        text = screen_greeting(app, title, settings.owner_name)
        loop.call_soon_threadsafe(feed.signal, "wake", text)
        return True

    key: PushToTalkKey | None = None
    if settings.push_to_talk:
        try:
            key = PushToTalkKey(signal)
            key.start()
            stops.append(key.stop)
            print("Push-to-talk: hold Caps Lock and speak.")
        except Exception:
            log.exception("Caps Lock push-to-talk is unavailable")

    def wake_signal(action: str) -> None:
        if action == "ptt_down_preroll":
            # "Hey Friday, play music": the words before listening began count too.
            for gate in list(wake_ref.get("gates", ()) if wake_ref is not None else ()):
                gate.preroll_next()
            action = "ptt_down"
        if action == "ptt_down" and not feed.watched:
            webbrowser.open(settings.stage_url)  # no voice screen yet: bring it up
        signal(action)

    # One listener for "Hey Friday" and the double clap: either one starts a
    # request that ends when the owner stops talking, like a Caps Lock hold.
    listener = WakeWord(wake_signal, None, busy=lambda: bool(key and key.talking))
    if settings.wake_greeting:
        listener.on_wake = greet
    if wake_ref is not None:
        wake_ref["listener"] = listener
    if settings.clap_wake or settings.wake_word:
        listener.start()
        stops.append(listener.stop)
    if settings.clap_wake:
        counter = ClapCounter(min_peak=settings.clap_level)
        if wake_ref is not None:
            wake_ref["claps"] = counter

        def clap_block(_samples, peak: float, rms: float, now: float) -> None:
            if counter.feed(peak, rms, now):
                print("Wake: double clap")
                clapped()

        mic_users.append(clap_block)
    if settings.wake_word:

        def load_wake_word() -> None:
            # The first start downloads the small model; the rest of Friday does not wait.
            try:
                model = ensure_wake_model(settings.wake_model_dir)
                listener.use(WakeWord.vosk_recognizer(model), WakeWord.vosk_confirmer(model))
                print('Wake word: say "Hey Friday" and then what you need.')
            except Exception:
                log.exception('"Hey Friday" is unavailable (vosk not installed, or no model)')

        threading.Thread(target=load_wake_word, name="friday-wake-load", daemon=True).start()
    if settings.clap_wake or settings.wake_word:
        heard = {"peak": 0.0, "since": None, "warned": False}

        loudness = Loudness()

        def wake_block(samples, peak: float, rms: float, now: float) -> None:
            # A soft "Hey Friday" is turned up before the recognizer hears it.
            listener.offer(pcm16(samples, loudness.gain(rms)), rms, now)
            # A mic that gives only digital silence (blocked by Windows, or by a
            # sandbox the services were started from) hears neither.
            heard["since"] = heard["since"] or now
            heard["peak"] = max(heard["peak"], peak)
            if (
                not heard["warned"]
                and now - heard["since"] > 10
                and heard["peak"] < 1e-4
                and not ears.live
            ):
                heard["warned"] = True
                print(
                    'Microphone: only silence for 10 s, so claps and "Hey Friday" cannot be '
                    "heard. Check Windows Settings > Privacy > Microphone, and start the "
                    "services from your own terminal or the Friday shortcut."
                )

        mic_users.append(wake_block)
    if mic_users:
        # The voice window's mic when it is sending, else the services' own.
        ears = Ears(mic_users)
        if wake_ref is not None:
            wake_ref["ears"] = ears
        try:
            mic = Microphone([ears.from_mic])
            mic.start()
            stops.append(mic.stop)
            if settings.clap_wake:
                print("Clap wake: clap twice to call Friday.")
        except Exception:
            log.exception("Clap wake and the wake word are unavailable (no microphone?)")

    def stop() -> None:
        for halt in stops:
            try:
                halt()
            except Exception:
                pass

    return stop


def main() -> None:
    import uvicorn

    load_dotenv(REPO_ROOT / ".env")
    settings = Settings.from_env()
    uvicorn.run(
        create_app(settings, wake=True, tunnel=True),
        host=settings.host,
        port=settings.port,
        # The voice screen's live streams never end by themselves; do not wait on them.
        timeout_graceful_shutdown=3,
    )


if __name__ == "__main__":
    main()
