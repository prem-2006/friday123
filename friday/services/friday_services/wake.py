"""Ways to reach Friday without touching the screen.

Push-to-talk: hold Caps Lock, speak, let go. A quick tap still toggles Caps
Lock as usual; only a hold counts. Ctrl + Caps Lock hides or shows the notch. Works in any app, through a low-level
keyboard hook that acts on Caps Lock alone.

Double clap: two sharp claps within about half a second open Friday's voice
screen if it is not open. (Friday hears only while Caps Lock is held.) The idea comes from
Julian Ivanov's jarvis-voice-assistant (MIT); here it is a small detector on
16 kHz mic blocks. Only loudness peaks are examined; no audio is kept or sent.

"Hey Friday": say it anywhere and Friday listens until you stop talking, as
if Caps Lock were held. Like Atticus's opt-in wake word
(SaarthurR/atticus-mac, MIT), it runs on this PC: a small offline Vosk model
listens only for the phrase, and no audio is kept or sent. The request itself
still goes to Gnani, as with a Caps Lock hold.

All of them send signals to the voice screen over the activity stream.
"""

from __future__ import annotations

import collections
import ctypes
import json
import logging
import queue
import re
import threading
import time
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("friday.wake")

HOLD_SECONDS = 0.3  # shorter presses are ordinary Caps Lock taps
VK_CONTROL = 0x11


def ctrl_is_down() -> bool:
    """Whether Ctrl is down right now, asked of Windows. The keyboard
    library's own record can stay "pressed" after a missed key release, and
    then every Caps Lock press hid the notch instead of listening."""
    return bool(ctypes.windll.user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)


class PushToTalkKey:
    def __init__(
        self,
        signal: Callable[[str], None],
        hook_key: Callable[..., Any] | None = None,
        send: Callable[[str], None] | None = None,
        hold_seconds: float = HOLD_SECONDS,
        ctrl_held: Callable[[], bool] | None = None,
    ):
        self._ctrl_held = ctrl_held
        self._toggling = False
        self._signal = signal
        self._hook_key = hook_key
        self._send = send
        self._hold = hold_seconds
        self._down_at: float | None = None
        self._talking = False
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._remove: Callable[[], None] | None = None

    def start(self) -> None:
        if self._hook_key is None or self._send is None:
            import keyboard

            self._hook_key = self._hook_key or keyboard.hook_key
            self._send = self._send or keyboard.send
            self._ctrl_held = self._ctrl_held or ctrl_is_down
        self._remove = self._hook_key("caps lock", self.on_event, suppress=True)

    @property
    def talking(self) -> bool:
        """Whether Caps Lock is held for talking right now."""
        return self._talking

    def stop(self) -> None:
        if self._remove:
            self._remove()
            self._remove = None

    def _held(self) -> None:
        with self._lock:
            if self._down_at is None or self._talking:
                return
            self._talking = True
        self._signal("ptt_down")

    def on_event(self, event: Any) -> bool:
        """Returns False so Windows never sees the real Caps Lock press."""
        if event.event_type == "down" and not self._down_at and not self._toggling:
            if self._ctrl_held and self._ctrl_held():
                # Ctrl + Caps Lock: hide or show the notch; nothing is heard.
                self._toggling = True
                self._signal("notch_toggle")
                return False
        if self._toggling:
            if event.event_type == "up":
                self._toggling = False
            return False
        if event.event_type == "down":
            with self._lock:
                if self._down_at is not None:
                    return False  # key repeat while held
                self._down_at = time.monotonic()
                self._timer = threading.Timer(self._hold, self._held)
                self._timer.daemon = True
                self._timer.start()
            return False
        with self._lock:
            if self._timer:
                self._timer.cancel()
            talking, self._talking, self._down_at = self._talking, False, None
        if talking:
            self._signal("ptt_up")
        elif self._send:
            # A tap: give Windows its Caps Lock back. Sent from another thread,
            # because injecting keys from inside the hook can stall it.
            threading.Thread(target=self._send, args=("caps lock",), daemon=True).start()
        return False


