"""The daily briefing's words, and finding the owner's city."""

from __future__ import annotations

import json
from datetime import datetime

from friday_services.briefing import briefing_sentence, owner_city
from friday_services.organizer import Reminder

NOW = datetime(2026, 11, 4, 9, 30)


def due(text: str, when: datetime) -> Reminder:
    return Reminder(id=text, kind="reminder", text=text, due=when.timestamp())


def test_says_the_day_weather_what_is_due_and_the_mail():
    items = [
        due("DBMS project", datetime(2026, 11, 4, 17, 0)),
        due("call Ravi", datetime(2026, 11, 4, 19, 30)),
        due("rent", datetime(2026, 11, 6, 9, 0)),
    ]
    assert briefing_sentence(NOW, items, "Pune is 28 degrees.", 3) == (
        "Good morning. Today is Wednesday, 4 November. Pune is 28 degrees. "
        "Today you have DBMS project at 5:00 PM and call Ravi at 7:30 PM. "
        "You have 3 unread emails."
    )


def test_says_the_next_thing_when_nothing_is_due_today():
    items = [
        due("already over", datetime(2026, 11, 4, 8, 0)),
        due("rent", datetime(2026, 11, 6, 9, 0)),
    ]
    assert "Nothing is due today. Next is rent, on Friday." in briefing_sentence(NOW, items)


def test_leaves_out_what_it_could_not_find():
    said = briefing_sentence(NOW, [])
    assert said == "Good morning. Today is Wednesday, 4 November. You have no reminders today."


def test_counts_what_it_does_not_name_and_one_email():
    items = [due(f"task {i}", datetime(2026, 11, 4, 12 + i, 0)) for i in range(5)]
    said = briefing_sentence(NOW, items, unread=1)
    assert "task 0 at 12:00 PM, task 1 at 1:00 PM and task 2 at 2:00 PM" in said
    assert "There are 2 more after that." in said
    assert said.endswith("You have 1 unread email.")


def test_speaks_hinglish_when_asked():
    items = [due("DBMS project", datetime(2026, 11, 4, 17, 0))]
    said = briefing_sentence(NOW, items, unread=0, hinglish=True)
    assert "Aaj ke liye: DBMS project at 5:00 PM." in said
    assert said.endswith("Koi unread email nahi hai.")


def test_finds_the_city_on_the_main_page(tmp_path):
    path = tmp_path / "profile.json"
    assert owner_city(path) == ""
    path.write_text(json.dumps({"city": " Pune "}), encoding="utf-8")
    assert owner_city(path) == "Pune"
    path.write_text("not json", encoding="utf-8")
    assert owner_city(path) == ""
