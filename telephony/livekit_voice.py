"""PH3D-B: verified LiveKit caller identity and serialized canonical Chat turns.

This module deliberately has no LiveKit, ElevenLabs, FISH or model dependency.
The transport runtime supplies speech components; the only conversation engine
is HomeServer's signed /api/v1/phone/agent -> context_chat.chat adapter.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .security import phone_number
from .voice_turns import CALL_SID, VoiceTurnError, _allowed_endpoint

_TRUNK = re.compile(r"^[A-Za-z0-9_-]{5,120}$")
_AGENT = re.compile(r"^[A-Za-z0-9_-]{3,80}$")
_MODEL = re.compile(r"^[A-Za-z0-9_./:-]{3,160}$")


@dataclass(frozen=True)
class WorkerSettings:
    edge_url: str
    worker_secret: str
    twilio_account_sid: str
    livekit_trunk_id: str
    callback_url: str
    callback_secret: str
    agent_name: str = "vp3-phone"
    speech_backend: str = "livekit-inference"
    stt_model: str = ""
    tts_model: str = ""
    tts_voice: str = ""
    disclosure: str = "Hello. You are speaking with the VP3 AI assistant. How can I help?"

    @classmethod
    def from_environment(cls) -> "WorkerSettings":
        return cls(
            edge_url=os.getenv("VP3_PHONE_PUBLIC_BASE", ""),
            worker_secret=os.getenv("VP3_PHONE_VOICE_WORKER_SECRET", ""),
            twilio_account_sid=os.getenv("VP3_PHONE_ACCOUNT_SID", ""),
            livekit_trunk_id=os.getenv("VP3_PHONE_LIVEKIT_TRUNK_ID", ""),
            callback_url=os.getenv("VP3_PHONE_VP3_CALLBACK", "http://127.0.0.1:4377/api/v1/phone/agent"),
            callback_secret=os.getenv("VP3_PHONE_VP3_CALLBACK_SECRET", ""),
            agent_name=os.getenv("VP3_PHONE_AGENT_NAME", "vp3-phone"),
            speech_backend=os.getenv("VP3_PHONE_SPEECH_BACKEND", "livekit-inference"),
            stt_model=os.getenv("VP3_PHONE_STT_MODEL", ""),
            tts_model=os.getenv("VP3_PHONE_TTS_MODEL", ""),
            tts_voice=os.getenv("VP3_PHONE_TTS_VOICE", ""),
        )

    def validate(self) -> None:
        edge = urlparse(self.edge_url)
        if (edge.scheme != "https" or not edge.hostname or edge.username or edge.password
                or edge.query or edge.fragment or edge.path not in {"", "/"}):
            raise VoiceTurnError("A fixed HTTPS public phone gateway is required")
        if not self.worker_secret or not self.callback_secret:
            raise VoiceTurnError("Distinct worker and platform signing credentials are required")
        if self.worker_secret == self.callback_secret:
            raise VoiceTurnError("Voice worker and platform callback credentials must differ")
        if not re.fullmatch(r"AC[a-fA-F0-9]{32}", self.twilio_account_sid):
            raise VoiceTurnError("The Twilio account SID must be configured")
        if not _TRUNK.fullmatch(self.livekit_trunk_id) or not _AGENT.fullmatch(self.agent_name):
            raise VoiceTurnError("An exact LiveKit SIP trunk and worker name are required")
        if (not _allowed_endpoint(self.callback_url, allow_local=True)
                or not urlparse(self.callback_url).path.endswith("/api/v1/phone/agent")):
            raise VoiceTurnError("The trusted HomeServer phone Chat callback is required")
        if self.speech_backend != "livekit-inference":
            # A future HomeServer provider must expose an explicit adapter, not
            # silently fall back to a different voice or unconfigured cloud key.
            raise VoiceTurnError("Speech adapter not installed; select livekit-inference or deploy the HomeServer adapter")
        if not _MODEL.fullmatch(self.stt_model) or not _MODEL.fullmatch(self.tts_model):
            raise VoiceTurnError("Explicit STT and TTS model selections are required")
        if self.tts_voice and not _MODEL.fullmatch(self.tts_voice):
            raise VoiceTurnError("Invalid voice selection")


@dataclass(frozen=True)
class SIPCaller:
    participant_identity: str
    call_sid: str
    from_number: str
    to_number: str


def verified_sip_caller(participant: object, settings: WorkerSettings, *, sip_kind: int) -> SIPCaller:
    """Use Twilio/LiveKit carrier attributes; never caller-provided JSON/metadata."""
    if getattr(participant, "kind", None) != sip_kind:
        raise VoiceTurnError("Only SIP carrier participants may invoke the phone Agent")
    attrs = getattr(participant, "attributes", None)
    if not isinstance(attrs, dict):
        raise VoiceTurnError("Trusted SIP attributes are unavailable")
    if (attrs.get("sip.twilio.accountSid") != settings.twilio_account_sid
            or attrs.get("sip.trunkID") != settings.livekit_trunk_id
            or attrs.get("sip.callStatus") in {"hangup", "failed"}):
        raise VoiceTurnError("SIP participant does not match the trusted carrier/trunk")
    call_sid = attrs.get("sip.twilio.callSid", "")
    if not isinstance(call_sid, str) or not CALL_SID.fullmatch(call_sid):
        raise VoiceTurnError("Twilio CallSid is missing or invalid")
    try:
        source = phone_number(attrs.get("sip.phoneNumber", ""))
        destination = phone_number(attrs.get("sip.trunkPhoneNumber", ""))
    except ValueError as exc:
        raise VoiceTurnError("The caller and destination must be verified E.164 numbers") from exc
    identity = getattr(participant, "identity", "")
    if not isinstance(identity, str) or not identity or len(identity) > 200:
        raise VoiceTurnError("Missing LiveKit SIP participant identity")
    return SIPCaller(identity, call_sid, source, destination)


async def wait_for_sip_caller(room: object, settings: WorkerSettings, *, sip_kind: int,
                              timeout: float = 15.0) -> SIPCaller:
    """Wait for asynchronously-populated SIP attributes, never guess a call ID."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        participants = list(getattr(room, "remote_participants", {}).values())
        sip = [p for p in participants if getattr(p, "kind", None) == sip_kind]
        if len(sip) > 1:
            raise VoiceTurnError("Ambiguous LiveKit room: multiple SIP callers")
        if sip:
            try:
                return verified_sip_caller(sip[0], settings, sip_kind=sip_kind)
            except VoiceTurnError:
                # SIP attributes may appear shortly after join. Never accept
                # partial identity; the deadline enforces a fail-closed result.
                pass
        await asyncio.sleep(0.15)
    raise VoiceTurnError("No verified SIP participant joined within the timeout")


class SerializedMainChat:
    """Preserve Chat turn ordering when LiveKit cancels speech on barge-in.

    A cancelled LLM stream does not cancel an in-flight HomeServer write. The
    existing turn finishes first; the next utterance then continues the same
    canonical conversation. Uncertain failures remain blocked by VoiceSession.
    """

    def __init__(self, phone_session: object) -> None:
        self.phone_session = phone_session
        self._tail: asyncio.Task[str] | None = None

    async def respond(self, transcript: str) -> str:
        previous = self._tail

        async def deliver() -> str:
            if previous is not None:
                try:
                    await previous
                except Exception:
                    # The session's fail-closed state handles uncertain turns.
                    pass
            return await asyncio.to_thread(self.phone_session.turn, transcript)

        task = asyncio.create_task(deliver(), name="vp3_phone_canonical_chat_turn")
        self._tail = task

        def consume_exception(done: asyncio.Task[str]) -> None:
            if not done.cancelled():
                done.exception()  # prevent an unobserved-error warning after interruption

        task.add_done_callback(consume_exception)
        return await asyncio.shield(task)
