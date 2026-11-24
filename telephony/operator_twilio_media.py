"""Strict Twilio bidirectional Media Streams fixture adapter (8A.3).

No WebSocket listener, HTTP requests, model or audio-provider calls. Consumers
must not treat this local reference as deployed carrier authentication, a
durable Cloud media grant, or consent. Do not feed real call audio into it.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
import threading
from dataclasses import dataclass, field

from .operator_twilio_contract import (
    StreamBinding, TwilioContractError, authenticate_media_upgrade, decimal, epoch,
)

MAX_FRAME_BYTES = 8192
MAX_INBOUND_BYTES = 1600  # 200ms of 8kHz mu-law
MAX_OUTBOUND_BYTES = 1600
MAX_MESSAGES = 100000
MAX_TIMESTAMP_MS = 900000
MARK_TIMEOUT_SECONDS = 5
_MARK = re.compile(r"[0-9a-f]{32}")
_DIGITS = frozenset("0123456789*#ABCD")


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise TwilioContractError("Duplicate media frame key")
        result[key] = value
    return result


def _parse(raw: bytes | str) -> dict:
    if isinstance(raw, str):
        if len(raw) > MAX_FRAME_BYTES or not raw.isascii():
            raise TwilioContractError("Invalid media frame size or encoding")
        raw = raw.encode("ascii")
    if type(raw) is not bytes or not 2 <= len(raw) <= MAX_FRAME_BYTES:
        raise TwilioContractError("Invalid media frame size")
    try:
        def nonfinite(_):
            raise TwilioContractError("Non-finite media value")
        obj = json.loads(raw.decode("ascii"), object_pairs_hook=_pairs,
                         parse_constant=nonfinite)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise TwilioContractError("Invalid media frame JSON") from exc
    if not isinstance(obj, dict) or not isinstance(obj.get("event"), str):
        raise TwilioContractError("Invalid media frame")
    return obj


def _audio(value: object, limit: int) -> bytes:
    if not isinstance(value, str) or not value.isascii() or not 4 <= len(value) <= ((limit + 2) // 3) * 4:
        raise TwilioContractError("Media payload size is invalid")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise TwilioContractError("Invalid media payload encoding") from exc
    if not 1 <= len(data) <= limit or base64.b64encode(data).decode("ascii") != value:
        raise TwilioContractError("Media payload is not bounded canonical base64")
    return data


def mulaw_to_pcm16le(data: bytes) -> bytes:
    """Decode raw G.711 mu-law to signed 8kHz little-endian PCM16."""
    if type(data) is not bytes or not 1 <= len(data) <= MAX_INBOUND_BYTES:
        raise TwilioContractError("Invalid raw mu-law input")
    out = bytearray()
    for byte in data:
        value = ~byte & 0xff
        magnitude = ((value & 0x0f) << 3) + 0x84
        magnitude <<= (value >> 4) & 7
        sample = 0x84 - magnitude if value & 0x80 else magnitude - 0x84
        out.extend(sample.to_bytes(2, "little", signed=True))
    return bytes(out)


def pcm16le_to_mulaw(data: bytes) -> bytes:
    """Encode bounded raw signed 8kHz little-endian PCM16, not a WAV/MP3 file."""
    if (type(data) is not bytes or not 2 <= len(data) <= 2 * MAX_OUTBOUND_BYTES
            or len(data) % 2 or data.startswith((b"RIFF", b"ID3", b"OggS", b"fLaC"))):
        raise TwilioContractError("Invalid bounded raw PCM16 input")
    out = bytearray()
    for i in range(0, len(data), 2):
        sample = int.from_bytes(data[i:i+2], "little", signed=True)
        sign = 0x80 if sample < 0 else 0
        magnitude = min(abs(sample), 32635) + 0x84
        exponent = min(7, max(0, magnitude.bit_length() - 8))
        mantissa = (magnitude >> (exponent + 3)) & 0x0f
        out.append(~(sign | (exponent << 4) | mantissa) & 0xff)
    return bytes(out)


@dataclass(frozen=True)
class MediaEvent:
    event: str
    audio: bytes = field(default=b"", repr=False)
    timestamp_ms: int | None = None
    digit: str | None = field(default=None, repr=False)
    mark_outcome: str | None = None


@dataclass(frozen=True)
class OutboundBatch:
    messages: tuple[dict, ...] = field(repr=False)
    generation: int
    close_required: bool = False


class FixtureMediaRegistry:
    """Exclusive in-process fixture stream claims.

    Holds at most 128 authorized fixture intents. Not a durable authority:
    a real Cloud claim must be atomic, cross-worker and fenced on restart.
    A spent binding cannot be reopened, even after this fixture closes.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._claims: dict[tuple[str, str], tuple[str, str]] = {}

    def claim(self, binding: StreamBinding, stream_sid: str) -> None:
        key = (binding.plan.account_sid, binding.call_sid)
        with self._lock:
            if (key in self._claims
                    or any(stream == stream_sid for _, stream in self._claims.values())
                    or any(intent == binding.plan.intent_id for intent, _ in self._claims.values())
                    or len(self._claims) >= 128):
                raise TwilioContractError("Fixture stream binding already consumed or unavailable")
            self._claims[key] = (binding.plan.intent_id, stream_sid)


