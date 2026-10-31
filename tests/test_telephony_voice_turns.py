"""Provider-neutral voice bridge tests; no live calls, LLMs or audio services."""

import unittest

from telephony.voice_turns import MainChatVoiceSession, VoiceTurnError, resolve_livekit_call


SID = "CA" + "a" * 32
BINDING = {"ok": True, "call_sid": SID, "platform": "vp3", "agent_id": "9",
           "caller_verified": False, "allow_actions": False}


class VoiceTurnBridgeTests(unittest.TestCase):
    def test_verification_never_trusts_sip_claims(self):
        calls = []

        def sender(url, secret, payload, *, allow_local):
            calls.append((url, secret, payload, allow_local))
            return dict(BINDING)

        result = resolve_livekit_call("https://phone.example.test", "worker-secret",
                                      call_sid=SID, to_number="+16025550100",
                                      from_number="+16025550101", sender=sender)
        self.assertEqual(result["agent_id"], "9")
        self.assertEqual(calls[0][0], "https://phone.example.test/v1/voice/resolve")
        self.assertFalse(calls[0][3])

    def test_rejects_unverified_or_action_capable_binding(self):
        def bad_sender(url, secret, payload, **kwargs):
            return {**BINDING, "caller_verified": True}

        with self.assertRaises(VoiceTurnError):
            resolve_livekit_call("https://phone.example.test", "worker-secret",
                                 call_sid=SID, to_number="+16025550100",
                                 from_number="+16025550101", sender=bad_sender)
        with self.assertRaises(VoiceTurnError):
            MainChatVoiceSession.from_verified_call({**BINDING, "platform": "stonefellow"},
                    callback_url="http://127.0.0.1:4377/api/v1/phone/agent", callback_secret="platform-secret")

    def test_transcripts_share_canonical_conversation_without_replay(self):
        session = MainChatVoiceSession.from_verified_call(BINDING,
                     callback_url="http://127.0.0.1:4377/api/v1/phone/agent", callback_secret="platform-secret")
        events = []

        def sender(url, secret, payload, *, allow_local):
            events.append((url, secret, payload, allow_local))
            return {"ok": True, "reply": "Hello", "conversation_id": "same-main-chat"}

        self.assertEqual(session.turn("Hi there", sender=sender), "Hello")
        self.assertEqual(session.turn("And now?", sender=sender), "Hello")
        self.assertEqual(events[0][2]["event_id"], SID + "_t1")
        self.assertEqual(events[1][2]["event_id"], SID + "_t2")
        self.assertEqual(events[1][2]["conversation_id"], "same-main-chat")
        self.assertFalse(events[1][2]["allow_actions"])
        self.assertTrue(events[1][3])

    def test_ambiguous_send_stops_call_instead_of_replaying(self):
        session = MainChatVoiceSession.from_verified_call(BINDING,
                     callback_url="http://127.0.0.1:4377/api/v1/phone/agent", callback_secret="platform-secret")

        def timeout(*args, **kwargs):
            raise VoiceTurnError("unconfirmed")

        with self.assertRaises(VoiceTurnError):
            session.turn("Hello", sender=timeout)
        with self.assertRaises(VoiceTurnError):
            session.turn("Again", sender=lambda *a, **k: {"ok": True})
        self.assertEqual(session.turn_index, 1)

    def test_no_arbitrary_public_http_platform_adapter(self):
        with self.assertRaises(VoiceTurnError):
            MainChatVoiceSession.from_verified_call(BINDING,
                callback_url="http://public.example.test/api/v1/phone/agent", callback_secret="secret")


if __name__ == "__main__":
    unittest.main()
