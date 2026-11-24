"""Offline paired worker credential and local lifecycle tests."""
from __future__ import annotations

import hashlib
import json
import ssl
import tempfile
import unittest
from pathlib import Path

from telephony.operator_worker_pairing import (
    CREDENTIAL_PATH, PAIRING_POLL_PATH, PairedOperatorWorker, PairedWorkerError, PairingSnapshot,
)
from telephony.operator_worker_https import REGISTRATION_PATH, SIGNATURE_HEADER, RegistrationTransportError
from telephony.operator_worker_protocol import SignedFrame, WorkerCredential, WorkerRegistrationError
from telephony.operator_worker_registry import ReferenceWorkerRegistry


class Reply:
    def __init__(self, raw, headers, status=200):
        self.raw = raw
        self.headers = headers
        self.status = status

    def getheaders(self):
        return self.headers

    def read(self, limit):
        return self.raw[:limit]


class Conn:
    def __init__(self, test, host, **kwargs):
        self.t = test
        self.host = host
        self.kwargs = kwargs
        self.closed = False
        test.connections.append(self)

    def request(self, method, path, *, body, headers):
        self.path = path
        self.body = body
        self.headers = headers
        self.t.requests.append((method, path, body, headers))

    def getresponse(self):
        t = self.t
        if t.mode == "redirect":
            return Reply(b"", [("Location", "https://other.example/")], 302)
        if t.mode == "denied":
            return Reply(b"", [], 401)
        if self.path == CREDENTIAL_PATH:
            data = dict(t.payload)
            data.update(t.changes)
            raw = json.dumps(data).encode("ascii")
            if t.mode == "duplicate":
                raw = raw[:-1] + b',"owner_id":1}'
            elif t.mode == "oversize":
                raw = b"x" * 4097
            elif t.mode == "invalid":
                raw = b"[]"
            elif t.mode == "changed":
                t.snapshot = None
            headers = [("Content-Type", "application/json; charset=utf-8"),
                       ("Content-Length", str(len(raw)))]
            if t.mode == "gzip":
                headers.append(("Content-Encoding", "gzip"))
            return Reply(raw, headers)
        if self.path != REGISTRATION_PATH:
            raise AssertionError("Unexpected endpoint")
        receipt = t.registry.process(SignedFrame(self.body, self.headers[SIGNATURE_HEADER]), t.now)
        if t.mode == "lost":
            raise OSError("fixture acknowledgement lost")
        if t.mode == "disconnect":
            t.snapshot = None
        return Reply(receipt.body, [("Content-Type", "application/json; charset=utf-8"),
                                    (SIGNATURE_HEADER, receipt.signature),
                                    ("Content-Length", str(len(receipt.body)))])

    def close(self):
        self.closed = True


class PairedWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.origin = "https://cloud.example"
        self.device = "hs-" + "a" * 24
        self.token = "B" * 64
        self.snapshot = PairingSnapshot(self.origin + PAIRING_POLL_PATH, self.device, self.token)
        self.now = 1_800_000_000
        device = hashlib.sha256(b"vp3.operator.device.v1\x00" + self.device.encode()).hexdigest()[:32]
        self.credential = WorkerCredential("1" * 32, 1, device, "operator-homeserver", bytes(range(32)))
        self.payload = {**self.credential.binding(), "secret_hex": self.credential.secret.hex(),
                        "capabilities": list(self.credential.allowed_capabilities),
                        "execution_enabled": False, "live_certified": False}
        self.registry = ReferenceWorkerRegistry(Path(self.temp.name) / "registry.sqlite", (self.credential,))
        self.mode = None
        self.changes = {}
        self.connections = []
        self.requests = []

    def open(self):
        return PairedOperatorWorker(self.origin, lambda: self.snapshot, clock=lambda: self.now,
                                    connection_factory=lambda host, **kw: Conn(self, host, **kw))

    def test_credential_delivery_and_signed_registration(self):
        worker = self.open()
        method, path, body, headers = self.requests[0]
        self.assertEqual((method, path, body), ("POST", CREDENTIAL_PATH, b"{}"))
        self.assertEqual(headers["Authorization"], "Bearer " + self.token)
        self.assertEqual(headers["X-HomeServer-Device"], self.device)
        self.assertNotIn(self.token, repr(self.snapshot))
        state = worker.send("register", ("session.recover", "session.simulate"))
        self.assertTrue(state["current"])
        self.assertFalse(state["execution_enabled"])
        self.assertFalse(state["live_certified"])
        self.assertNotIn("Authorization", self.requests[1][3])
        self.assertEqual(self.requests[1][1], REGISTRATION_PATH)
        self.assertTrue(all(c.closed for c in self.connections))
        self.assertTrue(all(c.host == "cloud.example" and c.kwargs["port"] == 443 for c in self.connections))
        self.assertTrue(all(c.kwargs["context"].verify_mode == ssl.CERT_REQUIRED
                            and c.kwargs["context"].check_hostname
                            and c.kwargs["context"].minimum_version == ssl.TLSVersion.TLSv1_2
                            for c in self.connections))
        self.assertNotIn(self.payload["secret_hex"], repr(worker.status()))
        self.assertEqual([p.name for p in Path(self.temp.name).iterdir()], ["registry.sqlite"])

    def test_no_session_or_different_origin_never_networks(self):
        for snap in (None, PairingSnapshot("https://else.example" + PAIRING_POLL_PATH, self.device, self.token),
                     PairingSnapshot(self.origin + "/other", self.device, self.token),
                     PairingSnapshot(self.origin + PAIRING_POLL_PATH, "wrong", self.token),
                     PairingSnapshot(self.origin + PAIRING_POLL_PATH, self.device, "short")):
            self.snapshot = snap
            with self.subTest(snap=snap), self.assertRaises(PairedWorkerError):
                self.open()
        self.assertFalse(self.requests)

    def test_redirect_and_denied_are_not_retried(self):
        for mode in ("redirect", "denied"):
            self.mode = mode
            with self.assertRaises(PairedWorkerError):
                self.open()
        self.assertEqual(len(self.requests), 2)
        self.assertTrue(all(c.closed for c in self.connections))

    def test_credential_response_rejects_wrong_binding_or_live_authority(self):
        for change in ({"device_id": "a" * 32}, {"worker_id": "other-worker"},
                       {"owner_id": True}, {"owner_id": 0}, {"credential_id": "BAD"},
                       {"secret_hex": "0" * 64}, {"execution_enabled": True},
                       {"live_certified": 0}, {"capabilities": ["call.dial"]},
                       {"unexpected": "field"}):
            self.changes = change
            with self.subTest(change=change), self.assertRaises(PairedWorkerError):
                self.open()
        self.assertTrue(all(c.closed for c in self.connections))

    def test_malformed_response_and_encoding(self):
        for mode in ("duplicate", "invalid", "oversize", "gzip"):
            self.mode = mode
            with self.subTest(mode=mode), self.assertRaises(PairedWorkerError):
                self.open()
        self.assertTrue(all(c.closed for c in self.connections))

    def test_pairing_changed_during_fetch(self):
        self.mode = "changed"
        with self.assertRaises(PairedWorkerError):
            self.open()
        self.assertEqual(len(self.requests), 1)

    def test_local_pairing_change_fences_handle_permanently(self):
        worker = self.open()
        worker.send("register", ("session.simulate",))
        original = self.snapshot
        self.snapshot = PairingSnapshot(original.endpoint, original.device_id, "C" * 64)
        with self.assertRaises(PairedWorkerError):
            worker.send("heartbeat", ("session.simulate",))
        self.snapshot = original
        with self.assertRaises(PairedWorkerError):
            worker.status()
        self.assertEqual(len(self.requests), 2)

    def test_disconnect_during_registration_drops_receipt(self):
        worker = self.open()
        self.mode = "disconnect"
        with self.assertRaises(PairedWorkerError):
            worker.send("register", ("session.simulate",))
        with self.assertRaises(PairedWorkerError):
            worker.status()

    def test_lost_registration_ack_uses_exact_pending_frame(self):
        worker = self.open()
        self.mode = "lost"
        with self.assertRaises(RegistrationTransportError):
            worker.send("register", ("session.simulate",))
        self.assertEqual(worker.status()["state"], "awaiting_ack")
        with self.assertRaises(WorkerRegistrationError):
            worker.send("heartbeat", ("session.simulate",))
        self.mode = None
        self.now += 2
        self.assertTrue(worker.retry_pending()["current"])
        self.assertEqual(self.requests[1], self.requests[2])
        self.assertEqual(worker.status()["lease_until"], 1_800_000_090)

    def test_explicit_retirement_and_local_close(self):
        worker = self.open()
        worker.send("register", ("session.simulate",))
        self.now += 1
        self.assertEqual(worker.send("retire")["state"], "retired")
        worker.close()
        worker.close()
        with self.assertRaises(PairedWorkerError):
            worker.status()
        self.assertEqual(len(self.requests), 3)


if __name__ == "__main__":
    unittest.main()
