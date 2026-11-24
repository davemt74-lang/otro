"""Phase 8A.3 native Twilio contract, with NO live HTTP or WebSocket client.

Build and verify the shapes needed by a future Cloud-authorized executor.
The only executable paths in this slice are local fixture exercises. Merely
constructing one of these value objects does not authorize contacting anyone.
"""
from __future__ import annotations

import hmac
import ipaddress
import re
import secrets
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from .security import twilio_valid

OPAQUE = re.compile(r"[0-9a-f]{32}")
TOKEN = re.compile(r"[0-9a-f]{64}")
PHONE = re.compile(r"\+[1-9][0-9]{7,14}")
HOST = re.compile(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+")
STATUSES = ("queued", "initiated", "ringing", "in-progress")
TERMINALS = frozenset({"completed", "canceled", "busy", "failed", "no-answer"})
MAX_STATUS_SEQUENCE = 64


class TwilioContractError(RuntimeError):
    """An exact, bounded carrier/stream binding could not be verified."""


def sid(value: object, prefix: str) -> bool:
    return isinstance(value, str) and re.fullmatch(prefix + r"[0-9a-fA-F]{32}", value) is not None


def opaque(value: object) -> bool:
    return isinstance(value, str) and OPAQUE.fullmatch(value) is not None


def token(value: object) -> bool:
    return isinstance(value, str) and TOKEN.fullmatch(value) is not None


def epoch(now: int) -> int:
    if type(now) is not int or not 1 <= now <= 4102444800:
        raise TwilioContractError("Invalid contract clock")
    return now


def decimal(value: object, low: int, high: int) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]{0,9}", value):
        raise TwilioContractError("Invalid decimal sequence")
    number = int(value)
    if not low <= number <= high:
        raise TwilioContractError("Decimal sequence exceeds bound")
    return number


def _origin(value: str, scheme: str) -> str:
    # Only fixed, provisioned DNS origins; this does not establish reachability,
    # trust in the domain, DNS safety, or deployment of TLS.
    if not isinstance(value, str) or len(value) > 256 or not value.isascii():
        raise TwilioContractError("Invalid configured endpoint")
    try:
        url = urlsplit(value)
        valid = (url.scheme == scheme and bool(url.hostname)
                 and HOST.fullmatch(url.hostname) is not None
                 and url.netloc.lower() == url.hostname and not url.port
                 and not url.username and not url.password and not url.query
                 and not url.fragment and url.path in ("", "/")
                 and value.rstrip("/").lower() == f"{scheme}://{url.hostname}")
        if valid:
            try:
                ipaddress.ip_address(url.hostname)
            except ValueError:
                return f"{scheme}://{url.hostname}"
    except ValueError as exc:
        raise TwilioContractError("Invalid configured endpoint") from exc
    raise TwilioContractError("Require a fixed secure DNS origin without custom path, port or query")


@dataclass(frozen=True)
class TwilioEndpoints:
    callback_origin: str
    media_origin: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "callback_origin", _origin(self.callback_origin, "https"))
        object.__setattr__(self, "media_origin", _origin(self.media_origin, "wss"))

    def voice(self, intent_id: str) -> str:
        if not opaque(intent_id):
            raise TwilioContractError("Invalid call intent")
        return self.callback_origin + "/operator/twilio/voice/" + intent_id

    def status(self, intent_id: str) -> str:
        if not opaque(intent_id):
            raise TwilioContractError("Invalid call intent")
        return self.callback_origin + "/operator/twilio/status/" + intent_id

    def media(self, intent_id: str) -> str:
        if not opaque(intent_id):
            raise TwilioContractError("Invalid call intent")
        return self.media_origin + "/operator/twilio/media/" + intent_id


