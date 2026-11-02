"""MCP tools for the Navigator Dot: a Chrome window on the owner's screen."""

from __future__ import annotations

from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from friday_services.activity import ActivityFeed
from friday_services.browser import BrowserError, OnScreenBrowser
from friday_services.browser_agent import BrowserAgent, BrowserAgentError
from friday_services.sites import site_name
from friday_services.worker import Worker

ACT = ToolAnnotations(read_only_hint=False, destructive_hint=False)
LOOK = ToolAnnotations(read_only_hint=True)
Ref = Annotated[int, Field(description="Element number from the latest page description.", ge=1)]


def build_browser_server(
    browser: OnScreenBrowser,
    worker: Worker,
    feed: ActivityFeed,
    agent: BrowserAgent | None = None,
) -> MCPServer:
    server = MCPServer(
        name="friday-browser",
        instructions=(
            "A real Chrome window on the owner's screen. Each result lists the page's "
            'elements as [number] role "label"; act on them by number. Never type '
            "passwords, card numbers or OTPs, and never pay for anything."
        ),
    )

    async def act(action: str, detail: str, fn, *args) -> dict:
        feed.guard()
        event = feed.emit("navigator", action, detail)
        try:
            result = await worker.run(fn, *args)
        except BrowserError as error:
            event.state = "failed"
            feed.emit("navigator", action, str(error), state="failed")
            raise ToolError(str(error)) from error
        except Exception as error:
            feed.emit("navigator", action, f"{detail} did not work.", state="failed")
            raise ToolError(f"The browser could not do that: {error}") from error
        title = result.get("title") or result.get("url", "")
        feed.emit(
            "navigator",
            action,
            f"{detail} Now on: {title}"[:200],
            state="done",
            image=await worker.run(browser.screenshot) or None,
        )
        return result

    @server.tool(
        name="browser_open",
        description="Open a web address in the on-screen browser.",
        annotations=ACT,
    )
    async def browser_open(
        url: Annotated[str, Field(description="Web address, e.g. google.com")],
    ) -> dict:
        return await act("browser_open", f"Opening {site_name(url)}.", browser.open, url)

    @server.tool(
        name="browser_search",
        description=(
            "Search in the on-screen browser by typing into the site's own search box, and "
            "return the top results (title, link, kind). Use site youtube for videos, "
            "channels, tutorials, songs or anything the owner says is on YouTube; otherwise google."
        ),
        annotations=ACT,
    )
    async def browser_search(
        site: Literal["youtube", "google"],
        query: Annotated[str, Field(min_length=1, max_length=300)],
    ) -> dict:
        name = "YouTube" if site == "youtube" else "Google"
        return await act(
            "browser_search", f'Searching {name} for "{query}".', browser.search, site, query
        )

    @server.tool(
        name="browser_task",
        description=(
            "Hand a whole multi-step web task to the browser agent, which plans, clicks, "
            "types and reads in the on-screen browser by itself and returns its answer. "
            "Use it for anything beyond a single search: comparing, filling forms, finding "
            "details across pages. Describe the goal in one or two plain sentences."
        ),
        annotations=ACT,
    )
    async def browser_task(
        task: Annotated[str, Field(min_length=3, max_length=1000)],
        max_steps: Annotated[int, Field(ge=1, le=30)] = 15,
    ) -> dict:
        if agent is None:
            raise ToolError("The browser agent is not set up; use the other browser tools.")
        feed.guard()
        feed.emit("navigator", "browser_task", f"Working on: {task[:160]}")

        async def on_step(step: dict) -> None:
            feed.emit(
                "navigator",
                "browser_step",
                (step.get("goal") or "Working in the browser.")[:200],
                state="done",
                image=await worker.run(browser.screenshot) or None,
            )

        try:
            cdp_url = await worker.run(browser.cdp_url)
            outcome = await agent.run(task, cdp_url, max_steps, on_step, lambda: feed.stopped)
        except (BrowserError, BrowserAgentError) as error:
            feed.emit("navigator", "browser_task", str(error), state="failed")
            raise ToolError(str(error)) from error
        feed.emit(
            "navigator",
            "browser_task",
            ("Finished: " if outcome.get("ok") else "Stopped: ") + outcome.get("result", "")[:160],
            state="done" if outcome.get("ok") else "failed",
            image=await worker.run(browser.screenshot) or None,
        )
        return {
            "done": bool(outcome.get("ok")),
            "answer": outcome.get("result", ""),
            "pages": outcome.get("urls", []),
            "steps": outcome.get("steps", 0),
        }

    @server.tool(
        name="code_editor_read",
        description=(
            "Read the code editor on the current page (LeetCode and similar sites): its "
            "language and the starting code with the function signature."
        ),
        annotations=LOOK,
    )
    async def code_editor_read() -> dict:
        return await act("code_editor_read", "Reading the code editor.", browser.code_editor)

    @server.tool(
        name="code_editor_write",
        description=(
            "Replace all the code in the page's code editor with this complete solution. "
            "Keep the site's function signature."
        ),
        annotations=ACT,
    )
    async def code_editor_write(
        code: Annotated[str, Field(min_length=1, max_length=20000)],
    ) -> dict:
        return await act(
            "code_editor_write", "Writing the solution in the editor.", browser.code_editor, code
        )

    @server.tool(
        name="browser_snapshot",
        description="Describe the current page and its numbered elements.",
        annotations=LOOK,
    )
    async def browser_snapshot() -> dict:
        return await act("browser_snapshot", "Looking at the page.", browser.snapshot)

    @server.tool(
        name="browser_click", description="Click an element by its number.", annotations=ACT
    )
    async def browser_click(ref: Ref) -> dict:
        return await act("browser_click", f"Clicking element {ref}.", browser.click, ref)

    @server.tool(
        name="browser_type",
        description="Type text into a field by its number, optionally pressing Enter.",
        annotations=ACT,
    )
    async def browser_type(
        ref: Ref,
        text: Annotated[str, Field(max_length=2000)],
        press_enter: bool = False,
    ) -> dict:
        return await act(
            "browser_type", f'Typing "{text[:60]}".', browser.type, ref, text, press_enter
        )

    @server.tool(
        name="browser_press",
        description="Press a key such as Enter, Escape, Tab or ArrowDown.",
        annotations=ACT,
    )
    async def browser_press(key: Annotated[str, Field(max_length=30)]) -> dict:
        return await act("browser_press", f"Pressing {key}.", browser.press, key)

    @server.tool(name="browser_scroll", description="Scroll the page up or down.", annotations=ACT)
    async def browser_scroll(direction: Literal["up", "down"] = "down") -> dict:
        return await act("browser_scroll", f"Scrolling {direction}.", browser.scroll, direction)

    @server.tool(
        name="browser_read",
        description="Read the visible text of the current page.",
        annotations=LOOK,
    )
    async def browser_read() -> dict:
        return await act("browser_read", "Reading the page.", browser.read)

    @server.tool(name="browser_back", description="Go back to the previous page.", annotations=ACT)
    async def browser_back() -> dict:
        return await act("browser_back", "Going back.", browser.back)

    return server
