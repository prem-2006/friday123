"""The running service: browser and WhatsApp tools, the live activity feed and
the owner's stop switch."""

from __future__ import annotations

import json
import threading

import httpx

from .conftest import TOKEN
from .mcp_client import McpClient

AUTH = {"Authorization": f"Bearer {TOKEN}"}


def client(server, name: str) -> McpClient:
    mcp = McpClient(f"{server.url}/{name}/mcp", TOKEN)
    mcp.initialize()
    return mcp


def test_each_dot_gets_its_own_tools(server):
    browser = client(server, "browser").tools()
    desktop = client(server, "desktop").tools()
    whatsapp = client(server, "whatsapp").tools()
    assert {"browser_open", "browser_click", "browser_type", "browser_read"} <= set(browser)
    assert {"open_app", "window_snapshot", "window_click", "press_keys"} <= set(desktop)
    assert set(whatsapp) == {"whatsapp_find_chat", "whatsapp_send_message", "whatsapp_call"}
    # Sending and calling are never read-only, so OpenDots asks the owner first.
    assert whatsapp["whatsapp_send_message"]["annotations"]["readOnlyHint"] is False
    assert whatsapp["whatsapp_call"]["annotations"]["readOnlyHint"] is False
    assert whatsapp["whatsapp_find_chat"]["annotations"]["readOnlyHint"] is True


def test_activity_needs_the_token(server):
    assert httpx.get(f"{server.url}/activity").status_code == 401
    assert httpx.post(f"{server.url}/activity/stop").status_code == 401


def test_actions_show_up_live_with_a_screenshot(server):
    received: list[dict] = []
    ready = threading.Event()

    def listen():
        with httpx.stream(
            "GET", f"{server.url}/activity/stream", headers=AUTH, timeout=20
        ) as stream:
            for line in stream.iter_lines():
                if line.startswith(": connected"):
                    ready.set()
                if line.startswith("data: "):
                    received.append(json.loads(line[6:]))
                    if len(received) >= 2:
                        return

    thread = threading.Thread(target=listen, daemon=True)
    thread.start()
    assert ready.wait(10)
    result = client(server, "browser").call("browser_open", {"url": "example.test"})
    assert result["data"]["title"] == "Example"
    thread.join(10)
    assert [(e["actor"], e["state"]) for e in received] == [
        ("navigator", "running"),
        ("navigator", "done"),
    ]
    assert received[0]["detail"] == "Opening Example."
    assert received[1]["image"] == "aW1hZ2U="


def test_whatsapp_send_through_mcp(server):
    whatsapp = client(server, "whatsapp")
    result = whatsapp.call("whatsapp_send_message", {"contact": "Rahul", "text": "On my way"})
    assert result["data"]["sent_to"] == "Rahul"
    assert ("enter", "Rahul") in server.whatsapp.log
    ambiguous = whatsapp.call("whatsapp_send_message", {"contact": "Priya", "text": "hi"})
    assert ambiguous["isError"] and "Ask the owner which one" in ambiguous["text"]
    events = httpx.get(f"{server.url}/activity", headers=AUTH).json()["events"]
    assert events[-1]["state"] == "failed"


def test_stop_switch_blocks_every_action_until_resumed(server):
    browser = client(server, "browser")
    assert httpx.post(f"{server.url}/activity/stop", headers=AUTH).json() == {"stopped": True}
    blocked = browser.call("browser_click", {"ref": 1})
    assert blocked["isError"] and "pressed stop" in blocked["text"]
    blocked_wa = client(server, "whatsapp").call("whatsapp_call", {"contact": "Rahul"})
    assert blocked_wa["isError"]
    assert not [entry for entry in server.whatsapp.log if entry[0] == "call"]
    assert server.browser.log == []
    httpx.post(f"{server.url}/activity/resume", headers=AUTH)
    assert not browser.call("browser_click", {"ref": 1})["isError"]
