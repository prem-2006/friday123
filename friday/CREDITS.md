# Where Friday's parts come from

Friday is built on one open-source project, took ideas from two more, and uses a set of libraries. This page says which is which and where each one lives in Friday.

## 1. Built on (Friday is a fork of it)

| Project                                                          | What Friday took                                                                                                                                                                                                                                                                                                  | Where in Friday                                                           | License |
| ---------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- | ------- |
| [OpenDots](https://github.com/CopilotKit/OpenDots) by CopilotKit | The whole base app: Dots (agents), Spaces and conversations, the chat screen, the server that runs a Dot's turns, MCP connections with "ask first" approvals, settings, dark mode, Parallel web research. Friday's changes to its files are small and listed in [CHANGES-TO-OPENDOTS.md](CHANGES-TO-OPENDOTS.md). | `src/` (except `src/friday/`), `tests/` (except `tests/friday/`), `docs/` | MIT     |

## 1b. Code ported from

| Project | What Friday took | Where in Friday | License |
| ------- | ---------------- | --------------- | ------- |
| [Atticus for Mac](https://github.com/SaarthurR/atticus-mac) by SaarthurR, and its Windows port [atticus-windows](https://github.com/FindlayHi/atticus-windows) | Instant commands with no AI call, learned shortcuts that replay a task said in any order or for another app, "When I say X, …" voice shortcuts, "watch for me", the opt-in wake word idea, and the Windows display and window code (laptop vs monitor, exact window frames). Adapted to replay Friday's own quick tools and to Hindi and Hinglish asks. | `friday/services/friday_services/instant.py`, `winctl.py`, `watch.py`, `wake.py` (wake word), `src/friday/server/instant.ts` | MIT |

## 1c. Run alongside Friday

| Project | What it does for Friday | Where in Friday | License |
| ------- | ----------------------- | --------------- | ------- |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research | Navigator's engine for multi-step browser jobs: it works Friday's Chrome over CDP with Friday's model, and keeps its own memory and skills. Installed as a package, not copied. | `friday/hermes-agent/` (`run_task.py`), chosen in `friday/services/friday_services/browser_agent.py` | MIT |

## 2. Ideas taken and rebuilt (no code copied)

| Project                                                                                      | What Friday took                                                                                                                                                                                                                                                                      | Where in Friday                                                                                                                 | License          |
| -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- | ---------------- |
| **jarvis-voice-assistant** by Julian Ivanov                                                  | "What's on my screen" (a screenshot described by a vision model), snapping windows to halves and quarters, waking with two claps. Rebuilt on Win32 and Friday's own model settings.                                                                                                   | `friday/services/friday_services/screen.py`, `wake.py`                                                                          | MIT              |
| **ARRC "Build Your Own J" kit** by Hari and Anurag (Sarvam AI hackathon, with IIT Hyderabad) | Phoning the laptop through a Sarvam agent that calls a tool on the PC; the quick actions (timer, reminders, deadlines with reminder calls, notes, modes, focus session, system status, lock screen, screenshot, weather, directions, email draft); the console look of the main page. | `friday/services/friday_services/organizer.py`, `mcp_quick.py`, `phone_in.py`, `src/friday/client/home/Console.tsx`, `home.css` | Apache-2.0       |
| **Wispr Flow** (an app, not open source)                                                     | The look of the desktop notch: a thin pill above the taskbar that widens while you talk.                                                                                                                                                                                              | `friday/desktop/`, `src/friday/client/stage/Notch.tsx`                                                                          | Design idea only |

## 3. Libraries Friday uses (installed, not copied)

**Voice and calls**

| Library                                               | What it does for Friday                                                                                                |
| ----------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| [Pipecat](https://github.com/pipecat-ai/pipecat)      | Runs live voice: mic in, Gnani speech-to-text, Friday's reply, Gnani text-to-speech out, over WebRTC and phone streams |
| pipecat-gnani, gnani-vachana                          | Gnani's speech-to-text (Prisma) and text-to-speech (Timbre) inside Pipecat                                             |
| Silero VAD (through Pipecat)                          | Notices when someone starts and stops talking                                                                          |
| [ngrok-python](https://github.com/ngrok/ngrok-python) | The fixed public address that Sarvam's agent uses to reach Friday                                                      |
| sounddevice, numpy                                    | Mic audio for the clap detector and the wake word                                                                      |
| [Vosk](https://github.com/alphacep/vosk-api) | Offline listening for "Hey Friday" only (small Indian-English model) |

**Browser and desktop**

| Library                                                      | What it does for Friday                                               |
| ------------------------------------------------------------ | --------------------------------------------------------------------- |
| [browser-use](https://github.com/browser-use/browser-use)    | Navigator's agent for multi-step web tasks (`friday/browser-agent`)   |
| [Playwright](https://github.com/microsoft/playwright-python) | Drives Friday's own Chrome window                                     |
| [pywinauto](https://github.com/pywinauto/pywinauto)          | Opens apps, types and clicks in Windows apps (UI Automation)          |
| [keyboard](https://github.com/boppreh/keyboard)              | Hold Caps Lock to talk; Ctrl + Caps Lock for the notch                |
| [pycaw](https://github.com/AndreMiras/pycaw)                 | Sets the speaker volume to an exact number                            |
| [pywinrt](https://github.com/pywinrt/pywinrt) (winrt-*)      | Windows media controls: pause, play, skip, rewind whatever is playing |
| [psutil](https://github.com/giampaolo/psutil)                | Finds which browser you used last                                     |
| [Electron](https://github.com/electron/electron)             | The desktop notch window and tray icon (`friday/desktop`)             |

**App and server**

| Library                                                                                                  | What it does for Friday                                                               |
| -------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| [CopilotKit](https://github.com/CopilotKit/CopilotKit), [AG-UI](https://github.com/ag-ui-protocol/ag-ui) | OpenDots' agent runtime and chat protocol                                             |
| [TanStack AI](https://github.com/TanStack/ai)                                                            | Calls the AI model and runs its tool calls                                            |
| [MCP SDKs](https://github.com/modelcontextprotocol) (Python and TypeScript)                              | How the Dots reach Friday's tools in `friday/services`                                |
| [FastAPI](https://github.com/fastapi/fastapi), uvicorn                                                   | The `friday/services` web server                                                      |
| [Hono](https://github.com/honojs/hono), zod, lucide-react, React, Vite                                   | OpenDots' server routes, input checks, icons and web app                              |
| [OpenBot](https://github.com/CopilotKit/OpenBot) by CopilotKit                                           | OpenDots' optional per-Dot computers (Docker); Friday runs on your own screen instead |

## 4. Services Friday calls (accounts, not code)

Gnani (Evon for planning, Prisma for hearing, Timbre for speaking, Inya for calls), Sarvam (voice agent and phone number), Google Gemini and an OpenAI-compatible backup model, Twilio (optional), ngrok, Cloudflare quick tunnels (with Twilio), Open-Meteo (weather), Parallel (web research in OpenDots).
