"""A reminder call opens with the reminder itself, not "How can I help?"."""

from __future__ import annotations

from friday_services.calls.store import CallStore
from friday_services.config import Settings
from friday_services.owner_calls import OwnerCaller


class FakeAgent:
    ready = True

    def __init__(self):
        self.calls: list[dict] = []

    async def call(self, **fields):
        self.calls.append(fields)
        return {"ok": True}


class NoPolish:
    ready = False

    async def greeting(self, note):
        raise AssertionError("an exact opening must not be reworded by the model")


async def test_reminder_calls_open_with_the_reminder(tmp_path):
    settings = Settings.from_env(
        {"FRIDAY_OWNER_NAME": "Prem", "FRIDAY_OWNER_PHONE": "+919876543210"}, data_dir=tmp_path
    )
    agent = FakeAgent()
    caller = OwnerCaller(settings, CallStore(tmp_path), None, gnani=NoPolish(), sarvam=agent)
    placed = await caller.call(
        "Reminder: drink water.", urgent=True, opening="Hi Prem, you have a reminder: drink water."
    )
    assert placed["called"] is not False
    assert agent.calls[0]["greeting"] == "Hi Prem, you have a reminder: drink water."
