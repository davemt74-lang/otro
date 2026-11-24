"""Native Twilio protocol fixtures: no sockets, carrier calls or credentials."""
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
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

from telephony.operator_twilio_contract import (
    CallPlan, CallProgress, StreamBinding, TwilioContractError, TwilioEndpoints,
    authenticate_media_upgrade, hangup_twiml,
)
from telephony.operator_twilio_media import (
    FixtureMediaRegistry, FixtureMediaSession, MAX_FRAME_BYTES, MAX_INBOUND_BYTES,
    mulaw_to_pcm16le, pcm16le_to_mulaw,
)

NOW = 1800000000
ACCOUNT = "AC" + "a" * 32
CALL = "CA" + "b" * 32
STREAM = "MZ" + "c" * 32
INTENT = "d" * 32
NONCE = "e" * 64
KEY = "public-synthetic-fixture-only"
ENDPOINTS = TwilioEndpoints("https://control.example.test", "wss://media.example.test")


def plan():
    return CallPlan(INTENT, ACCOUNT, "+12025550101", "+12025550102", ENDPOINTS, NOW + 600)


def binding():
    return StreamBinding(plan(), CALL, NONCE, NOW + 300)


def signature(url, form=None, key=KEY):
    payload = url + "".join(k + v for k, v in sorted((form or {}).items()))
    return base64.b64encode(hmac.new(key.encode(), payload.encode(), hashlib.sha1).digest()).decode()


def status_form(status="ringing", seq=1, **changes):
    p = plan()
    return dict({"AccountSid": ACCOUNT, "CallSid": CALL, "From": p.from_number,
                 "To": p.to_number, "Direction": "outbound-api",
                 "CallbackSource": "call-progress-events", "CallStatus": status,
                 "SequenceNumber": str(seq)}, **changes)


def start_frame(**changes):
    info = {"accountSid": ACCOUNT, "callSid": CALL, "streamSid": STREAM,
            "tracks": ["inbound"], "mediaFormat": {"encoding": "audio/x-mulaw",
                         "sampleRate": 8000, "channels": 1},
            "customParameters": {"intent": INTENT, "nonce": NONCE}}
    return {"event": "start", "sequenceNumber": "1", "streamSid": STREAM,
            "start": dict(info, **changes)}


def frame(event, seq, **payload):
    return {"event": event, "sequenceNumber": str(seq), "streamSid": STREAM, **payload}


def media(seq=2, chunk=1, stamp=0, data=b"\xff" * 160):
    return frame("media", seq, media={"track": "inbound", "chunk": str(chunk),
                 "timestamp": str(stamp), "payload": base64.b64encode(data).decode()})


def connected():
    return {"event": "connected", "protocol": "Call", "version": "1.0.0"}


