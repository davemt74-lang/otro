"""Phone gateway contract: signed inbound, governed outbound, no replay."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from telephony.app import Settings, create_app
from telephony.providers import CarrierError
from telephony.security import signed_envelope, twilio_valid, valid_envelope
from telephony.state import PhoneState
from telephony.worker import process_one


class FakeCarrier:
    def __init__(self):
        self.sent = []
        self.calls = []
        self.fail = False

    def send_sms(self, sender, recipient, body):
        if self.fail:
            raise CarrierError("simulated ambiguous send")
        self.sent.append((sender, recipient, body))
        return "SM" + str(len(self.sent))

    def start_call(self, sender, recipient, callback):
        self.calls.append((sender, recipient, callback))
        return "CA" + str(len(self.calls))


class PhoneGatewayTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.config = Settings(public_base="https://voice.example.test", account_sid="AC_test",
                               auth_token="twilio-secret", owner_token="owner-secret",
                               service_secret="bridge-secret", database=str(Path(self.folder.name) / "phone.db"))
        self.state = PhoneState(self.config.database)
        self.carrier = FakeCarrier()
        self.client = TestClient(create_app(self.config, state=self.state, carrier=self.carrier))
        self.owner = {"Authorization": "Bearer owner-secret"}

    def bind(self, *, voice=False, sms=True, sip=""):
        return self.client.put("/v1/admin/routes", headers=self.owner, json={
            "number": "+16025550100", "platform": "gelato", "agent_id": "public-restaurant-agent",
            "active": True, "allow_sms": sms, "allow_voice": voice, "sip_uri": sip,
        })

    def consent(self, channel="sms", granted=True):
        return self.client.put("/v1/admin/consents", headers=self.owner, json={
            "number": "+16025550100", "recipient": "+16025550101", "channel": channel,
            "granted": granted, "source": "test user permission receipt 123",
        })

    def twilio(self, path, form):
        url = self.config.public_base + path
        signature = base64.b64encode(hmac.new(self.config.auth_token.encode(),
                      (url + "".join(k + form[k] for k in sorted(form))).encode(), hashlib.sha1).digest()).decode()
        return self.client.post(path, data=form, headers={"X-Twilio-Signature": signature})

    def outbound(self, payload, *, sign=True):
        raw = json.dumps(payload, separators=(",", ":")).encode()
        stamp = str(int(time.time()))
        return self.client.post("/v1/outbound", content=raw, headers={
            "Content-Type": "application/json", "X-VP3-Timestamp": stamp,
            "X-VP3-Signature": signed_envelope(self.config.service_secret, int(stamp), raw) if sign else "bad",
        })

    def test_route_owner_required_and_sip_restricted(self):
        payload = {"number": "+16025550100", "platform": "gelato", "agent_id": "restaurant",
                   "active": True, "allow_voice": True, "sip_uri": "sip:evil.example.net"}
        self.assertEqual(self.client.put("/v1/admin/routes", json=payload).status_code, 401)
        self.assertEqual(self.client.put("/v1/admin/routes", headers=self.owner, json=payload).status_code, 422)
        self.assertEqual(self.bind().status_code, 200)

    def test_signed_sms_only_one_event_and_one_reply(self):
        self.bind()
        form = {"AccountSid": "AC_test", "MessageSid": "SM_123", "To": "+16025550100",
                "From": "+16025550101", "Body": "Do you have pizza?"}
        self.assertEqual(self.twilio("/v1/twilio/sms", form).status_code, 200)
        self.assertEqual(self.twilio("/v1/twilio/sms", form).status_code, 200)
        self.assertTrue(process_one(self.state, self.config, self.carrier,
                                    responder=lambda event, cfg: "Yes, please see our menu."))
        self.assertFalse(process_one(self.state, self.config, self.carrier,
                                     responder=lambda event, cfg: "duplicate"))
        self.assertEqual(len(self.carrier.sent), 1)
        self.assertEqual(self.carrier.sent[0][2], "Yes, please see our menu.")

    def test_forged_twilio_webhook_is_rejected(self):
        self.bind()
        form = {"AccountSid": "AC_test", "MessageSid": "SM_forget", "From": "+16025550101",
                "To": "+16025550100", "Body": "hello"}
        r = self.client.post("/v1/twilio/sms", data=form, headers={"X-Twilio-Signature": "invalid"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(process_one(self.state, self.config, self.carrier))

    def test_owner_approval_exact_message_single_use(self):
        self.bind()
        body = {"operation": "sms", "from_number": "+16025550100", "to_number": "+16025550101",
                "agent_id": "public-restaurant-agent", "approved_by": "owner:7", "payload": "Your order is ready"}
        self.assertEqual(self.client.post("/v1/admin/approvals", json=body).status_code, 401)
        self.assertEqual(self.client.post("/v1/admin/approvals", headers=self.owner, json=body).status_code, 422)
        self.assertEqual(self.consent().status_code, 200)
        approval = self.client.post("/v1/admin/approvals", headers=self.owner, json=body)
        self.assertEqual(approval.status_code, 200)
        req = {k: body[k] for k in ("operation", "from_number", "to_number", "payload")}
        req["approval_id"] = approval.json()["approval_id"]
        self.assertEqual(self.outbound(req, sign=False).status_code, 401)
        tamper = dict(req, payload="Different, unapproved text")
        self.assertEqual(self.outbound(tamper).status_code, 409)
        good = self.outbound(req)
        self.assertEqual(good.status_code, 200)
        self.assertEqual(self.outbound(req).status_code, 409)
        self.assertEqual(self.carrier.sent, [("+16025550100", "+16025550101", "Your order is ready")])

    def test_livekit_route_and_approved_outbound_call(self):
        self.assertEqual(self.bind(voice=True, sip="sip:agent.sip.livekit.cloud").status_code, 200)
        self.assertEqual(self.consent(channel="call").status_code, 200)
        form = {"AccountSid": "AC_test", "CallSid": "CA_123", "To": "+16025550100",
                "From": "+16025550101", "Direction": "inbound"}
        r = self.twilio("/v1/twilio/voice", form)
        self.assertEqual(r.status_code, 200)
        self.assertIn("sip:agent.sip.livekit.cloud", r.text)
        body = {"operation": "call", "from_number": "+16025550100", "to_number": "+16025550101",
                "agent_id": "public-restaurant-agent", "approved_by": "owner:7"}
        approval = self.client.post("/v1/admin/approvals", headers=self.owner, json=body).json()["approval_id"]
        req = {"operation": "call", "from_number": body["from_number"], "to_number": body["to_number"],
               "payload": "", "approval_id": approval}
        self.assertEqual(self.outbound(req).status_code, 200)
        self.assertEqual(self.carrier.calls[0][2], "https://voice.example.test/v1/twilio/voice")

    def test_route_reassignment_revokes_old_agent_approval(self):
        self.bind()
        self.consent()
        body = {"operation": "sms", "from_number": "+16025550100", "to_number": "+16025550101",
                "agent_id": "public-restaurant-agent", "approved_by": "owner:7", "payload": "Ready"}
        approval = self.client.post("/v1/admin/approvals", headers=self.owner, json=body).json()["approval_id"]
        self.client.put("/v1/admin/routes", headers=self.owner, json={
            "number": "+16025550100", "platform": "gelato", "agent_id": "new-agent",
            "active": True, "allow_sms": True, "allow_voice": False,
        })
        req = {"operation": "sms", "from_number": body["from_number"], "to_number": body["to_number"],
               "payload": body["payload"], "approval_id": approval}
        self.assertEqual(self.outbound(req).status_code, 409)
        self.assertEqual(self.carrier.sent, [])

    def test_inbound_sender_hourly_cap(self):
        self.bind()
        for i in range(14):
            form = {"AccountSid": "AC_test", "MessageSid": "SM_cap_" + str(i),
                    "From": "+16025550101", "To": "+16025550100", "Body": "hello"}
            self.assertEqual(self.twilio("/v1/twilio/sms", form).status_code, 200)
        processed = 0
        while process_one(self.state, self.config, self.carrier, responder=lambda event, cfg: "ok"):
            processed += 1
        self.assertEqual(processed, 12)

    def test_sms_stop_prevents_pending_reply_and_revokes_outbound(self):
        self.bind()
        self.consent()
        regular = {"AccountSid": "AC_test", "MessageSid": "SM_before_stop", "From": "+16025550101",
                   "To": "+16025550100", "Body": "What's new?"}
        stop = dict(regular, MessageSid="SM_stop", Body="STOP")
        self.assertEqual(self.twilio("/v1/twilio/sms", regular).status_code, 200)
        self.assertEqual(self.twilio("/v1/twilio/sms", stop).status_code, 200)
        self.assertTrue(process_one(self.state, self.config, self.carrier,
                                    responder=lambda event, cfg: "Here is what's new"))
        self.assertEqual(self.carrier.sent, [])
        approval = {"operation": "sms", "from_number": "+16025550100", "to_number": "+16025550101",
                    "agent_id": "public-restaurant-agent", "approved_by": "owner:7", "payload": "hello"}
        self.assertEqual(self.client.post("/v1/admin/approvals", headers=self.owner, json=approval).status_code, 422)

    def test_number_reassignment_resets_history_queue_and_cross_brand_consent(self):
        self.bind()
        self.consent()
        self.state.save_turn("+16025550100", "+16025550101", "private-old-id", "question", "answer")
        form = {"AccountSid": "AC_test", "MessageSid": "SM_old_queue", "From": "+16025550101",
                "To": "+16025550100", "Body": "old conversation"}
        self.twilio("/v1/twilio/sms", form)
        self.assertEqual(self.client.put("/v1/admin/routes", headers=self.owner, json={
            "number": "+16025550100", "platform": "stonefellow", "agent_id": "new-agent",
            "active": True, "allow_sms": True,
        }).status_code, 200)
        self.assertEqual(self.state.conversation("+16025550100", "+16025550101")["history"], [])
        self.assertFalse(self.state.has_consent("+16025550100", "+16025550101", "sms"))
        self.assertFalse(process_one(self.state, self.config, self.carrier,
                                     responder=lambda event, cfg: "must never run"))

    def test_start_restores_inbound_replies_but_not_proactive_consent(self):
        self.bind()
        self.consent(granted=False)
        start = {"AccountSid": "AC_test", "MessageSid": "SM_start", "From": "+16025550101",
                 "To": "+16025550100", "Body": "START"}
        self.assertEqual(self.twilio("/v1/twilio/sms", start).status_code, 200)
        self.assertFalse(self.state.explicitly_opted_out("+16025550100", "+16025550101"))
        self.assertFalse(self.state.has_consent("+16025550100", "+16025550101", "sms"))
        text = dict(start, MessageSid="SM_followup", Body="What's on the menu?")
        self.twilio("/v1/twilio/sms", text)
        self.assertTrue(process_one(self.state, self.config, self.carrier,
                                    responder=lambda event, cfg: "See our menu"))
        self.assertEqual(len(self.carrier.sent), 1)

    def test_ambiguous_carrier_send_not_retried(self):
        self.bind()
        form = {"AccountSid": "AC_test", "MessageSid": "SM_uncertain", "From": "+16025550101",
                "To": "+16025550100", "Body": "hello"}
        self.twilio("/v1/twilio/sms", form)
        self.carrier.fail = True
        self.assertTrue(process_one(self.state, self.config, self.carrier,
                                    responder=lambda event, cfg: "Hello"))
        self.carrier.fail = False
        self.assertFalse(process_one(self.state, self.config, self.carrier,
                                     responder=lambda event, cfg: "Hello"))

    def test_expired_service_signature(self):
        msg = b'{"operation":"sms"}'
        old = int(time.time()) - 301
        self.assertFalse(valid_envelope("secret", str(old), msg, signed_envelope("secret", old, msg)))
        self.assertFalse(twilio_valid("", "https://voice.example.test", {}, "fake"))


if __name__ == "__main__":
    unittest.main()
