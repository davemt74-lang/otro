"""PH3I-B worker heartbeat: no live network, carrier or audio calls."""

import threading
import unittest

from telephony.livekit_voice import WorkerSettings
from telephony.voice_turns import VoiceTurnError
from telephony.worker_health import WorkerHealthPublisher, start_worker_health


def settings():
    return WorkerSettings(
        edge_url="https://cloud.example.test", worker_secret="s" * 48,
        twilio_account_sid="AC" + "a" * 32, livekit_trunk_id="ST_worker123",
        callback_url="http://127.0.0.1:4377/api/v1/phone/agent",
        callback_secret="c" * 48, resolve_backend="cloud",
        stt_model="assemblyai/universal-3-6-pro", tts_model="fishaudio/s2.1-pro")


def environment():
    return {"LIVEKIT_URL": "wss://staging.livekit.cloud",
            "LIVEKIT_API_KEY": "sample-api", "LIVEKIT_API_SECRET": "sample-secret",
            "VP3_PHONE_WORKER_ID": "test-worker"}


class WorkerHealthTests(unittest.TestCase):
    def test_disabled_by_default_does_not_start_thread_or_network(self):
        self.assertIsNone(start_worker_health(settings(), environment()))

    def test_heartbeats_are_minimal_signed_by_existing_transport(self):
        calls = []

        def sender(url, secret, body, **kwargs):
            calls.append((url, secret, body, kwargs))
            return {"ok": True}

        worker = WorkerHealthPublisher(settings(), environment(), sender=sender,
                                       nonce_factory=lambda: "a" * 32)
        self.assertTrue(worker.publish_once())
        self.assertEqual(len(calls), 1)
        url, secret, body, options = calls[0]
        self.assertEqual(url, "https://cloud.example.test/api/phone-worker-heartbeat-v171.php")
        self.assertEqual(secret, settings().worker_secret)
        self.assertEqual(set(body), {"worker_id", "boot_id", "nonce", "phase", "preflight_ready"})
        self.assertTrue(body["preflight_ready"])
        self.assertEqual(body["phase"], "process_reporting")
        self.assertFalse(options["allow_local"])
        self.assertNotIn(secret, str(body))
        self.assertNotIn(settings().callback_secret, str(body))

    def test_failed_delivery_does_not_claim_healthy_or_replay(self):
        calls = []

        def failure(*args, **kwargs):
            calls.append(1)
            raise VoiceTurnError("Uncertain network outcome")

        worker = WorkerHealthPublisher(settings(), environment(), sender=failure)
        self.assertFalse(worker.publish_once())
        self.assertEqual(len(calls), 1)

    def test_bad_worker_id_or_edge_backend_never_publishes(self):
        with self.assertRaises(VoiceTurnError):
            WorkerHealthPublisher(settings(), {**environment(), "VP3_PHONE_WORKER_ID": "bad/id"})
        with self.assertRaises(VoiceTurnError):
            WorkerHealthPublisher(WorkerSettings(**{**settings().__dict__, "resolve_backend": "edge"}), environment())

    def test_run_exits_cleanly_when_supervisor_stops(self):
        stop = threading.Event()
        calls = []

        def sender(*args, **kwargs):
            calls.append(1)
            stop.set()
            return {"ok": True}

        WorkerHealthPublisher(settings(), environment(), sender=sender).run(stop)
        self.assertEqual(calls, [1])


if __name__ == "__main__":
    unittest.main()
