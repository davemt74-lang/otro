"""Participant/floor controls use only synthetic carrier frames."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

from telephony.operator_session_simulation import SessionBinding
from telephony.operator_twilio_contract import CallPlan, StreamBinding, TwilioEndpoints, TwilioContractError
from telephony.operator_twilio_media import FixtureMediaRegistry
from telephony.operator_participant_control import FixtureParticipantController, FixtureSpeechGrant

NOW = 1800000000
ACCOUNT = "AC" + "a" * 32
CALL = "CA" + "b" * 32
STREAM = "MZ" + "c" * 32
INTENT = "d" * 32
NONCE = "e" * 64
KEY = "public-synthetic-only"
PCM = b"\0\0" * 160


class ParticipantControlTests(unittest.TestCase):
    def setUp(self):
        endpoints = TwilioEndpoints("https://control.example.test", "wss://media.example.test")
        plan = CallPlan(INTENT, ACCOUNT, "+12025550101", "+12025550102", endpoints, NOW + 600)
        self.binding = StreamBinding(plan, CALL, NONCE, NOW + 300)
        principal = SessionBinding(1, 2, 3, 4, "1" * 32, "scheduled", "2" * 32)
        self.grant = FixtureSpeechGrant(principal, self.binding, 1, NOW + 90, True, True)
        self.current = self.grant
        self.seq = 1

    def create(self, grant=None, reader=None):
        url = self.binding.plan.endpoints.media(INTENT)
        signature = base64.b64encode(hmac.new(KEY.encode(), url.encode(), hashlib.sha1).digest()).decode()
        return FixtureParticipantController(grant or self.grant, reader or (lambda: self.current),
                                            FixtureMediaRegistry(), KEY, url, signature, NOW)

    def active(self):
        controller = self.create()
        controller.receive(json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}), NOW)
        controller.receive(json.dumps({"event": "start", "sequenceNumber": "1", "streamSid": STREAM,
                           "start": {"streamSid": STREAM, "accountSid": ACCOUNT, "callSid": CALL,
                                     "tracks": ["inbound"], "customParameters": {"intent": INTENT, "nonce": NONCE},
                                     "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}}}), NOW)
        self.seq = 1
        return controller

    def event(self, controller, kind, now=NOW, **fields):
        self.seq += 1
        return controller.receive(json.dumps({"event": kind, "sequenceNumber": str(self.seq),
                                              "streamSid": STREAM, **fields}), now)

    def acknowledge(self, controller, receipt, now=NOW):
        return self.event(controller, "mark", now, mark={"name": receipt.mark_name})

    def kinds(self, controller):
        return [kind for kind, _ in controller.output_trace()]

    def test_grant_requires_both_fixture_approvals(self):
        for grant in (replace(self.grant, owner_approved=False),
                      replace(self.grant, recipient_agreed=False),
                      replace(self.grant, expires_at=NOW)):
            with self.assertRaises(TwilioContractError):
                self.create(grant, lambda: grant)

    def test_grant_revision_must_match(self):
        with self.assertRaises(TwilioContractError):
            self.create(reader=lambda: replace(self.grant, revision=2))

    def test_cannot_speak_before_stream_start(self):
        c = self.create()
        with self.assertRaises(TwilioContractError):
            c.begin_agent_turn(NOW)
        self.assertEqual(c.output_trace(), ())

    def test_active_turn_and_playback_backpressure(self):
        c = self.active()
        permit = c.begin_agent_turn(NOW)
        receipt = c.publish_pcm(permit, PCM, NOW)
        self.assertFalse(receipt.execution_enabled)
        self.assertEqual(self.kinds(c), ["media", "mark"])
        with self.assertRaises(TwilioContractError):
            c.begin_agent_turn(NOW)
        with self.assertRaises(TwilioContractError):
            c.publish_pcm(permit, PCM, NOW)
        self.assertEqual(self.acknowledge(c, receipt).mark_outcome, "buffer_acknowledged")
        c.publish_pcm(permit, PCM, NOW)
        self.assertEqual(self.kinds(c), ["media", "mark", "media", "mark"])

    def test_caller_activity_owns_floor(self):
        c = self.active()
        c.caller_activity(True, NOW)
        self.assertEqual(c.report(NOW)["floor"], "caller")
        with self.assertRaises(TwilioContractError):
            c.begin_agent_turn(NOW)
        c.caller_activity(False, NOW)
        self.assertEqual(c.report(NOW)["floor"], "listening")
        c.begin_agent_turn(NOW)

    def test_barge_in_clears_and_fences_late_synthesis(self):
        c = self.active()
        old = c.begin_agent_turn(NOW)
        receipt = c.publish_pcm(old, PCM, NOW)
        c.caller_activity(True, NOW + 1)
        c.caller_activity(True, NOW + 1)
        self.assertEqual(self.kinds(c), ["media", "mark", "clear"])
        with self.assertRaises(TwilioContractError):
            c.publish_pcm(old, PCM, NOW + 1)
        c.caller_activity(False, NOW + 1)
        with self.assertRaises(TwilioContractError):
            c.begin_agent_turn(NOW + 1)
        self.assertEqual(self.acknowledge(c, receipt, NOW + 2).mark_outcome, "cleared_or_unconfirmed")
        fresh = c.begin_agent_turn(NOW + 2)
        self.assertGreater(fresh.generation, old.generation)
        self.assertEqual(c.report(NOW + 2)["floor"], "agent")

    def test_end_turn_invalidates_permit(self):
        c = self.active()
        permit = c.begin_agent_turn(NOW)
        c.end_agent_turn(permit, NOW)
        with self.assertRaises(TwilioContractError):
            c.publish_pcm(permit, PCM, NOW)
        self.assertGreater(c.begin_agent_turn(NOW).generation, permit.generation)

    def test_forged_permit_and_cross_owner_stop_denied(self):
        c = self.active()
        permit = c.begin_agent_turn(NOW)
        with self.assertRaises(TwilioContractError):
            c.publish_pcm(replace(permit, token="0" * 32), PCM, NOW)
        with self.assertRaises(TwilioContractError):
            c.owner_takeover(2)
        with self.assertRaises(TwilioContractError):
            c.owner_stop(True)
        self.assertEqual(c.output_trace(), ())
        self.assertEqual(c.report(NOW)["floor"], "agent")

    def test_takeover_fences_output_without_claiming_human_join(self):
        c = self.active()
        permit = c.begin_agent_turn(NOW)
        c.publish_pcm(permit, PCM, NOW)
        result = c.owner_takeover(1)
        self.assertEqual(result["state"], "closed")
        self.assertFalse(result["owner_joined"])
        self.assertFalse(result["carrier_terminal"])
        self.assertEqual(self.kinds(c), ["media", "mark", "clear", "close"])
        with self.assertRaises(TwilioContractError):
            c.publish_pcm(permit, PCM, NOW)
        c.owner_takeover(1)
        self.assertEqual(self.kinds(c), ["media", "mark", "clear", "close"])

    def test_simultaneous_takeover_and_speech_is_serialized(self):
        for _ in range(12):
            c = self.active()
            permit = c.begin_agent_turn(NOW)
            gate = threading.Barrier(2)
            def speak():
                gate.wait(timeout=2)
                try:
                    c.publish_pcm(permit, PCM, NOW)
                except TwilioContractError:
                    pass
            def stop():
                gate.wait(timeout=2)
                c.owner_takeover(1)
            with ThreadPoolExecutor(max_workers=2) as pool:
                a = pool.submit(speak)
                b = pool.submit(stop)
                a.result(timeout=3)
                b.result(timeout=3)
            self.assertIn(self.kinds(c), (["clear", "close"], ["media", "mark", "clear", "close"]))

    def test_consent_change_blocks_audio_and_closes(self):
        c = self.active()
        permit = c.begin_agent_turn(NOW)
        self.current = replace(self.grant, recipient_agreed=False)
        with self.assertRaises(TwilioContractError):
            c.publish_pcm(permit, PCM, NOW + 1)
        self.assertEqual(self.kinds(c), ["clear", "close"])
        self.assertEqual(c.report(NOW + 1)["reason"], "consent_revoked")

    def test_missing_authority_and_replacement_fail_closed(self):
        for changed in (None, replace(self.grant, revision=2),
                        replace(self.grant, principal=replace(self.grant.principal, contact_id=5))):
            self.current = self.grant
            c = self.active()
            self.current = changed
            self.assertEqual(c.report(NOW + 1)["state"], "closed")
            self.assertEqual(self.kinds(c), ["clear", "close"])

    def test_expiry_and_backwards_clock_fence(self):
        c = self.active()
        c.begin_agent_turn(NOW)
        self.assertEqual(c.report(NOW + 90)["state"], "closed")
        other = self.active()
        other.report(NOW + 1)
        self.assertEqual(other.report(NOW)["state"], "closed")

    def test_turn_duration_and_sample_budget(self):
        c = self.active()
        permit = c.begin_agent_turn(NOW, 1)
        for _ in range(5):
            receipt = c.publish_pcm(permit, b"\0\0" * 1600, NOW)
            self.acknowledge(c, receipt)
        with self.assertRaises(TwilioContractError):
            c.publish_pcm(permit, PCM, NOW)
        self.assertEqual(c.report(NOW + 1)["floor"], "listening")
        with self.assertRaises(TwilioContractError):
            c.publish_pcm(permit, PCM, NOW + 1)

    def test_mark_timeout_closes_and_never_claims_terminal(self):
        c = self.active()
        permit = c.begin_agent_turn(NOW)
        c.publish_pcm(permit, PCM, NOW)
        result = c.report(NOW + 5)
        self.assertEqual(result["state"], "closed")
        self.assertFalse(result["carrier_terminal"])
        self.assertEqual(self.kinds(c)[-1], "close")

    def test_dtmf_is_not_speaking_permission(self):
        c = self.active()
        c.caller_activity(True, NOW)
        self.assertEqual(self.event(c, "dtmf", dtmf={"track": "inbound", "digit": "1"}).digit, "1")
        self.assertEqual(c.report(NOW)["floor"], "caller")
        with self.assertRaises(TwilioContractError):
            c.begin_agent_turn(NOW)

    def test_stop_is_not_carrier_terminal(self):
        c = self.active()
        self.event(c, "stop", stop={"accountSid": ACCOUNT, "callSid": CALL})
        result = c.report(NOW)
        self.assertEqual(result["state"], "closed")
        self.assertFalse(result["carrier_terminal"])

    def test_no_network_or_private_data_in_reports(self):
        with patch("socket.create_connection", side_effect=AssertionError("network prohibited")):
            c = self.active()
            permit = c.begin_agent_turn(NOW)
            c.publish_pcm(permit, PCM, NOW)
            c.owner_stop(1)
        output = repr((c.report(NOW), c.output_trace()))
        for value in (CALL, ACCOUNT, STREAM, permit.token, KEY, "+12025550101"):
            self.assertNotIn(value, output)
        self.assertFalse(c.report(NOW)["execution_enabled"])
        self.assertFalse(c.report(NOW)["live_certified"])


if __name__ == "__main__":
    unittest.main()