class TwilioCallContractTests(unittest.TestCase):
    def test_outbound_plan_is_description_not_dispatch(self):
        p = plan()
        with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")), \
                patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
            result = p.create_description(NOW)
        self.assertFalse(result["execution_enabled"])
        self.assertEqual(result["method"], "POST")
        self.assertEqual(result["resource"], f"/2010-04-01/Accounts/{ACCOUNT}/Calls.json")
        self.assertEqual(dict(result["form"])["Record"], "false")
        self.assertEqual(dict(result["form"])["TimeLimit"], "300")
        self.assertEqual([v for k, v in result["form"] if k == "StatusCallbackEvent"],
                         ["initiated", "ringing", "answered", "completed"])
        self.assertIn("StatusCallbackEvent=initiated&StatusCallbackEvent=ringing",
                      urlencode(result["form"]))

    def test_reject_untrusted_or_non_tls_endpoints(self):
        for url in ("http://control.example.test", "https://evil@control.example.test",
                    "https://control.example.test:8443", "https://control.example.test/?foo=1",
                    "https://control.example.test/#bad", "https://control.example.test/path",
                    "https://127.0.0.1", "https://localhost", "https://control.example.test\n",
                    "https://control.example.test//"):
            with self.subTest(url=url), self.assertRaises(TwilioContractError):
                TwilioEndpoints(url, ENDPOINTS.media_origin)
        with self.assertRaises(TwilioContractError):
            TwilioEndpoints(ENDPOINTS.callback_origin, "ws://media.example.test")

    def test_exact_plan_validation(self):
        for change in ({"intent_id": "oops"}, {"account_sid": "CA" + "a" * 32},
                       {"to_number": "client:other"}, {"to_number": plan().from_number},
                       {"time_limit": True}, {"time_limit": 901}, {"ring_timeout": 60},
                       {"expires_at": 0}):
            with self.subTest(change=change), self.assertRaises(TwilioContractError):
                replace(plan(), **change)
        with self.assertRaises(TwilioContractError):
            plan().create_description(NOW + 276)

    def test_twiml_fixed_stream_and_hangup_after_disconnect(self):
        root = ET.fromstring(binding().describe_connect(NOW))
        stream = root.find("./Connect/Stream")
        self.assertIsNotNone(stream)
        self.assertEqual(stream.get("url"), ENDPOINTS.media(INTENT))
        self.assertEqual({p.get("name"): p.get("value") for p in stream},
                         {"intent": INTENT, "nonce": NONCE})
        self.assertEqual([child.tag for child in root], ["Connect", "Hangup"])
        self.assertEqual([c.tag for c in ET.fromstring(hangup_twiml())], ["Hangup"])
        self.assertNotIn(NONCE, repr(binding()))
        with self.assertRaises(TwilioContractError):
            binding().describe_connect(NOW + 300)

    def test_rest_snapshot_exact_account_call_destination_and_direction(self):
        p = plan()
        data = {"sid": CALL, "account_sid": ACCOUNT, "from": p.from_number,
                "to": p.to_number, "direction": "outbound-api", "status": "in-progress"}
        self.assertEqual(p.match_rest_snapshot(data, CALL), "in-progress")
        for key, value in (("sid", "CA" + "f" * 32), ("account_sid", "AC" + "f" * 32),
                           ("to", "+12025550103"), ("from", "+12025550104"),
                           ("direction", "inbound"), ("status", "unknown")):
            with self.subTest(key=key), self.assertRaises(TwilioContractError):
                p.match_rest_snapshot({**data, key: value}, CALL)

    def test_callback_signature_and_exact_leg(self):
        c = CallProgress(plan(), CALL)
        f = status_form()
        url = ENDPOINTS.status(INTENT)
        with self.assertRaises(TwilioContractError):
            c.observe(f, signature(url, f, "wrong-key"), KEY)
        with self.assertRaises(TwilioContractError):
            c.observe(f, signature(ENDPOINTS.status("f" * 32), f), KEY)
        for changes in ({"AccountSid": "AC" + "f" * 32}, {"CallSid": "CA" + "f" * 32},
                        {"From": "+12025550105"}, {"To": "+12025550106"},
                        {"Direction": "outbound-dial"}, {"CallbackSource": "other"},
                        {"SequenceNumber": "-1"}, {"SequenceNumber": "99999999999"},
                        {"CallStatus": "success"}):
            other = status_form(**changes)
            with self.subTest(changes=changes), self.assertRaises(TwilioContractError):
                c.observe(other, signature(url, other), KEY)
        self.assertEqual(c.view()["carrier_status"], "unconfirmed")

    def observe(self, c, state, seq, **other):
        f = status_form(state, seq, **other)
        return c.observe(f, signature(ENDPOINTS.status(INTENT), f), KEY)

    def test_callbacks_may_arrive_out_of_order_without_reopening(self):
        c = CallProgress(plan(), CALL)
        self.observe(c, "in-progress", 2)
        self.observe(c, "ringing", 1)
        self.observe(c, "completed", 3)
        view = self.observe(c, "initiated", 0)
        self.assertEqual(view["carrier_status"], "completed")
        self.assertTrue(view["carrier_terminal"])
        self.assertFalse(view["conversation_verified"])
        self.assertFalse(view["execution_enabled"])

    def test_duplicate_callback_is_idempotent_and_conflict_is_fenced(self):
        c = CallProgress(plan(), CALL)
        first = self.observe(c, "ringing", 1)
        self.assertEqual(first, self.observe(c, "ringing", 1, Timestamp="later metadata"))
        with self.assertRaises(TwilioContractError):
            self.observe(c, "in-progress", 1)
        self.assertTrue(c.view()["needs_review"])
        with self.assertRaises(TwilioContractError):
            c.cancel_description()

    def test_regressing_progress_is_never_accepted(self):
        c = CallProgress(plan(), CALL)
        self.observe(c, "in-progress", 2)
        view = self.observe(c, "ringing", 3)
        self.assertTrue(view["needs_review"])
        self.assertEqual(view["carrier_status"], "in-progress")

    def test_terminal_conflicts_require_reconciliation(self):
        c = CallProgress(plan(), CALL)
        self.observe(c, "completed", 3)
        view = self.observe(c, "failed", 4)
        self.assertTrue(view["needs_review"])
        self.assertFalse(view["carrier_terminal"])
        self.assertEqual(view["carrier_status"], "completed")

    def test_late_terminal_conflict_requires_review(self):
        c = CallProgress(plan(), CALL)
        self.observe(c, "in-progress", 4)
        view = self.observe(c, "completed", 3)
        self.assertFalse(view["carrier_terminal"])
        self.assertTrue(view["needs_review"])

    def test_non_success_terminal_states_remain_distinct(self):
        for state in ("busy", "no-answer", "failed", "canceled"):
            c = CallProgress(plan(), CALL)
            view = self.observe(c, state, 1)
            self.assertEqual(view["carrier_status"], state)
            self.assertTrue(view["carrier_terminal"])
            self.assertFalse(view["conversation_verified"])

    def test_cancel_descriptions_are_state_specific_and_never_execute(self):
        c = CallProgress(plan(), CALL)
        with self.assertRaises(TwilioContractError):
            c.cancel_description()
        self.observe(c, "queued", 0)
        self.assertEqual(dict(c.cancel_description()["form"]), {"Status": "canceled"})
        self.observe(c, "in-progress", 2)
        self.assertEqual(dict(c.cancel_description()["form"]), {"Status": "completed"})
        self.assertFalse(c.cancel_description()["execution_enabled"])
        self.observe(c, "completed", 3)
        with self.assertRaises(TwilioContractError):
            c.cancel_description()

    def test_wss_signature_exact_configured_url(self):
        url = ENDPOINTS.media(INTENT)
        authenticate_media_upgrade(binding(), KEY, url, signature(url))
        for other in (url + "/", url + "?anything=1", url.replace("wss:", "https:")):
            with self.subTest(other=other), self.assertRaises(TwilioContractError):
                authenticate_media_upgrade(binding(), KEY, other, signature(other))
        with self.assertRaises(TwilioContractError):
            authenticate_media_upgrade(binding(), KEY, url, signature(url, key="wrong"))


