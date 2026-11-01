"""PH3D-B LiveKit SIP worker: media transport + existing VP3 Main Chat only.

Run as a separate optional process/venv. Its 'LLM' is a transport adapter to
the signed HomeServer context_chat.chat endpoint, never a second model. STT,
VAD, TTS and audio interruption stay within LiveKit's voice pipeline.

No SIP credentials, phone numbers or speech-provider keys are shipped in code.
"""

from __future__ import annotations

import asyncio
import os
import uuid

from livekit import agents, rtc
from livekit.agents import Agent, AgentServer, AgentSession, TurnHandlingOptions, inference, llm, room_io
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN

from .livekit_voice import SerializedMainChat, WorkerSettings, wait_for_sip_caller
from .voice_turns import (MainChatVoiceSession, VoiceTurnError,
                          authorize_cloud_live_call, resolve_livekit_call)


def last_recognized_utterance(chat_ctx: llm.ChatContext) -> tuple[str, str]:
    """Extract the latest caller message ID and its completed transcript."""
    for item in reversed(chat_ctx.items):
        if isinstance(item, llm.ChatMessage) and item.role == "user":
            return str(item.id), str(item.text_content or "").strip()
    return "", ""


class _CanonicalChatStream(llm.LLMStream):
    async def _run(self) -> None:
        message_id, transcript = last_recognized_utterance(self.chat_ctx)
        if not transcript or not self._llm.claim_transcript(message_id):
            return
        reply = await self._llm.coordinator.respond(transcript)
        # The text is already the canonical Chat response. No external model
        # or LiveKit tool/function planner may reinterpret it.
        if reply:
            self._event_ch.send_nowait(
                llm.ChatChunk(id="vp3-phone-" + uuid.uuid4().hex,
                              delta=llm.ChoiceDelta(role="assistant", content=reply)))


class VP3CanonicalChatLLM(llm.LLM):
    """LiveKit-compatible LLM *adapter*; all reasoning remains in HomeServer."""

    def __init__(self, coordinator: SerializedMainChat):
        super().__init__()
        self.coordinator = coordinator
        self._handled_message_ids: set[str] = set()

    def claim_transcript(self, message_id: str) -> bool:
        """One canonical turn per STT message, even if the SDK reruns the LLM."""
        if not message_id or message_id in self._handled_message_ids:
            return False
        self._handled_message_ids.add(message_id)
        return True

    @property
    def provider(self) -> str:
        return "vp3-homeserver"

    @property
    def model(self) -> str:
        return "canonical-context-chat"

    def chat(self, *, chat_ctx: llm.ChatContext,
             tools: list[llm.Tool] | None = None,
             conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
             parallel_tool_calls=NOT_GIVEN, tool_choice=NOT_GIVEN,
             extra_kwargs=NOT_GIVEN) -> llm.LLMStream:
        if tools:
            raise VoiceTurnError("Phone callers cannot invoke LiveKit tools")
        # The existing HomeServer phone adapter also forces read_only=True.
        # No speculative retry after a potentially completed Chat turn.
        return _CanonicalChatStream(
            self, chat_ctx=chat_ctx, tools=[],
            conn_options=APIConnectOptions(max_retry=0, timeout=30, liveness_timeout=30),
        )


class PhoneVoiceAgent(Agent):
    def __init__(self) -> None:
        # This is a media presentation instruction, not a second LLM prompt.
        super().__init__(instructions="Use the existing VP3 Main Chat for every reply. Never invoke local tools.")


def create_speech_session(settings: WorkerSettings, coordinator: SerializedMainChat) -> AgentSession:
    """Explicit optional speech backend; no silent replacement for user settings.

    PH3D-B supports LiveKit inference STT/TTS. A later HomeServer ElevenLabs
    adapter can supply its selected speech providers here without changing the
    canonical Chat adapter or telephony security contract.
    """
    if settings.speech_backend != "livekit-inference":
        raise VoiceTurnError("Requested speech provider is not integrated")
    voice = {"voice": settings.tts_voice} if settings.tts_voice else {}
    return AgentSession(
        stt=inference.STT(model=settings.stt_model, language="en"),
        llm=VP3CanonicalChatLLM(coordinator),
        tts=inference.TTS(model=settings.tts_model, **voice),
        vad=inference.VAD(),
        turn_handling=TurnHandlingOptions(
            turn_detection="stt",
            interruption={"enabled": True, "mode": "vad", "min_duration": 0.25},
        ),
    )


def build_server(settings: WorkerSettings) -> AgentServer:
    settings.validate()
    server = AgentServer()

    @server.rtc_session(agent_name=settings.agent_name)
    async def phone_call(ctx: agents.JobContext) -> None:
        # LiveKit connects outbound; the HomeServer port stays private.
        await ctx.connect()
        try:
            caller = await wait_for_sip_caller(
                ctx.room, settings, sip_kind=rtc.ParticipantKind.PARTICIPANT_KIND_SIP)
            binding = await asyncio.to_thread(
                resolve_livekit_call, settings.edge_url, settings.worker_secret,
                call_sid=caller.call_sid, to_number=caller.to_number,
                from_number=caller.from_number, backend=settings.resolve_backend,
                parent_call_sid=caller.parent_call_sid,
                issued=caller.issued, proof=caller.proof)
            if binding.get("platform") != "vp3":
                raise VoiceTurnError("The assigned phone number does not belong to VP3")
            phone_session = MainChatVoiceSession.from_verified_call(
                binding, callback_url=settings.callback_url,
                callback_secret=settings.callback_secret)
            if settings.resolve_backend == "cloud":
                phone_session.authorize = lambda: authorize_cloud_live_call(
                    settings.edge_url, settings.worker_secret,
                    call_sid=caller.call_sid, parent_call_sid=caller.parent_call_sid)
        except (VoiceTurnError, OSError, TimeoutError):
            # Do not begin speech or process audio before carrier identity and
            # existing Agent grants are verified. Do not log caller PII.
            ctx.shutdown(reason="phone caller verification failed")
            return

        coordinator = SerializedMainChat(phone_session)
        session = create_speech_session(settings, coordinator)
        await session.start(
            agent=PhoneVoiceAgent(), room=ctx.room, record=False,
            room_options=room_io.RoomOptions(
                participant_identity=caller.participant_identity,
                participant_kinds=[rtc.ParticipantKind.PARTICIPANT_KIND_SIP],
                text_input=False, text_output=False, video_input=False,
            ),
        )
        await session.say(settings.disclosure, allow_interruptions=True, add_to_chat_ctx=False)

    return server


def main() -> None:
    settings = WorkerSettings.from_environment()
    settings.validate()
    # LiveKit API credentials are consumed by its SDK; never expose them via
    # telephony health endpoints, logs or browser-side voice settings.
    if not all(os.getenv(k) for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")):
        raise VoiceTurnError("LiveKit server credentials are required")
    agents.cli.run_app(build_server(settings))


if __name__ == "__main__":
    main()
