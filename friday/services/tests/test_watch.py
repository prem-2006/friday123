"""Watch for me (watch.py): a pretend window, checked by hand instead of a timer."""

from __future__ import annotations

import pytest

from friday_services.watch import Watches


def make(screen: dict[str, str | None], give_up: float = 3600):
    said: list[str] = []
    watches = Watches(said.append, read=lambda app, _words: screen.get(app), give_up=give_up)
    watches._thread = object()  # tests call check() themselves; no background thread
    return watches, said


def test_tells_the_owner_when_a_phrase_shows():
    screen = {"Chrome": "Downloads\nsetup.exe 40%"}
    watches, said = make(screen)
    watches.add("Chrome", "shows", "Completed")
    watches.check()
    assert said == []
    screen["Chrome"] = "Downloads\nsetup.exe Completed"
    watches.check()
    assert said == ['Chrome now shows "Completed".']
    assert watches.list() == []


def test_window_closing_and_a_custom_sentence():
    screen = {"Zoom": "Meeting"}
    watches, said = make(screen)
    watches.add("Zoom", "gone", say="Your meeting has ended.")
    screen["Zoom"] = None
    watches.check()
    assert said == ["Your meeting has ended."]


def test_refuses_what_cannot_be_watched():
    watches, _ = make({"Chrome": "Build succeeded"})
    with pytest.raises(LookupError):
        watches.add("Ghost", "gone")
    with pytest.raises(ValueError):
        watches.add("Chrome", "shows", "succeeded")  # already there


def test_gives_up_after_a_while_and_stop_all():
    watches, said = make({"Chrome": "waiting"}, give_up=-1)
    watches.add("Chrome", "shows", "done")
    watches.check()
    assert said and "stopped watching" in said[0]
    watches.add("Chrome", "shows", "done")
    assert watches.stop_all() == 1
