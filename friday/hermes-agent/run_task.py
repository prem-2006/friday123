"""Navigator's engine with Hermes Agent (Nous Research, MIT): one browser task in
the Chrome window friday/services opened.

The same protocol as friday/browser-agent/run_task.py, so friday/services can
use either. A JSON request on stdin:
    {"task": "...", "cdp_url": "http://127.0.0.1:9333", "max_steps": 15}
and lines that start with EVENT_PREFIX on stdout:
    {"type": "step", "n": 1, "goal": "...", "url": "..."}
    {"type": "done", "ok": true, "result": "...", "urls": [...], "steps": 4}
Everything else Hermes prints goes to stderr, which friday/services keeps in a log.

Hermes attaches its browser tools to Friday's Chrome over CDP, and keeps its own
memory and skills in ./home, so it gets better at sites it has worked on before.
The model comes from Friday's settings: FRIDAY_HERMES_* (base URL, API key,
model), else FRIDAY_BROWSER_*, else EVON_*.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
EVENT_PREFIX = "FRIDAY_EVENT "
EVENTS = sys.stdout  # main() points sys.stdout at stderr for Hermes's own output

SAFETY = """
You are Navigator, working for Friday, a personal assistant, in a Chrome window on the owner's screen.
Use the browser tools on the page that is already open; open new pages with browser_navigate.
Read pages with browser_snapshot; don't use browser_console or browser_cdp. Answer as soon as you know.
- Never type passwords, card numbers, UPI PINs or one-time codes, and never solve CAPTCHAs.
- Never buy, pay, book anything paid, place orders, accept terms or send messages.
- If the task needs a login, payment or any of the above, stop and say what the owner must do.
- Close cookie banners with the least-permissive option.
- Finish with a short plain answer: what you found (titles and links) or what you did.
"""


def model_settings(env: Mapping[str, str]) -> dict[str, str]:
    """Which model to use, from the same settings as Friday."""

    def pick(name: str) -> str:
        return (
            env.get(f"FRIDAY_HERMES_{name}")
            or env.get(f"FRIDAY_BROWSER_{name}")
            or env.get(f"EVON_{name}", "")
        )

    settings = {"base_url": pick("BASE_URL"), "api_key": pick("API_KEY"), "model": pick("MODEL")}
    if not (settings["base_url"] and settings["api_key"] and settings["model"]):
        raise ValueError("Set EVON_BASE_URL, EVON_API_KEY and EVON_MODEL (or FRIDAY_HERMES_*) in .env.")
    return settings


def emit(event: dict[str, Any]) -> None:
    EVENTS.write(EVENT_PREFIX + json.dumps(event) + "\n")
    EVENTS.flush()


def step_event(n: int, tool: str, args: Any) -> dict[str, Any]:
    """A tool call as one of Navigator's steps: "browser_click: Search button"."""
    detail = ""
    if isinstance(args, dict):
        detail = str(
            args.get("url") or args.get("text") or args.get("ref") or args.get("query") or ""
        )
    elif args:
        detail = str(args)
    goal = tool.replace("browser_", "").replace("_", " ") + (f": {detail}" if detail else "")
    url = args.get("url", "") if isinstance(args, dict) else ""
    return {"type": "step", "n": n, "goal": goal[:200], "url": url}


def run(request: dict[str, Any]) -> dict[str, Any]:
    from run_agent import AIAgent

    model = model_settings(os.environ)
    os.environ["BROWSER_CDP_URL"] = request["cdp_url"]
    steps: list[dict[str, Any]] = []

    def started(_call_id: str, name: str, args: Any) -> None:
        event = step_event(len(steps) + 1, name, args)
        steps.append(event)
        emit(event)

    agent = AIAgent(
        base_url=model["base_url"],
        api_key=model["api_key"],
        provider="custom",
        model=model["model"],
        enabled_toolsets=["browser"],
        max_iterations=int(request.get("max_steps", 15)),
        quiet_mode=True,
        ephemeral_system_prompt=SAFETY,
        tool_start_callback=started,
        skip_context_files=True,
        platform="friday",
    )
    result = agent.run_conversation(request["task"])
    answer = str((result or {}).get("final_response") or "").strip()
    return {
        "type": "done",
        "ok": bool(answer),
        "result": answer[:4000] or "Hermes stopped without an answer.",
        "urls": [s["url"] for s in steps if s.get("url")][-5:],
        "steps": len(steps),
    }


def main() -> None:
    os.environ.setdefault("HERMES_HOME", str(HERE / "home"))
    Path(os.environ["HERMES_HOME"]).mkdir(parents=True, exist_ok=True)
    # Hermes prints to stdout; keep stdout for Friday's events only.
    sys.stdout = sys.stderr
    try:
        request = json.loads(sys.stdin.read().lstrip("﻿"))  # PowerShell pipes add a BOM
        outcome = run(request)
    except Exception as error:  # noqa: BLE001 - reported to Navigator, which tells the owner
        outcome = {"type": "done", "ok": False, "result": f"Hermes failed: {error}"}
    emit(outcome)


if __name__ == "__main__":
    main()
