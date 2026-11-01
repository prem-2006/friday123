"""Settings for Friday's services, read from the repo's .env."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICES_ROOT = Path(__file__).resolve().parents[1]

CALL_SETTINGS = (
    "GNANI_API_KEY",
    "EVON_BASE_URL",
    "EVON_MODEL",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_FROM_NUMBER",
    "PUBLIC_URL",
)
# Friday's own calls through Exotel (an Indian number): the same Pipecat
# pipeline, with Exotel's Voicebot applet streaming the audio (calls/exotel.py).
EXOTEL_CALL_SETTINGS = (
    "GNANI_API_KEY",
    "EVON_BASE_URL",
    "EVON_MODEL",
    "EXOTEL_API_KEY",
    "EXOTEL_API_TOKEN",
    "EXOTEL_ACCOUNT_SID",
    "EXOTEL_CALLER_ID",
    "EXOTEL_APP_ID",
    "EXOTEL_STREAM_KEY",
    "PUBLIC_URL",
)


@dataclass(frozen=True)
class Settings:
    values: Mapping[str, str]
    data_dir: Path

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, data_dir: Path | None = None
    ) -> Settings:
        source = os.environ if env is None else env
        values = {name: value.strip() for name, value in source.items() if value}
        if values.get("FRIDAY_TWILIO", "").lower() == "off":
            # Twilio is kept but not used: every call goes through Sarvam.
            values = {
                name: value for name, value in values.items() if not name.startswith("TWILIO_")
            }
        return cls(
            values=values,
            data_dir=data_dir or Path(values.get("FRIDAY_DATA_DIR") or SERVICES_ROOT / "data"),
        )

    def get(self, name: str, default: str = "") -> str:
        return self.values.get(name, "").strip() or default

    def missing(self, names: tuple[str, ...]) -> list[str]:
        return [name for name in names if not self.get(name)]

    @property
    def services_token(self) -> str:
        return self.get("FRIDAY_SERVICES_TOKEN")

    @property
    def owner_name(self) -> str:
        return self.get("FRIDAY_OWNER_NAME", "my owner")

    @property
    def timezone(self) -> str:
        return self.get("TIMEZONE", "Asia/Kolkata")

    @property
    def host(self) -> str:
        return self.get("FRIDAY_SERVICES_HOST", "127.0.0.1")

    @property
    def port(self) -> int:
        return int(self.get("FRIDAY_SERVICES_PORT", "8765"))

    @property
    def public_url(self) -> str:
        return self.get("PUBLIC_URL").rstrip("/")

    @property
    def call_provider(self) -> str:
        """Who carries Friday's own calls: exotel or twilio. FRIDAY_CALL_PROVIDER
        picks; else Exotel once its API key is set."""
        chosen = self.get("FRIDAY_CALL_PROVIDER").lower()
        if chosen in ("exotel", "twilio"):
            return chosen
        return "exotel" if self.get("EXOTEL_API_KEY") else "twilio"

    @property
    def call_settings(self) -> tuple[str, ...]:
        return EXOTEL_CALL_SETTINGS if self.call_provider == "exotel" else CALL_SETTINGS

    @property
    def own_calls_ready(self) -> bool:
        """Whether Friday's own pipeline can place and answer calls."""
        return not self.missing(self.call_settings)

    @property
    def calendar_url(self) -> str:
        # Navigator's browser runs on the owner's screen, so localhost works.
        return self.get("FRIDAY_CALENDAR_URL", f"http://127.0.0.1:{self.port}/calendar").rstrip("/")

    @property
    def browser_cdp_port(self) -> int:
        # Chrome's debugging port for the on-screen window, on 127.0.0.1 only.
        return int(self.get("FRIDAY_BROWSER_CDP_PORT", "9333"))

    @property
    def push_to_talk(self) -> bool:
        return self.get("FRIDAY_PUSH_TO_TALK", "true").lower() != "false"

    @property
    def clap_wake(self) -> bool:
        return self.get("FRIDAY_CLAP_WAKE", "true").lower() != "false"

    @property
    def clap_level(self) -> float:
        # How loud a clap's peak must be (0 to 1); lower it for a quiet mic.
        return float(self.get("FRIDAY_CLAP_LEVEL", "0.25"))

    @property
    def wake_word(self) -> bool:
        # "Hey Friday": an offline Vosk model listens for the phrase only.
        return self.get("FRIDAY_WAKE_WORD", "true").lower() != "false"

    @property
    def wake_greeting(self) -> bool:
        # On waking, Friday says hello about what is on screen before listening.
        return self.get("FRIDAY_WAKE_GREETING", "true").lower() != "false"

    @property
    def wake_model_dir(self) -> Path:
        return Path(self.get("FRIDAY_WAKE_MODEL_DIR") or self.data_dir / "wake-word")

    @property
    def stage_url(self) -> str:
        # Opened by a double clap when no voice screen is open.
        return self.get("FRIDAY_STAGE_URL", "http://localhost:5173/#/friday")

    @property
    def browser_prewarm(self) -> bool:
        # Off: Friday's own Chrome opens only when a web task needs it, not at
        # every start (FRIDAY_BROWSER_PREWARM=true opens it at start again).
        return self.get("FRIDAY_BROWSER_PREWARM", "false").lower() == "true"

    @property
    def browser_agent_dir(self) -> Path:
        return Path(self.get("FRIDAY_BROWSER_AGENT_DIR") or REPO_ROOT / "friday" / "browser-agent")

    @property
    def hermes_agent_dir(self) -> Path:
        # Hermes Agent, Navigator's engine when installed (FRIDAY_BROWSER_AGENT).
        return Path(self.get("FRIDAY_HERMES_AGENT_DIR") or REPO_ROOT / "friday" / "hermes-agent")

    @property
    def evon_disable_thinking(self) -> bool:
        # Evon reasons before answering by default, which is too slow on a phone
        # line. Set EVON_DISABLE_THINKING=false if the endpoint rejects
        # chat_template_kwargs (friday/scripts/test-evon-tools.mjs shows this).
        return self.get("EVON_DISABLE_THINKING", "true").lower() != "false"
