"""PH3H static worker preflight tests with no carrier calls or credentials."""

import json
import unittest
from dataclasses import replace

from telephony.livekit_voice import WorkerSettings
from telephony.preflight import inspect_worker_config


def sample_settings():
    return WorkerSettings(
        edge_url="https://vp3.example.test",
        worker_secret="worker-" + "x" * 40,
        twilio_account_sid="AC" + "a" * 32,
        livekit_trunk_id="ST_example987",
        callback_url="http://127.0.0.1:4377/api/v1/phone/agent",
        callback_secret="callback-" + "y" * 40,
        stt_model="assemblyai/universal-3-6-pro",
        tts_model="fishaudio/s2.1-pro", tts_voice="testvoice123",
        resolve_backend="cloud",
    )


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.settings = sample_settings()
        self.env = {"LIVEKIT_URL": "wss://staging.livekit.cloud",
                    "LIVEKIT_API_KEY": "test-api-key", "LIVEKIT_API_SECRET": "test-api-secret"}

    def run_preflight(self, **settings):
        return inspect_worker_config(replace(self.settings, **settings), self.env)

    def test_ready_for_staging_is_never_live_certification(self):
        result = self.run_preflight()
        self.assertTrue(result["ready_for_staging"])
        self.assertIs(result["live_certified"], False)
        self.assertEqual(len(result["live_tests_pending"]), 5)

    def test_cloud_backend_and_separate_credential_are_mandatory(self):
        for override in ({"resolve_backend": "edge"}, {"worker_secret": "short"},
                         {"callback_secret": self.settings.worker_secret},
                         {"stt_model": ""}, {"livekit_trunk_id": ""}):
            with self.subTest(override=override):
                self.assertFalse(self.run_preflight(**override)["ready_for_staging"])

    def test_livekit_transport_and_credentials_fail_closed(self):
        for key, value in (("LIVEKIT_URL", "ws://staging.livekit.cloud"),
                           ("LIVEKIT_URL", "wss://user:pass@staging.livekit.cloud"),
                           ("LIVEKIT_URL", "wss://staging.livekit.cloud?token=unsafe"),
                           ("LIVEKIT_API_KEY", ""), ("LIVEKIT_API_SECRET", "")):
            with self.subTest(key=key, value=value):
                environment = dict(self.env, **{key: value})
                self.assertFalse(inspect_worker_config(self.settings, environment)["ready_for_staging"])

    def test_never_print_secret_in_report(self):
        serialized = json.dumps(self.run_preflight())
        for value in (self.settings.worker_secret, self.settings.callback_secret,
                      self.env["LIVEKIT_API_KEY"], self.env["LIVEKIT_API_SECRET"]):
            self.assertNotIn(value, serialized)


if __name__ == "__main__":
    unittest.main()
