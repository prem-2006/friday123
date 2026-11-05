"""Who is this: Friday recognises only the people the owner has saved.

Runs on this PC with OpenCV's YuNet (detects faces) and SFace (compares them),
both from opencv/opencv_zoo. Nothing goes to the internet or to a model, no
photo is kept, and a saved person is only a list of numbers, which "forget"
deletes. A face that matches no saved person is "someone I do not know":
Friday never guesses, and never searches for a face anywhere.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np

from friday_services.config import REPO_ROOT

MODELS_URL = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models"
DETECTOR = (
    "face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
)
RECOGNIZER = (
    "face_recognition_sface/face_recognition_sface_2021dec.onnx",
    "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
)
# SFace's own cut-off is 0.363; a wrong name is worse than "I do not know".
SAME_PERSON = 0.45
# The best match must also beat the next person by this much.
CLEAR_LEAD = 0.05
MAX_SAMPLES = 8
MIN_FACE_PIXELS = 60
# Looks for a good shot this long, and stops early once it has a sharp one.
CAPTURE_SECONDS = 4.0
SHARP_ENOUGH = 120.0


class PeopleError(Exception):
    """The message is said to the owner."""


@dataclass
class Face:
    box: tuple[int, int, int, int]
    score: float
    row: Any  # the detector's whole row: SFace aligns the face with it


@dataclass
class Shot:
    frame: Any
    faces: list[Face]
    quality: float


def cosine(a: Any, b: Any) -> float:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    size = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b / size) if size else 0.0


def best_match(embedding: Any, people: dict[str, list[list[float]]]) -> tuple[str, float] | None:
    """(name, similarity) of the saved person this clearly is, else None."""
    scored = sorted(
        (
            (max(cosine(embedding, sample) for sample in samples), name)
            for name, samples in people.items()
            if samples
        ),
        reverse=True,
    )
    if not scored or scored[0][0] < SAME_PERSON:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < CLEAR_LEAD:
        return None
    return scored[0][1], scored[0][0]


def clean_name(name: str) -> str:
    name = " ".join(name.split()).strip(" .,:;!?\"'")
    if not 2 <= len(name) <= 60:
        raise PeopleError("Tell me the person's name.")
    return name


def same_name(saved: dict[str, Any], name: str) -> str | None:
    wanted = name.casefold()
    return next((known for known in saved if known.casefold() == wanted), None)


class SavedPeople:
    """data/friday-people.json: each person's face numbers, never a photo."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=self._path.parent, suffix=".tmp", delete=False, encoding="utf-8"
        ) as f:
            json.dump(data, f)
        os.replace(f.name, self._path)

    def samples(self) -> dict[str, list[list[float]]]:
        return {name: person.get("samples", []) for name, person in self._load().items()}

    def listed(self) -> list[dict[str, Any]]:
        return sorted(
            (
                {"name": name, "added": person.get("added", 0)}
                for name, person in self._load().items()
            ),
            key=lambda person: person["name"].casefold(),
        )

    def add(self, name: str, samples: list[list[float]]) -> str:
        """Saves the samples, most recent kept; a known name gets more of them."""
        name = clean_name(name)
        with self._lock:
            data = self._load()
            known = same_name(data, name)
            person = data.setdefault(known or name, {"added": self._clock(), "samples": []})
            person["samples"] = (person["samples"] + samples)[-MAX_SAMPLES:]
            self._save(data)
            return known or name

    def forget(self, name: str) -> str | None:
        with self._lock:
            data = self._load()
            known = same_name(data, name)
            if known is None:
                return None
            del data[known]
            self._save(data)
            return known


def model_path(folder: Path, model: tuple[str, str]) -> Path:
    """The model file, downloaded once and checked against its known hash."""
    relative, expected = model
    path = folder / Path(relative).name
    if path.exists():
        return path
    folder.mkdir(parents=True, exist_ok=True)
    try:
        data = httpx.get(f"{MODELS_URL}/{relative}", follow_redirects=True, timeout=120).content
    except httpx.HTTPError as error:
        raise PeopleError("I could not download the face models.") from error
    if hashlib.sha256(data).hexdigest() != expected:
        raise PeopleError("The face model I downloaded looks wrong, so I did not use it.")
    path.write_bytes(data)
    return path


class FaceEngine:
    def __init__(self, folder: Path | None = None) -> None:
        folder = folder or REPO_ROOT / "data" / "face-models"
        self._detector = cv2.FaceDetectorYN.create(
            str(model_path(folder, DETECTOR)), "", (320, 320), score_threshold=0.8
        )
        self._recognizer = cv2.FaceRecognizerSF.create(str(model_path(folder, RECOGNIZER)), "")

    def faces(self, frame: Any) -> list[Face]:
        """Faces big enough to compare, largest first."""
        height, width = frame.shape[:2]
        self._detector.setInputSize((width, height))
        _, rows = self._detector.detect(frame)
        found = [
            Face(tuple(int(v) for v in row[:4]), float(row[-1]), row)
            for row in ([] if rows is None else rows)
            if min(row[2], row[3]) >= MIN_FACE_PIXELS
        ]
        return sorted(found, key=lambda face: face.box[2] * face.box[3], reverse=True)

    def embedding(self, frame: Any, face: Face) -> list[float]:
        aligned = self._recognizer.alignCrop(frame, face.row)
        return [float(v) for v in self._recognizer.feature(aligned).flatten()]


