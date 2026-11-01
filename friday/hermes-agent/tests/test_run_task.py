"""Navigator's Hermes runner, without Hermes or a model."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from run_task import model_settings, step_event  # noqa: E402


def test_model_comes_from_friday_settings_most_specific_first():
    env = {"EVON_BASE_URL": "https://evon/v1", "EVON_API_KEY": "k", "EVON_MODEL": "evon"}
    assert model_settings(env) == {"base_url": "https://evon/v1", "api_key": "k", "model": "evon"}
    env |= {"FRIDAY_HERMES_MODEL": "gemini-flash"}
    assert model_settings(env)["model"] == "gemini-flash"
    with pytest.raises(ValueError):
        model_settings({})


def test_tool_calls_become_navigator_steps():
    step = step_event(2, "browser_navigate", {"url": "https://irctc.co.in"})
    assert step == {
        "type": "step",
        "n": 2,
        "goal": "navigate: https://irctc.co.in",
        "url": "https://irctc.co.in",
    }
    assert step_event(3, "browser_click", {"ref": "@e4"})["goal"] == "click: @e4"
