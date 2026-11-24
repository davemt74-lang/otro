"""Operator 8A.5A: serialized, consent-fenced participant/speech fixture.

Only synthetic authority and media fixtures. The controller consumes batches
inside its local lock and retains metadata only; there is no network sink,
carrier dispatch, STT/TTS, recording, real consent proof or human join route.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
import secrets
import threading

from .operator_session_simulation import SessionBinding
from .operator_twilio_contract import StreamBinding, TwilioContractError, epoch
from .operator_twilio_media import (
    FixtureMediaRegistry, FixtureMediaSession, MediaEvent, OutboundBatch,
)

MAX_TURN_SECONDS = 20
SAMPLES_PER_SECOND = 8000


@dataclass(frozen=True)
class FixtureSpeechGrant:
    principal: SessionBinding
    stream: StreamBinding = field(repr=False)
    revision: int
    expires_at: int
    owner_approved: bool
    recipient_agreed: bool

    def __post_init__(self):
        if (not isinstance(self.principal, SessionBinding)
                or not isinstance(self.stream, StreamBinding)
                or type(self.revision) is not int or not 1 <= self.revision <= 9007199254740991
                or type(self.owner_approved) is not bool
                or type(self.recipient_agreed) is not bool
                or epoch(self.expires_at) > self.stream.deadline):
            raise TwilioContractError("Invalid synthetic participant grant")


@dataclass(frozen=True)
class FixtureTurnPermit:
    token: str = field(repr=False)
    generation: int
    expires_at: int
    sample_limit: int


@dataclass(frozen=True)
class FixturePlaybackReceipt:
    mark_name: str = field(repr=False)
    generation: int
    samples: int
    execution_enabled: bool = field(default=False, init=False)


class FixtureParticipantController:
    """One local owner of a fixture stream and its output ordering.

    The injected grant reader represents a trusted synthetic authority only.
    It is rechecked at each operation; distributed authority and actual socket
    serialization must be implemented and verified separately. No raw outbound
    batch escapes this controller, and the output trace contains event types
    and generations only. The inbound PCM returned by receive is synthetic.
    """

    def __init__(self, grant: FixtureSpeechGrant, read_grant: Callable[[], FixtureSpeechGrant | None],
                 registry: FixtureMediaRegistry, auth_token: str, signed_url: str,
                 signature: str, now: int):
        if (not isinstance(grant, FixtureSpeechGrant) or not callable(read_grant)
                or not grant.owner_approved or not grant.recipient_agreed
                or epoch(now) >= grant.expires_at):
            raise TwilioContractError("Synthetic speaking authority unavailable")
        try:
            current = read_grant()
        except Exception:
            raise TwilioContractError("Synthetic speaking authority unavailable") from None
        if current != grant:
            raise TwilioContractError("Synthetic speaking authority changed")
        self._grant = grant
        self._read_grant = read_grant
        self._media = FixtureMediaSession(grant.stream, registry, auth_token, signed_url, signature, now)
        self._lock = threading.RLock()
        self._last_now = now
        self._caller_speaking = False
        self._turn: FixtureTurnPermit | None = None
        self._samples = 0
        self._closed = False
        self._reason = ""
        self._trace = deque(maxlen=256)
        self._output_count = 0

    def _note(self, kind: str, generation: int) -> None:
        self._trace.append((kind, generation))
        self._output_count += 1

    def _consume(self, batch: OutboundBatch) -> None:
        # Called only under our lock. A production network sender is NOT used.
        for message in batch.messages:
            self._note(message["event"], batch.generation)
        if batch.close_required:
            self._note("close", batch.generation)

    def _close(self, reason: str) -> None:
        if self._closed:
            return
        self._closed = True
        self._reason = reason
        self._turn = None
        self._samples = 0
        self._caller_speaking = False
        self._consume(self._media.revoke(reason))

    def _interrupt(self, now: int) -> None:
        self._turn = None
        self._samples = 0
        try:
            self._consume(self._media.interrupt(now))
        except TwilioContractError:
            self._close("stream_closed")
            raise

    def _guard(self, now: int) -> dict:
        if self._closed:
            raise TwilioContractError("Participant fixture is closed")
        try:
            epoch(now)
        except TwilioContractError:
            self._close("deadline")
            raise
        if now < self._last_now or now >= self._grant.expires_at:
            self._close("deadline")
            raise TwilioContractError("Participant fixture clock or grant expired")
        self._last_now = now
        try:
            current = self._read_grant()
        except Exception:
            self._close("worker_lost")
            raise TwilioContractError("Synthetic speaking authority unavailable") from None
        if current != self._grant:
            self._close("consent_revoked")
            raise TwilioContractError("Synthetic speaking authority changed")
        health = self._media.check_health(now)
        if health["close_required"]:
            self._close("stream_closed")
            raise TwilioContractError("Participant media is no longer current")
        if self._turn is not None and now >= self._turn.expires_at:
            self._interrupt(now)
            health = self._media.check_health(now)
        return health

    def receive(self, raw: bytes | str, now: int) -> MediaEvent:
        with self._lock:
            self._guard(now)
            try:
                event = self._media.receive(raw, now)
            except TwilioContractError:
                self._close("stream_closed")
                raise
            if event.event == "stop":
                self._close("stream_closed")
            # DTMF is only data. It cannot authorize speech, infer consent,
            # toggle a participant, or create a human media route.
            return event

    def caller_activity(self, speaking: bool, now: int) -> None:
        """Trusted synthetic VAD input, not a real speech detector."""
        with self._lock:
            if type(speaking) is not bool:
                raise TwilioContractError("Invalid fixture voice activity")
            health = self._guard(now)
            if health["state"] != "active":
                raise TwilioContractError("Participant media is not active")
            if speaking and not self._caller_speaking and self._turn is not None:
                self._interrupt(now)
            self._caller_speaking = speaking

    def begin_agent_turn(self, now: int, duration_seconds: int = MAX_TURN_SECONDS) -> FixtureTurnPermit:
        with self._lock:
            health = self._guard(now)
            if (health["state"] != "active" or health["playback_pending"]
                    or self._caller_speaking or self._turn is not None
                    or type(duration_seconds) is not int or not 1 <= duration_seconds <= MAX_TURN_SECONDS):
                raise TwilioContractError("Agent speech turn is unavailable")
            expires = min(now + duration_seconds, self._grant.expires_at)
            permit = FixtureTurnPermit(secrets.token_hex(16), self._media.generation,
                                       expires, (expires - now) * SAMPLES_PER_SECOND)
            self._turn = permit
            self._samples = 0
            return permit

    def _exact_turn(self, permit: FixtureTurnPermit) -> None:
        if (not isinstance(permit, FixtureTurnPermit) or self._turn is None
                or permit != self._turn or permit.generation != self._media.generation
                or self._caller_speaking):
            raise TwilioContractError("Stale or unauthorized speech turn")

    def publish_pcm(self, permit: FixtureTurnPermit, pcm: bytes, now: int) -> FixturePlaybackReceipt:
        with self._lock:
            self._guard(now)
            self._exact_turn(permit)
            if (type(pcm) is not bytes or len(pcm) < 2 or len(pcm) % 2
                    or self._samples + len(pcm)//2 > permit.sample_limit):
                raise TwilioContractError("Synthetic turn sample budget exhausted or invalid")
            batch = self._media.queue_pcm(pcm, permit.generation, now)
            # One-use in-process publication; no queued audio leaves the lock.
            self._consume(batch)
            count = len(pcm)//2
            self._samples += count
            return FixturePlaybackReceipt(batch.messages[1]["mark"]["name"], batch.generation, count)

    def end_agent_turn(self, permit: FixtureTurnPermit, now: int) -> None:
        with self._lock:
            self._guard(now)
            self._exact_turn(permit)
            self._interrupt(now)

    def owner_takeover(self, owner_id: int) -> dict:
        """Stop Agent media. A real human join/transfer is NOT performed."""
        with self._lock:
            if type(owner_id) is not int or owner_id != self._grant.principal.owner_id:
                raise TwilioContractError("Owner takeover binding is invalid")
            self._close("owner_takeover")
            return self._report()

    def owner_stop(self, owner_id: int) -> dict:
        with self._lock:
            if type(owner_id) is not int or owner_id != self._grant.principal.owner_id:
                raise TwilioContractError("Owner stop binding is invalid")
            self._close("owner_stop")
            return self._report()

    def _report(self) -> dict:
        return {"state": "closed" if self._closed else self._media.state,
                "floor": "none" if self._closed else "caller" if self._caller_speaking else
                         "agent" if self._turn is not None else "listening",
                "generation": self._media.generation,
                "reason": self._reason,
                "output_count": self._output_count,
                "owner_joined": False, "carrier_terminal": False,
                "execution_enabled": False, "live_certified": False, "reference_only": True}

    def report(self, now: int) -> dict:
        with self._lock:
            if not self._closed:
                try:
                    self._guard(now)
                except TwilioContractError:
                    pass
            return self._report()

    def output_trace(self) -> tuple[tuple[str, int], ...]:
        """Bounded metadata-only fixture trace, never audio or carrier IDs."""
        with self._lock:
            return tuple(self._trace)
