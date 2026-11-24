"""Phase 8A.2 worker registration contract; all credentials and sessions are synthetic."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from telephony.operator_worker_protocol import (
    CAPABILITIES, LEASE_SECONDS, MAX_FRAME_BYTES, PROTOCOL,
    OperatorWorkerClient, SignedFrame, WorkerCredential, WorkerRegistrationError,
    canonical_body, parse_body, request_digest, sign_frame, verify_frame,
)
from telephony.operator_worker_registry import ReferenceWorkerRegistry


NOW = 1800000000
CAPS = tuple(sorted(CAPABILITIES))


def credential(index=1, *, owner=1, **changes):
    values = dict(credential_id=f"{index:032x}", owner_id=owner,
                  device_id=f"{index + 100:032x}", worker_id=f"worker-{index}",
                  secret=hashlib.sha256(f"synthetic-key-{index}".encode()).digest())
    values.update(changes)
    return WorkerCredential(**values)


class WorkerRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "registry.sqlite3"
        self.cred = credential()
        self.registry = ReferenceWorkerRegistry(self.path, (self.cred,))
        self.client = OperatorWorkerClient(self.cred, boot_id="a" * 32)

    def register(self, now=NOW):
        frame = self.client.request("register", now, CAPS)
        ack = self.registry.process(frame, now)
        status = self.client.accept(ack, now)
        return frame, ack, status

    def heartbeat(self, now=NOW + 10, caps=CAPS):
        frame = self.client.request("heartbeat", now, caps)
        ack = self.registry.process(frame, now)
        return self.client.accept(ack, now)

    def test_wire_vector_compatible_with_php_hmac(self):
        key = WorkerCredential("1" * 32, 1, "2" * 32, "fixture-worker", bytes(range(32)))
        payload = {"action": "register", "boot_id": "3" * 32, "capabilities": list(CAPS),
                   "credential_id": "1" * 32, "device_id": "2" * 32, "generation": 0,
                   "nonce": "4" * 32, "owner_id": 1, "protocol": PROTOCOL, "sent_at": NOW,
                   "sequence": 1, "worker_id": "fixture-worker"}
        frame = sign_frame(payload, key, "request")
        self.assertEqual(frame.signature,
                         "9dd194caff6cf5485f5b2e7440f683d0e948f658c0a57894b7dc5f33c15d5daf")
        self.assertEqual(request_digest(frame),
                         "5146efef3ba073df025789d2cf63cd0506dd16e6fb21e28cb33cd71681e51532")
        self.assertEqual(verify_frame(frame, key, "request"), payload)
        with self.assertRaises(WorkerRegistrationError):
            verify_frame(frame, key, "response")

    def test_registration_is_metadata_only(self):
        frame, ack, status = self.register()
        self.assertTrue(status["current"])
        self.assertEqual(status["generation"], 1)
        self.assertEqual(status["lease_until"], NOW + LEASE_SECONDS)
        self.assertEqual(status["capabilities"], list(CAPS))
        self.assertFalse(status["execution_enabled"])
        self.assertFalse(status["live_certified"])
        self.assertEqual(parse_body(ack.body)["request_sha256"], request_digest(frame))
        view = self.registry.list_workers(1, NOW)
        self.assertEqual(len(view), 1)
        self.assertTrue(view[0]["reference_only"])
        self.assertFalse(view[0]["execution_enabled"])
        self.assertFalse(view[0]["live_certified"])

    def test_secret_is_not_printed_or_persisted(self):
        frame, ack, _ = self.register()
        self.assertNotIn(self.cred.secret.hex(), repr(self.cred))
        self.assertEqual(repr(frame), "SignedFrame()")
        self.assertEqual(repr(ack), "SignedFrame()")
        self.assertNotIn(self.cred.secret, self.path.read_bytes())
        with sqlite3.connect(self.path) as db:
            cols = [row[1] for row in db.execute("PRAGMA table_info(operator_worker_registry)")]
        self.assertNotIn("secret", cols)
        projection = json.dumps(self.registry.list_workers(1, NOW))
        self.assertNotIn(self.cred.secret.hex(), projection)
        self.assertNotIn(frame.signature, projection)

    def test_exact_lost_registration_receipt_replays_without_renewal(self):
        frame = self.client.request("register", NOW, CAPS)
        ack = self.registry.process(frame, NOW)
        self.assertEqual(self.client.status(NOW)["state"], "awaiting_ack")
        self.assertFalse(self.client.status(NOW)["current"])
        self.assertEqual(self.client.retry(NOW + 20), frame)
        recovered = self.registry.process(frame, NOW + 20)
        self.assertEqual(ack, recovered)
        status = self.client.accept(recovered, NOW + 20)
        self.assertEqual(status["lease_until"], NOW + LEASE_SECONDS)
        self.assertEqual(self.registry.list_workers(1, NOW + 20)[0]["last_seen"], NOW)

    def test_duplicate_heartbeat_does_not_extend_lease(self):
        self.register()
        frame = self.client.request("heartbeat", NOW + 10, CAPS)
        ack = self.registry.process(frame, NOW + 10)
        again = self.registry.process(frame, NOW + 25)
        self.assertEqual(again, ack)
        self.assertEqual(self.client.accept(again, NOW + 25)["lease_until"], NOW + 100)

    def test_heartbeat_updates_only_advertised_capabilities(self):
        self.register()
        status = self.heartbeat(caps=("session.recover",))
        self.assertEqual(status["generation"], 1)
        self.assertEqual(status["sequence"], 2)
        self.assertEqual(status["capabilities"], ["session.recover"])
        self.assertEqual(self.registry.list_workers(1, NOW + 10)[0]["capabilities"],
                         ["session.recover"])

    def test_pending_heartbeat_never_reports_current(self):
        self.register()
        self.client.request("heartbeat", NOW + 1, CAPS)
        self.assertEqual(self.client.status(NOW + 2)["capabilities"], [])
        with self.assertRaises(WorkerRegistrationError):
            self.client.request("retire", NOW + 2)
        self.assertFalse(self.client.status(NOW + 2)["current"])

    def test_request_binding_and_bad_signature_rejected(self):
        frame = self.client.request("register", NOW, CAPS)
        payload = parse_body(frame.body)
        with self.assertRaises(WorkerRegistrationError):
            self.registry.process(replace(frame, signature="0" * 64), NOW)
        for change in ({"owner_id": 2}, {"device_id": "b" * 32}, {"worker_id": "other"},
                       {"credential_id": "c" * 32}, {"owner_id": True}):
            with self.subTest(change=change), self.assertRaises(WorkerRegistrationError):
                self.registry.process(sign_frame({**payload, **change}, self.cred, "request"), NOW)
        self.assertEqual(self.registry.list_workers(1, NOW), [])

    def test_no_live_capability_or_call_request_is_accepted(self):
        frame = self.client.request("register", NOW, CAPS)
        payload = parse_body(frame.body)
        for change in ({"capabilities": ["call.execute"]}, {"action": "claim"},
                       {"capabilities": ["session.simulate", "session.recover"]},
                       {"capabilities": ["session.simulate", "session.simulate"]},
                       {"capabilities": [True]}, {"phone_number": "+15555550100"},
                       {"execution_enabled": True}, {"sequence": True}):
            with self.subTest(change=change), self.assertRaises(WorkerRegistrationError):
                self.registry.process(sign_frame({**payload, **change}, self.cred, "request"), NOW)

    def test_capability_allowlist_cannot_be_elevated(self):
        restricted = credential(2, allowed_capabilities=("session.recover",))
        registry = ReferenceWorkerRegistry(self.path, (self.cred, restricted))
        client = OperatorWorkerClient(restricted)
        with self.assertRaises(WorkerRegistrationError):
            client.request("register", NOW, CAPS)
        frame = client.request("register", NOW, ("session.recover",))
        forged = {**parse_body(frame.body), "capabilities": list(CAPS)}
        with self.assertRaises(WorkerRegistrationError):
            registry.process(sign_frame(forged, restricted, "request"), NOW)
        self.assertTrue(client.accept(registry.process(frame, NOW), NOW)["current"])

    def test_invalid_json_and_oversized_frames(self):
        for body in (b'{"x":1,"x":2}', b'{"x": 1}', b'[]',
                     b'{"x":NaN}', b'\xff', b'{' * MAX_FRAME_BYTES,
                     b'x' * (MAX_FRAME_BYTES + 1)):
            with self.subTest(body=body[:30]), self.assertRaises(WorkerRegistrationError):
                parse_body(body)

    def test_old_and_future_request_rejected(self):
        frame = self.client.request("register", NOW, CAPS)
        payload = parse_body(frame.body)
        for stamp in (NOW - 31, NOW + 6, "1800000000", True):
            with self.subTest(stamp=stamp), self.assertRaises(WorkerRegistrationError):
                self.registry.process(sign_frame({**payload, "sent_at": stamp},
                                                 self.cred, "request"), NOW)
        with self.assertRaises(WorkerRegistrationError):
            self.client.retry(NOW + 31)

    def test_old_request_does_not_revive_expired_registration(self):
        frame, _, _ = self.register()
        self.assertEqual(self.registry.list_workers(1, NOW + 90)[0]["state"], "stale")
        with self.assertRaises(WorkerRegistrationError):
            self.registry.process(frame, NOW + 90)
        self.assertEqual(self.client.status(NOW + 90)["capabilities"], [])
        with self.assertRaises(WorkerRegistrationError):
            self.client.request("heartbeat", NOW + 90, CAPS)

    def test_new_boot_cannot_displace_current_worker(self):
        self.register()
        second = OperatorWorkerClient(self.cred, boot_id="b" * 32)
        frame = second.request("register", NOW + 5, CAPS)
        with self.assertRaises(WorkerRegistrationError):
            self.registry.process(frame, NOW + 5)
        self.assertEqual(self.registry.list_workers(1, NOW + 5)[0]["generation"], 1)

    def test_restart_after_expiry_fences_old_boot_and_generation(self):
        old, _, _ = self.register()
        late = self.client.request("heartbeat", NOW + 89, CAPS)
        second = OperatorWorkerClient(self.cred, boot_id="b" * 32)
        frame = second.request("register", NOW + 90, CAPS)
        restarted = ReferenceWorkerRegistry(self.path, (self.cred,))
        status = second.accept(restarted.process(frame, NOW + 90), NOW + 90)
        self.assertEqual(status["generation"], 2)
        with self.assertRaises(WorkerRegistrationError):
            restarted.process(late, NOW + 91)
        again = {**parse_body(old.body), "sent_at": NOW + 91, "nonce": "c" * 32}
        with self.assertRaises(WorkerRegistrationError):
            restarted.process(sign_frame(again, self.cred, "request"), NOW + 91)
        self.assertEqual(restarted.list_workers(1, NOW + 91)[0]["generation"], 2)

    def test_retirement_is_replay_safe_and_allows_new_boot(self):
        old, _, _ = self.register()
        frame = self.client.request("retire", NOW + 5)
        ack = self.registry.process(frame, NOW + 5)
        self.assertEqual(self.registry.process(frame, NOW + 10), ack)
        status = self.client.accept(ack, NOW + 10)
        self.assertEqual(status["state"], "retired")
        self.assertEqual(status["lease_until"], 0)
        self.assertFalse(status["current"])
        second = OperatorWorkerClient(self.cred, boot_id="b" * 32)
        new = second.request("register", NOW + 11, CAPS)
        self.assertEqual(second.accept(self.registry.process(new, NOW + 11), NOW + 11)["generation"], 2)
        with self.assertRaises(WorkerRegistrationError):
            self.registry.process(old, NOW + 12)

    def test_sequence_collision_and_out_of_order_request(self):
        old, _, _ = self.register()
        frame = self.client.request("heartbeat", NOW + 1, CAPS)
        payload = parse_body(frame.body)
        jump = sign_frame({**payload, "sequence": 3}, self.cred, "request")
        with self.assertRaises(WorkerRegistrationError):
            self.registry.process(jump, NOW + 1)
        ack = self.registry.process(frame, NOW + 1)
        changed = sign_frame({**payload, "nonce": "d" * 32}, self.cred, "request")
        with self.assertRaises(WorkerRegistrationError):
            self.registry.process(changed, NOW + 1)
        with self.assertRaises(WorkerRegistrationError):
            self.registry.process(old, NOW + 2)
        self.assertEqual(self.client.accept(ack, NOW + 2)["sequence"], 2)

    def test_reject_forged_ack_wrong_request_hash_and_live_flags(self):
        frame = self.client.request("register", NOW, CAPS)
        ack = self.registry.process(frame, NOW)
        payload = parse_body(ack.body)
        with self.assertRaises(WorkerRegistrationError):
            self.client.accept(replace(ack, signature="0" * 64), NOW)
        for change in ({"request_sha256": "0" * 64}, {"nonce": "d" * 32},
                       {"execution_enabled": True}, {"live_certified": True},
                       {"lease_until": NOW + 10000}, {"status": "ready"},
                       {"owner_id": True}, {"generation": True},
                       {"received_at": NOW - 10}, {"sequence": 2}):
            with self.subTest(change=change), self.assertRaises(WorkerRegistrationError):
                self.client.accept(sign_frame({**payload, **change}, self.cred, "response"), NOW)
        self.assertFalse(self.client.status(NOW)["current"])
        self.assertTrue(self.client.accept(ack, NOW)["current"])

    def test_ack_from_another_request_cannot_be_reused(self):
        _, old, _ = self.register()
        frame = self.client.request("heartbeat", NOW + 1, CAPS)
        with self.assertRaises(WorkerRegistrationError):
            self.client.accept(old, NOW + 1)
        self.assertTrue(self.client.accept(self.registry.process(frame, NOW + 1), NOW + 1)["current"])
        with self.assertRaises(WorkerRegistrationError):
            self.client.accept(old, NOW + 2)

    def test_clock_regression_denied_on_both_sides(self):
        frame, _, _ = self.register()
        with self.assertRaises(WorkerRegistrationError):
            self.registry.process(frame, NOW - 1)
        with self.assertRaises(WorkerRegistrationError):
            self.client.status(NOW - 1)

    def test_revoked_credential_cannot_appear_available_or_renew(self):
        self.register()
        frame = self.client.request("heartbeat", NOW + 5, CAPS)
        revoked = ReferenceWorkerRegistry(self.path, ())
        self.assertEqual(revoked.list_workers(1, NOW + 5), [])
        with self.assertRaises(WorkerRegistrationError):
            revoked.process(frame, NOW + 5)

    def test_key_change_requires_new_credential_id(self):
        self.register()
        rotated = replace(self.cred, secret=hashlib.sha256(b"different-key").digest())
        registry = ReferenceWorkerRegistry(self.path, (rotated,))
        self.assertEqual(registry.list_workers(1, NOW + 1), [])
        client = OperatorWorkerClient(rotated)
        frame = client.request("register", NOW + 91, CAPS)
        with self.assertRaises(WorkerRegistrationError):
            registry.process(frame, NOW + 91)
        fresh = replace(rotated, credential_id="f" * 32)
        registry = ReferenceWorkerRegistry(self.path, (fresh,))
        client = OperatorWorkerClient(fresh)
        frame = client.request("register", NOW + 92, CAPS)
        self.assertTrue(client.accept(registry.process(frame, NOW + 92), NOW + 92)["current"])
        self.assertEqual(len(registry.list_workers(1, NOW + 92)), 1)

    def test_different_owners_and_workers_are_isolated(self):
        second = credential(2, owner=2)
        registry = ReferenceWorkerRegistry(self.path, (self.cred, second))
        first = self.client.request("register", NOW, CAPS)
        registry.process(first, NOW)
        client = OperatorWorkerClient(second)
        registry.process(client.request("register", NOW, CAPS), NOW)
        self.assertEqual([x["worker_id"] for x in registry.list_workers(1, NOW)],
                         [self.cred.worker_id])
        self.assertEqual([x["worker_id"] for x in registry.list_workers(2, NOW)],
                         [second.worker_id])
        self.assertEqual(registry.list_workers(3, NOW), [])

    def test_duplicate_preprovisioned_id_or_key_rejected(self):
        for item in (credential(2, credential_id=self.cred.credential_id),
                     credential(2, secret=self.cred.secret),
                     credential(2, device_id=self.cred.device_id, worker_id=self.cred.worker_id)):
            with self.assertRaises(WorkerRegistrationError):
                ReferenceWorkerRegistry(self.path, (self.cred, item))

    def test_simultaneous_boot_registration_is_exclusive(self):
        barrier = threading.Barrier(2)
        clients = [self.client, OperatorWorkerClient(self.cred, boot_id="b" * 32)]

        def compete(client):
            local = ReferenceWorkerRegistry(self.path, (self.cred,))
            frame = client.request("register", NOW, CAPS)
            barrier.wait(timeout=10)
            try:
                return local.process(frame, NOW)
            except WorkerRegistrationError:
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(compete, clients))
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_simultaneous_exact_request_is_idempotent(self):
        frame = self.client.request("register", NOW, CAPS)
        barrier = threading.Barrier(4)

        def repeat(_):
            local = ReferenceWorkerRegistry(self.path, (self.cred,))
            barrier.wait(timeout=10)
            return local.process(frame, NOW)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(repeat, range(4)))
        self.assertEqual(len(set(replies)), 1)
        self.assertEqual(self.client.accept(replies[0], NOW)["generation"], 1)

    def test_existing_unrelated_database_is_untouched(self):
        other = Path(self.tmp.name) / "other.sqlite3"
        with sqlite3.connect(other) as db:
            db.execute("CREATE TABLE important(value TEXT)")
            db.execute("INSERT INTO important VALUES('preserve')")
        if os.name == "posix":
            os.chmod(other, 0o600)
        with self.assertRaises(WorkerRegistrationError):
            ReferenceWorkerRegistry(other, (self.cred,))
        with sqlite3.connect(other) as db:
            self.assertEqual(db.execute("SELECT * FROM important").fetchall(), [("preserve",)])

    @unittest.skipUnless(os.name == "posix", "POSIX mode bits")
    def test_private_file_mode_and_symlink_refusal(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        os.chmod(self.path, 0o644)
        with self.assertRaises(WorkerRegistrationError):
            ReferenceWorkerRegistry(self.path, (self.cred,))
        os.chmod(self.path, 0o600)
        link = Path(self.tmp.name) / "link.sqlite3"
        link.symlink_to(self.path)
        with self.assertRaises(WorkerRegistrationError):
            ReferenceWorkerRegistry(link, (self.cred,))

    def test_registration_exercise_requires_no_network(self):
        from telephony.operator_worker_simulation import exercise
        with patch("socket.create_connection", side_effect=AssertionError("network unavailable")), \
                patch("urllib.request.urlopen", side_effect=AssertionError("provider unavailable")):
            report = exercise()
        self.assertTrue(report["simulation_only"])
        self.assertFalse(report["execution_enabled"])
        self.assertFalse(report["live_certified"])
        self.assertEqual(report["registration"]["generation"], 1)
        self.assertEqual(report["replacement"]["generation"], 2)
        self.assertEqual(report["retired"]["state"], "retired")


if __name__ == "__main__":
    unittest.main()
