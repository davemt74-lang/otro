"""8A.5B: non-networked Twilio two-human conference takeover reference.

Requires an independently approved existing owner leg. Never creates a call,
assumes a personal phone number is verified, or treats a Stream as a conference.
Every carrier mutation is a one-use fixture description; real dispatch,
durable reconciliation, consent proof and distributed fencing are separate.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import threading
from xml.etree import ElementTree as ET

from .operator_session_simulation import SessionBinding
from .operator_twilio_contract import StreamBinding, TwilioContractError, epoch, opaque, sid
from .operator_participant_control import FixtureParticipantController


@dataclass(frozen=True)
class FixtureConferenceGrant:
    principal: SessionBinding
    stream: StreamBinding = field(repr=False)
    handoff_id: str = field(repr=False)
    owner_call_sid: str = field(repr=False)
    revision: int
    expires_at: int
    owner_approved: bool
    recipient_agreed: bool

    def __post_init__(self):
        if (not isinstance(self.principal, SessionBinding)
                or not isinstance(self.stream, StreamBinding)
                or not opaque(self.handoff_id) or not sid(self.owner_call_sid, "CA")
                or self.owner_call_sid == self.stream.call_sid
                or type(self.revision) is not int or not 1 <= self.revision <= 9007199254740991
                or epoch(self.expires_at) > self.stream.deadline
                or type(self.owner_approved) is not bool or type(self.recipient_agreed) is not bool):
            raise TwilioContractError("Invalid synthetic conference grant")


class FixtureConferenceHandoff:
    """One-way, muted-first two-person handoff; no concurrent Agent audio.

    The snapshot reader in this exercise supplies synthetic normalized
    evidence. A real executor must authenticate the exact existing owner
    leg, secure call updates, read actual participant state from Twilio,
    persist reservations before effects and handle provider ambiguity.
    """
    def __init__(self, grant: FixtureConferenceGrant, read_grant: Callable,
                 participant: FixtureParticipantController, now: int):
        if (not isinstance(grant, FixtureConferenceGrant) or not callable(read_grant)
                or not grant.owner_approved or not grant.recipient_agreed
                or epoch(now) >= grant.expires_at
                or not isinstance(participant, FixtureParticipantController)
                or not participant.matches_handoff(grant.principal, grant.stream)):
            raise TwilioContractError("Synthetic conference authority unavailable")
        self._grant = grant
        self._reader = read_grant
        self._participant = participant
        self._lock = threading.RLock()
        self._last_now = now
        self._state = "prepared"
        self._conference_sid = None
        self._confirmed = {}
        self._expected = {}
        self._reason = ""
        self._name = "vp3-handoff-" + grant.handoff_id
        self._check(now)

    def _review(self, reason: str):
        self._state = "needs_review"
        self._reason = reason
        self._confirmed = {}
        self._expected = {}
        # Fence the original fixture, not a real carrier action. Do not report
        # the conference or either call terminal from this local operation.
        self._participant.owner_stop(self._grant.principal.owner_id)

    def _check(self, now: int):
        if self._state in ("needs_review", "conference_ended"):
            raise TwilioContractError("Conference handoff is fenced")
        try:
            valid = (self._last_now <= epoch(now) < self._grant.expires_at
                     and self._reader() == self._grant)
        except Exception:
            valid = False
        if not valid:
            self._review("authority_or_deadline_changed")
            raise TwilioContractError("Synthetic conference authority changed")
        self._last_now = now

    def _owner(self, owner_id: int):
        if type(owner_id) is not int or owner_id != self._grant.principal.owner_id:
            raise TwilioContractError("Conference owner binding is invalid")

    def _leg(self, role: str) -> str:
        if role == "owner":
            return self._grant.owner_call_sid
        if role == "caller":
            return self._grant.stream.call_sid
        raise TwilioContractError("Unknown conference participant")

    def _route(self, role: str, now: int) -> dict:
        # Descriptions only, no HTTP. Both participants initially muted;
        # first participant controls callbacks, so both use the exact same URL.
        response = ET.Element("Response")
        dial = ET.SubElement(response, "Dial", {"timeLimit": str(min(300, self._grant.expires_at-now))})
        attrs = {"muted": "true", "beep": "true", "startConferenceOnEnter": "true" if role=="caller" else "false",
                 "endConferenceOnExit": "true", "participantLabel": role, "maxParticipants": "2",
                 "record": "do-not-record", "waitUrl": "",
                 "statusCallback": self._grant.stream.plan.endpoints.callback_origin
                     + "/operator/twilio/conference/" + self._grant.handoff_id,
                 "statusCallbackMethod": "POST", "statusCallbackEvent": "start end join leave mute hold"}
        ET.SubElement(dial, "Conference", attrs).text = self._name
        ET.SubElement(response, "Hangup")
        return {"method": "POST",
                "resource": f"/2010-04-01/Accounts/{self._grant.stream.plan.account_sid}/Calls/{self._leg(role)}.json",
                "form": (("Twiml", ET.tostring(response, encoding="unicode")),),
                "execution_enabled": False}

    def reserve_owner_route(self, owner_id: int, now: int) -> dict:
        with self._lock:
            self._owner(owner_id);self._check(now)
            if self._state != "prepared":
                raise TwilioContractError("Owner route already reserved")
            self._state = "owner_route_reserved"
            return self._route("owner", now)

    def reserve_caller_route(self, owner_id: int, now: int) -> dict | None:
        with self._lock:
            self._owner(owner_id);self._check(now)
            if self._state != "owner_ready":
                raise TwilioContractError("Owner conference leg not verified")
            if not self._participant.pause_for_handoff(self._grant.principal, self._grant.stream, now):
                return None  # Wait for the exact cleared playback mark, never replay.
            self._state = "caller_route_reserved"
            return self._route("caller", now)

    def _snapshot(self, data: dict) -> tuple[str, dict]:
        if (not isinstance(data, dict) or data.get("account_sid") != self._grant.stream.plan.account_sid
                or data.get("friendly_name") != self._name or not sid(data.get("sid"), "CF")
                or (self._conference_sid is not None and data["sid"] != self._conference_sid)
                or data.get("status") not in ("init", "in-progress", "completed")
                or not isinstance(data.get("participants"), list) or len(data["participants"]) > 2):
            raise TwilioContractError("Conference evidence does not match")
        participants = {}
        for row in data["participants"]:
            if not isinstance(row, dict) or row.get("label") not in ("owner", "caller"):
                raise TwilioContractError("Unexpected conference participant")
            role = row["label"]
            if (role in participants or row.get("call_sid") != self._leg(role)
                    or row.get("conference_sid") != data["sid"]
                    or row.get("account_sid") != data["account_sid"]
                    or row.get("status") != "connected" or row.get("hold") is not False
                    or type(row.get("muted")) is not bool):
                raise TwilioContractError("Conference participant evidence does not match")
            participants[role] = row["muted"]
        return data["status"], participants

    def observe(self, evidence: dict, now: int) -> dict:
        """Consume fixture trusted read-only evidence; never replay mutations."""
        with self._lock:
            self._check(now)
            try:
                status, parts = self._snapshot(evidence)
                if self._state == "owner_route_reserved":
                    if status not in ("init","in-progress") or parts != {"owner": True}:
                        raise TwilioContractError("Muted owner leg is not verified")
                    self._conference_sid = evidence["sid"]
                    self._state = "owner_ready"
                    self._confirmed = parts
                elif self._state == "caller_route_reserved":
                    if status != "in-progress" or parts != {"owner":True,"caller":True}:
                        raise TwilioContractError("Exact two muted participants are not verified")
                    report = self._participant.report(now)
                    if not report["handoff_paused"] or not report["stream_stop_seen"]:
                        raise TwilioContractError("Original stream stop is not verified")
                    self._state = "participants_verified"
                    self._confirmed = parts
                elif self._state == "mute_reserved":
                    if status != "in-progress" or parts != self._expected:
                        raise TwilioContractError("Participant mutation is unresolved")
                    self._confirmed = parts
                    self._state = "participants_verified"
                elif self._state == "disconnect_reserved":
                    if status != "completed" or parts:
                        raise TwilioContractError("Conference closure is not verified")
                    self._state = "conference_ended"
                    self._confirmed = {}
                else:
                    # A read may confirm the existing exact state, not permit
                    # the caller to skip reservations or add a third leg.
                    if self._state not in ("owner_ready","participants_verified") or parts != self._confirmed:
                        raise TwilioContractError("Unexpected conference transition")
                    if self._state=="participants_verified" and status!="in-progress":
                        raise TwilioContractError("Conference status changed")
                    if self._state=="owner_ready" and status not in ("init","in-progress"):
                        raise TwilioContractError("Owner waiting room changed")
                return self._report()
            except TwilioContractError:
                self._review("conference_evidence_unresolved")
                raise

    def reserve_mute(self, owner_id: int, role: str, muted: bool, now: int) -> dict:
        with self._lock:
            self._owner(owner_id);self._check(now)
            if (self._state != "participants_verified" or role not in self._confirmed
                    or type(muted) is not bool or self._confirmed[role] == muted):
                raise TwilioContractError("Participant mute change unavailable")
            self._expected = {**self._confirmed,role:muted}
            self._state = "mute_reserved"
            return {"method":"POST","resource":self._participant_resource(role),
                    "form":(("Muted","true" if muted else "false"),),"execution_enabled":False}

    def _participant_resource(self, role: str) -> str:
        return (f"/2010-04-01/Accounts/{self._grant.stream.plan.account_sid}/Conferences/"
                f"{self._conference_sid}/Participants/{self._leg(role)}.json")

    def reserve_disconnect(self, owner_id: int, role: str, now: int) -> dict:
        with self._lock:
            self._owner(owner_id);self._check(now)
            if self._state!="participants_verified" or role not in self._confirmed:
                raise TwilioContractError("Participant disconnect unavailable")
            self._state="disconnect_reserved"
            return {"method":"DELETE","resource":self._participant_resource(role),"form":(),
                    "execution_enabled":False}

    def _report(self) -> dict:
        verified=self._state=="participants_verified"
        return {"state":self._state,"reason":self._reason,
                "participants_verified":verified,
                "owner_muted":self._confirmed.get("owner") if verified else None,
                "caller_muted":self._confirmed.get("caller") if verified else None,
                "human_conversation_ready":verified and self._confirmed=={"owner":False,"caller":False},
                "agent_can_speak":False,"agent_resume_supported":False,
                "conference_ended":self._state=="conference_ended","carrier_terminal":False,
                "execution_enabled":False,"live_certified":False,"reference_only":True}

    def report(self, now: int) -> dict:
        with self._lock:
            if self._state not in ("needs_review","conference_ended"):
                try:self._check(now)
                except TwilioContractError:pass
            return self._report()
