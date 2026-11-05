"""Who is this: saved people only, with a pretend camera and face engine."""

from __future__ import annotations

import numpy as np
import pytest

from friday_services import people as module
from friday_services.people import (
    Face,
    People,
    PeopleError,
    SavedPeople,
    best_match,
    best_shots,
    clean_name,
    model_path,
    rate_shot,
    who_sentence,
)

RAHUL = [1.0, 0.0, 0.0]
PRIYA = [0.0, 1.0, 0.0]
STRANGER = [0.0, 0.0, 1.0]


def test_names_only_a_clear_match_and_never_guesses():
    known = {"Rahul": [RAHUL], "Priya": [PRIYA]}
    assert best_match([0.9, 0.1, 0.0], known)[0] == "Rahul"
    assert best_match(STRANGER, known) is None
    assert best_match(RAHUL, {}) is None
    # Close to two people at once: neither is named.
    assert best_match([1.0, 1.0, 0.0], known) is None


def test_keeps_numbers_only_and_forgets_on_request(tmp_path):
    path = tmp_path / "people.json"
    saved = SavedPeople(path, clock=lambda: 5.0)
    assert saved.add("  rahul  sharma ", [RAHUL]) == "rahul sharma"
    assert saved.add("Rahul Sharma", [PRIYA]) == "rahul sharma"  # same person, more samples
    assert saved.samples() == {"rahul sharma": [RAHUL, PRIYA]}
    assert saved.listed() == [{"name": "rahul sharma", "added": 5.0}]
    assert saved.forget("RAHUL SHARMA") == "rahul sharma"
    assert saved.forget("Rahul Sharma") is None
    assert saved.samples() == {}


def test_keeps_only_the_latest_samples(tmp_path):
    saved = SavedPeople(tmp_path / "people.json")
    saved.add("Rahul", [[float(i), 0.0] for i in range(12)])
    assert len(saved.samples()["Rahul"]) == module.MAX_SAMPLES
    assert saved.samples()["Rahul"][-1] == [11.0, 0.0]


def test_a_name_is_required():
    with pytest.raises(PeopleError):
        clean_name(" . ")


def frame(value: int = 0):
    return np.full((200, 200, 3), value, dtype=np.uint8)


class Engine:
    """Each frame's first pixel says which pretend faces it holds."""

    def __init__(self, shown: dict[int, list[list[float]]]):
        self.shown = shown

    def faces(self, picture):
        vectors = self.shown.get(int(picture[0, 0, 0]), [])
        return [Face((10 + i * 80, 10, 70, 70), 0.9, vector) for i, vector in enumerate(vectors)]

    def embedding(self, _picture, face):
        return face.row


def people_with(tmp_path, shown, frames):
    saved = SavedPeople(tmp_path / "people.json")
    return People(saved, engine=lambda: Engine(shown), frames=lambda: iter(frames))


def test_who_names_saved_people_and_not_strangers(tmp_path):
    people = people_with(tmp_path, {1: [RAHUL, STRANGER]}, [frame(1)])
    people.saved.add("Rahul", [RAHUL])
    seen = people.who()
    assert seen["names"] == ["Rahul", None]
    assert seen["scores"] == [100, None]
    assert seen["image"]


def test_who_with_no_one_in_view(tmp_path):
    assert people_with(tmp_path, {}, [frame()]).who()["names"] == []


def test_remember_saves_one_person_only(tmp_path):
    people = people_with(tmp_path, {1: [RAHUL]}, [frame(1)] * 3)
    assert people.remember("Rahul")["name"] == "Rahul"
    assert len(people.saved.samples()["Rahul"]) == 3

    crowd = people_with(tmp_path, {1: [RAHUL, PRIYA]}, [frame(1)])
    with pytest.raises(PeopleError, match="more than one"):
        crowd.remember("Priya")
    assert "Priya" not in crowd.saved.samples()

    empty = people_with(tmp_path, {}, [frame()])
    with pytest.raises(PeopleError, match="could not see"):
        empty.remember("Priya")


def test_picks_the_sharpest_shots():
    noisy = np.random.default_rng(1).integers(0, 255, (200, 200, 3), dtype=np.uint8)
    face = Face((10, 10, 150, 150), 0.9, None)
    sharp, flat = rate_shot(noisy, [face]), rate_shot(frame(), [face])
    assert sharp.quality > flat.quality
    assert best_shots([flat, sharp, rate_shot(noisy, [])], 2) == [sharp, flat]


def test_says_who_it_saw_without_guessing():
    assert who_sentence(["Rahul"]) == "This looks like Rahul."
    assert who_sentence([None]) == "This person is not one of the people I have saved."
    assert who_sentence(["Rahul", None]) == (
        "This looks like Rahul. 1 person is not among the people I have saved."
    )
    assert who_sentence([]).startswith("I do not see anyone")
    assert who_sentence(["Rahul"], True) == "Ye Rahul lag rahe hain."
    assert who_sentence([None], True) == "Ye mere saved logon mein nahi hain."


def test_a_model_with_the_wrong_hash_is_not_used(tmp_path, monkeypatch):
    class Reply:
        content = b"something else"

    monkeypatch.setattr(module.httpx, "get", lambda *a, **k: Reply())
    with pytest.raises(PeopleError, match="looks wrong"):
        model_path(tmp_path, ("models/face.onnx", "0" * 64))
    assert not (tmp_path / "face.onnx").exists()
