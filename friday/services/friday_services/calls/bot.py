"""One phone call: Exotel or Twilio audio -> Prisma -> Evon -> Timbre -> back."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger
from openai import AsyncOpenAI
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import EndWorkerFrame, LLMRunFrame, TTSSpeakFrame
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.serializers.exotel import ExotelFrameSerializer
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transcriptions.language import Language
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.workers.runner import WorkerRunner
from pipecat_gnani import GnaniTTSService

from friday_services.calls.prompts import (
    OUTCOMES,
    SUMMARY_PROMPT,
    owner_inbound_prompt,
    owner_prompt,
    phone_prompt,
    reception_prompt,
)
from friday_services.calls.store import CallRecord, CallStore
from friday_services.calls.stt import FramedGnaniSTTService
from friday_services.config import Settings
from friday_services.voices import voice_for

# Who Friday is talking to: someone for the owner, or the owner (either way).
PROMPTS = {"owner": owner_prompt, "owner_in": owner_inbound_prompt, "reception": reception_prompt}
# Calls with the owner, where Friday may do anything it can on the PC.
OWNER_KINDS = ("owner", "owner_in")

TELEPHONY_SAMPLE_RATE = 8000
# The pinned Gnani SDK defaults to timbre-v2.0; Friday speaks with Timbre v2.5.
TIMBRE_MODEL = "timbre-v2.5"
MAX_CALL_SECONDS = 5 * 60
MAX_SILENCE_SECONDS = 45
# Exotel wants audio in chunks of at least 3.2 KB: 200 ms at 8 kHz.
EXOTEL_OUT_10MS_CHUNKS = 20
# ask_friday runs the request through the Friday Dot; PhoneDesk answers within 22 s.
ASK_FRIDAY_TIMEOUT_SECONDS = 30
# Said while ask_friday works, so the line is not silent.
WORKING = {
    "hi-IN": "Ek second, dekh rahi hoon.",
    "en-IN": "One second, I'm on it.",
}

END_CALL = FunctionSchema(
    name="end_call",
    description="Hang up after saying goodbye, and record how the call went.",
    properties={
        "outcome": {"type": "string", "enum": list(OUTCOMES)},
        "summary": {
            "type": "string",
            "description": "One or two sentences for the owner about what was agreed.",
        },
        "details": {
            "type": "object",
            "description": "Facts worth keeping, e.g. confirmed_time, person, price_quoted.",
            "additionalProperties": {"type": "string"},
        },
    },
    required=["outcome", "summary"],
)
ASK_FRIDAY = FunctionSchema(
    name="ask_friday",
    description=(
        "Have Friday do what the owner asked on this call (messages, reminders, the "
        "calendar, the PC, searches), or pass on their yes or no to an OK Friday asked "
        "for. Returns the sentence to say back."
    ),
    properties={
        "request": {
            "type": "string",
            "description": "What the owner asked, in their own words.",
        },
    },
    required=["request"],
)
TAKE_MESSAGE = FunctionSchema(
    name="take_message",
    description="Save a message from the caller for the owner.",
    properties={
        "name": {"type": "string", "description": "Who is calling, as they said it."},
        "message": {"type": "string", "description": "The message, in a sentence or two."},
    },
    required=["message"],
)


@dataclass
class CallHelpers:
    """What a call can reach outside its pipeline (main.py wires these)."""

    # (record, request) -> what to say: the Friday Dot, for the owner's calls.
    ask: Callable[[CallRecord, str], Awaitable[str]] | None = None
    # (record, name, message): a caller's message, saved for the owner.
    take_message: Callable[[CallRecord, str, str], Awaitable[None]] | None = None


@dataclass
class CallPipeline:
    pipeline: Pipeline
    worker: PipelineWorker
    transport: FastAPIWebsocketTransport
    ended_by_friday: asyncio.Event


def evon_llm(settings: Settings, **extra_settings: Any) -> OpenAILLMService:
    extra: dict[str, Any] = {}
    if settings.evon_disable_thinking:
        extra["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
    return OpenAILLMService(
        api_key=settings.get("EVON_API_KEY") or "not-needed",
        base_url=settings.get("EVON_BASE_URL"),
        settings=OpenAILLMService.Settings(
            model=settings.get("EVON_MODEL"),
            temperature=0.6,
            max_completion_tokens=300,
            extra=extra,
            **extra_settings,
        ),
    )


def end_call_handler(store: CallStore, record: CallRecord, ended: asyncio.Event):
    async def end_call(params: FunctionCallParams):
        args = dict(params.arguments)
        outcome = args.get("outcome") if args.get("outcome") in OUTCOMES else "other"
        details = {str(k): str(v) for k, v in (args.get("details") or {}).items()}
        store.finish(
            record.id,
            "answered",
            outcome=outcome,
            summary=str(args.get("summary") or ""),
            details=details,
        )
        ended.set()
        await params.result_callback({"ok": True})
        # Ends the pipeline after queued speech; the serializer hangs up Twilio.
        await params.llm.push_frame(EndWorkerFrame(), FrameDirection.UPSTREAM)

    return end_call


def ask_friday_handler(record: CallRecord, ask: Callable[[CallRecord, str], Awaitable[str]]):
    async def ask_friday(params: FunctionCallParams):
        request = str(params.arguments.get("request") or "")
        try:
            reply = await ask(record, request)
        except Exception as error:
            logger.warning(f"[{record.id}] ask_friday failed: {error}")
            reply = f"Sorry, that did not work: {error}"
        await params.result_callback({"reply": reply})

    return ask_friday


def take_message_handler(
    record: CallRecord, take: Callable[[CallRecord, str, str], Awaitable[None]]
):
    async def take_message(params: FunctionCallParams):
        args = dict(params.arguments)
        await take(record, str(args.get("name") or ""), str(args.get("message") or ""))
        await params.result_callback({"saved": True})

    return take_message


def call_transport(
    websocket: Any, provider: str, stream_sid: str, call_sid: str, settings: Settings
) -> FastAPIWebsocketTransport:
    """The call's audio over the provider's WebSocket. Twilio's serializer
    hangs up through its API; Exotel's flow hangs up when the socket closes."""
    if provider == "exotel":
        serializer = ExotelFrameSerializer(stream_sid=stream_sid, call_sid=call_sid)
        chunks = EXOTEL_OUT_10MS_CHUNKS
    else:
        serializer = TwilioFrameSerializer(
            stream_sid=stream_sid,
            call_sid=call_sid,
            account_sid=settings.get("TWILIO_ACCOUNT_SID"),
            auth_token=settings.get("TWILIO_AUTH_TOKEN"),
        )
        chunks = 4
    return FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            audio_out_10ms_chunks=chunks,
            serializer=serializer,
        ),
    )


