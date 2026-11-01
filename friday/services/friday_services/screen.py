"""Looking at the owner's screen and arranging windows, for Friday's quick actions.

Both ideas come from Julian Ivanov's jarvis-voice-assistant (MIT): describe a
screenshot with a vision model, and snap windows into halves or quarters.
Here they use Win32 directly and the same model settings as Friday (EVON_*).
"""

from __future__ import annotations

import base64
import io
from collections.abc import Mapping
from typing import Any, Literal

import httpx

from friday_services.usage import counted

Position = Literal[
    "left",
    "right",
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
    "center",
    "maximize",
    "minimize",
]
Screen = Literal["current", "laptop", "monitor"]
POSITIONS: tuple[str, ...] = Position.__args__  # type: ignore[attr-defined]

LOOK_PROMPT = (
    "You see the owner's computer screen. Answer the question in at most three short, "
    "plain sentences meant to be spoken aloud. Never read out passwords, card numbers, "
    "one-time codes or other secrets you notice; say that something private is visible instead."
)


class ScreenError(RuntimeError):
    pass


def snap_rect(work: tuple[int, int, int, int], position: str) -> tuple[int, int, int, int]:
    """Where a window goes inside a monitor's work area, as x, y, width, height."""
    left, top, right, bottom = work
    width, height = right - left, bottom - top
    half_w, half_h = width // 2, height // 2
    rects = {
        "left": (left, top, half_w, height),
        "right": (left + half_w, top, width - half_w, height),
        "top-left": (left, top, half_w, half_h),
        "top-right": (left + half_w, top, width - half_w, half_h),
        "bottom-left": (left, top + half_h, half_w, height - half_h),
        "bottom-right": (left + half_w, top + half_h, width - half_w, height - half_h),
    }
    if position not in rects:
        raise ScreenError(f"Unknown position {position!r}.")
    return rects[position]


def snap_window(title: str, position: str, screen: str = "current") -> dict[str, Any]:
    """Move a window, with Atticus's window matching and multi-display placement
    (see winctl.py): by app, process or title, onto the laptop's panel, the
    external monitor or the display it is on now."""
    from friday_services import winctl

    if position not in POSITIONS:
        raise ScreenError(f"Unknown position {position!r}.")
    try:
        return winctl.place(title, position, screen)
    except LookupError as error:
        raise ScreenError(str(error)) from error


def capture_screen(max_width: int = 1280) -> str:
    """The whole screen as a base64 JPEG, scaled down to keep the request small."""
    from PIL import ImageGrab

    image = ImageGrab.grab(all_screens=False).convert("RGB")
    if image.width > max_width:
        image = image.resize((max_width, round(image.height * max_width / image.width)))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=70)
    return base64.b64encode(buffer.getvalue()).decode()


@counted("Tools", "Screen description", "look at screen (vision model)")
def describe_screen(
    image_b64: str,
    question: str,
    env: Mapping[str, str],
    transport: httpx.BaseTransport | None = None,
) -> str:
    base_url = (env.get("EVON_BASE_URL") or "").rstrip("/")
    api_key = env.get("EVON_API_KEY") or ""
    model = env.get("FRIDAY_VISION_MODEL") or env.get("EVON_MODEL") or ""
    if not (base_url and api_key and model):
        raise ScreenError("Set EVON_BASE_URL, EVON_API_KEY and EVON_MODEL to look at the screen.")
    body = {
        "model": model,
        "max_tokens": 300,
        "messages": [
            {"role": "system", "content": LOOK_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                    },
                ],
            },
        ],
    }
    with httpx.Client(transport=transport, timeout=40) as client:
        response = client.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
        )
    if response.status_code == 429:
        raise ScreenError("The vision model is rate limiting requests; try again in a moment.")
    if response.status_code >= 400:
        raise ScreenError(f"The vision model returned HTTP {response.status_code}.")
    try:
        text = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as error:
        raise ScreenError("The vision model gave no answer.") from error
    return " ".join(str(text or "").split()) or "I could not make out the screen."
