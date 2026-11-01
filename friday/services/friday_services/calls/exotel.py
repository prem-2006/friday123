"""Exotel: an Indian number for Friday's own calls (no Exotel SDK needed).

Friday places a call with the "Connect Number to Call Flow" API. The call
flow made on my.exotel.com (EXOTEL_APP_ID) has two applets: a Voicebot whose
URL is stream_url() below, then a Hangup. The Voicebot streams the call
audio both ways to /exotel/stream, where the same Pipecat pipeline as the
Twilio calls runs (calls/bot.py). When Friday ends the call it closes the
WebSocket, and the flow goes on to the Hangup.

Calls to the ExoPhone run the same flow, so Friday answers them too: the
owner's own number gets Friday with all its tools, anyone else Reception.

Exotel signs nothing, so the stream carries EXOTEL_STREAM_KEY as a custom
parameter, and status callbacks carry it in their URL.
"""

from __future__ import annotations

import hmac
from urllib.parse import parse_qsl

import httpx

from friday_services.calls.store import CallRecord
from friday_services.config import Settings
from friday_services.usage import counted

# India accounts use api.in.exotel.com; Singapore ones api.exotel.com.
DEFAULT_SUBDOMAIN = "api.in.exotel.com"
# Exotel's Status -> Friday status, for calls whose audio never started.
NOT_CONNECTED = {
    "busy": "no_answer",
    "no-answer": "no_answer",
    "failed": "failed",
    "canceled": "failed",
}
# How long Exotel may keep a call up, a little over the pipeline's own limit.
TIME_LIMIT_SECONDS = 6 * 60


class ExotelError(RuntimeError):
    pass


def stream_url(settings: Settings) -> str:
    """What goes in the Voicebot applet's URL box on my.exotel.com."""
    base = settings.public_url.replace("https://", "wss://", 1)
    return f"{base}/exotel/stream?key={settings.get('EXOTEL_STREAM_KEY')}"


def custom_parameters(value: object) -> dict[str, str]:
    """The start event's custom_parameters: an object, or a query string."""
    if isinstance(value, dict):
        return {str(key): str(item) for key, item in value.items()}
    if isinstance(value, str):
        return dict(parse_qsl(value.lstrip("?")))
    return {}


def valid_key(settings: Settings, supplied: str | None) -> bool:
    key = settings.get("EXOTEL_STREAM_KEY")
    return bool(key and supplied) and hmac.compare_digest(str(supplied), key)


class ExotelDialer:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._client = client

    @counted("Phone calls", "Exotel", "calls Friday places")
    async def place(self, record: CallRecord) -> str:
        """Start an outbound call into Friday's call flow; returns Exotel's call SID."""
        get = self._settings.get
        sid = get("EXOTEL_ACCOUNT_SID")
        subdomain = get("EXOTEL_SUBDOMAIN", DEFAULT_SUBDOMAIN).removeprefix("https://").strip("/")
        public = self._settings.public_url
        data = {
            "From": record.phone,
            "CallerId": get("EXOTEL_CALLER_ID"),
            "Url": f"http://my.exotel.com/{sid}/exoml/start_voice/{get('EXOTEL_APP_ID')}",
            "CallType": "trans",
            "TimeLimit": str(TIME_LIMIT_SECONDS),
            "TimeOut": "30",
            "StatusCallback": (
                f"{public}/exotel/status/{record.id}?key={get('EXOTEL_STREAM_KEY')}"
            ),
            "StatusCallbackEvents[0]": "terminal",
            "StatusCallbackEvents[1]": "answered",
            "CustomField": record.id,
        }
        client = self._client or httpx.AsyncClient(timeout=15)
        try:
            response = await client.post(
                f"https://{subdomain}/v1/Accounts/{sid}/Calls/connect.json",
                data=data,
                auth=(get("EXOTEL_API_KEY"), get("EXOTEL_API_TOKEN")),
            )
        finally:
            if not self._client:
                await client.aclose()
        try:
            body = response.json() if response.content else {}
        except ValueError:
            body = {}
        if response.status_code >= 400 or not (body.get("Call") or {}).get("Sid"):
            problem = (body.get("RestException") or {}).get("Message") or "no details"
            raise ExotelError(f"Exotel refused the call (HTTP {response.status_code}): {problem}")
        return str(body["Call"]["Sid"])