@dataclass(frozen=True)
class CallPlan:
    intent_id: str
    account_sid: str
    from_number: str = field(repr=False)
    to_number: str = field(repr=False)
    endpoints: TwilioEndpoints
    expires_at: int
    time_limit: int = 300
    ring_timeout: int = 20

    def __post_init__(self) -> None:
        if (not opaque(self.intent_id) or not sid(self.account_sid, "AC")
                or not isinstance(self.endpoints, TwilioEndpoints)
                or not all(isinstance(x, str) and PHONE.fullmatch(x)
                           for x in (self.from_number, self.to_number))
                or self.from_number == self.to_number
                or type(self.time_limit) is not int or not 60 <= self.time_limit <= 900
                or type(self.ring_timeout) is not int or not 5 <= self.ring_timeout <= 30):
            raise TwilioContractError("Invalid exact call plan")
        epoch(self.expires_at)

    def create_description(self, now: int) -> dict:
        """Describe a future Calls POST; does not send it or reserve spending.

        No TwiML is accepted from models or users. Recording defaults OFF.
        A future signed voice webhook must fail closed before consented media.
        """
        epoch(now)
        if self.expires_at - now < self.time_limit + self.ring_timeout + 5:
            raise TwilioContractError("Insufficient plan lifetime for bounded call")
        return {"method": "POST",
                "resource": f"/2010-04-01/Accounts/{self.account_sid}/Calls.json",
                "form": (("From", self.from_number), ("To", self.to_number),
                         ("Url", self.endpoints.voice(self.intent_id)), ("Method", "POST"),
                         ("StatusCallback", self.endpoints.status(self.intent_id)),
                         ("StatusCallbackMethod", "POST"),
                         ("StatusCallbackEvent", "initiated"), ("StatusCallbackEvent", "ringing"),
                         ("StatusCallbackEvent", "answered"), ("StatusCallbackEvent", "completed"),
                         ("Timeout", str(self.ring_timeout)), ("TimeLimit", str(self.time_limit)),
                         ("Record", "false")),
                "execution_enabled": False}

    def match_rest_snapshot(self, snapshot: dict, call_sid: str) -> str:
        """Validate a snapshot supplied by a trusted future read-only REST client.

        JSON alone is not carrier authentication. Do not bind an unknown CallSid
        by searching for a similar phone number or time. Only an exact persisted
        provider identity or independently reconciled dispatch is eligible.
        """
        if not sid(call_sid, "CA") or not isinstance(snapshot, dict):
            raise TwilioContractError("Carrier call identity is unavailable")
        checks = {"sid": call_sid, "account_sid": self.account_sid,
                  "from": self.from_number, "to": self.to_number,
                  "direction": "outbound-api"}
        if any(snapshot.get(k) != v for k, v in checks.items()):
            raise TwilioContractError("Carrier call does not match the exact plan")
        status = snapshot.get("status")
        if not isinstance(status, str) or status not in STATUSES and status not in TERMINALS:
            raise TwilioContractError("Carrier status is not recognized")
        return status


def hangup_twiml() -> str:
    response = ET.Element("Response")
    ET.SubElement(response, "Hangup")
    return ET.tostring(response, encoding="unicode")


@dataclass(frozen=True)
class StreamBinding:
    """Expected stream metadata for a *local fixture* after its assumed consent gate.

    Not a consent proof or permission to generate or publish live TwiML.
    """
    plan: CallPlan
    call_sid: str
    nonce: str = field(repr=False)
    deadline: int

    def __post_init__(self) -> None:
        if (not isinstance(self.plan, CallPlan) or not sid(self.call_sid, "CA")
                or not token(self.nonce) or epoch(self.deadline) > self.plan.expires_at):
            raise TwilioContractError("Invalid exact stream binding")

    @classmethod
    def fixture(cls, plan: CallPlan, call_sid: str, deadline: int) -> "StreamBinding":
        return cls(plan, call_sid, secrets.token_hex(32), deadline)

    def describe_connect(self, now: int) -> str:
        """Non-networked TwiML fixture; future receiver MUST verify live consent."""
        if epoch(now) >= self.deadline:
            raise TwilioContractError("Stream binding expired")
        response = ET.Element("Response")
        connect = ET.SubElement(response, "Connect")
        stream = ET.SubElement(connect, "Stream", {"url": self.plan.endpoints.media(self.plan.intent_id)})
        ET.SubElement(stream, "Parameter", {"name": "intent", "value": self.plan.intent_id})
        ET.SubElement(stream, "Parameter", {"name": "nonce", "value": self.nonce})
        # If socket disconnects, never continue into another action or call.
        ET.SubElement(response, "Hangup")
        return ET.tostring(response, encoding="unicode")

    def match_start(self, stream: dict, now: int) -> str:
        if epoch(now) >= self.deadline or not isinstance(stream, dict):
            raise TwilioContractError("Stream binding expired or unavailable")
        info = stream.get("start")
        if not isinstance(info, dict):
            raise TwilioContractError("Stream start missing metadata")
        stream_sid = stream.get("streamSid")
        media_format = info.get("mediaFormat")
        parameters = info.get("customParameters")
        if (not sid(stream_sid, "MZ") or info.get("streamSid") != stream_sid
                or info.get("accountSid") != self.plan.account_sid
                or info.get("callSid") != self.call_sid or info.get("tracks") != ["inbound"]
                or not isinstance(parameters, dict) or set(parameters) != {"intent", "nonce"}
                or parameters["intent"] != self.plan.intent_id or not token(parameters["nonce"])
                or not hmac.compare_digest(parameters["nonce"], self.nonce)
                or not isinstance(media_format, dict)
                or media_format.get("encoding") != "audio/x-mulaw"
                or type(media_format.get("sampleRate")) is not int or media_format["sampleRate"] != 8000
                or type(media_format.get("channels")) is not int or media_format["channels"] != 1):
            raise TwilioContractError("Media stream identity or format does not match")
        return stream_sid