def build_call_pipeline(
    websocket: Any,
    *,
    stream_sid: str,
    call_sid: str,
    record: CallRecord,
    store: CallStore,
    settings: Settings,
    provider: str = "twilio",
    helpers: CallHelpers | None = None,
) -> CallPipeline:
    """Wire the pipeline for one call. Nothing connects until it runs."""
    voice = voice_for(record.language)
    gnani_key = settings.get("GNANI_API_KEY")
    helpers = helpers or CallHelpers()
    transport = call_transport(websocket, provider, stream_sid, call_sid, settings)
    tools = [END_CALL]
    if record.kind in OWNER_KINDS and helpers.ask:
        tools.append(ASK_FRIDAY)
    if record.kind == "reception" and helpers.take_message:
        tools.append(TAKE_MESSAGE)
    stt = FramedGnaniSTTService(
        api_key=gnani_key,
        sample_rate=TELEPHONY_SAMPLE_RATE,
        settings=FramedGnaniSTTService.Settings(
            language=Language(record.language), format="transcribe"
        ),
    )
    llm = evon_llm(settings)
    tts = GnaniTTSService(
        api_key=gnani_key,
        model=TIMBRE_MODEL,
        sample_rate=TELEPHONY_SAMPLE_RATE,
        settings=GnaniTTSService.Settings(voice=voice.voice, language=Language(record.language)),
    )
    context = LLMContext(
        messages=[
            {
                "role": "system",
                "content": PROMPTS.get(record.kind, phone_prompt)(
                    record, settings.owner_name, settings.timezone
                ),
            },
            {"role": "user", "content": "[The call has just been answered. Speak first.]"},
        ],
        tools=ToolsSchema(standard_tools=tools),
    )
    user, assistant = LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer())
    )
    pipeline = Pipeline([transport.input(), stt, user, llm, tts, transport.output(), assistant])

    # Target: under about 2 s from the end of their speech to Friday's first audio.
    latency = UserBotLatencyObserver()

    @latency.event_handler("on_latency_measured")
    async def on_latency(_observer, seconds: float):
        milliseconds = round(seconds * 1000)
        store.add_latency(record.id, milliseconds)
        logger.info(f"[{record.id}] end of speech -> first audio: {milliseconds} ms")

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=TELEPHONY_SAMPLE_RATE,
            audio_out_sample_rate=TELEPHONY_SAMPLE_RATE,
            enable_metrics=True,
        ),
        observers=[latency],
        idle_timeout_secs=MAX_SILENCE_SECONDS,
    )
    ended_by_friday = asyncio.Event()
    llm.register_function("end_call", end_call_handler(store, record, ended_by_friday))
    if ASK_FRIDAY in tools:
        # A "hmm" from the owner must not cancel a WhatsApp message half sent.
        llm.register_function(
            "ask_friday",
            ask_friday_handler(record, helpers.ask),
            cancel_on_interruption=False,
            timeout_secs=ASK_FRIDAY_TIMEOUT_SECONDS,
        )

        @llm.event_handler("on_function_calls_started")
        async def on_working(service, function_calls):
            if any(call.function_name == "ask_friday" for call in function_calls):
                working = WORKING.get(record.language, WORKING["en-IN"])
                await service.push_frame(TTSSpeakFrame(working))

    if TAKE_MESSAGE in tools:
        llm.register_function("take_message", take_message_handler(record, helpers.take_message))

    @transport.event_handler("on_client_connected")
    async def on_connected(_transport, _client):
        store.set_stage(record.id, "in_progress")
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(_transport, _client):
        await worker.cancel(reason="caller hung up")

    @user.event_handler("on_user_turn_stopped")
    async def on_their_turn(_aggregator, _strategy, message):
        store.add_turn(record.id, "them", message.content or "")

    @assistant.event_handler("on_assistant_turn_stopped")
    async def on_friday_turn(_aggregator, message):
        store.add_turn(record.id, "friday", message.content or "")

    return CallPipeline(
        pipeline=pipeline, worker=worker, transport=transport, ended_by_friday=ended_by_friday
    )