class ClapCounter:
    """Finds a double clap in a stream of mic blocks.

    A clap is a block whose peak is loud, many times the room's usual level,
    and which follows a quiet block: a short, sharp sound, unlike speech or
    music. Typing makes sharp sounds too, but in runs: so a pair counts only
    with quiet before the first clap and after the second (settle).
    """

    def __init__(
        self,
        min_peak: float = 0.3,
        over_floor: float = 9.0,
        gap: tuple[float, float] = (0.12, 0.7),
        cooldown: float = 2.0,
        quiet_before: float = 0.8,
        settle: float = 0.6,
    ):
        self.min_peak = min_peak
        self.over_floor = over_floor
        self.gap = gap
        self.cooldown = cooldown
        self.quiet_before = quiet_before
        self.settle = settle
        self.floor = 0.01
        self._was_loud = False
        self._last_clap: float | None = None
        self._last_sharp = -1e9  # any sharp sound, counted or not
        self._pending: float | None = None  # second clap, waiting for quiet
        self._fired_at = -1e9
        # What the last sharp sounds were taken for (GET /wake/status).
        self.history: collections.deque[dict] = collections.deque(maxlen=20)
        self.loudest = 0.0

    def _note(self, what: str, peak: float) -> None:
        self.history.append({"at": time.strftime("%H:%M:%S"), "peak": round(peak, 2), "was": what})

    def feed(self, peak: float, rms: float, now: float) -> bool:
        self.loudest = max(self.loudest * 0.999, peak)
        sharp = (
            peak >= self.min_peak and peak >= self.floor * self.over_floor and not self._was_loud
        )
        self._was_loud = rms >= self.floor * 4
        if not self._was_loud:
            self.floor = self.floor * 0.98 + max(rms, 1e-4) * 0.02
        if self._pending is not None:
            if sharp:  # a third sharp sound: typing or knocking, not a double clap
                self._pending, self._last_clap, self._last_sharp = None, None, now
                self._note("a third sound: not a double clap", peak)
                return False
            if now - self._pending >= self.settle:
                self._pending, self._fired_at = None, now
                self._note("double clap", peak)
                return True
            return False
        if not sharp:
            return False
        before, self._last_sharp = self._last_sharp, now
        if now - self._fired_at < self.cooldown:
            return False
        last = self._last_clap
        if last is not None and self.gap[0] <= now - last <= self.gap[1]:
            self._pending, self._last_clap = now, None
            self._note("second clap, waiting for quiet", peak)
            return False
        # A first clap needs quiet before it.
        quiet = now - before >= self.quiet_before
        self._last_clap = now if quiet else None
        self._note("first clap" if quiet else "ignored: another sound just before", peak)
        return False