def check_signature(auth_token: str, expected_url: str, form: dict, signature: str) -> None:
    # Only single-valued form inputs are accepted by this contract. A future
    # framework ingress must detect duplicate form keys before normalization.
    if (not isinstance(auth_token, str) or not 1 <= len(auth_token) <= 256
            or not isinstance(signature, str) or not re.fullmatch(r"[A-Za-z0-9+/]{27}=", signature)
            or not isinstance(form, dict) or len(form) > 128
            or any(not isinstance(k, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,100}", k)
                   or not isinstance(v, str) or len(v) > 4096 for k, v in form.items())
            or sum(len(k) + len(v) for k, v in form.items()) > 16384
            or not twilio_valid(auth_token, expected_url, form, signature)):
        raise TwilioContractError("Carrier request signature is invalid")


def authenticate_media_upgrade(binding: StreamBinding, auth_token: str,
                               signed_url: str, signature: str) -> None:
    """Fixture WSS signature validation against the exact configured URL.

    Live provider canonicalization (including a trailing slash if required)
    must be certified with Twilio's SDK at actual deployment, not inferred
    from request Host or proxy headers.
    """
    expected = binding.plan.endpoints.media(binding.plan.intent_id)
    if signed_url != expected:
        raise TwilioContractError("Unexpected media handshake URL")
    check_signature(auth_token, expected, {}, signature)


@dataclass
class CallProgress:
    """In-memory callback reducer, for fixture replay into a future durable ledger.

    Completion describes the carrier leg, not conversation success or consent.
    Stream stop alone is not carrier-terminal proof. No callback creates a call.
    """
    plan: CallPlan
    call_sid: str
    status: str = field(default="unconfirmed", init=False)
    sequence: int = field(default=-1, init=False)
    needs_review: bool = field(default=False, init=False)
    _receipts: dict[int, str] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self):
        if not isinstance(self.plan, CallPlan) or not sid(self.call_sid, "CA"):
            raise TwilioContractError("Known carrier CallSid required")

    def observe(self, form: dict, signature: str, auth_token: str) -> dict:
        check_signature(auth_token, self.plan.endpoints.status(self.plan.intent_id), form, signature)
        exact = {"AccountSid": self.plan.account_sid, "CallSid": self.call_sid,
                 "From": self.plan.from_number, "To": self.plan.to_number,
                 "Direction": "outbound-api", "CallbackSource": "call-progress-events"}
        if any(form.get(k) != v for k, v in exact.items()):
            raise TwilioContractError("Callback does not match the expected outbound leg")
        sequence = decimal(form.get("SequenceNumber"), 0, MAX_STATUS_SEQUENCE)
        status = form.get("CallStatus")
        if not isinstance(status, str) or status not in STATUSES and status not in TERMINALS:
            raise TwilioContractError("Unexpected carrier status")
        # Compare the normalized event status, not incidental retry metadata.
        previous = self._receipts.get(sequence)
        if previous is not None:
            if previous != status:
                self.needs_review = True
                raise TwilioContractError("Conflicting callback sequence")
            return self.view()
        self._receipts[sequence] = status
        if self.status in TERMINALS:
            if status in TERMINALS and status != self.status:
                self.needs_review = True
            elif status in STATUSES and sequence > self.sequence:
                self.needs_review = True
            return self.view()  # Never reopen a terminal carrier leg.
        if status in TERMINALS:
            if sequence < self.sequence:
                self.needs_review = True
            self.status = status
            self.sequence = max(sequence, self.sequence)
        elif sequence > self.sequence:
            if self.status in STATUSES and STATUSES.index(status) < STATUSES.index(self.status):
                self.needs_review = True
            else:
                self.status = status
                self.sequence = sequence
        return self.view()

    def view(self) -> dict:
        return {"carrier_status": self.status, "sequence": self.sequence,
                "carrier_terminal": self.status in TERMINALS and not self.needs_review,
                "needs_review": self.needs_review, "conversation_verified": False,
                "execution_enabled": False, "live_certified": False}

    def cancel_description(self) -> dict:
        # No provider action: a future executor must verify current authority,
        # read status, then reconcile. Do not replay an unknown create request.
        if self.needs_review or self.status == "unconfirmed" or self.status in TERMINALS:
            raise TwilioContractError("Read-only carrier reconciliation required")
        # Twilio supports 'canceled' while queued/ringing, 'completed' when active.
        return {"method": "POST",
                "resource": f"/2010-04-01/Accounts/{self.plan.account_sid}/Calls/{self.call_sid}.json",
                "form": (("Status", "completed" if self.status == "in-progress" else "canceled"),),
                "execution_enabled": False}
