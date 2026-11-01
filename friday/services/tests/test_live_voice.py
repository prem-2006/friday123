"""Friday's live voice pipeline, without WebRTC or Gnani: what reaches Gnani,
what the screen is told, and how the screen's messages are handled."""

from __future__ import annotations

import asyncio

import httpx
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    InterruptionFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    VADUserStartedSpeakingFrame,
)
from pipecat.tests.utils import run_test

from friday_services.live_voice import (
    AudioGate,
    HeardRelay,
    LiveVoiceSession,
    SpeakingRelay,
    language_of,
)

from .conftest import TOKEN

CHUNK = 320  # 10 ms of 16 kHz mono 16-bit audio


def audio(value: int = 1) -> InputAudioRawFrame:
    return InputAudioRawFrame(audio=bytes([value]) * CHUNK, sample_rate=16_000, num_channels=1)


def passed(gate: AudioGate, count: int):
    down, _ = asyncio.run(
        run_test(gate, frames_to_send=[audio() for _ in range(count)], expected_down_frames=None)
    )
    return [frame for frame in down if isinstance(frame, InputAudioRawFrame)]


def test_gate_sends_nothing_to_gnani_until_caps_lock_is_held():
    # A processor joins one pipeline only, so each run gets its own gate.
    assert passed(AudioGate(), 5) == []
    held = AudioGate()
    held.push_to_talk(True)
    assert len(passed(held, 5)) == 5


def test_gate_adds_a_second_of_silence_after_caps_lock_is_let_go():
    gate = AudioGate(tail_seconds=0.05)
    gate.push_to_talk(True)
    gate.push_to_talk(False)
    frames = passed(gate, 10)
    assert len(frames) == 5  # 50 ms of 10 ms frames
    assert all(frame.audio == bytes(CHUNK) for frame in frames)  # silence, not the mic


def test_hands_free_keeps_the_gate_open():
    gate = AudioGate()
    gate.hands_free = True
    assert len(passed(gate, 3)) == 3


def test_relays_tell_the_screen_what_was_heard_and_when_friday_speaks():
    sent: list[dict] = []
    gate = AudioGate()
    gate.hands_free = True
    asyncio.run(
        run_test(
            HeardRelay(sent.append, gate),
            frames_to_send=[
                VADUserStartedSpeakingFrame(),
                TranscriptionFrame(text=" open calculator ", user_id="", timestamp=""),
            ],
            expected_down_frames=None,
        )
    )
    asyncio.run(
        run_test(
            SpeakingRelay(sent.append),
            frames_to_send=[BotStartedSpeakingFrame(), BotStoppedSpeakingFrame()],
            expected_down_frames=None,
        )
    )
    assert [(m["event"], m.get("text"), m.get("on")) for m in sent] == [
        ("speech_start", None, None),
        ("heard", "open calculator", None),
        ("speaking", None, True),
        ("speaking", None, False),
    ]
    assert all(m["type"] == "friday-live" for m in sent)


class FakeTask:
    def __init__(self):
        self.frames = []

    async def queue_frame(self, frame):
        self.frames.append(frame)


def test_screen_messages_drive_the_pipeline():
    session = LiveVoiceSession(connection=None, api_key="k", language="en-IN", voice="Kaveri")
    task = FakeTask()
    session._task = task

    async def say(*messages):
        for message in messages:
            await session.on_message(message)

    asyncio.run(
        say(
            {"type": "ptt", "down": True},
            {"type": "speak", "text": "Calculator is open."},
            {"type": "interrupt"},
            {"type": "hands_free", "on": True},
            {"type": "unknown"},
            "not a dict",
        )
    )
    assert session.gate.held and session.gate.hands_free
    assert [type(frame) for frame in task.frames] == [
        InterruptionFrame,  # Caps Lock cuts Friday off
        TTSSpeakFrame,
        InterruptionFrame,
    ]
    assert task.frames[1].text == "Calculator is open."


def test_languages_fall_back_to_indian_english():
    assert language_of("hi-IN").value == "hi-IN"
    assert language_of("xx-YY").value == "en-IN"


def test_offer_needs_the_token_and_an_sdp(server):
    url = f"{server.url}/voice/offer"
    assert httpx.post(url, json={}).status_code == 401
    bad = httpx.post(url, json={"type": "offer"}, headers={"Authorization": f"Bearer {TOKEN}"})
    assert bad.status_code == 400


def test_routine_disconnects_are_not_shown_but_real_problems_are():
    from pipecat.frames.frames import ErrorFrame

    from friday_services.live_voice import is_routine

    assert is_routine("Error sending TTS request: received 1001 (going away) Idle timeout exceeded")
    assert is_routine("Gnani STT: Session closed: no speech segment received for 60 seconds.")
    assert not is_routine("Gnani: invalid API key")
    sent: list[dict] = []
    asyncio.run(
        run_test(
            HeardRelay(sent.append, AudioGate()),
            frames_to_send=[
                ErrorFrame(error="Error sending TTS request: received 1001 (going away)"),
                ErrorFrame(error="Gnani: invalid API key"),
            ],
            expected_down_frames=None,
        )
    )
    # Friday stops waiting to speak after a voice error; only the real one shows.
    assert [(m["event"], m.get("on"), m.get("message")) for m in sent] == [
        ("speaking", False, None),
        ("error", None, "Gnani: invalid API key"),
    ]


async def test_timbre_requests_take_turns_across_voice_windows():
    import asyncio

    from friday_services.live_voice import one_at_a_time

    active, most = 0, 0

    class FakeTTS:
        async def run_tts(self, text, context_id):
            nonlocal active, most
            active += 1
            most = max(most, active)
            await asyncio.sleep(0.02)
            yield text
            active -= 1

    tts = one_at_a_time(FakeTTS)

    async def say(text):
        return [frame async for frame in tts().run_tts(text, "c")]

    said = await asyncio.gather(say("one"), say("two"), say("three"))
    assert said == [["one"], ["two"], ["three"]] and most == 1


async def test_a_hey_friday_hold_starts_with_the_words_said_just_before():
    from pipecat.frames.frames import InputAudioRawFrame
    from pipecat.processors.frame_processor import FrameDirection

    from friday_services.live_voice import AudioGate

    gate = AudioGate()
    passed: list[bytes] = []

    async def push(frame, _direction):
        passed.append(frame.audio)

    gate.push_frame = push
    for n in range(3):  # 3 x 0.2 s before listening began ("play mu-")
        await gate.process_frame(
            InputAudioRawFrame(audio=bytes([n + 1]) * 6400, sample_rate=16000, num_channels=1),
            FrameDirection.DOWNSTREAM,
        )
    assert passed == []
    gate.preroll_next()
    gate.push_to_talk(True)
    now = InputAudioRawFrame(audio=bytes([9]) * 6400, sample_rate=16000, num_channels=1)
    await gate.process_frame(now, FrameDirection.DOWNSTREAM)
    assert [chunk[0] for chunk in passed] == [1, 2, 3, 9]
    # A Caps Lock hold gets no replay.
    passed.clear()
    gate.push_to_talk(False)
    gate._tail_left = 0
    gate.push_to_talk(True)
    await gate.process_frame(now, FrameDirection.DOWNSTREAM)
    assert [chunk[0] for chunk in passed] == [9]