def sharpness(frame: Any, box: tuple[int, int, int, int]) -> float:
    x, y, w, h = box
    crop = frame[max(y, 0) : y + h, max(x, 0) : x + w]
    if crop.size == 0:
        return 0.0
    return float(cv2.Laplacian(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())


def rate_shot(frame: Any, faces: list[Face]) -> Shot:
    """Sharp, clearly detected and large beats everything else."""
    if not faces:
        return Shot(frame, faces, 0.0)
    main = faces[0]
    area = main.box[2] * main.box[3] / max(frame.shape[0] * frame.shape[1], 1)
    return Shot(frame, faces, sharpness(frame, main.box) * main.score * (1 + area))


def best_shots(shots: list[Shot], count: int) -> list[Shot]:
    return sorted((s for s in shots if s.faces), key=lambda s: s.quality, reverse=True)[:count]


def webcam_frames(seconds: float = CAPTURE_SECONDS, index: int = 0):
    """Frames from the camera, which is on only while this runs."""
    camera = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    try:
        if not camera.isOpened():
            raise PeopleError("I could not open the camera. Check that no other app is using it.")
        for _ in range(5):  # the first frames are dark while the camera adjusts
            camera.read()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            ok, frame = camera.read()
            if ok:
                yield frame
    finally:
        camera.release()


def annotated(shot: Shot, names: list[str | None]) -> str:
    """The shot with a box round each face, as JPEG base64 for the voice screen."""
    frame = shot.frame.copy()
    for face, name in zip(shot.faces, names, strict=True):
        x, y, w, h = face.box
        color = (161, 199, 108) if name else (117, 127, 239)
        cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
        cv2.putText(
            frame, name or "Unknown", (x, max(y - 8, 16)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2
        )
    ok, data = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return base64.b64encode(data.tobytes()).decode() if ok else ""


class People:
    def __init__(
        self,
        saved: SavedPeople,
        engine: Callable[[], Any] = FaceEngine,
        frames: Callable[[], Any] = webcam_frames,
    ) -> None:
        self.saved = saved
        self._make_engine = engine
        self._frames = frames
        self._engine: Any = None

    def _shots(self, wanted: int) -> list[Shot]:
        """Rates the camera's frames; stops once `wanted` sharp shots are in."""
        if self._engine is None:
            self._engine = self._make_engine()
        shots: list[Shot] = []
        for frame in self._frames():
            shot = rate_shot(frame, self._engine.faces(frame))
            if shot.faces:
                shots.append(shot)
            if sum(s.quality >= SHARP_ENOUGH for s in shots) >= wanted:
                break
        return best_shots(shots, wanted)

    def who(self) -> dict[str, Any]:
        """-> {"names": [name or None for each face], "image", "scores"}."""
        shots = self._shots(1)
        if not shots:
            return {"names": [], "scores": [], "image": ""}
        shot = shots[0]
        known = self.saved.samples()
        matches = [
            best_match(self._engine.embedding(shot.frame, face), known) for face in shot.faces
        ]
        names = [match[0] if match else None for match in matches]
        return {
            "names": names,
            "scores": [round(match[1] * 100) if match else None for match in matches],
            "image": annotated(shot, names),
        }

    def remember(self, name: str) -> dict[str, Any]:
        """Saves the one face in front of the camera under this name."""
        name = clean_name(name)
        shots = self._shots(3)
        if not shots:
            raise PeopleError("I could not see a face. Look at the camera and try again.")
        if any(len(shot.faces) > 1 for shot in shots):
            raise PeopleError("I can see more than one face. Only that person should be in view.")
        samples = [self._engine.embedding(shot.frame, shot.faces[0]) for shot in shots]
        saved = self.saved.add(name, samples)
        return {"name": saved, "image": annotated(shots[0], [saved])}


def who_sentence(names: list[str | None], hinglish: bool = False) -> str:
    """What Friday says about the faces it saw, largest first."""
    known = [name for name in names if name]
    strangers = len(names) - len(known)
    if not names:
        return (
            "Mujhe koi chehra nahi dikha."
            if hinglish
            else "I do not see anyone. Look at the camera and ask again."
        )
    if hinglish:
        parts = [f"Ye {' aur '.join(known)} lag rahe hain."] if known else []
        if strangers:
            parts.append(
                "Ye mere saved logon mein nahi hain."
                if not known and strangers == 1
                else f"{strangers} log mere saved logon mein nahi hain."
            )
        return " ".join(parts)
    parts = [f"This looks like {' and '.join(known)}."] if known else []
    if strangers:
        parts.append(
            "This person is not one of the people I have saved."
            if not known and strangers == 1
            else f"{strangers} {'person is' if strangers == 1 else 'people are'} "
            "not among the people I have saved."
        )
    return " ".join(parts)
