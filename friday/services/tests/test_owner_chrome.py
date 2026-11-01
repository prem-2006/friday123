"""Finding the owner's signed-in Chrome profile, without Chrome."""

from __future__ import annotations

import json
from pathlib import Path

from friday_services.config import Settings
from friday_services.owner_chrome import chrome_profiles, owner_chrome

PROFILES = {
    "Default": "someone.else@gmail.com",
    "Profile 4": "Prem.Owner@gmail.com",
}


def details(tmp_path: Path, email: str) -> Path:
    path = tmp_path / "friday-profile.json"
    path.write_text(json.dumps({"email": email}), encoding="utf-8")
    return path


def test_the_profile_signed_in_to_the_owners_gmail(tmp_path):
    exe = tmp_path / "chrome.exe"
    found = owner_chrome(
        Settings.from_env({}, data_dir=tmp_path),
        PROFILES,
        exe,
        details(tmp_path, "prem.owner@gmail.com"),
    )
    assert found and found.folder == "Profile 4" and found.exe == exe


def test_a_chosen_profile_wins_and_no_match_means_none(tmp_path):
    exe = tmp_path / "chrome.exe"
    chosen = Settings.from_env({"FRIDAY_CHROME_PROFILE": "Default"}, data_dir=tmp_path)
    assert owner_chrome(chosen, PROFILES, exe, details(tmp_path, "x@y.z")).folder == "Default"
    plain = Settings.from_env({}, data_dir=tmp_path)
    assert owner_chrome(plain, PROFILES, exe, details(tmp_path, "nobody@gmail.com")) is None
    assert owner_chrome(plain, PROFILES, exe, tmp_path / "missing.json") is None


def test_chrome_says_which_email_each_profile_is(tmp_path):
    (tmp_path / "Local State").write_text(
        json.dumps({"profile": {"info_cache": {"Profile 4": {"user_name": "a@b.c"}}}}),
        encoding="utf-8",
    )
    assert chrome_profiles(tmp_path) == {"Profile 4": "a@b.c"}
    assert chrome_profiles(tmp_path / "nowhere") == {}


def test_the_account_chosen_for_friday_wins_over_the_owners_email(tmp_path):
    path = tmp_path / "friday-profile.json"
    path.write_text(
        json.dumps({"email": "someone.else@gmail.com", "friday_google": "prem.owner@gmail.com"}),
        encoding="utf-8",
    )
    found = owner_chrome(
        Settings.from_env({}, data_dir=tmp_path), PROFILES, tmp_path / "c.exe", path
    )
    assert found and found.folder == "Profile 4"


def test_chrome_lists_only_signed_in_profiles(tmp_path):
    from friday_services.owner_chrome import chrome_accounts

    (tmp_path / "Local State").write_text(
        json.dumps(
            {
                "profile": {
                    "info_cache": {
                        "Default": {"user_name": "", "name": "Guest-like"},
                        "Profile 4": {"user_name": "a@b.c", "name": "Prem"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    assert chrome_accounts(tmp_path) == [{"folder": "Profile 4", "email": "a@b.c", "name": "Prem"}]
