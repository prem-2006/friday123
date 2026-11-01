"""Navigator's browser agent: the child process that runs browser-use, and the
browser_task tool around it. A small script stands in for browser-use here."""

from __future__ import annotations

import asyncio
import sys
import textwrap

import httpx
import pytest

from friday_services.browser_agent import BrowserAgent, BrowserAgentError

from .conftest import TOKEN
from .mcp_client import McpClient

AUTH = {"Authorization": f"Bearer {TOKEN}"}


def fake_runner(tmp_path, body: str) -> list[str]:
    script = tmp_path / "fake_runner.py"
    script.write_text(
        textwrap.dedent(
            """
            import json, sys, time
            request = json.loads(sys.stdin.read())
            def emit(event):
                print("FRIDAY_EVENT " + json.dumps(event), flush=True)
            print("browser-use log line that Friday ignores", flush=True)
            """
        )
        + textwrap.dedent(body),
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


def run(agent: BrowserAgent, stopped=lambda: False):
    steps: list[dict] = []

    async def on_step(step):
        steps.append(step)

    outcome = asyncio.run(agent.run("Find X", "http://127.0.0.1:9333", 5, on_step, stopped))
    return outcome, steps


def test_streams_steps_and_returns_the_answer(tmp_path):
    command = fake_runner(
        tmp_path,
        """
        emit({"type": "step", "n": 1, "goal": "Open " + request["cdp_url"]})
        emit({"type": "step", "n": 2, "goal": "Read the page नमस्ते"})
        emit({"type": "done", "ok": True, "result": "Found it", "urls": ["https://x.test"],
              "steps": request["max_steps"]})
        """,
    )
    outcome, steps = run(BrowserAgent(command, tmp_path / "agent.log"))
    assert [s["goal"] for s in steps] == ["Open http://127.0.0.1:9333", "Read the page नमस्ते"]
    assert outcome["result"] == "Found it" and outcome["steps"] == 5


def test_stop_switch_ends_the_agent(tmp_path):
    command = fake_runner(
        tmp_path,
        """
        emit({"type": "step", "n": 1, "goal": "Start"})
        time.sleep(30)
        """,
    )
    with pytest.raises(BrowserAgentError, match="pressed stop"):
        run(BrowserAgent(command, tmp_path / "agent.log"), stopped=lambda: True)


def test_crash_and_timeout_are_reported(tmp_path):
    crash = fake_runner(tmp_path, "raise SystemExit(3)\n")
    with pytest.raises(BrowserAgentError, match="stopped unexpectedly"):
        run(BrowserAgent(crash, tmp_path / "agent.log"))
    slow = fake_runner(tmp_path, "time.sleep(30)\n")
    with pytest.raises(BrowserAgentError, match="ran out of time"):
        run(BrowserAgent(slow, tmp_path / "agent.log", timeout_s=1))


def test_missing_install_says_how_to_fix_it(tmp_path):
    from friday_services.browser_agent import agent_from_settings

    with pytest.raises(BrowserAgentError, match="uv sync"):
        run(agent_from_settings(tmp_path / "nowhere", tmp_path))


def browser(server) -> McpClient:
    mcp = McpClient(f"{server.url}/browser/mcp", TOKEN)
    mcp.initialize()
    return mcp


def test_browser_task_tool_reports_steps_and_answer(server):
    tools = browser(server).tools()
    assert tools["browser_task"]["annotations"]["readOnlyHint"] is False
    result = browser(server).call("browser_task", {"task": "Latest Python version?"})
    assert result["data"] == {
        "done": True,
        "answer": "Python 3.14 is the latest.",
        "pages": ["https://docs.python.org/"],
        "steps": 2,
    }
    assert server.browser_agent.tasks == [("Latest Python version?", "http://127.0.0.1:9333", 15)]
    feed = httpx.get(f"{server.url}/activity", headers=AUTH).json()
    events = feed["events"] if isinstance(feed, dict) else feed
    actions = [(e["action"], e["state"]) for e in events]
    assert actions[-4:] == [
        ("browser_task", "running"),
        ("browser_step", "done"),
        ("browser_step", "done"),
        ("browser_task", "done"),
    ]


def test_browser_task_failure_and_stop(server):
    server.browser_agent.error = BrowserAgentError("The browser agent ran out of time.")
    result = browser(server).call("browser_task", {"task": "Something slow"})
    assert result["isError"] and "ran out of time" in result["text"]
    httpx.post(f"{server.url}/activity/stop", headers=AUTH)
    result = browser(server).call("browser_task", {"task": "Anything"})
    assert result["isError"] and "pressed stop" in result["text"]
    assert len(server.browser_agent.tasks) == 1


def test_hermes_is_navigators_engine_when_installed(tmp_path):
    from friday_services.browser_agent import agent_from_settings

    hermes, use = tmp_path / "hermes-agent", tmp_path / "browser-agent"
    for folder in (hermes, use):
        (folder / ".venv" / "Scripts").mkdir(parents=True)
        (folder / ".venv" / "Scripts" / "python.exe").write_text("")
        (folder / "run_task.py").write_text("")
    assert "hermes-agent" in str(agent_from_settings(use, tmp_path, hermes)._command()[1])
    picked = agent_from_settings(use, tmp_path, hermes, "browser-use")
    assert "browser-agent" in str(picked._command()[1])
    assert "browser-agent" in str(agent_from_settings(use, tmp_path, tmp_path / "none")._command()[1])
