"""Offline HTTPS boundary tests for Phase 8A.4B. No sockets opened."""
from __future__ import annotations

import ssl
import tempfile
import unittest
from pathlib import Path

from telephony.operator_worker_https import (
    OperatorRegistrationHTTPS, RegistrationTransportError, REGISTRATION_PATH, SIGNATURE_HEADER,
)
from telephony.operator_worker_protocol import (
    OperatorWorkerClient, WorkerCredential, SignedFrame, WorkerRegistrationError,
)
from telephony.operator_worker_registry import ReferenceWorkerRegistry


class Reply:
    def __init__(self, body: bytes, headers: list[tuple[str, str]], status: int = 200):
        self.body = body
        self.headers = headers
        self.status = status
        self.read_limit = None

    def getheaders(self):
        return self.headers

    def read(self, limit):
        self.read_limit = limit
        return self.body[:limit]


class Connection:
    def __init__(self, harness, host, **kwargs):
        self.h = harness
        self.host = host
        self.kwargs = kwargs
        self.closed = False
        self.frame = None
        self.h.connections.append(self)

    def request(self, method, path, *, body, headers):
        self.h.requests.append((method, path, body, dict(headers)))
        self.frame = SignedFrame(body, headers[SIGNATURE_HEADER])
        if self.h.failure == "send":
            raise OSError("synthetic write failure")

    def getresponse(self):
        if self.h.failure == "redirect":
            return Reply(b"", [("Location", "https://wrong.example/")], 302)
        if self.h.failure == "error":
            return Reply(b"", [], 503)
        receipt = self.h.registry.process(self.frame, self.h.now)
        if self.h.failure == "lost":
            raise OSError("synthetic lost acknowledgement")
        headers = [("Content-Type", "application/json; charset=utf-8"),
                   (SIGNATURE_HEADER, receipt.signature),
                   ("Content-Length", str(len(receipt.body)))]
        body = receipt.body
        if self.h.failure == "forged":
            headers[1] = (SIGNATURE_HEADER, "0" * 64)
        if self.h.failure == "duplicate":
            headers.append((SIGNATURE_HEADER.lower(), receipt.signature))
        if self.h.failure == "gzip":
            headers.append(("Content-Encoding", "gzip"))
        if self.h.failure == "both":
            headers.append(("Transfer-Encoding", "chunked"))
        if self.h.failure == "oversize":
            headers[2] = ("Content-Length", "4097")
            body = b"x" * 4097
        if self.h.failure == "chunkoversize":
            headers[2] = ("Transfer-Encoding", "chunked")
            body = b"x" * 4097
        if self.h.failure == "truncated":
            body = body[:-1]
        if self.h.failure == "wrongtype":
            headers[0] = ("Content-Type", "text/html")
        self.h.reply = Reply(body, headers)
        return self.h.reply

    def close(self):
        self.closed = True


class WorkerHTTPSTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1_800_000_000
        self.credential = WorkerCredential("1" * 32, 1, "2" * 32, "fixture-worker", bytes(range(32)))
        self.client = OperatorWorkerClient(self.credential, boot_id="3" * 32)
        self.registry = ReferenceWorkerRegistry(Path(self.temp.name) / "registry.sqlite", (self.credential,))
        self.requests = []
        self.connections = []
        self.failure = None
        self.transport = OperatorRegistrationHTTPS(
            "https://cloud.example", self.client, clock=lambda: self.now,
            connection_factory=lambda host, **kw: Connection(self, host, **kw),
        )

    def register(self):
        return self.transport.send("register", ("session.recover", "session.simulate"))

    def test_verified_tls_and_exact_endpoint(self):
        state = self.register()
        self.assertTrue(state["current"])
        self.assertFalse(state["execution_enabled"])
        self.assertFalse(state["live_certified"])
        c = self.connections[0]
        self.assertEqual(c.host, "cloud.example")
        self.assertEqual(c.kwargs["port"], 443)
        self.assertEqual(c.kwargs["timeout"], 5.0)
        self.assertTrue(c.kwargs["context"].check_hostname)
        self.assertEqual(c.kwargs["context"].verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(c.kwargs["context"].minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertEqual(self.requests[0][:2], ("POST", REGISTRATION_PATH))
        self.assertEqual(self.requests[0][3]["Accept-Encoding"], "identity")
        self.assertTrue(c.closed)
        self.assertEqual(self.reply.read_limit, 4097)

    def test_reject_untrusted_origin_shapes(self):
        for origin in ("http://cloud.example", "https://cloud.example:444", "https://cloud.example/",
                       "https://user@cloud.example", "https://cloud.example?q=1", "https://cloud.example#frag",
                       "https://127.0.0.1", "https://localhost", "https://cloud.example.evil/",
                       "https://cloud.example\n", "https://CLOUD.example", "https://cloud..example"):
            with self.subTest(origin=origin), self.assertRaises(RegistrationTransportError):
                OperatorRegistrationHTTPS(origin, self.client)

    def test_lost_receipt_requires_exact_explicit_retry(self):
        self.failure = "lost"
        with self.assertRaises(RegistrationTransportError):
            self.register()
        self.assertEqual(self.client.status(self.now)["state"], "awaiting_ack")
        self.assertEqual(len(self.requests), 1)
        with self.assertRaises(WorkerRegistrationError):
            self.transport.send("heartbeat", ("session.simulate",))
        self.assertEqual(len(self.requests), 1)
        self.failure = None
        self.now += 2
        state = self.transport.retry_pending()
        self.assertEqual(self.requests[0], self.requests[1])
        self.assertEqual(state["lease_until"], 1_800_000_090)
        self.assertTrue(all(c.closed for c in self.connections))

    def test_no_redirect_or_http_status_retry(self):
        self.failure = "redirect"
        with self.assertRaises(RegistrationTransportError):
            self.register()
        self.assertEqual(len(self.connections), 1)
        self.assertTrue(self.connections[0].closed)
        self.failure = "error"
        with self.assertRaises(RegistrationTransportError):
            self.transport.retry_pending()
        self.assertEqual(len(self.connections), 2)
        self.assertTrue(all(c.host == "cloud.example" for c in self.connections))

    def test_ambiguous_send_is_not_automatically_retried(self):
        self.failure = "send"
        with self.assertRaises(RegistrationTransportError):
            self.register()
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(self.client.status(self.now)["current"])
        self.failure = None
        self.assertTrue(self.transport.retry_pending()["current"])
        self.assertEqual(self.requests[0], self.requests[1])

    def test_response_validation_keeps_pending(self):
        self.failure = "forged"
        with self.assertRaises(RegistrationTransportError):
            self.register()
        for mode in ("duplicate", "gzip", "both", "oversize", "chunkoversize", "truncated", "wrongtype"):
            self.failure = mode
            with self.subTest(mode=mode), self.assertRaises(RegistrationTransportError):
                self.transport.retry_pending()
            self.assertFalse(self.client.status(self.now)["current"])
            self.assertTrue(self.connections[-1].closed)
        self.failure = None
        self.assertTrue(self.transport.retry_pending()["current"])
        self.assertTrue(all(r == self.requests[0] for r in self.requests))

    def test_stale_pending_request_cannot_be_replayed(self):
        self.failure = "lost"
        with self.assertRaises(RegistrationTransportError):
            self.register()
        self.now += 31
        with self.assertRaises(WorkerRegistrationError):
            self.transport.retry_pending()
        self.assertEqual(len(self.requests), 1)

    def test_heartbeat_and_retirement(self):
        self.register()
        self.now += 20
        state = self.transport.send("heartbeat", ("session.recover",))
        self.assertEqual(state["lease_until"], self.now + 90)
        self.assertEqual(state["capabilities"], ["session.recover"])
        self.now += 1
        state = self.transport.send("retire")
        self.assertEqual(state["state"], "retired")
        self.assertFalse(state["current"])
        self.assertEqual(state["lease_until"], 0)
        self.assertFalse(state["execution_enabled"])


if __name__ == "__main__":
    unittest.main()