async def summarize(settings: Settings, record: CallRecord) -> dict[str, Any]:
    """Ask Evon for an outcome when the call ended without end_call."""
    if not record.transcript:
        return {"outcome": "other", "summary": "The call connected but nobody spoke."}
    client = AsyncOpenAI(
        api_key=settings.get("EVON_API_KEY") or "not-needed",
        base_url=settings.get("EVON_BASE_URL"),
    )
    lines = "\n".join(record.result()["transcript"])
    extra = (
        {"chat_template_kwargs": {"enable_thinking": False}}
        if settings.evon_disable_thinking
        else {}
    )
    try:
        reply = await client.chat.completions.create(
            model=settings.get("EVON_MODEL"),
            messages=[
                {"role": "system", "content": SUMMARY_PROMPT},
                {"role": "user", "content": f"Goal: {record.goal}\n\nTranscript:\n{lines}"},
            ],
            max_completion_tokens=300,
            extra_body=extra or None,
        )
        text = reply.choices[0].message.content or ""
        data = json.loads(text[text.find("{") : text.rfind("}") + 1])
        outcome = data.get("outcome") if data.get("outcome") in OUTCOMES else "other"
        return {
            "outcome": outcome,
            "summary": str(data.get("summary") or ""),
            "details": {str(k): str(v) for k, v in (data.get("details") or {}).items()},
        }
    except Exception as error:  # the transcript is still returned
        logger.warning(f"[{record.id}] summary failed: {error}")
        return {"outcome": "other", "summary": "The call ended. See the transcript."}
    finally:
        await client.close()


async def run_call(
    websocket: Any,
    *,
    stream_sid: str,
    call_sid: str,
    record: CallRecord,
    store: CallStore,
    settings: Settings,
    provider: str = "twilio",
    helpers: CallHelpers | None = None,
) -> None:
    store.set_stage(record.id, "in_progress")
    record.stream_started = True
    call = build_call_pipeline(
        websocket,
        stream_sid=stream_sid,
        call_sid=call_sid,
        record=record,
        store=store,
        settings=settings,
        provider=provider,
        helpers=helpers,
    )
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(call.worker)
    try:
        await asyncio.wait_for(runner.run(), MAX_CALL_SECONDS)
    except TimeoutError:
        await call.worker.cancel(reason="call time limit")
        store.finish(
            record.id,
            "answered",
            outcome="other",
            summary=f"Friday stopped the call after {MAX_CALL_SECONDS // 60} minutes.",
        )
    except Exception as error:
        logger.exception(f"[{record.id}] call pipeline failed")
        store.finish(record.id, "failed", error=f"Call audio failed: {error}")
    if not store.get(record.id).final:
        store.finish(record.id, "answered", **await summarize(settings, record))
