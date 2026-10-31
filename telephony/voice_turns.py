"""PH3D spoken-turn bridge to the EXISTING HomeServer Agent Chat.

The LiveKit worker owns STT, TTS, VAD and interruptions. This module owns only
signed transcript delivery and per-call continuity. It never creates an LLM,
duplicates the Agent memory, or grants an unidentified caller tool access.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .security import json_bytes, signed_envelope

CALL_SID = re.compile(r"^CA[a-fA-F0-9]{32}$")


class VoiceTurnError(RuntimeError):
    """A call/turn cannot be safely delivered or replayed."""


def _allowed_endpoint(url: str, *, allow_local: bool) -> bool:
    parsed = urlparse(url)
    return (not parsed.username and not parsed.password and not parsed.query
            and not parsed.fragment and bool(parsed.hostname) and
            (parsed.scheme == "https" or (allow_local and parsed.scheme == "http"
             and parsed.hostname in {"127.0.0.1", "localhost"})))


def signed_json_post(url: str, secret: str, payload: dict, *, allow_local: bool = False,
                     timeout: int = 15) -> dict:
    """Use only preconfigured transport URLs, never phone caller supplied URLs."""
    if not secret or not _allowed_endpoint(url, allow_local=allow_local):
        raise VoiceTurnError("A trusted voice service endpoint is required")
    raw = json_bytes(payload)
    stamp = int(time.time())
    request = Request(url, data=raw, method="POST", headers={
        "Content-Type": "application/json", "X-VP3-Timestamp": str(stamp),
        "X-VP3-Signature": signed_envelope(secret, stamp, raw),
    })
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read(8192))
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        raise VoiceTurnError("Voice service did not acknowledge the turn") from exc
    if not isinstance(result, dict) or not result.get("ok"):
        raise VoiceTurnError("Voice service rejected the turn")
    return result


def resolve_livekit_call(edge_url: str, voice_worker_secret: str, *,
                         call_sid: str, to_number: str, from_number: str,
                         sender: Callable[..., dict] = signed_json_post) -> dict:
    """Consult signed Twilio ingress before allowing a SIP participant to speak."""
    if not CALL_SID.fullmatch(call_sid):
        raise VoiceTurnError("A valid carrier call ID is required")
    url = edge_url.rstrip("/") + "/v1/voice/resolve"
    payload = {"call_sid": call_sid, "to_number": to_number, "from_number": from_number}
    result = sender(url, voice_worker_secret, payload, allow_local=False)
    if (result.get("call_sid") != call_sid or result.get("caller_verified") is not False
            or result.get("allow_actions") is not False
            or result.get("platform") not in {"vp3", "stonefellow", "gelato"}
            or not result.get("agent_id")):
        raise VoiceTurnError("Carrier call binding is inconsistent")
    return result


@dataclass
class MainChatVoiceSession:
    """A live call's transcript turns, routed through canonical context_chat.chat.

    Construct only from resolve_livekit_call() output. No retry after an
    uncertain send: the same event ID may have already completed at HomeServer.
    """

    call_sid: str
    agent_id: int
    callback_url: str
    callback_secret: str
    conversation_id: str = ""
    turn_index: int = 0
    blocked: bool = False

    @classmethod
    def from_verified_call(cls, binding: dict, *, callback_url: str,
                           callback_secret: str) -> "MainChatVoiceSession":
        if (binding.get("ok") is not True or binding.get("platform") != "vp3"
                or binding.get("caller_verified") is not False
                or binding.get("allow_actions") is not False
                or not CALL_SID.fullmatch(str(binding.get("call_sid", "")))):
            raise VoiceTurnError("A verified VP3 call binding is required")
        try:
            agent_id = int(binding["agent_id"])
        except (ValueError, TypeError, KeyError) as exc:
            raise VoiceTurnError("Invalid bound Agent") from exc
        if agent_id < 1 or not callback_secret or not _allowed_endpoint(callback_url, allow_local=True):
            raise VoiceTurnError("Voice Agent callback is not configured")
        return cls(str(binding["call_sid"]), agent_id, callback_url, callback_secret)

    def turn(self, transcript: str, *, sender: Callable[..., dict] = signed_json_post) -> str:
        if self.blocked or self.turn_index >= 500:
            raise VoiceTurnError("Call must be reviewed before another Agent turn")
        utterance = str(transcript or "").strip()
        if not utterance or len(utterance) > 2000:
            raise VoiceTurnError("A nonempty, bounded transcription is required")
        self.turn_index += 1
        payload = {"event_id": f"{self.call_sid}_t{self.turn_index}",
                   "call_sid": self.call_sid, "turn_index": self.turn_index,
                   "channel": "voice", "agent_id": self.agent_id,
                   "message": utterance, "conversation_id": self.conversation_id,
                   "caller_verified": False, "allow_actions": False}
        try:
            result = sender(self.callback_url, self.callback_secret, payload, allow_local=True)
            if not isinstance(result.get("reply"), str) or not isinstance(result.get("conversation_id"), str):
                raise VoiceTurnError("Invalid main Chat response")
        except Exception:
            # A timeout may still have committed an Agent turn. Fail closed.
            self.blocked = True
            raise
        self.conversation_id = result["conversation_id"][:100]
        return result["reply"][:1200]
