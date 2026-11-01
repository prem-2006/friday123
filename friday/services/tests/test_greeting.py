"""The wake greeting: hello, and an offer that fits the window in front."""

from __future__ import annotations

from friday_services.greeting import screen_greeting, short_title


def first(options):
    return options[0]


def test_greets_about_what_is_on_screen():
    said = screen_greeting(
        "Google Chrome", "Two Sum - LeetCode - Google Chrome", "Prem Kumar", first
    )
    assert said == (
        "Hi Prem! You're on Two Sum in Google Chrome. "
        "Want me to explain this problem or help solve it?"
    )
    code = screen_greeting("Visual Studio Code", "main.py - friday - Visual Studio Code", "", first)
    assert code.endswith("Need help with this code?")
    assert "main.py" in code


def test_plain_hello_when_nothing_useful_is_in_front():
    assert screen_greeting("", "", "", first) == "Hi! What can I do for you?"
    assert screen_greeting("Friday", "Friday", "", first) == "Hi! What can I do for you?"
    assert screen_greeting("Paint", "Untitled - Paint", "", first) == (
        "Hi! You're on Untitled in Paint. What can I do for you?"
    )


def test_long_titles_are_cut_short():
    title = "one two three four five six seven eight nine ten - Google Chrome"
    assert short_title(title, "Google Chrome") == "one two three four five six seven eight…"
