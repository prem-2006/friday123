"""Friday's live voice: a Pipecat pipeline that hears and speaks in real time.

The voice screen connects over WebRTC (audio both ways plus a data channel):

    mic -> AudioGate -> Silero VAD -> Gnani streaming STT -> HeardRelay
        -> Gnani streaming TTS (SSE) -> speaker -> SpeakingRelay

Speech goes out over Gnani's SSE stream, one request per reply: its WebSocket
variant closes after a quiet spell ("Idle timeout exceeded") and does not come
back, which left Friday unable to speak.

Friday's thinking stays on the voice screen and the app (the Friday Dot, its
quick actions and the helper Dots): this pipeline sends each final transcript
to the screen and speaks whatever text the screen sends back. While Friday
speaks, the owner talking (hands-free) or holding Caps Lock interrupts it.

Gnani is billed for audio it receives, so the gate passes mic audio only while
Caps Lock is held (plus a second of silence so Gnani closes the turn) or while
hands-free listening is on.

Data channel, screen -> pipeline:
    {"type": "ptt", "down": bool}        Caps Lock held / let go
    {"type": "hands_free", "on": bool}   orb tapped on / off
    {"type": "speak", "text": str}       say this
    {"type": "interrupt"}                stop talking now
Pipeline -> screen:
    {"type": "friday-live", "event": "heard", "text": str}
    {"type": "friday-live", "event": "speech_start"}
    {"type": "friday-live", "event": "speaking", "on": bool}
    {"type": "friday-live", "event": "error", "message": str}
"""

from __future__ import annotations

import asyncio
import collections
import time
from collections.abc import Callable
from typing import Any

import numpy as np
from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    VADUserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from friday_services.usage import usage

TAIL_SECONDS = 1.0
PREROLL_SECONDS = 1.5  # audio kept from before a "Hey Friday" hold began  # silence after Caps Lock is let go, so Gnani ends the turn
TTS_MODEL = "timbre-v2.5"
# Gnani allows one Timbre request at a time per key. Every voice window has its
# own pipeline, so they take turns here instead of Gnani refusing one (429).
_ONE_VOICE: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}


def one_voice_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    return _ONE_VOICE.setdefault(loop, asyncio.Lock())


def one_at_a_time(service_class):
    """A TTS service class whose requests wait for each other."""

    class OneAtATime(service_class):
        async def run_tts(self, text: str, context_id: str):
            async with one_voice_lock():
                async for frame in super().run_tts(text, context_id):
                    yield frame

    return OneAtATime


Send = Callable[[dict[str, Any]], None]


def live_event(event: str, **fields: Any) -> dict[str, Any]:
    return {"type": "friday-live", "event": event, **fields}


# Gnani closes quiet connections ("no speech segment received for 60 seconds",
# "Idle timeout exceeded") and they reconnect on the next use: routine, not
# something to show the owner.
ROUTINE_ERRORS = ("idle timeout", "session closed", "1001", "going away", "no speech segment")


def is_routine(error: str) -> bool:
    text = error.casefold()
    return any(marker in text for marker in ROUTINE_ERRORS)


class AudioGate(FrameProcessor):
    """Lets mic audio through only while the owner means to talk to Friday."""

    def __init__(self, tail_seconds: float = TAIL_SECONDS, **kwargs):
        super().__init__(**kwargs)
        self.held = False
        self.hands_free = False
        self._tail_seconds = tail_seconds
        self._tail_left = 0.0
        self._held_audio = 0.0
        self._loudest = 0
        # Every mic frame, open or not, for "Hey Friday" and claps (wake.Ears).
        self.tap: Callable[[bytes, int], None] | None = None
        # The last moments of mic audio. "Hey Friday, play music" said in one
        # breath: listening starts a moment after "play" began, so the next
        # hold that "Hey Friday" opens starts with these (preroll).
        self._recent: collections.deque[InputAudioRawFrame] = collections.deque()
        self._recent_seconds = 0.0
        self._preroll_until = 0.0
        self._replay: list[InputAudioRawFrame] = []

    def preroll_next(self, within: float = 3.0) -> None:
        """The next hold (opened by "Hey Friday") also gets the recent audio."""
        self._preroll_until = time.monotonic() + within

    def _remember(self, frame: InputAudioRawFrame) -> None:
        self._recent.append(frame)
        self._recent_seconds += frame.num_frames / frame.sample_rate
        while self._recent and self._recent_seconds > PREROLL_SECONDS:
            old = self._recent.popleft()
            self._recent_seconds -= old.num_frames / old.sample_rate

    @property
    def open(self) -> bool:
        return self.held or self.hands_free

    def push_to_talk(self, down: bool) -> None:
        if self.held and not down:
            self._tail_left = self._tail_seconds
            # One line per hold, so a press Friday did not hear can be traced:
            # no audio at all, or only silence (a mic unplugged or switched).
            logger.info(
                "Caps Lock: {:.1f} s of mic audio, loudest {:.2f} (0 is silence)",
                self._held_audio,
                self._loudest / 32768,
            )
        if down and not self.held:
            self._held_audio = 0.0
            self._loudest = 0
            if time.monotonic() < self._preroll_until:
                self._replay = list(self._recent)
            self._preroll_until = 0.0
        self.held = down

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            if self.tap is not None and frame.audio:
                try:
                    self.tap(frame.audio, frame.sample_rate)
                except Exception:
                    logger.exception("wake audio tap failed")
            if self.open:
                replay, self._replay = self._replay, []
                for earlier in replay:  # the words said just before listening began
                    await self.push_frame(earlier, direction)
                if self.held and frame.audio:
                    self._held_audio += frame.num_frames / frame.sample_rate
                    samples = np.frombuffer(frame.audio, dtype=np.int16).astype(np.int32)
                    self._loudest = max(self._loudest, int(np.abs(samples).max()))
                await self.push_frame(frame, direction)
            elif self._tail_left > 0:
                self._remember(frame)
                seconds = frame.num_frames / frame.sample_rate
                self._tail_left -= seconds
                await self.push_frame(
                    InputAudioRawFrame(
                        audio=bytes(len(frame.audio)),
                        sample_rate=frame.sample_rate,
                        num_channels=frame.num_channels,
                    ),
                    direction,
                )
            else:
                self._remember(frame)
            return
        await self.push_frame(frame, direction)