class ClapListener:
    def __init__(self, on_double_clap: Callable[[], None], counter: ClapCounter | None = None):
        self._on_double_clap = on_double_clap
        self._counter = counter or ClapCounter()
        self._stream: Any = None

    def start(self) -> None:
        import numpy as np
        import sounddevice as sd

        def block(data, _frames, _time, _status) -> None:
            samples = data[:, 0]
            peak = float(np.max(np.abs(samples)))
            rms = float(np.sqrt(np.mean(samples * samples)))
            if self._counter.feed(peak, rms, time.monotonic()):
                try:
                    self._on_double_clap()
                except Exception:
                    log.exception("double clap handler failed")

        self._stream = sd.InputStream(
            samplerate=16_000, channels=1, blocksize=512, dtype="float32", callback=block
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


# ---------- "Hey Friday" ----------

WAKE_PHRASES = ("hey friday", "hi friday", "ok friday", "okay friday")
# How free-form recognition may spell "Friday" (Indian English model).
FRIDAY_SOUND = re.compile(r"\b(fri ?d[ae]y|fry ?day|fridays?|frida)\b")
RECENT_BLOCKS = 80  # about 2.5 s of audio for the confirming listen
# Vosk's small Indian-English model (about 36 MB), fetched once into data/.
WAKE_MODEL_NAME = "vosk-model-small-en-in-0.4"
WAKE_MODEL_URL = f"https://alphacephei.com/vosk/models/{WAKE_MODEL_NAME}.zip"
SAMPLE_RATE = 16_000
BLOCK = 512  # 32 ms


def has_wake_phrase(text: str) -> bool:
    words = re.sub(r"[^a-z ]", " ", text.lower())
    words = " " + re.sub(r"\s+", " ", words).strip() + " "
    return any(f" {phrase} " in words for phrase in WAKE_PHRASES)


def ensure_wake_model(folder: Path, url: str = WAKE_MODEL_URL) -> Path:
    """The model's folder, downloaded and unpacked the first time."""
    model = folder / WAKE_MODEL_NAME
    if (model / "conf").is_dir():
        return model
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / f"{WAKE_MODEL_NAME}.zip"
    print(f"Hey Friday: downloading the offline wake-word model (~36 MB) to {folder} …")
    urllib.request.urlretrieve(url, archive)
    with zipfile.ZipFile(archive) as zipped:
        root = folder.resolve()
        for name in zipped.namelist():  # nothing may land outside the folder
            if not (folder / name).resolve().is_relative_to(root):
                raise RuntimeError("Unexpected path in the wake-word model archive.")
        zipped.extractall(folder)
    archive.unlink(missing_ok=True)
    return model


def sounds_like_friday(text: str) -> bool:
    """Whether free-form recognition heard something close to "Friday": accents
    and a soft voice give "fryday", "fried", "frida", "fri day"."""
    import difflib

    if FRIDAY_SOUND.search(text):
        return True
    words = text.lower().split()
    pieces = words + ["".join(pair) for pair in zip(words, words[1:], strict=False)]
    return any(difflib.SequenceMatcher(None, piece, "friday").ratio() >= 0.6 for piece in pieces)


def words_after_friday(text: str) -> list[str]:
    """The words said after "Friday" ("hey friday play music" -> play, music)."""
    words = text.lower().split()
    for i, word in enumerate(words):
        if sounds_like_friday(word):
            return words[i + 1 :]
    return []


class Endpoint:
    """After the wake phrase: is the owner still talking? Speech is a block well
    above the room's level; the request ends after a pause."""

    def __init__(
        self,
        pause: float = 1.0,
        no_speech: float = 4.0,
        longest: float = 15.0,
        over_floor: float = 3.0,
    ):
        self.pause = pause
        self.no_speech = no_speech
        self.longest = longest
        self.over_floor = over_floor
        self.floor = 0.01
        self.started_at = 0.0
        self.spoke_at: float | None = None
        self.last_voice = 0.0

    def begin(self, now: float, floor: float) -> None:
        self.started_at, self.spoke_at, self.last_voice = now, None, now
        self.floor = max(floor, 0.002)

    def feed(self, rms: float, now: float) -> bool:
        """True when the request is over."""
        if rms >= max(self.floor * self.over_floor, 0.008):
            self.spoke_at = self.spoke_at or now
            self.last_voice = now
        if now - self.started_at >= self.longest:
            return True
        if self.spoke_at is None:
            return now - self.started_at >= self.no_speech
        return now - self.last_voice >= self.pause


class WakeWord:
    """Listens for "Hey Friday", then signals ptt_down and, when the owner stops
    talking, ptt_up: the same signals as holding Caps Lock."""

    def __init__(
        self,
        signal: Callable[[str], None],
        recognizer: Any = None,
        busy: Callable[[], bool] = lambda: False,
        endpoint: Endpoint | None = None,
        cooldown: float = 1.5,
        confirm: Callable[[bytes], bool | str] | None = None,
    ):
        self._signal = signal
        # A second, free-form listen over the last seconds of audio: the
        # phrase-only recognizer maps any sound to its closest phrase, so on
        # its own it wakes on noise.
        self._confirm = confirm
        self._recent: collections.deque[bytes] = collections.deque(maxlen=RECENT_BLOCKS)
        # The last wake attempts and what was heard (GET /wake/status).
        self.history: collections.deque[dict] = collections.deque(maxlen=20)
        self._recognizer = recognizer
        self._busy = busy
        self._endpoint = endpoint or Endpoint()
        self._cooldown = cooldown
        self._listening = False
        self._ended_at = -1e9
        self._floor = 0.01
        self._blocks: queue.Queue = queue.Queue(maxsize=200)
        self._thread: threading.Thread | None = None

    @staticmethod
    def vosk_recognizer(model_folder: Path):
        import vosk

        vosk.SetLogLevel(-1)
        grammar = json.dumps([*WAKE_PHRASES, "friday", "[unk]"])
        return vosk.KaldiRecognizer(vosk.Model(str(model_folder)), SAMPLE_RATE, grammar)

    @staticmethod
    def vosk_confirmer(model_folder: Path) -> Callable[[bytes], str]:
        """What free-form recognition makes of the recent audio (sounds_like_friday
        then decides)."""
        import vosk

        model = vosk.Model(str(model_folder))

        def heard(pcm: bytes) -> str:
            recognizer = vosk.KaldiRecognizer(model, SAMPLE_RATE)
            recognizer.AcceptWaveform(pcm)
            return json.loads(recognizer.FinalResult()).get("text", "")

        return heard

    @property
    def listening(self) -> bool:
        return self._listening

    def use(self, recognizer: Any, confirm: Callable[[bytes], bool | str] | None = None) -> None:
        """Start hearing the phrase once its model has loaded; until then a
        double clap (trigger) still starts a request."""
        self._recognizer = recognizer
        if confirm is not None:
            self._confirm = confirm

    def trigger(self) -> None:
        """Listen now (Friday has finished its greeting)."""
        self._triggered = True

    def wake(self) -> None:
        """As if "Hey Friday" was said (a double clap)."""
        self._woken = True

    _triggered = False
    _woken = False
    _greeting_since: float | None = None
    # Friday greets first (on_wake); if the screen never says the greeting is
    # over, listen anyway after this long.
    GREETING_WAIT = 10.0

    def _woke(self, now: float) -> None:
        if self._recognizer is not None:
            self._recognizer.Reset()
        if self._busy() or self._greeting_since is not None:
            return
        if self.on_wake is not None and self.on_wake():
            self._greeting_since = now  # listen once the greeting is spoken (trigger)
        else:
            self._begin(now)

    on_wake: Callable[[], bool] | None = None

    def _begin(self, now: float, command: bool = False) -> None:
        """Listen. command: the owner is already saying it ("Hey Friday, play
        music"), so the hold also gets the audio from just before (preroll)."""
        self._greeting_since = None
        self._deciding_at = None
        if self._recognizer is not None:
            self._recognizer.Reset()
        if self._busy():  # Caps Lock is already held
            return
        self._listening = True
        self._endpoint.begin(now, self._floor)
        self._signal("ptt_down_preroll" if command else "ptt_down")

    # After "Hey Friday": more speech at once means a command (run it, no
    # greeting); quiet means just the name (greet, then listen).
    _deciding_at: float | None = None
    DECIDE_WITHIN = 0.7
    DECIDE_GRACE = 0.12  # the end of "Friday" itself is not the command

    def feed(self, pcm16: bytes, rms: float, now: float) -> None:
        """One mic block. Called on the wake-word thread (see start), or by tests."""
        if self._listening:
            self._triggered = False
            if self._endpoint.feed(rms, now):
                self._listening = False
                self._ended_at = now
                if self._recognizer is not None:
                    self._recognizer.Reset()
                self._signal("ptt_up")
            return
        if self._triggered or (
            self._greeting_since is not None and now - self._greeting_since > self.GREETING_WAIT
        ):
            self._triggered = False
            self._begin(now)
            return
        if self._greeting_since is not None:
            return  # Friday is greeting; its own voice is not a request
        if self._deciding_at is not None:
            waited = now - self._deciding_at
            if waited > self.DECIDE_GRACE and rms >= max(self._floor * 3, 0.008):
                print("Wake: Hey Friday + a command")
                self._begin(now, command=True)
            elif waited >= self.DECIDE_WITHIN:
                self._deciding_at = None
                self._woke(now)
            return
        if self._woken:
            self._woken = False
            self._woke(now)
            return
        self._floor = self._floor * 0.98 + max(rms, 1e-4) * 0.02
        recognizer = self._recognizer
        self._recent.append(pcm16)
        if self._confirm_at is not None:
            # The phrase was heard; check once the word has been said in full.
            if now < self._confirm_at:
                return
            self._confirm_at = None
            if recognizer is not None:
                recognizer.Reset()
            heard: bool | str = True
            if self._confirm is not None:
                heard = self._confirm(b"".join(self._recent))
            ok = sounds_like_friday(heard) if isinstance(heard, str) else bool(heard)
            self.history.append({"at": time.strftime("%H:%M:%S"), "heard": heard, "woke": ok})
            if not ok:  # a noise like the phrase
                print(f"Wake: not confirmed (the second listen heard {heard!r})")
                return
            if isinstance(heard, str) and words_after_friday(heard):
                print("Wake: Hey Friday + a command")
                self._begin(now, command=True)  # "hey friday play music", said as one
            else:
                print("Wake: Hey Friday")
                self._deciding_at = now
            return
        if recognizer is None or now - self._ended_at < self._cooldown:
            return
        final = recognizer.AcceptWaveform(pcm16)
        said = json.loads(recognizer.Result() if final else recognizer.PartialResult())
        text = said.get("text") or said.get("partial") or ""
        if has_wake_phrase(text):
            self._confirm_at = now + self.CONFIRM_AFTER

    _confirm_at: float | None = None
    # Partial results name the phrase while "Friday" is still being said.
    CONFIRM_AFTER = 0.4

    def offer(self, pcm16: bytes, rms: float, now: float) -> None:
        """From the audio callback: hand the block to the wake-word thread."""
        try:
            self._blocks.put_nowait((pcm16, rms, now))
        except queue.Full:
            pass  # the recognizer fell behind; drop audio rather than lag

    def start(self) -> None:
        def work() -> None:
            while True:
                item = self._blocks.get()
                if item is None:
                    return
                try:
                    self.feed(*item)
                except Exception:
                    log.exception("wake word check failed")

        self._thread = threading.Thread(target=work, name="friday-wake-word", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._blocks.put(None)


class Microphone:
    """One mic stream shared by the clap detector and the wake word."""

    def __init__(self, consumers: list[Callable[[Any, float, float, float], None]]):
        self._consumers = consumers
        self._stream: Any = None

    def start(self) -> None:
        import numpy as np
        import sounddevice as sd

        def block(data, _frames, _time, _status) -> None:
            samples = data[:, 0]
            peak = float(np.max(np.abs(samples)))
            rms = float(np.sqrt(np.mean(samples * samples)))
            now = time.monotonic()
            for consume in self._consumers:
                try:
                    consume(samples, peak, rms, now)
                except Exception:
                    log.exception("mic consumer failed")

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, blocksize=BLOCK, dtype="float32", callback=block
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


def pcm16(samples, gain: float = 1.0) -> bytes:
    import numpy as np

    return (np.clip(samples * gain, -1.0, 1.0) * 32767).astype("<i2").tobytes()


class Loudness:
    """Turns a soft voice up for the wake word, like a phone's auto gain:
    the recent speech level is brought near a normal level, at most 6x, and
    silence is not boosted."""

    def __init__(self, target: float = 0.08, most: float = 6.0):
        self.target, self.most = target, most
        self.level = target

    def gain(self, rms: float) -> float:
        self.level = max(rms, self.level * 0.985, 1e-4)
        if rms < 0.002:
            return 1.0
        return min(self.most, max(1.0, self.target / self.level))


class Ears:
    """One stream of mic audio for the wake word and the clap detector.

    The voice window (the notch) sends its mic to the services all the time
    over its live connection, with echo cancellation, so Friday's own voice is
    left out. Its audio is used whenever it is arriving; the services' own mic
    (Microphone) only fills in when no window is sending. On some laptops that
    mic records only silence, while the window's mic works.
    """

    LIVE_FRESH = 2.0  # seconds: a window that stopped sending this long ago is gone

    def __init__(self, consumers: list[Callable[[Any, float, float, float], None]]):
        self._consumers = consumers
        self._source: object = None
        self._live_at = -1e9
        self._pending: Any = None

    @property
    def live(self) -> bool:
        return time.monotonic() - self._live_at < self.LIVE_FRESH

    def from_mic(self, samples, _peak: float = 0.0, _rms: float = 0.0, now: float = 0.0) -> None:
        if not self.live:
            self._emit(samples)

    def from_live(self, source: object, pcm: bytes, rate: int) -> None:
        """Mic audio from one voice window; another window's is ignored while
        this one keeps sending (several windows would double every sound)."""
        import numpy as np

        now = time.monotonic()
        if self._source is not None and source != self._source and self.live:
            return
        self._source, self._live_at = source, now
        samples = np.frombuffer(pcm, dtype="<i2").astype("float32") / 32768
        if rate and rate != SAMPLE_RATE:
            positions = np.arange(0, len(samples), rate / SAMPLE_RATE)
            samples = np.interp(positions, np.arange(len(samples)), samples).astype("float32")
        pending = samples if self._pending is None else np.concatenate([self._pending, samples])
        while len(pending) >= BLOCK:
            self._emit(pending[:BLOCK])
            pending = pending[BLOCK:]
        self._pending = pending

    def _emit(self, block) -> None:
        import numpy as np

        peak = float(np.max(np.abs(block))) if len(block) else 0.0
        rms = float(np.sqrt(np.mean(block * block))) if len(block) else 0.0
        now = time.monotonic()
        for consume in self._consumers:
            try:
                consume(block, peak, rms, now)
            except Exception:
                log.exception("wake audio consumer failed")
