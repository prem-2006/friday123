"""A stop ends what is running, and never leaves Friday stopped for good."""

from __future__ import annotations

import friday_services.activity as activity
from friday_services.activity import ActivityFeed


def test_a_stop_clears_itself_when_the_resume_is_lost(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(activity.time, "monotonic", lambda: now[0])
    feed = ActivityFeed()
    feed.stop()
    assert feed.stopped
    now[0] += activity.STOP_HOLDS - 1
    assert feed.stopped
    now[0] += 2
    assert not feed.stopped  # no "resume" came: it lapsed
    feed.stop()
    feed.resume()
    assert not feed.stopped
