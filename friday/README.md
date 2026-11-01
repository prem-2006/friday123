# Friday

Friday is a voice-first personal assistant for India. You talk to it in Hindi, English, Hinglish or another Indian language, and it gets things done on your own PC. It searches and works on websites in a real browser window you can watch, opens desktop apps, and sends WhatsApp messages or starts WhatsApp calls once you say yes. It talks you through what it's doing as it goes.

Built for the Gnani AI #GreatIndianAIInternshipChallenge on top of [OpenDots](https://github.com/CopilotKit/OpenDots) by CopilotKit (MIT).

## The Dots

OpenDots calls each agent a **Dot**. Friday uses three:

| Dot           | What it does                                                                                                                                                                     | Its tools (from `friday/services`)                                                                                                                                               |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Friday**    | The one you talk to. Does one-step jobs itself at once, hands bigger jobs to the helpers.                                                                                        | Quick actions: `open_app`, `open_website`, `play_youtube`, `web_search`, `look_at_screen`, `snap_window`; `delegate_to_dot`; WhatsApp: find a chat, send a message, start a call |
| **Navigator** | Drives a real Chrome window on your screen. Multi-step jobs go to its browser agent ([browser-use](https://github.com/browser-use/browser-use)), which plans and acts by itself. | `browser_task`, `browser_search`, `browser_open`, `browser_click`, `browser_type`, …                                                                                             |
| **Operator**  | Opens and works Windows apps like Notepad or Calculator through Windows UI Automation.                                                                                           | `list_apps`, `open_app`, `window_snapshot`, `window_click`, `window_type`, `press_keys`                                                                                          |

When you ask for something, Friday says what it is handing over and calls `delegate_to_dot`. The helper then works in its own "Tasks from Friday" conversation. Each action it takes streams to the voice screen with a screenshot, Friday narrates the important ones out loud, and when the helper finishes, Friday sums up.

`friday/scripts/setup-friday-dot.mjs` creates all three Dots, gives each its persona (`friday/persona.md`, `friday/dots/*.md`) and connects its tools. Docker and OpenDots' "Dot computers" are not needed, because everything runs on your PC.

## Where the Gnani models are used

| Model           | Job in Friday                                         | Code                                                                                                                                 |
| --------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| **Evon v3.3**   | The brain of all three Dots                           | App model via `EVON_*` ([config.ts](../src/friday/server/config.ts))                                                                 |
| **Prisma v2.5** | Speech-to-text for everything you say                 | [gnani.ts](../src/friday/server/gnani.ts), fed by the hands-free listener ([handsFree.ts](../src/friday/client/stage/handsFree.ts))  |
| **Timbre v2.5** | Every word Friday says: replies, narration, approvals | [gnani.ts](../src/friday/server/gnani.ts), played by the voice stage ([FridayStage.tsx](../src/friday/client/stage/FridayStage.tsx)) |

## What you need

| Item                                                                        | Goes in `.env`                                |
| --------------------------------------------------------------------------- | --------------------------------------------- |
| Gnani API key                                                               | `GNANI_API_KEY`                               |
| Evon endpoint, key, model id (challenge dashboard)                          | `EVON_BASE_URL`, `EVON_API_KEY`, `EVON_MODEL` |
| CopilotKit account (free): `npx copilotkit@latest login` + `project select` | Written for you                               |
| Your name, for "sent by Friday, …'s AI assistant"                           | `FRIDAY_OWNER_NAME`                           |
| `FRIDAY_SERVICES_TOKEN`                                                     | Already generated in your `.env`              |
| Google Chrome, and WhatsApp Desktop signed in to your account               | Nothing to set                                |

## Setup and run

Use Node.js 24, npm and [uv](https://docs.astral.sh/uv/) (`python -m pip install uv`). Keep the project outside OneDrive.

```sh
npm ci
cd friday/services; python -m uv sync; cd ../..
cd friday/browser-agent; python -m uv sync; cd ../..   # Navigator's browser agent
```

Two terminals:

```sh
npm run dev               # the app
npm run friday:services   # browser, apps, WhatsApp, live activity (from the repo root)
```

Then, once:

```sh
npm run friday:setup    # creates Friday, Navigator, Operator; run again after updating Friday
node --env-file-if-exists=.env friday/scripts/check-services.mjs      # lists their tools as OpenDots sees them
node --env-file-if-exists=.env friday/scripts/test-evon-tools.mjs     # Evon tool calling (a few Evon tokens)
```

Open http://127.0.0.1:5173. The voice stage is the home screen.

- Tap the orb and talk.
- Tap it again to stop listening.
- The square button at the top right opens the classic chat view.
- `#/friday-demo` plays every animation without any keys.

Try saying:

- "Search for dentists near Indiranagar open this evening."
- "Open Notepad and write my shopping list: milk, eggs, bread."
- "WhatsApp Priya that I'm running ten minutes late." Friday reads the message back; you say yes.
- "Call Rahul on WhatsApp." Friday starts the call after your yes, and you talk.

## Pages and ports

| What                                                             | Address                              |
| ---------------------------------------------------------------- | ------------------------------------ |
| Main page: your details, Google account, permissions, shortcuts  | http://localhost:5173/ (or `#/home`) |
| Voice screen (the orb)                                           | http://localhost:5173/#/friday       |
| Desktop notch page (shown by `friday/desktop`)                   | http://localhost:5173/#/notch        |
| Dots (the OpenDots dashboard underneath)                         | http://localhost:5173/#/chat         |
| App server (API only; the pages above come through port 5173)    | http://127.0.0.1:4310                |
| Friday services (tools, live voice; `/health`, demo `/calendar`) | http://127.0.0.1:8765                |
| Friday's Chrome debugging port (for Friday's own tools only)     | 127.0.0.1:9333                       |

## Desktop notch

`friday/desktop` is a small Electron shell: a thin black pill just above the taskbar, like Wispr Flow. Hold Caps Lock anywhere to talk: the pill widens with bars that follow your voice, and again while Friday answers out loud. Clicking it opens the main page; its tray icon has Start with Windows and Quit.

```sh
cd friday/desktop; npm install; npm run install-shortcuts
```

This adds "Friday" to the Start menu and the desktop. The shell shows Friday from this folder's dev server, so changes to the folder appear at once, and it restarts itself when its own files change: there is nothing to reinstall. If the app or the services are not running, it starts them (logs in `data/desktop-*.log`). Only one voice window answers Caps Lock at a time, the newest: close the browser's voice screen and the notch takes over.

## Call Friday

Friday has its own calling agent. Exotel gives it an Indian number and carries the call audio, and everything else is Friday's own: a Pipecat pipeline in `friday/services` (`calls/bot.py`) hears with **Gnani Prisma**, thinks with **Evon** and speaks with **Gnani Timbre**, with Silero voice detection so the caller can talk over it. Exotel's Voicebot applet streams the call both ways to `/exotel/stream` through a fixed ngrok address; when Friday ends the call it closes the stream and the call flow hangs up.

**Calls Friday places.** `start_call` phones someone for you with one goal ("book a dental check-up tomorrow at 6 PM"). Friday says it is your AI assistant, never agrees to pay, checks with you before accepting a different option, and ends with an outcome, a summary and the transcript (`get_call_result`). `schedule_call` does the same at a later time. Friday also phones **you** when you ask ("call me at 5") and before deadlines, with your updates.

**Calls to Friday's number.** Who answers depends on who is calling. Friday matches the caller against your number and your contacts:

- **You** (`FRIDAY_OWNER_PHONE` or Your details) get **Friday** itself, with your updates first. Ask for anything ("set a timer for 20 minutes", "what's my battery", "WhatsApp Ravi that I'm late") and the call's `ask_friday` tool runs it through the Friday Dot, in its own "Friday phone" conversation: everything Friday can do on the desktop, it can do on a call, and it says "one second" while it works. Something that needs your OK is read back, and a "yes" or "haan" on the same call approves it. A request that takes longer than about 20 s keeps running; ask "is it done?" to hear the result.
- **Anyone else** gets **Reception**: no tasks, and none of your details or contacts. It says it is Friday, your AI assistant, shares nothing about you and takes a message (`take_message`), which is saved to your notes under their contact name.

Every call's transcript, outcome and reply times are kept in `friday/services/data/calls`.

Once:

1. **ngrok:** sign up free at dashboard.ngrok.com, copy your authtoken, and claim your free static domain (Domains). Put them in `.env` as `NGROK_AUTHTOKEN` and `NGROK_DOMAIN`. Exotel's call flow points at this address, so it must not change.
2. **Exotel:** sign up at exotel.com. The free trial lasts 7 days, has one number (ExoPhone), and calls only numbers you verify under Settings → Whitelist Numbers. Ask hello@exotel.com to switch on the Voicebot applet (AgentStream) for your account. From API settings put `EXOTEL_API_KEY`, `EXOTEL_API_TOKEN` and `EXOTEL_ACCOUNT_SID` in `.env`, your ExoPhone as `EXOTEL_CALLER_ID`, and any random string of 20+ characters as `EXOTEL_STREAM_KEY`. An India account uses `api.in.exotel.com` (the default); set `EXOTEL_SUBDOMAIN=api.exotel.com` for a Singapore one.
3. **The call flow:** in App Bazaar, create a flow with a **Voicebot** applet followed by a **Hangup** applet. The Voicebot's URL is `wss://<your domain>/exotel/stream?key=<EXOTEL_STREAM_KEY>`; the services print it at start (`Phone: Exotel Voicebot URL: …`). Put the flow's id (the number in its address) in `.env` as `EXOTEL_APP_ID`, and set the ExoPhone's incoming calls to the same flow.
4. **Check:** restart the services. `GET /health` shows `"calls": {"ready": true, "provider": "exotel"}`; then ask Friday to call you, or call the ExoPhone from your own number.

Exotel signs nothing, so the audio stream and its status callbacks must carry `EXOTEL_STREAM_KEY`, and through the tunnel only the call routes answer.

## How the voice stage works

- **Fast turns:** the voice stage runs Friday directly on the app server and keeps its own short conversation (the circular-arrow button starts a new one). The hosted conversation service is skipped there because it added 4–6 s per turn; the classic dashboard still uses it. Helper Dots also run directly.
- **Live voice:** the voice screen talks to `friday/services` over WebRTC, where a Pipecat pipeline hears you with **Gnani streaming speech-to-text** (with Silero voice detection) and speaks with **Gnani streaming text-to-speech**, so Friday's voice starts as it is made and stops the moment you talk over it. Friday's thinking is unchanged: the screen sends what was heard to the Friday Dot, which uses its quick actions or the helper Dots. Mic audio reaches Gnani only while Caps Lock is held (plus a second of silence), so idle time costs no credits. If the services are not running, the screen falls back to sending recorded clips to Gnani.
- **Talking:** Friday hears you **only while Caps Lock is held**, in any app (a quick tap is still ordinary Caps Lock). **Clap twice** to open the voice screen if it is closed. Caps Lock and claps are handled by `friday/services` (a keyboard hook that acts on Caps Lock alone, and a clap detector that looks only at loudness peaks; no audio is kept or sent). Turn them off with `FRIDAY_PUSH_TO_TALK=false` or `FRIDAY_CLAP_WAKE=false`; lower `FRIDAY_CLAP_LEVEL` (default 0.25) if your claps are not noticed.
- **Quick actions:** one-step requests ("open Notepad", "play Arijit Singh", "what's on my screen", "put Chrome on the left") run as Friday's own tools, with no helper hand-off. Each returns the sentence to speak, which ends the turn without another model call: "Open Notepad" takes about 1.5 s instead of 17 s. "Look at my screen" and window snapping come from Julian Ivanov's jarvis-voice-assistant (MIT), rebuilt on Win32 and the EVON_* model. A screenshot goes to that model only when you ask Friday to look.
- **Gemini speed:** with Gemini as the brain, `FRIDAY_MODEL_OPTIONS='{"reasoning_effort":"none"}'` skips its thinking step and halves the wait.
- **Paused:** if the dashboard's Pause button is on, Friday shows "Friday is paused" with a Resume button; nothing runs while paused.
- **Timings:** each turn logs `[Friday] heard … · answered … · voice …` in the browser console.

- **Hands-free:** the mic stays open, but a voice-activity detector records only while you speak (with half a second of pre-roll so your first word is kept, ending 0.7 s after you stop), so silence never spends Gnani credits. Each stretch of speech (up to 20 s) goes to Prisma.
- **Talking back:** Timbre speaks the reply sentence by sentence, starting while the rest is still being made. While Friday is speaking, only "stop", "yes" or "no" count, so its own voice coming back through the mic is ignored; "stop", "ruko", "bas" or the red button stops the task and every on-screen action.
- **Narration:** helper actions arrive live and Friday speaks the important ones, at most one line every few seconds. Small steps like clicks are shown but not spoken.
- **Approvals:** a WhatsApp send or call shows OpenDots' approval card, and Friday asks out loud. Short answers like "yes", "haan" or "theek hai", or "no" and "nahin", press the real Approve or Decline button. You can also tap them.
- **The orb:** it reacts to your voice while listening, swirls while thinking, pulses with Friday's voice, and shows each busy helper Dot orbiting it.

## Safety

- **WhatsApp:** messages and calls always need your yes. Friday checks that the chat it opened is the person you named, stops if a name matches several chats, and signs messages as sent by Friday, your AI assistant.
- **Browser and apps:** Navigator and Operator refuse password, card and OTP fields, never pay or accept terms, and can't touch WhatsApp windows.
- **Your Chrome profile:** Friday's browser has its own profile, so your personal logins are untouched.
- **Stop switch:** the stop button blocks every on-screen action until the task has ended.

## What lives where

| Path                                             | What it is                                                                    |
| ------------------------------------------------ | ----------------------------------------------------------------------------- |
| `friday/persona.md`, `friday/dots/`              | Personas for Friday, Navigator and Operator                                   |
| `friday/services/`                               | Python service: browser, desktop apps, WhatsApp, activity feed, demo calendar |
| `src/friday/client/stage/`                       | The voice stage: orb, hands-free listener, narration, approvals               |
| `src/friday/server/`                             | Evon settings, Gnani voice, `delegate_to_dot`, activity relay                 |
| `tests/friday/`, `friday/services/tests/`        | Tests (Gnani, Evon and WhatsApp are faked; the browser test uses real Chrome) |
| [CHANGES-TO-OPENDOTS.md](CHANGES-TO-OPENDOTS.md) | Every OpenDots file Friday edits, and why                                     |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)     | The design                                                                    |

`main` tracks upstream OpenDots and the `friday` branch holds this work, so `git diff main..friday` shows exactly what Friday adds.

## Tests

```sh
npm run typecheck; npx vitest run tests/friday
cd friday/services; python -m uv run pytest; python -m uv run ruff check .; cd ../..
cd friday/browser-agent; python -m uv run pytest
```

## Known limits

- **Not tested live with your keys.** Gnani, Evon, CopilotKit and WhatsApp are faked in tests. The browser code was tested by driving real Chrome through the demo calendar, and app listing was checked on this PC.
- **WhatsApp automation** relies on WhatsApp Desktop's control names ("Search or start a new chat", "Voice call"). A WhatsApp update can change them. Try `whatsapp_find_chat` first, since it sends nothing.
- **Friday doesn't speak on WhatsApp calls;** it starts them and you talk. Speaking on calls would need a virtual audio cable.
- **Operator** depends on how well each app exposes Windows UI Automation. Most standard apps work; some custom-drawn ones don't.
- **Helpers have no approval cards,** because they run in background conversations. Their browser and app tools run without asking. Anything sensitive (WhatsApp) stays with Friday behind your OK.
- **Echo:** echo cancellation and a raised threshold while Friday speaks keep it from hearing itself, but headphones work best.
- **Phone calls over Twilio** (the earlier calling service) are parked; the code remains under `friday/services/friday_services/calls`.
- **The browser agent** (browser-use, `friday/browser-agent`) runs in its own Python environment because it pins exact library versions; it attaches to Navigator's Chrome window through Chrome's debugging port on 127.0.0.1 (`FRIDAY_BROWSER_CDP_PORT`). Every step is one model call, so a free Gemini key runs out quickly; its log is `friday/services/data/browser-agent.log`.
- **Playing on YouTube** takes 4–7 s, mostly YouTube's own page load; the first web task also starts Friday's own Chrome. `FRIDAY_BROWSER_PREWARM=true` opens it when the services start instead.
- **Conversations live in CopilotKit's cloud** (its local version is macOS-only). Telegram was dropped.

## Credit and license

Friday is built on [OpenDots](https://github.com/CopilotKit/OpenDots), an open-source template by CopilotKit released under the MIT license; its license is kept in [LICENSE](../LICENSE). The browser uses [Playwright](https://playwright.dev), desktop apps use [pywinauto](https://github.com/pywinauto/pywinauto), and speech uses Gnani's Prisma and Timbre. Friday's additions are released under the same MIT license. Every project Friday is built on, took ideas from or uses, and where each one lives, is listed in [CREDITS.md](CREDITS.md).
