"""Phase 8A.2: authenticated Operator worker registration wire contract.

Only simulation capabilities exist in this release. This module has no network
client/server, carrier, model, or media imports. Worker registration is not
consent, a call claim, an execution grant, or certification of live audio.
The reference authority is for private scratch testing; a future Cloud receiver
must independently provision credentials from authenticated account/device state.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass, field

PROTOCOL = "vp3.operator.worker.v1"
CAPABILITIES = frozenset({"session.simulate", "session.recover"})
LEASE_SECONDS = 90
REQUEST_MAX_AGE = 30
FUTURE_SKEW = 5
MAX_FRAME_BYTES = 4096
MAX_INTEGER = 9007199254740991
HEX32 = re.compile(r"[0-9a-f]{32}")
HEX64 = re.compile(r"[0-9a-f]{64}")
WORKER_ID = re.compile(r"[A-Za-z0-9_-]{3,64}")
ACTIONS = frozenset({"register", "heartbeat", "retire"})
REQUEST_FIELDS = frozenset({
    "protocol", "action", "credential_id", "owner_id", "device_id", "worker_id",
    "boot_id", "sequence", "generation", "nonce", "sent_at", "capabilities",
})
RESPONSE_FIELDS = REQUEST_FIELDS - {"sent_at"} | {
    "request_sha256", "received_at", "lease_until", "status",
    "execution_enabled", "live_certified",
}
_DOMAIN = {"request": b"vp3.operator.worker.v1/request\x00",
           "response": b"vp3.operator.worker.v1/response\x00"}


class WorkerRegistrationError(RuntimeError):
    """Registration could not be authenticated or reconciled."""


def opaque(value: object) -> bool:
    return isinstance(value, str) and HEX32.fullmatch(value) is not None


def integer(value: object, low: int = 1, high: int = MAX_INTEGER) -> bool:
    return type(value) is int and low <= value <= high


def epoch(value: object) -> bool:
    return integer(value, 1, 4102444800)


def check_clock(now: int) -> None:
    if not epoch(now):
        raise WorkerRegistrationError("Invalid worker clock")


def fresh(stamp: int, now: int) -> bool:
    return epoch(stamp) and now - REQUEST_MAX_AGE <= stamp <= now + FUTURE_SKEW


def capability_list(value: object) -> tuple[str, ...]:
    if (not isinstance(value, (tuple, list)) or len(value) > len(CAPABILITIES)
            or any(not isinstance(item, str) or item not in CAPABILITIES for item in value)
            or list(value) != sorted(set(value))):
        raise WorkerRegistrationError("Unsupported or unordered worker capabilities")
    return tuple(value)


@dataclass(frozen=True)
class WorkerCredential:
    credential_id: str
    owner_id: int
    device_id: str
    worker_id: str
    secret: bytes = field(repr=False)
    allowed_capabilities: tuple[str, ...] = ("session.recover", "session.simulate")

    def __post_init__(self) -> None:
        if (not opaque(self.credential_id) or not integer(self.owner_id)
                or not opaque(self.device_id)
                or not isinstance(self.worker_id, str) or not WORKER_ID.fullmatch(self.worker_id)
                or type(self.secret) is not bytes or len(self.secret) != 32
                or len(set(self.secret)) < 2):
            raise WorkerRegistrationError("Invalid preprovisioned worker credential")
        object.__setattr__(self, "allowed_capabilities", capability_list(self.allowed_capabilities))

    def binding(self) -> dict:
        return {"credential_id": self.credential_id, "owner_id": self.owner_id,
                "device_id": self.device_id, "worker_id": self.worker_id}


@dataclass(frozen=True)
class SignedFrame:
    # Avoid accidentally printing even private device/worker metadata.
    body: bytes = field(repr=False)
    signature: str = field(repr=False)


def canonical_body(payload: dict) -> bytes:
    try:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise WorkerRegistrationError("Invalid worker frame") from exc
    if not 2 <= len(raw) <= MAX_FRAME_BYTES:
        raise WorkerRegistrationError("Worker frame exceeds size limit")
    return raw


def _pairs(items: list[tuple]) -> dict:
    obj = {}
    for key, value in items:
        if key in obj:
            raise WorkerRegistrationError("Duplicate worker frame field")
        obj[key] = value
    return obj


def parse_body(raw: bytes) -> dict:
    if type(raw) is not bytes or not 2 <= len(raw) <= MAX_FRAME_BYTES:
        raise WorkerRegistrationError("Invalid worker frame size")
    try:
        payload = json.loads(raw.decode("ascii"), object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise WorkerRegistrationError("Invalid worker frame JSON") from exc
    if not isinstance(payload, dict):
        raise WorkerRegistrationError("Invalid worker frame object")
    # Exact canonical encoding is part of the interoperable protocol. Reject
    # alternate field encodings, duplicate keys, and non-finite numbers.
    if canonical_body(payload) != raw:
        raise WorkerRegistrationError("Noncanonical worker frame")
    return payload


def _mac(raw: bytes, credential: WorkerCredential, direction: str) -> str:
    if direction not in _DOMAIN:
        raise WorkerRegistrationError("Invalid signing direction")
    return hmac.new(credential.secret, _DOMAIN[direction] + raw, hashlib.sha256).hexdigest()


def sign_frame(payload: dict, credential: WorkerCredential, direction: str) -> SignedFrame:
    raw = canonical_body(payload)
    return SignedFrame(raw, _mac(raw, credential, direction))


def verify_frame(frame: SignedFrame, credential: WorkerCredential, direction: str) -> dict:
    if (not isinstance(frame, SignedFrame) or type(frame.body) is not bytes
            or not 2 <= len(frame.body) <= MAX_FRAME_BYTES
            or not isinstance(frame.signature, str) or not HEX64.fullmatch(frame.signature)
            or not hmac.compare_digest(frame.signature, _mac(frame.body, credential, direction))):
        raise WorkerRegistrationError("Worker frame authentication failed")
    return parse_body(frame.body)


def validate_request(payload: dict, credential: WorkerCredential, now: int) -> None:
    check_clock(now)
    if (not isinstance(payload, dict) or set(payload) != REQUEST_FIELDS
            or payload["protocol"] != PROTOCOL
            or not isinstance(payload["action"], str) or payload["action"] not in ACTIONS
            or not integer(payload["owner_id"])
            or any(payload[key] != val for key, val in credential.binding().items())
            or not opaque(payload["boot_id"]) or not opaque(payload["nonce"])
            or not integer(payload["sequence"]) or not integer(payload["generation"], 0)
            or not fresh(payload["sent_at"], now)):
        raise WorkerRegistrationError("Worker request is invalid or stale")
    caps = capability_list(payload["capabilities"])
    if not set(caps).issubset(credential.allowed_capabilities):
        raise WorkerRegistrationError("Worker capability is not provisioned")
    if payload["action"] == "register":
        if payload["sequence"] != 1 or payload["generation"] != 0:
            raise WorkerRegistrationError("Registration must start a new boot")
    elif payload["sequence"] < 2 or payload["generation"] < 1:
        raise WorkerRegistrationError("Worker lease is not established")
    if payload["action"] == "retire" and caps:
        raise WorkerRegistrationError("Retirement cannot advertise capabilities")


def request_digest(frame: SignedFrame) -> str:
    return hashlib.sha256(frame.body).hexdigest()


class OperatorWorkerClient:
    """Worker half of the signed protocol; the caller owns the actual transport.

    A pending request can be retransmitted byte-for-byte while fresh. No new
    sequence is sent before a signed matching response. An expired/uncertain
    lease requires a new boot, not a guessed generation or successful state.
    """

    def __init__(self, credential: WorkerCredential, *, boot_id: str | None = None):
        if not isinstance(credential, WorkerCredential):
            raise WorkerRegistrationError("Preprovisioned credential required")
        self.credential = credential
        self.boot_id = secrets.token_hex(16) if boot_id is None else boot_id
        if not opaque(self.boot_id):
            raise WorkerRegistrationError("Invalid worker boot identity")
        self._sequence = 0
        self._generation = 0
        self._lease_until = 0
        self._last_clock = 0
        self._caps: tuple[str, ...] = ()
        self._retired = False
        self._pending: SignedFrame | None = None

    def _check_clock(self, now: int) -> None:
        check_clock(now)
        if now < self._last_clock:
            raise WorkerRegistrationError("Worker clock moved backwards")

    def request(self, action: str, now: int,
                capabilities: tuple[str, ...] | list[str] = ()) -> SignedFrame:
        self._check_clock(now)
        if self._pending is not None or self._retired:
            raise WorkerRegistrationError("Worker has an unresolved or retired request")
        if not isinstance(action, str) or action not in ACTIONS:
            raise WorkerRegistrationError("Unsupported worker request")
        if action == "register":
            if self._sequence != 0:
                raise WorkerRegistrationError("Worker must start a new boot")
        elif not self._generation or self._lease_until <= now:
            raise WorkerRegistrationError("Worker registration is not current")
        payload = {"protocol": PROTOCOL, "action": action, **self.credential.binding(),
                   "boot_id": self.boot_id, "sequence": self._sequence + 1,
                   "generation": self._generation, "nonce": secrets.token_hex(16),
                   "sent_at": now, "capabilities": list(capability_list(capabilities))}
        validate_request(payload, self.credential, now)
        self._pending = sign_frame(payload, self.credential, "request")
        self._last_clock = now
        return self._pending

    def retry(self, now: int) -> SignedFrame:
        self._check_clock(now)
        if self._pending is None or not fresh(parse_body(self._pending.body)["sent_at"], now):
            raise WorkerRegistrationError("No fresh unresolved worker request")
        self._last_clock = now
        return self._pending

    def accept(self, frame: SignedFrame, now: int) -> dict:
        self._check_clock(now)
        if self._pending is None:
            raise WorkerRegistrationError("No matching pending worker request")
        request = parse_body(self._pending.body)
        payload = verify_frame(frame, self.credential, "response")
        expected = {key: value for key, value in request.items()
                    if key not in {"sent_at", "generation"}}
        if (set(payload) != RESPONSE_FIELDS
                or any(payload.get(key) != val for key, val in expected.items())
                or not integer(payload["sequence"]) or not integer(payload["owner_id"])
                or not integer(payload["generation"])
                or payload["request_sha256"] != request_digest(self._pending)
                or not fresh(request["sent_at"], now)
                or not fresh(payload["received_at"], now)
                or payload["received_at"] < request["sent_at"] - FUTURE_SKEW
                or payload["execution_enabled"] is not False
                or payload["live_certified"] is not False):
            raise WorkerRegistrationError("Worker acknowledgement is invalid or stale")
        if request["action"] != "register" and payload["generation"] != self._generation:
            raise WorkerRegistrationError("Worker acknowledgement generation changed")
        retiring = request["action"] == "retire"
        expected_status = {"register": "registered", "heartbeat": "reporting",
                           "retire": "retired"}[request["action"]]
        if (payload["status"] != expected_status
                or not integer(payload["lease_until"], 0, 4102444800)
                or (retiring and payload["lease_until"] != 0)
                or (not retiring and (payload["lease_until"] != payload["received_at"] + LEASE_SECONDS
                                      or payload["lease_until"] <= now))):
            raise WorkerRegistrationError("Worker acknowledgement lease is invalid")
        self._sequence = payload["sequence"]
        self._generation = payload["generation"]
        self._lease_until = payload["lease_until"]
        self._caps = capability_list(payload["capabilities"])
        self._retired = retiring
        self._pending = None
        self._last_clock = now
        return self.status(now)

    def status(self, now: int) -> dict:
        self._check_clock(now)
        # A pending heartbeat/retirement is not a verified current state.
        if self._pending is not None:
            state = "awaiting_ack"
        elif self._retired:
            state = "retired"
        elif not self._generation:
            state = "unregistered"
        elif self._lease_until <= now:
            state = "stale"
        else:
            state = "reporting"
        self._last_clock = now
        return {"state": state, "generation": self._generation, "sequence": self._sequence,
                "lease_until": self._lease_until, "capabilities": list(self._caps),
                "current": state == "reporting", "execution_enabled": False,
                "live_certified": False}
