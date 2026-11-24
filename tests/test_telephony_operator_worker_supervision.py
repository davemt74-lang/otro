"""Offline supervision tests, real wire client + local authority, fake network."""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from telephony.operator_worker_protocol import (
    OperatorWorkerClient, WorkerCredential, WorkerRegistrationError,
)
from telephony.operator_worker_registry import ReferenceWorkerRegistry
from telephony.operator_worker_supervisor import (
    OperatorWorkerSupervisor, CAPABILITIES, RESTART_FENCE_SECONDS,
)
from app.services.operator_worker_runtime import OperatorWorkerRuntime


class FakeHandle:
    def __init__(self, harness):
        self.h = harness
        self.client = OperatorWorkerClient(harness.credential)
        self.closed = False
        self.frames = []

    def send(self, action, capabilities=()):
        if self.closed:
            raise WorkerRegistrationError("closed")
        frame = self.client.request(action, self.h.wall(), capabilities)
        return self.exchange(frame)

    def retry_pending(self):
        frame = self.client.retry(self.h.wall())
        return self.exchange(frame)

    def exchange(self, frame):
        self.frames.append(frame)
        if self.h.fail_before:
            raise WorkerRegistrationError("fixture unavailable")
        receipt = self.h.registry.process(frame, self.h.wall())
        if self.h.fail_after:
            raise WorkerRegistrationError("fixture lost acknowledgement")
        return self.client.accept(receipt, self.h.wall())

    def status(self):
        if self.closed or self.h.disconnected:
            raise WorkerRegistrationError("fixture disconnected")
        return self.client.status(self.h.wall())

    def close(self):
        self.closed = True


class SupervisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.elapsed = 0.0
        self.wall_shift = 0
        self.credential = WorkerCredential("1" * 32, 1, "2" * 32, "fixture-worker", bytes(range(32)))
        self.registry = ReferenceWorkerRegistry(Path(self.temp.name) / "registry.sqlite", (self.credential,))
        self.fail_open = self.fail_before = self.fail_after = self.disconnected = False
        self.handles = []
        self.opens = 0
        self.supervisor = OperatorWorkerSupervisor(self.open, monotonic=lambda: self.elapsed, wall=self.wall)

    def wall(self):
        return 1_800_000_000 + int(self.elapsed) + self.wall_shift

    def open(self):
        self.opens += 1
        if self.fail_open:
            raise WorkerRegistrationError("private fixture error")
        handle = FakeHandle(self)
        self.handles.append(handle)
        return handle

    def advance(self, seconds):
        self.elapsed += seconds
        return self.supervisor.step()

    def test_registration_and_periodic_heartbeat(self):
        self.assertTrue(self.supervisor.step())
        report = self.supervisor.report()
        self.assertTrue(report["current"])
        self.assertEqual(report["capabilities"], list(CAPABILITIES))
        self.assertFalse(report["execution_enabled"])
        self.assertFalse(report["live_certified"])
        self.assertFalse(self.advance(19))
        self.assertTrue(self.advance(1))
        self.assertEqual(self.supervisor.report()["lease_until"], self.wall() + 90)
        self.assertEqual(self.opens, 1)
        self.assertEqual(len(self.handles[0].frames), 2)

    def test_stale_report_without_a_tick(self):
        self.supervisor.step()
        self.elapsed += 25
        self.assertEqual(self.supervisor.report()["state"], "stale")
        self.assertFalse(self.supervisor.report()["current"])
        self.assertEqual(self.supervisor.report()["capabilities"], [])
        self.assertTrue(self.supervisor.step())
        self.assertTrue(self.supervisor.report()["current"])

    def test_wall_clock_rollback_fails_closed(self):
        self.supervisor.step()
        self.wall_shift = -100
        self.assertFalse(self.supervisor.report()["current"])
        self.advance(20)
        self.assertEqual(self.supervisor.report()["state"], "cooldown")

    def test_lost_registration_exact_retry(self):
        self.fail_after = True
        self.supervisor.step()
        self.assertEqual(self.supervisor.report()["state"], "awaiting_ack")
        self.assertFalse(self.supervisor.report()["current"])
        self.assertFalse(self.advance(1))
        self.fail_after = False
        self.assertTrue(self.advance(1))
        self.assertEqual(self.handles[0].frames[0], self.handles[0].frames[1])
        self.assertEqual(self.supervisor.report()["lease_until"], 1_800_000_090)
        self.assertTrue(self.supervisor.report()["current"])

    def test_lost_heartbeat_never_advances_sequence(self):
        self.supervisor.step()
        self.fail_after = True
        self.advance(20)
        self.assertFalse(self.supervisor.report()["current"])
        self.fail_after = False
        self.advance(2)
        frames = self.handles[0].frames
        self.assertEqual(frames[1], frames[2])
        self.assertEqual(self.supervisor.report()["lease_until"], 1_800_000_110)

    def test_exhausted_retries_fence_new_boot(self):
        self.fail_after = True
        self.supervisor.step()
        for delay in (2, 4, 8):
            self.advance(delay)
        self.assertEqual(self.supervisor.report()["state"], "cooldown")
        self.assertTrue(self.handles[0].closed)
        self.assertEqual(len(self.handles[0].frames), 4)
        self.assertTrue(all(f == self.handles[0].frames[0] for f in self.handles[0].frames))
        self.fail_after = False
        self.assertFalse(self.advance(RESTART_FENCE_SECONDS - 1))
        self.assertEqual(self.opens, 1)
        self.assertTrue(self.advance(1))
        self.assertEqual(self.opens, 2)
        self.assertTrue(self.supervisor.report()["current"])
        self.assertNotEqual(self.handles[0].client.boot_id, self.handles[1].client.boot_id)

    def test_old_pending_never_sent_after_freshness_window(self):
        self.fail_before = True
        self.supervisor.step()
        self.advance(31)
        self.assertEqual(len(self.handles[0].frames), 1)
        self.advance(4)
        self.advance(8)
        self.assertEqual(self.supervisor.report()["state"], "cooldown")
        self.assertEqual(len(self.handles[0].frames), 1)

    def test_open_backoff_is_bounded_and_redacted(self):
        self.fail_open = True
        self.supervisor.step()
        for delay in (5, 10, 20, 40, 60, 60):
            self.assertFalse(self.advance(delay - 1))
            self.assertTrue(self.advance(1))
        self.assertEqual(self.opens, 7)
        self.assertNotIn("private", repr(self.supervisor.report()))
        self.fail_open = False
        self.advance(60)
        self.assertTrue(self.supervisor.report()["current"])

    def test_expired_lease_uses_fenced_restart(self):
        self.supervisor.step()
        self.advance(91)
        self.assertEqual(len(self.handles[0].frames), 1)
        self.assertEqual(self.supervisor.report()["state"], "cooldown")
        self.advance(RESTART_FENCE_SECONDS)
        self.assertTrue(self.supervisor.report()["current"])

    def test_disconnected_pairing_drops_handle(self):
        self.supervisor.step()
        self.disconnected = True
        self.advance(20)
        self.assertEqual(self.supervisor.report()["state"], "cooldown")
        self.assertTrue(self.handles[0].closed)
        self.assertFalse(self.supervisor.report()["current"])

    def test_shutdown_retires_once(self):
        self.supervisor.step()
        self.supervisor.shutdown()
        self.supervisor.shutdown()
        self.assertEqual(len(self.handles[0].frames), 2)
        self.assertEqual(self.handles[0].client.status(self.wall())["state"], "retired")
        self.assertEqual(self.supervisor.report()["state"], "stopped")
        self.assertFalse(self.supervisor.report()["current"])
        self.assertIsNone(self.supervisor.report()["reason"])
        self.assertFalse(self.advance(200))

    def test_shutdown_never_sends_retire_over_pending(self):
        self.fail_after = True
        self.supervisor.step()
        self.supervisor.shutdown()
        self.assertEqual(len(self.handles[0].frames), 1)
        self.assertEqual(self.supervisor.report()["reason"], "retirement_unconfirmed")

    def test_stop_during_open_does_not_register(self):
        stop = threading.Event()
        def open_then_stop():
            handle = self.open()
            stop.set()
            return handle
        supervisor = OperatorWorkerSupervisor(open_then_stop, monotonic=lambda: self.elapsed, wall=self.wall)
        supervisor.run(stop)
        self.assertEqual(len(self.handles[0].frames), 0)
        self.assertTrue(self.handles[0].closed)
        self.assertEqual(supervisor.report()["state"], "stopped")

    def test_runtime_disabled_or_bad_config_never_networks(self):
        runtime = OperatorWorkerRuntime(lambda origin: self.open())
        self.assertFalse(runtime.start({}))
        self.assertEqual(runtime.report()["state"], "disabled")
        self.assertFalse(runtime.start({"VP3_OPERATOR_WORKER_ENABLED": "1"}))
        self.assertEqual(runtime.report()["state"], "configuration_required")
        self.assertFalse(runtime.start({"VP3_OPERATOR_WORKER_ENABLED": "1",
                                        "VP3_OPERATOR_CLOUD_ORIGIN": "https://user@cloud.example"}))
        self.assertEqual(self.opens, 0)

    def test_runtime_single_thread_and_bounded_stop_during_blocked_open(self):
        entered, release = threading.Event(), threading.Event()
        def opener(origin):
            entered.set()
            if not release.wait(3):
                raise WorkerRegistrationError("fixture timeout")
            return self.open()
        runtime = OperatorWorkerRuntime(opener)
        env = {"VP3_OPERATOR_WORKER_ENABLED": "1", "VP3_OPERATOR_CLOUD_ORIGIN": "https://cloud.example"}
        self.assertTrue(runtime.start(env))
        try:
            self.assertTrue(entered.wait(2))
            self.assertFalse(runtime.start(env))
            self.assertFalse(runtime.stop(timeout=0))
            self.assertEqual(runtime.report()["state"], "stopping")
            self.assertFalse(runtime.report()["current"])
            self.assertFalse(runtime.start(env))
        finally:
            release.set()
            self.assertTrue(runtime.stop(timeout=2))
        self.assertEqual(self.opens, 1)
        self.assertEqual(len(self.handles[0].frames), 0)
        self.assertEqual(runtime.report()["state"], "stopped")

    def test_app_lifespan_wiring_and_status_contract(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("operator_worker_runtime.start()", source)
        self.assertIn("operator_worker_runtime.stop()", source)
        self.assertIn('"operator_worker": operator_worker_runtime.report()', source)


if __name__ == "__main__":
    unittest.main()
