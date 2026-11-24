"""8A.5B tests: exact synthetic conference handoff; no live Twilio I/O."""
import base64
import hashlib
import hmac
import json
import unittest
from dataclasses import replace
from xml.etree import ElementTree as ET

from telephony.operator_session_simulation import SessionBinding
from telephony.operator_twilio_contract import CallPlan, StreamBinding, TwilioEndpoints, TwilioContractError
from telephony.operator_twilio_media import FixtureMediaRegistry
from telephony.operator_participant_control import FixtureParticipantController, FixtureSpeechGrant
from telephony.operator_conference_handoff import FixtureConferenceGrant, FixtureConferenceHandoff

NOW=1800000000
AC="AC"+"a"*32
CALL="CA"+"b"*32
OWNER="CA"+"c"*32
STREAM="MZ"+"d"*32
CF="CF"+"e"*32
INTENT="1"*32
HANDOFF="2"*32
NONCE="f"*64
KEY="synthetic-only"

class ConferenceTests(unittest.TestCase):
    def setUp(self):
        endpoints=TwilioEndpoints("https://control.example.test","wss://media.example.test")
        plan=CallPlan(INTENT,AC,"+12025550101","+12025550102",endpoints,NOW+600)
        stream=StreamBinding(plan,CALL,NONCE,NOW+300)
        principal=SessionBinding(1,2,3,4,"3"*32,"manual","4"*32)
        speech=FixtureSpeechGrant(principal,stream,1,NOW+120,True,True)
        url=endpoints.media(INTENT)
        sig=base64.b64encode(hmac.new(KEY.encode(),url.encode(),hashlib.sha1).digest()).decode()
        self.p=FixtureParticipantController(speech,lambda:speech,FixtureMediaRegistry(),KEY,url,sig,NOW)
        self.p.receive(json.dumps({"event":"connected","protocol":"Call","version":"1.0.0"}),NOW)
        self.p.receive(json.dumps({"event":"start","sequenceNumber":"1","streamSid":STREAM,
            "start":{"streamSid":STREAM,"accountSid":AC,"callSid":CALL,"tracks":["inbound"],
            "customParameters":{"intent":INTENT,"nonce":NONCE},
            "mediaFormat":{"encoding":"audio/x-mulaw","sampleRate":8000,"channels":1}}}),NOW)
        self.grant=FixtureConferenceGrant(principal,stream,HANDOFF,OWNER,1,NOW+90,True,True)
        self.current=self.grant
        self.h=FixtureConferenceHandoff(self.grant,lambda:self.current,self.p,NOW)
        self.seq=1

    def event(self,kind,**data):
        self.seq+=1
        return self.p.receive(json.dumps({"event":kind,"sequenceNumber":str(self.seq),"streamSid":STREAM,**data}),NOW+1)

    def snapshot(self,roles,status="in-progress",**changes):
        rows=[{"label":r,"call_sid":OWNER if r=="owner" else CALL,"account_sid":AC,
               "conference_sid":CF,"status":"connected","muted":mute,"hold":False}
              for r,mute in roles.items()]
        return {"account_sid":AC,"sid":CF,"friendly_name":"vp3-handoff-"+HANDOFF,
                "status":status,"participants":rows,**changes}

    def owner_ready(self):
        self.h.reserve_owner_route(1,NOW)
        return self.h.observe(self.snapshot({"owner":True},"init"),NOW)

    def both_ready(self):
        self.owner_ready()
        self.h.reserve_caller_route(1,NOW)
        self.event("stop",stop={"accountSid":AC,"callSid":CALL})
        return self.h.observe(self.snapshot({"owner":True,"caller":True}),NOW+1)

    def test_owner_route_is_muted_fixed_two_person_and_no_recording(self):
        r=self.h.reserve_owner_route(1,NOW)
        self.assertFalse(r["execution_enabled"])
        self.assertTrue(r["resource"].endswith("/Calls/"+OWNER+".json"))
        tree=ET.fromstring(dict(r["form"])["Twiml"])
        conf=tree.find("Dial/Conference")
        self.assertEqual(conf.text,"vp3-handoff-"+HANDOFF)
        for k,v in {"muted":"true","startConferenceOnEnter":"false","maxParticipants":"2",
                    "record":"do-not-record","endConferenceOnExit":"true","participantLabel":"owner"}.items():
            self.assertEqual(conf.get(k),v)
        with self.assertRaises(TwilioContractError):self.h.reserve_owner_route(1,NOW)

    def test_both_explicit_consents_and_exact_binding(self):
        for grant in (replace(self.grant,owner_approved=False),replace(self.grant,recipient_agreed=False),
                      replace(self.grant,principal=replace(self.grant.principal,owner_id=2))):
            with self.assertRaises(TwilioContractError):
                FixtureConferenceHandoff(grant,lambda:grant,self.p,NOW)
        with self.assertRaises(TwilioContractError):self.h.reserve_owner_route(2,NOW)

    def test_caller_route_waits_for_verified_owner(self):
        with self.assertRaises(TwilioContractError):self.h.reserve_caller_route(1,NOW)
        self.owner_ready()
        r=self.h.reserve_caller_route(1,NOW)
        self.assertTrue(r["resource"].endswith("/Calls/"+CALL+".json"))
        conf=ET.fromstring(dict(r["form"])["Twiml"]).find("Dial/Conference")
        self.assertEqual(conf.get("startConferenceOnEnter"),"true")
        self.assertEqual(conf.get("muted"),"true")
        self.assertTrue(self.p.report(NOW)["handoff_paused"])
        self.assertEqual(self.p.report(NOW)["state"],"active")
        with self.assertRaises(TwilioContractError):self.h.reserve_caller_route(1,NOW)

    def test_pause_fences_audio_and_waits_for_cleared_mark(self):
        self.owner_ready()
        permit=self.p.begin_agent_turn(NOW)
        playback=self.p.publish_pcm(permit,b"\0\0"*160,NOW)
        self.assertIsNone(self.h.reserve_caller_route(1,NOW))
        self.assertEqual(self.p.report(NOW)["floor"],"none")
        with self.assertRaises(TwilioContractError):self.p.publish_pcm(permit,b"\0\0",NOW)
        with self.assertRaises(TwilioContractError):self.p.begin_agent_turn(NOW)
        self.assertEqual(self.event("mark",mark={"name":playback.mark_name}).mark_outcome,"cleared_or_unconfirmed")
        self.assertIsNotNone(self.h.reserve_caller_route(1,NOW+1))
        self.assertEqual([k for k,_ in self.p.output_trace()],["media","mark","clear"])

    def test_paused_inbound_audio_not_returned(self):
        self.owner_ready()
        self.h.reserve_caller_route(1,NOW)
        event=self.event("media",media={"track":"inbound","chunk":"1","timestamp":"0",
                         "payload":base64.b64encode(b"\xff"*160).decode()})
        self.assertEqual(event.audio,b"")

    def test_no_verified_stream_stop_no_unmute(self):
        self.owner_ready();self.h.reserve_caller_route(1,NOW)
        with self.assertRaises(TwilioContractError):
            self.h.observe(self.snapshot({"owner":True,"caller":True}),NOW+1)
        self.assertEqual(self.h.report(NOW+1)["state"],"needs_review")
        with self.assertRaises(TwilioContractError):self.h.reserve_mute(1,"owner",False,NOW+1)

    def test_exact_two_participants_then_independent_mute(self):
        r=self.both_ready()
        self.assertTrue(r["participants_verified"])
        self.assertFalse(r["human_conversation_ready"])
        first=self.h.reserve_mute(1,"caller",False,NOW+1)
        self.assertTrue(first["resource"].endswith("/Participants/"+CALL+".json"))
        self.assertFalse(self.h.report(NOW+1)["human_conversation_ready"])
        self.h.observe(self.snapshot({"owner":True,"caller":False}),NOW+2)
        self.h.reserve_mute(1,"owner",False,NOW+2)
        r=self.h.observe(self.snapshot({"owner":False,"caller":False}),NOW+3)
        self.assertTrue(r["human_conversation_ready"])
        self.assertFalse(r["agent_can_speak"]);self.assertFalse(r["agent_resume_supported"])
        self.h.reserve_mute(1,"owner",True,NOW+3)
        self.assertFalse(self.h.observe(self.snapshot({"owner":True,"caller":False}),NOW+4)["human_conversation_ready"])

    def test_unexpected_identity_is_terminal_review(self):
        self.h.reserve_owner_route(1,NOW)
        with self.assertRaises(TwilioContractError):
            self.h.observe(self.snapshot({"owner":True},"init",account_sid="AC"+"0"*32),NOW)
        self.assertEqual(self.h.report(NOW)["state"],"needs_review")

    def test_no_third_participant(self):
        self.owner_ready();self.h.reserve_caller_route(1,NOW)
        self.event("stop",stop={"accountSid":AC,"callSid":CALL})
        snapshot=self.snapshot({"owner":True,"caller":True})
        snapshot["participants"].append(snapshot["participants"][0])
        with self.assertRaises(TwilioContractError):self.h.observe(snapshot,NOW+1)
        self.assertEqual(self.h.report(NOW+1)["state"],"needs_review")

    def test_uncertain_mutation_never_replays(self):
        self.both_ready()
        self.h.reserve_mute(1,"owner",False,NOW+1)
        with self.assertRaises(TwilioContractError):self.h.reserve_mute(1,"owner",False,NOW+1)
        with self.assertRaises(TwilioContractError):
            self.h.observe(self.snapshot({"owner":True,"caller":True}),NOW+2)
        self.assertEqual(self.h.report(NOW+2)["state"],"needs_review")

    def test_disconnect_receipt_is_not_carrier_terminal(self):
        self.both_ready()
        r=self.h.reserve_disconnect(1,"owner",NOW+1)
        self.assertEqual(r["method"],"DELETE")
        self.assertTrue(r["resource"].endswith("/Participants/"+OWNER+".json"))
        with self.assertRaises(TwilioContractError):self.h.reserve_disconnect(1,"owner",NOW+1)
        done=self.h.observe(self.snapshot({},"completed"),NOW+2)
        self.assertTrue(done["conference_ended"]);self.assertFalse(done["carrier_terminal"])
        self.assertFalse(done["execution_enabled"]);self.assertFalse(done["live_certified"])

    def test_revoke_fences_pending_actions(self):
        self.owner_ready()
        self.current=replace(self.grant,recipient_agreed=False)
        with self.assertRaises(TwilioContractError):self.h.reserve_caller_route(1,NOW+1)
        self.assertEqual(self.h.report(NOW+1)["state"],"needs_review")

if __name__=="__main__":
    unittest.main()
