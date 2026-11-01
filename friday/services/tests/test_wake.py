"""Caps Lock push-to-talk and double-clap wake, without a real keyboard or mic."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

from friday_services.activity import ActivityFeed
from friday_services.wake import (
    ClapCounter,
    Endpoint,
    Loudness,
    PushToTalkKey,
    WakeWord,
    has_wake_phrase,
)


def key(event_type: str):
    return SimpleNamespace(event_type=event_type)


def make_key():
    signals: list[str] = []
    sent: list[str] = []
    ptt = PushToTalkKey(
        signals.append, hook_key=lambda *a, **k: None, send=sent.append, hold_seconds=0.05
    )
    return ptt, signals, sent


def test_holding_caps_lock_talks_and_never_reaches_windows():
    ptt, signals, sent = make_key()
    assert ptt.on_event(key("down")) is False
    for _ in range(5):  # key repeat while held
        assert ptt.on_event(key("down")) is False
    time.sleep(0.15)
    assert signals == ["ptt_down"]
    assert ptt.on_event(key("up")) is False
    assert signals == ["ptt_down", "ptt_up"]
    time.sleep(0.05)
    assert sent == []  # Caps Lock state untouched


def test_a_quick_tap_is_still_caps_lock():
    ptt, signals, sent = make_key()
    ptt.on_event(key("down"))
    ptt.on_event(key("up"))
    time.sleep(0.15)
    assert signals == []
    assert sent == ["caps lock"]


def quiet(counter: ClapCounter, start: float, seconds: float) -> float:
    t = start
    while t < start + seconds:
        counter.feed(0.01, 0.003, t)
        t += 0.032
    return t


def settle(counter: ClapCounter, start: float, seconds: float) -> tuple[float, bool]:
    t, fired = start, False
    while t < start + seconds:
        fired |= counter.feed(0.01, 0.003, t)
        t += 0.032
    return t, fired


def test_two_claps_wake_once_the_room_is_quiet_again():
    counter = ClapCounter(min_peak=0.25)
    t = quiet(counter, 0, 1.0)
    assert counter.feed(0.8, 0.2, t) is False  # first clap
    t = quiet(counter, t + 0.032, 0.3)
    assert counter.feed(0.7, 0.2, t) is False  # second clap: wait for quiet
    t, fired = settle(counter, t + 0.032, 0.8)
    assert fired is True
    # Cooldown: claps right after do nothing.
    assert counter.feed(0.8, 0.2, t) is False


def test_one_clap_speech_and_typing_do_not_wake():
    single = ClapCounter(min_peak=0.25)
    t = quiet(single, 0, 1.0)
    single.feed(0.8, 0.2, t)
    t = quiet(single, t + 0.032, 1.5)  # too long a gap
    single.feed(0.8, 0.2, t)
    assert settle(single, t + 0.032, 1.0)[1] is False

    speech = ClapCounter(min_peak=0.25)
    t = quiet(speech, 0, 1.0)
    fired = False
    for n in range(60):  # loud, continuous speech
        fired |= speech.feed(0.6, 0.15, t + n * 0.032)
    assert fired is False

    typing = ClapCounter(min_peak=0.25)
    t, fired = quiet(typing, 0, 1.0), False
    for _ in range(12):  # key presses every 0.2 s
        fired |= typing.feed(0.7, 0.2, t)
        t = quiet(typing, t + 0.032, 0.17)
    assert fired is False
    assert settle(typing, t, 1.0)[1] is False


def test_signals_reach_the_newest_voice_window_only_and_are_not_kept():
    feed = ActivityFeed()
    watcher = feed.listen()  # e.g. a page that only shows activity
    assert feed.watched is False
    notch = feed.listen(voice=True)
    stage = feed.listen(voice=True)
    assert feed.watched is True
    feed.signal("ptt_down")
    event = stage.get_nowait()
    assert (event.actor, event.action) == ("control", "ptt_down")
    assert notch.empty() and watcher.empty()  # one request never runs twice
    feed.unlisten(stage)  # the voice screen closed: the notch takes over
    feed.signal("ptt_up")
    assert notch.get_nowait().action == "ptt_up"
    assert feed.recent() == []


class FakeRecognizer:
    """Says each scripted partial transcript in turn, like Vosk would."""

    def __init__(self, partials):
        self.partials = list(partials)
        self.resets = 0

    def AcceptWaveform(self, _pcm):
        return False

    def PartialResult(self):
        return json.dumps({"partial": self.partials.pop(0) if self.partials else ""})

    def Result(self):
        return "{}"

    def Reset(self):
        self.resets += 1


def blocks(word, rms, seconds, t):
    end = t + seconds
    while t < end:
        word.feed(b"", rms, t)
        t += 0.032
    return t


def test_hey_friday_listens_until_the_owner_stops_talking():
    signals: list[str] = []
    word = WakeWord(signals.append, FakeRecognizer(["", "hey", "hey friday"]))
    # The phrase, a moment to confirm it, then a pause: just the name.
    t = blocks(word, 0.005, 1.4, 0.0)
    assert signals == ["ptt_down"] and word.listening
    t = blocks(word, 0.2, 2.0, t)  # the request
    assert signals == ["ptt_down"]
    blocks(word, 0.004, 1.2, t)  # a pause ends it
    assert signals == ["ptt_down", "ptt_up"] and not word.listening


def test_saying_nothing_after_hey_friday_ends_quietly():
    signals: list[str] = []
    word = WakeWord(signals.append, FakeRecognizer(["hey friday"]), endpoint=Endpoint(no_speech=1))
    blocks(word, 0.004, 2.5, 0.0)
    assert signals == ["ptt_down", "ptt_up"]


def test_hey_friday_and_a_command_in_one_breath_runs_it_without_greeting():
    signals: list[str] = []
    greeted: list[bool] = []
    # The second listen heard the command too: listen at once, with the words before.
    word = WakeWord(
        signals.append, FakeRecognizer(["hey friday"]), confirm=lambda _pcm: "hey friday play music"
    )
    word.on_wake = lambda: greeted.append(True) or True
    blocks(word, 0.005, 0.6, 0.0)
    assert signals == ["ptt_down_preroll"] and greeted == []
    # Or the owner goes on talking right after the name.
    signals.clear()
    word = WakeWord(
        signals.append, FakeRecognizer(["hey friday"]), confirm=lambda _pcm: "hey friday"
    )
    word.on_wake = lambda: greeted.append(True) or True
    t = blocks(word, 0.005, 0.6, 0.0)
    blocks(word, 0.2, 0.3, t)
    assert signals == ["ptt_down_preroll"] and greeted == []


def test_only_the_phrase_wakes_friday_and_not_while_caps_lock_is_held():
    assert has_wake_phrase("Okay, Friday!")
    assert not has_wake_phrase("see you on friday")
    assert not has_wake_phrase("hey fridays")
    signals: list[str] = []
    word = WakeWord(signals.append, FakeRecognizer(["hey friday"]), busy=lambda: True)
    word.feed(b"", 0.01, 0.0)
    assert signals == []


def test_a_double_clap_starts_listening_even_before_the_model_loads():
    signals: list[str] = []
    word = WakeWord(signals.append, None, endpoint=Endpoint(no_speech=1))
    word.feed(b"", 0.004, 0.0)  # no model yet: nothing to hear
    word.trigger()
    t = blocks(word, 0.2, 0.5, 0.032)
    assert signals == ["ptt_down"]
    blocks(word, 0.004, 1.2, t)
    assert signals == ["ptt_down", "ptt_up"]


def test_waking_greets_first_then_listens_when_the_greeting_is_done():
    signals: list[str] = []
    greeted: list[bool] = []
    word = WakeWord(signals.append, FakeRecognizer(["hey friday"]), endpoint=Endpoint(no_speech=1))
    word.on_wake = lambda: greeted.append(True) or True
    t = blocks(word, 0.005, 1.4, 0.0)  # "Hey Friday", then a pause: just the name
    t = blocks(word, 0.2, 1.0, t)  # Friday's own greeting is not a request
    assert greeted == [True] and signals == []
    word.trigger()  # the screen has spoken the greeting
    blocks(word, 0.004, 1.5, t)
    assert signals == ["ptt_down", "ptt_up"]


def test_a_greeting_the_screen_never_finishes_still_leads_to_listening():
    signals: list[str] = []
    word = WakeWord(signals.append, None, endpoint=Endpoint(no_speech=1))
    word.on_wake = lambda: True
    word.GREETING_WAIT = 0.5
    word.wake()
    blocks(word, 0.004, 0.8, 0.0)
    assert signals == ["ptt_down"]


def test_a_noise_the_second_listen_does_not_confirm_is_ignored():
    signals: list[str] = []
    word = WakeWord(signals.append, FakeRecognizer(["hey friday"]), confirm=lambda _pcm: False)
    blocks(word, 0.005, 1.0, 0.0)
    assert signals == []
    heard: list[bytes] = []
    word = WakeWord(
        signals.append,
        FakeRecognizer(["hey friday"]),
        confirm=lambda pcm: heard.append(pcm) or True,
    )
    blocks(word, 0.005, 1.4, 0.0)
    assert signals == ["ptt_down"] and heard


def test_a_soft_voice_is_turned_up_but_silence_is_not():
    loud = Loudness()
    assert loud.gain(0.0005) == 1.0
    soft = [loud.gain(0.01) for _ in range(200)][-1]
    assert 5.0 <= soft <= 6.0
    assert Loudness().gain(0.2) == 1.0


def test_close_spellings_of_friday_still_count():
    from friday_services.wake import sounds_like_friday

    for heard in ("hey friday", "a fried", "fri day", "hey frida", "freddy"):
        assert sounds_like_friday(heard), heard
    for heard in ("hey brother", "right", "okay", "", "hey priya"):
        assert not sounds_like_friday(heard), heard


def test_ears_use_the_voice_windows_mic_and_one_window_only():
    import numpy as np

    from friday_services.wake import BLOCK, Ears

    heard: list[float] = []
    ears = Ears([lambda block, peak, rms, now: heard.append(round(peak, 2))])
    ears.from_mic(np.zeros(BLOCK, "float32"))  # no window yet: the services' mic counts
    assert heard == [0.0]
    loud = (np.full(BLOCK * 3, 0.5) * 32767).astype("<i2").tobytes()  # 48 kHz, 1 block at 16 kHz
    ears.from_live("notch", loud, 48_000)
    assert heard[1:] == [0.5]
    ears.from_live("browser tab", loud, 48_000)  # a second window is ignored
    ears.from_mic(np.zeros(BLOCK, "float32"))  # and so is the silent mic, while the notch sends
    assert heard[1:] == [0.5]
    ears.from_live("notch", (np.full(BLOCK, 0.25) * 32767).astype("<i2").tobytes(), 16_000)
    assert heard[1:] == [0.5, 0.25]
