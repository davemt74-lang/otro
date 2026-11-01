"""PH3D-B SIP identity and cancellation tests without live carrier credentials."""

from __future__ import annotations

import asyncio
import threading
import unittest
from dataclasses import replace
from types import SimpleNamespace

from telephony.livekit_voice import (SIPCaller, SerializedMainChat,
                                    WorkerSettings, verified_sip_caller,
                                    wait_for_sip_caller)
from telephony.voice_turns import VoiceTurnError


SID = "CA" + "d" * 32
ACCOUNT = "AC" + "a" * 32


def settings() -> WorkerSettings:
    return WorkerSettings(
        edge_url="https://phone.example.test", worker_secret="voice-worker-only",
        twilio_account_sid=ACCOUNT, livekit_trunk_id="ST_trust123",
        callback_url="http://127.0.0.1:4377/api/v1/phone/agent",
        callback_secret="vp3-main-chat-only", stt_model="assemblyai/universal-3-6-pro",
        tts_model="fishaudio/s2.1-pro", tts_voice="testvoice123",
    )


def participant(**overrides):
    attrs = {"sip.twilio.accountSid": ACCOUNT, "sip.twilio.callSid": SID,
             "sip.trunkID": "ST_trust123", "sip.phoneNumber": "+16025550101",
             "sip.trunkPhoneNumber": "+16025550100", "sip.callStatus": "active"}
    attrs.update(overrides)
    return SimpleNamespace(identity="sip-caller", kind=1, attributes=attrs)


class LiveKitVoiceContractTests(unittest.TestCase):
    def test_settings_are_explicit_and_secrets_are_separated(self):
        settings().validate()
        with self.assertRaises(VoiceTurnError):
            replace(settings(), callback_secret="voice-worker-only").validate()
        with self.assertRaises(VoiceTurnError):
            replace(settings(), edge_url="http://phone.example.test").validate()
        with self.assertRaises(VoiceTurnError):
            replace(settings(), speech_backend="homeserver-elevenlabs").validate()
        with self.assertRaises(VoiceTurnError):
            replace(settings(), tts_model="").validate()

    def test_sip_binding_requires_trusted_carrier_trunk_and_numbers(self):
        caller = verified_sip_caller(participant(), settings(), sip_kind=1)
        self.assertEqual(caller, SIPCaller("sip-caller", SID, "+16025550101", "+16025550100"))
        for instance in [participant(**{"sip.twilio.accountSid": "AC" + "b"*32}),
                         participant(**{"sip.trunkID": "ST_attacker"}),
                         participant(**{"sip.twilio.callSid": "untrusted"}),
                         participant(**{"sip.phoneNumber": "anonymous"}),
                         participant(**{"sip.trunkPhoneNumber": "invalid"})]:
            with self.assertRaises(VoiceTurnError):
                verified_sip_caller(instance, settings(), sip_kind=1)
        with self.assertRaises(VoiceTurnError):
            verified_sip_caller(participant(), settings(), sip_kind=2)

    def test_cloud_streaming_requires_signed_parent_headers_on_trusted_sip_trunk(self):
        cloud = replace(settings(), resolve_backend="cloud")
        cloud.validate()
        attrs = {"vp3.parent": "CA" + "b"*32, "vp3.issued": "1793520000",
                 "vp3.proof": "f"*64}
        caller = verified_sip_caller(participant(**attrs), cloud, sip_kind=1)
        self.assertEqual(caller.parent_call_sid, "CA" + "b"*32)
        self.assertEqual(caller.issued, "1793520000")
        self.assertEqual(caller.proof, "f"*64)
        for bad in [{"vp3.parent": ""}, {"vp3.proof": "forged"},
                    {"vp3.issued": "yesterday"}, {"sip.trunkID": "ST_untrusted"}]:
            with self.assertRaises(VoiceTurnError):
                verified_sip_caller(participant(**{**attrs, **bad}), cloud, sip_kind=1)
        with self.assertRaises(VoiceTurnError):
            replace(cloud, resolve_backend="unknown").validate()

    def test_waits_for_attributes_but_never_guesses_identity(self):
        async def scenario():
            member = participant(**{"sip.twilio.callSid": ""})
            room = SimpleNamespace(remote_participants={"caller": member})
            async def late_attrs():
                await asyncio.sleep(0.05)
                member.attributes["sip.twilio.callSid"] = SID
            task = asyncio.create_task(late_attrs())
            result = await wait_for_sip_caller(room, settings(), sip_kind=1, timeout=0.5)
            await task
            return result
        self.assertEqual(asyncio.run(scenario()).call_sid, SID)
        async def duplicate():
            room = SimpleNamespace(remote_participants={"a": participant(), "b": participant()})
            await wait_for_sip_caller(room, settings(), sip_kind=1, timeout=0.3)
        with self.assertRaises(VoiceTurnError):
            asyncio.run(duplicate())

    def test_barge_in_does_not_concurrently_replay_inflight_main_chat(self):
        gate = threading.Event()
        entered = threading.Event()
        calls = []
        class FakeMainChat:
            def turn(self, text):
                calls.append(text)
                if text == "first":
                    entered.set()
                    gate.wait(3)
                return "answered " + text

        async def scenario():
            bridge = SerializedMainChat(FakeMainChat())
            first = asyncio.create_task(bridge.respond("first"))
            while not entered.is_set():
                await asyncio.sleep(0.01)
            first.cancel()  # LiveKit interrupts speech or response generation.
            with self.assertRaises(asyncio.CancelledError):
                await first
            second = asyncio.create_task(bridge.respond("second"))
            await asyncio.sleep(0.03)
            self.assertEqual(calls, ["first"])
            gate.set()
            return await second

        self.assertEqual(asyncio.run(scenario()), "answered second")
        self.assertEqual(calls, ["first", "second"])


if __name__ == "__main__":
    unittest.main()