class FixtureMediaSession:
    """Ordered single-stream media state and generation-fenced speech output.

    The caller must serialize actual socket output and revocation in a future
    executor. This reference never publishes audio. A returned batch is an
    envelope, not delivery confirmation; unknown send outcomes require
    fencing and reconciliation, not replay.
    """

    def __init__(self, binding: StreamBinding, registry: FixtureMediaRegistry,
                 auth_token: str, signed_url: str, signature: str, now: int):
        if not isinstance(binding, StreamBinding) or not isinstance(registry, FixtureMediaRegistry):
            raise TwilioContractError("Expected fixture binding and registry")
        if epoch(now) >= binding.deadline:
            raise TwilioContractError("Fixture media deadline expired")
        authenticate_media_upgrade(binding, auth_token, signed_url, signature)
        self.binding = binding
        self.registry = registry
        self._lock = threading.RLock()
        self.state = "awaiting_connected"
        self.stream_sid = ""
        self.sequence = 0
        self.chunk = 0
        self.timestamp_ms = -1
        self.generation = 1
        self._last_now = now
        self._pending: tuple[str, int, bool] | None = None
        self._issued_marks = 0
        self.reason = ""

    def _check(self, now: int) -> None:
        epoch(now)
        if self.state == "closed":
            raise TwilioContractError("Fixture media is closed")
        if now < self._last_now or now >= self.binding.deadline:
            raise TwilioContractError("Fixture media clock or deadline expired")
        if self._pending is not None and now >= self._pending[1]:
            raise TwilioContractError("Playback acknowledgement timed out")
        self._last_now = now

    def _fence(self, reason: str) -> None:
        if self.state != "closed":
            self.state = "closed"
            self.generation += 1
            self._pending = None
            self.reason = reason

    def _require_active(self) -> None:
        if self.state != "active":
            raise TwilioContractError("Fixture media is not active")

    def _message(self, event: str, **fields) -> dict:
        return {"event": event, "streamSid": self.stream_sid, **fields}

    def receive(self, raw: bytes | str, now: int) -> MediaEvent:
        """Accept exact ordered Twilio events. Any protocol error fences output."""
        with self._lock:
            try:
                self._check(now)
                msg = _parse(raw)
                event = msg["event"]
                if self.state == "awaiting_connected":
                    if (event != "connected" or msg.get("protocol") != "Call"
                            or msg.get("version") != "1.0.0"):
                        raise TwilioContractError("Unsupported initial media protocol")
                    self.state = "awaiting_start"
                    return MediaEvent(event)
                sequence = decimal(msg.get("sequenceNumber"), 1, MAX_MESSAGES)
                if sequence != self.sequence + 1:
                    raise TwilioContractError("Duplicate or out-of-order media frame")
                if self.state == "awaiting_start":
                    if event != "start" or sequence != 1:
                        raise TwilioContractError("Expected exact media start")
                    stream = self.binding.match_start(msg, now)
                    self.registry.claim(self.binding, stream)
                    self.stream_sid = stream
                    self.sequence = sequence
                    self.state = "active"
                    return MediaEvent(event)
                self._require_active()
                if msg.get("streamSid") != self.stream_sid:
                    raise TwilioContractError("Media frame belongs to another stream")
                if event == "media":
                    media = msg.get("media")
                    if not isinstance(media, dict) or media.get("track") != "inbound":
                        raise TwilioContractError("Unexpected media track")
                    chunk = decimal(media.get("chunk"), 1, MAX_MESSAGES)
                    stamp = decimal(media.get("timestamp"), 0, MAX_TIMESTAMP_MS)
                    if chunk != self.chunk + 1 or stamp < self.timestamp_ms:
                        raise TwilioContractError("Media chunk or timestamp out of order")
                    data = _audio(media.get("payload"), MAX_INBOUND_BYTES)
                    self.chunk = chunk
                    self.timestamp_ms = stamp
                    result = MediaEvent(event, audio=mulaw_to_pcm16le(data), timestamp_ms=stamp)
                elif event == "dtmf":
                    dtmf = msg.get("dtmf")
                    if (not isinstance(dtmf, dict) or dtmf.get("track") not in ("inbound", "inbound_track")
                            or not isinstance(dtmf.get("digit"), str) or dtmf["digit"] not in _DIGITS):
                        raise TwilioContractError("Invalid inbound keypad event")
                    # Keypad data is not authenticated consent or an instruction.
                    result = MediaEvent(event, digit=dtmf["digit"])
                elif event == "mark":
                    mark = msg.get("mark")
                    if (not isinstance(mark, dict) or not isinstance(mark.get("name"), str)
                            or not _MARK.fullmatch(mark["name"]) or self._pending is None
                            or mark["name"] != self._pending[0]):
                        raise TwilioContractError("Unexpected playback acknowledgement")
                    outcome = "cleared_or_unconfirmed" if self._pending[2] else "buffer_acknowledged"
                    self._pending = None
                    result = MediaEvent(event, mark_outcome=outcome)
                elif event == "stop":
                    stop = msg.get("stop")
                    if (not isinstance(stop, dict) or stop.get("accountSid") != self.binding.plan.account_sid
                            or stop.get("callSid") != self.binding.call_sid):
                        raise TwilioContractError("Stop receipt belongs to another call")
                    self._fence("stream_stopped")
                    result = MediaEvent(event)
                else:
                    raise TwilioContractError("Unsupported media event")
                self.sequence = sequence
                return result
            except TwilioContractError:
                self._fence("protocol_or_authority_error")
                raise

    def queue_pcm(self, data: bytes, generation: int, now: int) -> OutboundBatch:
        """At most one bounded audio segment in flight; mark is not human hearing proof."""
        with self._lock:
            try:
                self._check(now)
                self._require_active()
            except TwilioContractError:
                self._fence("media_not_current")
                raise
            if type(generation) is not int or generation != self.generation:
                raise TwilioContractError("Stale speech generation")
            if self._pending is not None:
                raise TwilioContractError("Previous playback is unresolved")
            if self._issued_marks >= MAX_MESSAGES:
                self._fence("playback_limit")
                raise TwilioContractError("Playback limit reached")
            audio = pcm16le_to_mulaw(data)
            name = secrets.token_hex(16)
            self._pending = (name, min(now + MARK_TIMEOUT_SECONDS, self.binding.deadline), False)
            self._issued_marks += 1
            return OutboundBatch((
                self._message("media", media={"payload": base64.b64encode(audio).decode("ascii")}),
                self._message("mark", mark={"name": name}),
            ), self.generation)

    def interrupt(self, now: int) -> OutboundBatch:
        """Barge-in: invalidate old synthesis and clear Twilio's buffered audio."""
        with self._lock:
            try:
                self._check(now)
                self._require_active()
            except TwilioContractError:
                self._fence("media_not_current")
                raise
            self.generation += 1
            if self._pending is not None:
                name, deadline, _ = self._pending
                self._pending = (name, deadline, True)
            return OutboundBatch((self._message("clear"),), self.generation)

    def revoke(self, reason: str = "owner_stop") -> OutboundBatch:
        """Fence locally first; future socket executor must clear then close.

        The local result cannot prove the carrier call has ended. Owner takeover
        currently means stopping Agent media; joining a conference is later.
        """
        if not isinstance(reason, str) or reason not in {"owner_stop", "owner_takeover", "consent_revoked",
                          "worker_lost", "transport_uncertain", "deadline", "stream_closed"}:
            raise TwilioContractError("Unknown revocation reason")
        with self._lock:
            active = self.state == "active"
            clear = (self._message("clear"),) if active else ()
            self._fence(reason)
            return OutboundBatch(clear, self.generation, close_required=True)

    def check_health(self, now: int) -> dict:
        with self._lock:
            try:
                self._check(now)
            except TwilioContractError:
                self._fence("media_not_current")
            return {"state": self.state, "reason": self.reason,
                    "generation": self.generation,
                    "playback_pending": self._pending is not None,
                    "close_required": self.state == "closed",
                    "carrier_terminal": False, "execution_enabled": False,
                    "live_certified": False, "reference_only": True}
