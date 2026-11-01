"""Runs one browser task in the on-screen Chrome, with Hermes Agent
(friday/hermes-agent) or browser-use (friday/browser-agent).

Each lives in its own environment because it pins exact versions of
libraries these services also use, so it runs as a child process. Its steps
come back as JSON lines; the owner's stop switch ends the process at once.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

EVENT_PREFIX = "FRIDAY_EVENT "


class BrowserAgentError(RuntimeError):
    pass


def default_command(agent_dir: Path) -> list[str]:
    python = agent_dir / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        raise BrowserAgentError(
            f"The browser agent is not installed. Run `uv sync` in {agent_dir} first."
        )
    return [str(python), str(agent_dir / "run_task.py")]


class BrowserAgent:
    def __init__(
        self,
        command: Sequence[str] | Callable[[], Sequence[str]],
        log_path: Path,
        timeout_s: float = 270,
    ):
        self._command = command
        self._log_path = log_path
        # Below Friday's tool timeout (FRIDAY_TOOL_TIMEOUT_MS, 5 minutes by default).
        self._timeout_s = timeout_s

    async def run(
        self,
        task: str,
        cdp_url: str,
        max_steps: int,
        on_step: Callable[[dict[str, Any]], Awaitable[None]],
        stopped: Callable[[], bool],
    ) -> dict[str, Any]:
        command = list(self._command() if callable(self._command) else self._command)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_running_loop()
        events: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        with self._log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} {task[:200]}\n")
            log.flush()
            # Popen and a reader thread, not asyncio subprocesses, which need the
            # Proactor event loop on Windows.
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            def read() -> None:
                assert process.stdout is not None
                for line in process.stdout:
                    if line.startswith(EVENT_PREFIX):
                        try:
                            event = json.loads(line[len(EVENT_PREFIX) :])
                        except json.JSONDecodeError:
                            continue
                        loop.call_soon_threadsafe(events.put_nowait, event)
                loop.call_soon_threadsafe(events.put_nowait, None)

            reader = threading.Thread(target=read, name="friday-browser-agent", daemon=True)
            reader.start()
            assert process.stdin is not None
            process.stdin.write(
                json.dumps({"task": task, "cdp_url": cdp_url, "max_steps": max_steps})
            )
            process.stdin.close()

            deadline = time.monotonic() + self._timeout_s
            outcome: dict[str, Any] | None = None
            ended = ""
            try:
                while True:
                    if stopped():
                        ended = "The owner pressed stop, so the browser agent was ended."
                        break
                    if time.monotonic() > deadline:
                        ended = f"The browser agent ran out of time after {int(self._timeout_s)} seconds."
                        break
                    try:
                        event = await asyncio.wait_for(events.get(), 0.5)
                    except TimeoutError:
                        continue
                    if event is None:
                        break
                    if event.get("type") == "step":
                        await on_step(event)
                    elif event.get("type") == "done":
                        outcome = event
            finally:
                if process.poll() is None:
                    process.kill()
                await asyncio.to_thread(process.wait, 10)
        if ended:
            raise BrowserAgentError(ended)
        if outcome is None:
            raise BrowserAgentError(
                f"The browser agent stopped unexpectedly (see {self._log_path.name})."
            )
        return outcome


def installed(agent_dir: Path) -> bool:
    python = agent_dir / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return python.exists() and (agent_dir / "run_task.py").exists()


def agent_from_settings(
    agent_dir: Path, data_dir: Path, hermes_dir: Path | None = None, engine: str = "auto"
) -> BrowserAgent:
    """Hermes when it is installed (FRIDAY_BROWSER_AGENT=auto or hermes), else browser-use."""
    if hermes_dir and engine != "browser-use" and (engine == "hermes" or installed(hermes_dir)):
        return BrowserAgent(lambda: default_command(hermes_dir), data_dir / "hermes-agent.log")
    return BrowserAgent(lambda: default_command(agent_dir), data_dir / "browser-agent.log")