class TwilioMediaTests(unittest.TestCase):
    def setUp(self):
        self.registry = FixtureMediaRegistry()

    def create(self, registry=None, expected=None, now=NOW):
        b = expected or binding()
        url = b.plan.endpoints.media(b.plan.intent_id)
        return FixtureMediaSession(b, registry or self.registry, KEY, url, signature(url), now)

    def send(self, s, message, now=NOW):
        return s.receive(json.dumps(message), now)

    def activate(self, registry=None, expected=None):
        s = self.create(registry, expected)
        self.send(s, connected())
        self.send(s, start_frame())
        return s

    def test_exact_handshake_and_start_required(self):
        s = self.create()
        self.assertEqual(self.send(s, connected()).event, "connected")
        self.assertEqual(self.send(s, start_frame()).event, "start")
        self.assertEqual(s.check_health(NOW)["state"], "active")
        self.assertFalse(s.check_health(NOW)["execution_enabled"])

    def test_first_message_cannot_be_audio_or_start(self):
        for msg in (start_frame(), media(), {**connected(), "version": "2.0.0"}):
            s = self.create()
            with self.assertRaises(TwilioContractError):
                self.send(s, msg)
            self.assertTrue(s.check_health(NOW)["close_required"])

    def test_forged_stream_start_is_rejected(self):
        for change in ({"accountSid": "AC" + "f" * 32}, {"callSid": "CA" + "f" * 32},
                       {"streamSid": "MZ" + "f" * 32}, {"tracks": ["outbound"]},
                       {"mediaFormat": {"encoding": "audio/pcm", "sampleRate": 8000, "channels": 1}},
                       {"mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 16000, "channels": 1}},
                       {"mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": True}},
                       {"customParameters": {"intent": INTENT, "nonce": "0" * 64}},
                       {"customParameters": {"intent": "f" * 32, "nonce": NONCE}},
                       {"customParameters": {"intent": INTENT, "nonce": NONCE, "allow": "yes"}}):
            s = self.create()
            self.send(s, connected())
            with self.subTest(change=change), self.assertRaises(TwilioContractError):
                self.send(s, start_frame(**change))
            self.assertTrue(s.check_health(NOW)["close_required"])

    def test_simultaneous_claims_accept_only_one_stream(self):
        barrier = threading.Barrier(2)

        def claim(_):
            s = self.create()
            self.send(s, connected())
            barrier.wait(timeout=10)
            try:
                self.send(s, start_frame())
                return True
            except TwilioContractError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(claim, range(2))), 1)

    def test_spent_stream_cannot_reopen_after_stop(self):
        s = self.activate()
        self.send(s, frame("stop", 2, stop={"accountSid": ACCOUNT, "callSid": CALL}))
        other = self.create()
        self.send(other, connected())
        with self.assertRaises(TwilioContractError):
            self.send(other, start_frame())

    def test_bad_sequence_fences_media(self):
        for seq in (1, 3, "02", "-1", 100001):
            s = self.activate(FixtureMediaRegistry())
            with self.subTest(seq=seq), self.assertRaises(TwilioContractError):
                self.send(s, media(seq=seq))
            self.assertTrue(s.check_health(NOW)["close_required"])

    def test_strict_payload_and_frame_size(self):
        for raw in ("{}", '{"event":"media","event":"stop"}', '{"event":NaN}',
                    "[" + "0," * MAX_FRAME_BYTES + "0]", "\N{SNOWMAN}"):
            s = self.activate(FixtureMediaRegistry())
            with self.assertRaises(TwilioContractError):
                s.receive(raw, NOW)
        for value in ("***=", "!!!!", "", "Zm9v\n", base64.b64encode(b"x" * (MAX_INBOUND_BYTES+1)).decode()):
            s = self.activate(FixtureMediaRegistry())
            msg = media()
            msg["media"]["payload"] = value
            with self.assertRaises(TwilioContractError):
                self.send(s, msg)

    def test_media_wrong_stream_track_chunk_or_time_is_fenced(self):
        for changes in ({"track": "outbound"}, {"chunk": "2"}, {"timestamp": "-1"}):
            s = self.activate(FixtureMediaRegistry())
            msg = media()
            msg["media"].update(changes)
            with self.assertRaises(TwilioContractError):
                self.send(s, msg)
        s = self.activate(FixtureMediaRegistry())
        msg = media()
        msg["streamSid"] = "MZ" + "f" * 32
        with self.assertRaises(TwilioContractError):
            self.send(s, msg)
        s = self.activate(FixtureMediaRegistry())
        self.send(s, media(stamp=20))
        with self.assertRaises(TwilioContractError):
            self.send(s, media(seq=3, chunk=2, stamp=19))

    def test_inbound_audio_decodes_to_pcm16_without_persistence(self):
        s = self.activate()
        e = self.send(s, media())
        self.assertEqual(e.audio, b"\x00\x00" * 160)
        self.assertEqual(e.timestamp_ms, 0)
        self.assertNotIn(repr(e.audio), repr(e))
        second = self.send(s, media(seq=3, chunk=2, stamp=20, data=b"\x80"))
        self.assertEqual(second.audio, (32124).to_bytes(2, "little", signed=True))

    def test_mulaw_known_vectors(self):
        samples = [0, 0, 32124, -32124, 8, -8]
        expected = b"".join(x.to_bytes(2, "little", signed=True) for x in samples)
        self.assertEqual(mulaw_to_pcm16le(bytes([255, 127, 128, 0, 254, 126])), expected)
        for value, expected_byte in ((0, 255), (32124, 128), (-32124, 0), (8, 254), (-8, 126)):
            self.assertEqual(pcm16le_to_mulaw(value.to_bytes(2, "little", signed=True)),
                             bytes([expected_byte]))
        values = bytes(range(256))
        decoded = mulaw_to_pcm16le(values)
        # All mu-law codes except the negative-zero representation round-trip.
        self.assertEqual(pcm16le_to_mulaw(decoded), values[:127] + b"\xff" + values[128:])

    def test_pcm_bounds_and_file_headers(self):
        for invalid in (b"", b"\x00", b"RIFF" + b"\0" * 10, b"\x00" * 3202):
            with self.assertRaises(TwilioContractError):
                pcm16le_to_mulaw(invalid)
        with self.assertRaises(TwilioContractError):
            mulaw_to_pcm16le(b"x" * 1601)

    def test_outbound_audio_and_mark_backpressure(self):
        s = self.activate()
        audio = s.queue_pcm(b"\0\0" * 160, s.generation, NOW)
        m, mark = audio.messages
        self.assertEqual(m, {"event": "media", "streamSid": STREAM,
                             "media": {"payload": base64.b64encode(b"\xff" * 160).decode()}})
        self.assertEqual(mark["event"], "mark")
        with self.assertRaises(TwilioContractError):
            s.queue_pcm(b"\0\0", s.generation, NOW)
        result = self.send(s, frame("mark", 2, mark=mark["mark"]))
        self.assertEqual(result.mark_outcome, "buffer_acknowledged")
        self.assertEqual(s.queue_pcm(b"\0\0", s.generation, NOW).messages[0]["event"], "media")

    def test_barge_in_invalidates_old_audio_and_cleared_mark_is_uncertain(self):
        s = self.activate()
        generation = s.generation
        audio = s.queue_pcm(b"\0\0" * 160, generation, NOW)
        clear = s.interrupt(NOW + 1)
        self.assertEqual(clear.messages, ({"event": "clear", "streamSid": STREAM},))
        self.assertGreater(clear.generation, generation)
        with self.assertRaises(TwilioContractError):
            s.queue_pcm(b"\0\0", generation, NOW + 1)
        with self.assertRaises(TwilioContractError):
            s.queue_pcm(b"\0\0", clear.generation, NOW + 1)
        result = self.send(s, frame("mark", 2, mark=audio.messages[1]["mark"]), NOW + 2)
        self.assertEqual(result.mark_outcome, "cleared_or_unconfirmed")
        self.assertEqual(s.queue_pcm(b"\0\0", clear.generation, NOW + 2).generation,
                         clear.generation)

    def test_unknown_mark_fences_session(self):
        s = self.activate()
        with self.assertRaises(TwilioContractError):
            self.send(s, frame("mark", 2, mark={"name": "0" * 32}))
        self.assertTrue(s.check_health(NOW)["close_required"])

    def test_owner_stop_takeover_consent_revocation_and_disconnect_fence_audio(self):
        for reason in ("owner_stop", "owner_takeover", "consent_revoked",
                       "worker_lost", "transport_uncertain", "stream_closed"):
            s = self.activate(FixtureMediaRegistry())
            old = s.generation
            s.queue_pcm(b"\0\0", old, NOW)
            revoked = s.revoke(reason)
            self.assertTrue(revoked.close_required)
            self.assertEqual(revoked.messages[0]["event"], "clear")
            with self.assertRaises(TwilioContractError):
                s.queue_pcm(b"\0\0", old, NOW)
            self.assertFalse(s.check_health(NOW)["carrier_terminal"])

    def test_mark_deadline_and_session_expiry_fail_closed(self):
        s = self.activate()
        s.queue_pcm(b"\0\0", s.generation, NOW)
        self.assertTrue(s.check_health(NOW + 5)["close_required"])
        other = self.activate(FixtureMediaRegistry())
        self.assertTrue(other.check_health(NOW + 300)["close_required"])
        with self.assertRaises(TwilioContractError):
            self.create(now=NOW + 300)

    def test_backwards_clock_fails_closed(self):
        s = self.activate()
        self.send(s, media(), NOW + 1)
        with self.assertRaises(TwilioContractError):
            s.queue_pcm(b"\0\0", s.generation, NOW)
        self.assertTrue(s.check_health(NOW + 1)["close_required"])

    def test_stop_is_not_terminal_call_evidence(self):
        s = self.activate()
        self.assertEqual(self.send(s, frame("stop", 2, stop={"accountSid": ACCOUNT,
                         "callSid": CALL})).event, "stop")
        self.assertFalse(s.check_health(NOW)["carrier_terminal"])
        self.assertTrue(s.check_health(NOW)["close_required"])

    def test_wrong_stop_binding_is_not_accepted(self):
        s = self.activate()
        with self.assertRaises(TwilioContractError):
            self.send(s, frame("stop", 2, stop={"accountSid": ACCOUNT, "callSid": "CA" + "f" * 32}))

    def test_dtmf_is_data_not_permission(self):
        s = self.activate()
        event = self.send(s, frame("dtmf", 2, dtmf={"track": "inbound_track", "digit": "1"}))
        self.assertEqual(event.digit, "1")
        self.assertFalse(s.check_health(NOW)["execution_enabled"])
        self.assertFalse(s.check_health(NOW)["live_certified"])

    def test_malformed_dtmf_and_unknown_events_fail_closed(self):
        for msg in (frame("dtmf", 2, dtmf={"track": "inbound", "digit": "yes"}),
                    frame("say", 2, text="test")):
            s = self.activate(FixtureMediaRegistry())
            with self.assertRaises(TwilioContractError):
                self.send(s, msg)
            self.assertTrue(s.check_health(NOW)["close_required"])


if __name__ == "__main__":
    unittest.main()