class HeardRelay(FrameProcessor):
    """Sends each final transcript to the screen; a voice heard while
    hands-free interrupts Friday at once."""

    def __init__(self, send: Send, gate: AudioGate, **kwargs):
        super().__init__(**kwargs)
        self._send = send
        self._gate = gate

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            usage.record("Speech to text", "Gnani Prisma", "live voice (Caps Lock)", ok=True)
            self._send(live_event("heard", text=frame.text.strip()))
        elif isinstance(frame, VADUserStartedSpeakingFrame) and self._gate.hands_free:
            self._send(live_event("speech_start"))
        elif isinstance(frame, ErrorFrame):
            message = str(frame.error)
            speaking = "tts" in message.casefold() or "speech" in message.casefold()
            if speaking:
                # Friday could not speak: the screen must not wait for it.
                self._send(live_event("speaking", on=False))
            if not is_routine(message):
                usage.record(
                    "Voice" if speaking else "Speech to text",
                    "Gnani Timbre" if speaking else "Gnani Prisma",
                    "live voice (Caps Lock)",
                    ok=False,
                    error=message,
                )
                self._send(live_event("error", message=message[:300]))
        await self.push_frame(frame, direction)


class SpeakingRelay(FrameProcessor):
    """Tells the screen when Friday's voice starts and stops playing."""

    def __init__(self, send: Send, **kwargs):
        super().__init__(**kwargs)
        self._send = send

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame):
            usage.record("Voice", "Gnani Timbre", "live voice (Caps Lock)", ok=True)
            self._send(live_event("speaking", on=True))
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._send(live_event("speaking", on=False))
        await self.push_frame(frame, direction)


def language_of(code: str):
    from pipecat.transcriptions.language import Language

    try:
        return Language(code)
    except ValueError:
        return Language.EN_IN


class LiveVoiceSession:
    """One voice screen's pipeline, from WebRTC connect to disconnect."""

    def __init__(self, connection: Any, api_key: str, language: str, voice: str):
        self._connection = connection
        self._api_key = api_key
        self._language = language
        self._voice = voice
        self.gate = AudioGate()
        self._task: Any = None

    def send(self, message: dict[str, Any]) -> None:
        try:
            self._connection.send_app_message(message)
        except Exception:
            logger.debug("voice screen gone; message dropped")

    async def on_message(self, message: Any) -> None:
        if not isinstance(message, dict) or self._task is None:
            return
        kind = message.get("type")
        if kind == "ptt":
            down = bool(message.get("down"))
            if down:
                await self._task.queue_frame(InterruptionFrame())
            self.gate.push_to_talk(down)
        elif kind == "hands_free":
            self.gate.hands_free = bool(message.get("on"))
        elif kind == "speak":
            text = str(message.get("text") or "").strip()
            if text:
                await self._task.queue_frame(TTSSpeakFrame(text[:2000]))
        elif kind == "interrupt":
            await self._task.queue_frame(InterruptionFrame())

    async def run(self) -> None:
        import aiohttp
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from pipecat.audio.vad.vad_analyzer import VADParams
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.pipeline.task import PipelineParams, PipelineTask
        from pipecat.processors.audio.vad_processor import VADProcessor
        from pipecat.transports.base_transport import TransportParams
        from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
        from pipecat_gnani import GnaniSSETTSService, GnaniSTTService

        transport = SmallWebRTCTransport(
            webrtc_connection=self._connection,
            params=TransportParams(audio_in_enabled=True, audio_out_enabled=True),
        )
        stt = GnaniSTTService(
            api_key=self._api_key,
            settings=GnaniSTTService.Settings(language=language_of(self._language)),
        )
        http = aiohttp.ClientSession()
        tts = one_at_a_time(GnaniSSETTSService)(
            api_key=self._api_key, aiohttp_session=http, voice_id=self._voice, model=TTS_MODEL
        )
        vad = VADProcessor(
            vad_analyzer=SileroVADAnalyzer(params=VADParams(start_secs=0.25, stop_secs=0.5))
        )
        pipeline = Pipeline(
            [
                transport.input(),
                self.gate,
                vad,
                stt,
                HeardRelay(self.send, self.gate),
                tts,
                transport.output(),
                SpeakingRelay(self.send),
            ]
        )
        self._task = PipelineTask(
            pipeline,
            params=PipelineParams(audio_in_sample_rate=16_000, audio_out_sample_rate=24_000),
            idle_timeout_secs=None,
            # The screen speaks Friday's own messages (see the module notes), not RTVI.
            enable_rtvi=False,
        )

        @self._connection.event_handler("app-message")
        async def on_app_message(_connection, message):
            await self.on_message(message)

        @self._connection.event_handler("closed")
        async def on_closed(_connection):
            if self._task:
                await self._task.cancel()

        try:
            await PipelineRunner(handle_sigint=False).run(self._task)
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
            await http.close()
