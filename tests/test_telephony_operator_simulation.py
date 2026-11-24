"""Phase 8A.1 synthetic Operator lifecycle tests; no paid/live provider access."""
from __future__ import annotations

import concurrent.futures
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from telephony.operator_session_simulation import (
    OperatorSessionError, OperatorSimulationStore, SessionBinding,
    SimulatedTransport, run_simulated_dispatch,
)
from telephony.operator_simulation import exercise


NOW = 1800000000
BOOT = "b" * 32


def binding(occurrence: int = 1, **changes) -> SessionBinding:
    values = dict(owner_id=1, agent_id=7, conversation_id=11, contact_id=19,
                  workspace_id="a" * 32, source="scheduled",
                  occurrence_id=f"{occurrence:032x}")
    values.update(changes)
    return SessionBinding(**values)


class OperatorSimulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "operator.sqlite3"
        self.store = OperatorSimulationStore(self.path)
        self.binding = binding()

    def prepare(self, scope=None, now=NOW, *, lifetime=300):
        return self.store.prepare(scope or self.binding, now, lifetime_seconds=lifetime)

    def claim(self, session=None, scope=None, now=NOW + 1):
        scope = scope or self.binding
        session = session or self.prepare(scope)
        return self.store.claim(session.session_id, scope, "worker-one", BOOT, now)

    def row(self, session_id):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            return db.execute("SELECT * FROM operator_sim_sessions WHERE id=?",
                              (session_id,)).fetchone()

    def leg(self, session_id):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            return db.execute("SELECT * FROM operator_sim_legs WHERE session_id=?",
                              (session_id,)).fetchone()

    def test_initialization_is_repeatable_and_private(self):
        self.assertEqual(self.store.health(NOW)["pending_sessions"], 0)
        reopened = OperatorSimulationStore(self.path)
        self.assertEqual(reopened.health(NOW), self.store.health(NOW))
        if os.name == "posix":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_existing_unrelated_database_is_not_migrated(self):
        foreign = Path(self.directory.name) / "phone.sqlite3"
        with sqlite3.connect(foreign) as db:
            db.execute("CREATE TABLE phone_routes(value TEXT)")
            db.execute("INSERT INTO phone_routes VALUES ('untouched')")
        if os.name == "posix":
            os.chmod(foreign, 0o600)
        with self.assertRaises(OperatorSessionError):
            OperatorSimulationStore(foreign)
        with sqlite3.connect(foreign) as db:
            self.assertEqual(db.execute("SELECT * FROM phone_routes").fetchall(), [("untouched",)])
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),
                             [("phone_routes",)])

    @unittest.skipUnless(os.name == "posix", "POSIX mode bits")
    def test_shared_permissions_and_symlink_refused(self):
        os.chmod(self.path, 0o644)
        with self.assertRaises(OperatorSessionError):
            OperatorSimulationStore(self.path)
        os.chmod(self.path, 0o600)
        link = Path(self.directory.name) / "link.sqlite3"
        link.symlink_to(self.path)
        with self.assertRaises(OperatorSessionError):
            OperatorSimulationStore(link)

    def test_binding_validation(self):
        for change in ({"owner_id": True}, {"agent_id": 0}, {"conversation_id": -1},
                       {"contact_id": "5551234"}, {"workspace_id": "not-opaque"},
                       {"occurrence_id": "short"}, {"source": "marketing"}):
            with self.subTest(change=change), self.assertRaises(OperatorSessionError):
                binding(**change)

    def test_idempotent_occurrence_and_exact_binding(self):
        initial = self.prepare()
        self.assertEqual(self.prepare(now=NOW + 20), initial)
        self.assertEqual(len(self.store.events(initial.session_id, self.binding)), 1)
        for change in ({"agent_id": 9}, {"contact_id": 20}, {"conversation_id": 12},
                       {"workspace_id": "c" * 32}, {"source": "proactive"}):
            with self.subTest(change=change), self.assertRaises(OperatorSessionError):
                self.prepare(replace(self.binding, **change))
        with self.assertRaises(OperatorSessionError):
            self.prepare(lifetime=600)

    def test_scoped_reads_and_mutations(self):
        session = self.prepare()
        for other in (binding(owner_id=2), binding(agent_id=8), binding(conversation_id=12),
                      binding(contact_id=20), binding(occurrence=2)):
            with self.subTest(other=other):
                for operation in (lambda: self.store.snapshot(session.session_id, other),
                                  lambda: self.store.events(session.session_id, other),
                                  lambda: self.store.claim(session.session_id, other, "worker-one", BOOT, NOW + 1),
                                  lambda: self.store.cancel(session.session_id, other, NOW + 1)):
                    with self.assertRaises(OperatorSessionError):
                        operation()
        self.assertEqual(self.store.snapshot(session.session_id, self.binding).state, "prepared")

    def test_distinct_recurrences_share_workspace_not_session(self):
        first = self.prepare()
        second = self.prepare(binding(occurrence=2))
        self.assertNotEqual(first.session_id, second.session_id)
        self.assertEqual(first.binding.workspace_id, second.binding.workspace_id)
        self.assertEqual(first.binding.source, "scheduled")
        self.assertEqual(second.state, "prepared")

    def test_simultaneous_preparation_only_one_record(self):
        barrier = threading.Barrier(4)

        def prepare(_):
            local = OperatorSimulationStore(self.path)
            barrier.wait(timeout=10)
            return local.prepare(self.binding, NOW).session_id

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(prepare, range(4)))
        self.assertEqual(len(set(ids)), 1)

    def test_exclusive_claims_even_across_agents(self):
        first = self.prepare()
        second_scope = binding(occurrence=2, agent_id=8)
        second = self.prepare(second_scope)
        barrier = threading.Barrier(2)

        def race(item):
            session, scope, worker = item
            local = OperatorSimulationStore(self.path)
            barrier.wait(timeout=10)
            try:
                return local.claim(session.session_id, scope, worker, BOOT, NOW + 1)
            except OperatorSessionError:
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(race, [(first, self.binding, "worker-one"),
                                           (second, second_scope, "worker-two")]))
        self.assertEqual(sum(item is not None for item in results), 1)
        self.assertEqual(self.store.health(NOW + 1)["current_leases"], 1)

    def test_different_owners_have_independent_simulations(self):
        one = self.claim()
        other_scope = binding(owner_id=2)
        other = self.claim(scope=other_scope)
        self.assertNotEqual(one.session_id, other.session_id)
        self.assertEqual(self.store.health(NOW + 1)["current_leases"], 2)

    def test_lease_secret_not_persisted_or_projected(self):
        lease = self.claim()
        self.assertNotIn(lease.token, repr(lease))
        row = self.row(lease.session_id)
        self.assertNotEqual(row["lease_hash"], lease.token)
        self.assertEqual(len(row["lease_hash"]), 64)
        projection = json.dumps(asdict(self.store.snapshot(lease.session_id, self.binding)))
        self.assertNotIn(lease.token, projection)
        self.assertNotIn(row["lease_hash"], projection)
        self.assertNotIn("worker-one", projection)

    def test_forged_or_wrong_boot_lease_denied(self):
        lease = self.claim()
        for forged in (replace(lease, token="f" * 64), replace(lease, boot_id="d" * 32),
                       replace(lease, worker_id="worker-two"), replace(lease, generation=0)):
            with self.subTest(forged=forged), self.assertRaises(OperatorSessionError):
                self.store.reserve_dispatch(forged, NOW + 2)
        self.assertEqual(self.row(lease.session_id)["dispatch_count"], 0)

    def test_heartbeat_and_expiration_never_extend_deadline(self):
        session = self.prepare(lifetime=60)
        lease = self.claim(session, now=NOW + 1)
        for now in (NOW + 25, NOW + 50):
            snap = self.store.heartbeat(lease, now)
            self.assertEqual(snap.lease_until, min(now + 30, NOW + 60))
        with self.assertRaises(OperatorSessionError):
            self.store.reserve_dispatch(lease, NOW + 60)
        self.assertEqual(self.store.recover(NOW + 60)[0].state, "expired")

    def test_clock_regression_fails_closed(self):
        lease = self.claim(now=NOW + 2)
        with self.assertRaises(OperatorSessionError):
            self.store.reserve_dispatch(lease, NOW + 1)
        self.assertEqual(self.row(lease.session_id)["dispatch_count"], 0)

    def test_expired_unspent_lease_reclaim_fences_old_generation(self):
        lease = self.claim()
        restart = OperatorSimulationStore(self.path)
        self.assertEqual(restart.recover(NOW + 31)[0].state, "prepared")
        replacement = restart.claim(lease.session_id, self.binding, "worker-two", "c" * 32, NOW + 32)
        self.assertGreater(replacement.generation, lease.generation)
        for operation in (lambda: self.store.reserve_dispatch(lease, NOW + 33),
                          lambda: self.store.heartbeat(lease, NOW + 33)):
            with self.assertRaises(OperatorSessionError):
                operation()
        self.assertEqual(run_simulated_dispatch(restart, replacement, NOW + 33).state, "ringing")

    def test_nominal_call_has_one_receipt_and_ordered_events(self):
        lease = self.claim()
        ringing = run_simulated_dispatch(self.store, lease, NOW + 2)
        receipt = "sim-" + lease.session_id
        self.assertEqual(ringing.state, "ringing")
        self.assertEqual(ringing.dispatch_count, 1)
        self.assertFalse(ringing.execution_enabled)
        self.assertEqual(self.leg(lease.session_id)["receipt"], receipt)
        self.assertEqual(self.store.mark_answered(lease, receipt, NOW + 3).state, "active")
        completed = self.store.finish(lease, receipt, NOW + 4)
        self.assertEqual(completed.state, "completed")
        self.assertEqual(completed.lease_until, 0)
        self.assertEqual(self.leg(lease.session_id)["state"], "completed")
        events = self.store.events(lease.session_id, self.binding)
        self.assertEqual([e["state"] for e in events],
                         ["prepared", "leased", "dispatch_reserved", "ringing", "active", "completed"])
        self.assertEqual([e["sequence"] for e in events], list(range(1, 7)))
        with self.assertRaises(OperatorSessionError):
            self.store.heartbeat(lease, NOW + 5)

    def test_dispatch_reserved_before_transport(self):
        lease = self.claim()
        with self.assertRaises(OperatorSessionError):
            SimulatedTransport(self.store).start(lease, NOW + 2)
        self.assertIsNone(self.leg(lease.session_id))
        self.store.reserve_dispatch(lease, NOW + 2)
        self.assertEqual(self.row(lease.session_id)["dispatch_count"], 1)
        self.assertIsNone(self.leg(lease.session_id))
        transport = SimulatedTransport(self.store)
        receipt = transport.start(lease, NOW + 3)
        with self.assertRaises(OperatorSessionError):
            transport.start(lease, NOW + 3)
        self.assertEqual(self.store.confirm_start(lease, receipt, NOW + 4).state, "ringing")

    def test_concurrent_dispatch_reservations_are_one_use(self):
        lease = self.claim()
        barrier = threading.Barrier(2)

        def reserve(_):
            store = OperatorSimulationStore(self.path)
            barrier.wait(timeout=10)
            try:
                store.reserve_dispatch(lease, NOW + 2)
                return True
            except OperatorSessionError:
                return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(reserve, range(2))), 1)
        self.assertEqual(self.row(lease.session_id)["dispatch_count"], 1)
        self.assertIsNone(self.leg(lease.session_id))

    def test_fake_effect_fences_earlier_clock(self):
        lease = self.claim()
        self.store.reserve_dispatch(lease, NOW + 2)
        receipt = SimulatedTransport(self.store).start(lease, NOW + 10)
        with self.assertRaises(OperatorSessionError):
            self.store.confirm_start(lease, receipt, NOW + 9)
        self.assertEqual(self.store.confirm_start(lease, receipt, NOW + 11).state, "ringing")

    def test_outcomes_are_not_fabricated(self):
        lease = self.claim()
        self.store.reserve_dispatch(lease, NOW + 2)
        with self.assertRaises(OperatorSessionError):
            self.store.confirm_start(lease, "sim-" + lease.session_id, NOW + 2)
        receipt = SimulatedTransport(self.store).start(lease, NOW + 3)
        with self.assertRaises(OperatorSessionError):
            self.store.confirm_start(lease, "CA" + "a" * 32, NOW + 3)
        self.store.confirm_start(lease, receipt, NOW + 4)
        with self.assertRaises(OperatorSessionError):
            self.store.finish(lease, receipt, NOW + 5)
        self.assertEqual(self.store.finish(lease, receipt, NOW + 5, outcome="busy").reason, "busy")

    def test_no_answer_and_failed_outcomes(self):
        for occurrence, outcome in ((1, "no_answer"), (2, "failed")):
            scope = binding(occurrence=occurrence)
            lease = self.claim(scope=scope, now=NOW + 1 + occurrence * 10)
            run_simulated_dispatch(self.store, lease, NOW + 2 + occurrence * 10)
            result = self.store.finish(lease, "sim-" + lease.session_id,
                                       NOW + 3 + occurrence * 10, outcome=outcome)
            self.assertEqual((result.state, result.reason), ("failed", outcome))

    def test_cancel_before_dispatch_is_final(self):
        lease = self.claim()
        ended = self.store.cancel(lease.session_id, self.binding, NOW + 2)
        self.assertEqual(ended.state, "canceled")
        with self.assertRaises(OperatorSessionError):
            run_simulated_dispatch(self.store, lease, NOW + 3)
        self.assertIsNone(self.leg(lease.session_id))
        self.assertEqual(self.prepare(now=NOW + 3), ended)

    def test_cancel_after_reservation_fences_unstarted_transport(self):
        lease = self.claim()
        self.store.reserve_dispatch(lease, NOW + 2)
        self.assertEqual(self.store.cancel(lease.session_id, self.binding, NOW + 3).state,
                         "needs_review")
        with self.assertRaises(OperatorSessionError):
            SimulatedTransport(self.store).start(lease, NOW + 4)
        result = self.store.reconcile_simulation(lease.session_id, self.binding, NOW + 5)
        self.assertEqual((result.state, result.reason, result.dispatch_count),
                         ("canceled", "simulated_leg_absent", 1))

    def test_cancel_active_call_requires_reconciliation(self):
        lease = self.claim()
        run_simulated_dispatch(self.store, lease, NOW + 2)
        receipt = "sim-" + lease.session_id
        self.store.mark_answered(lease, receipt, NOW + 3)
        self.assertEqual(self.store.cancel(lease.session_id, self.binding, NOW + 4).state,
                         "needs_review")
        with self.assertRaises(OperatorSessionError):
            self.store.finish(lease, receipt, NOW + 5)
        result = self.store.reconcile_simulation(lease.session_id, self.binding, NOW + 6)
        self.assertEqual(result.state, "canceled")
        self.assertEqual(self.leg(lease.session_id)["state"], "canceled")
        self.assertEqual(self.store.reconcile_simulation(lease.session_id, self.binding, NOW + 7),
                         result)

    def test_lost_response_before_effect_is_spent(self):
        lease = self.claim()
        unknown = run_simulated_dispatch(self.store, lease, NOW + 2, fault="before_effect")
        self.assertEqual((unknown.state, unknown.dispatch_count), ("needs_review", 1))
        self.assertIsNone(self.leg(lease.session_id))
        with self.assertRaises(OperatorSessionError):
            run_simulated_dispatch(self.store, lease, NOW + 3)
        result = self.store.reconcile_simulation(lease.session_id, self.binding, NOW + 4)
        self.assertEqual((result.state, result.dispatch_count), ("canceled", 1))

    def test_lost_response_after_effect_survives_restart_without_redial(self):
        lease = self.claim()
        unknown = run_simulated_dispatch(self.store, lease, NOW + 2, fault="after_effect")
        self.assertEqual((unknown.state, unknown.dispatch_count), ("needs_review", 1))
        self.assertEqual(self.leg(lease.session_id)["state"], "ringing")
        other_scope = binding(occurrence=2, agent_id=8)
        other = self.prepare(other_scope, NOW + 3)
        restart = OperatorSimulationStore(self.path)
        with self.assertRaises(OperatorSessionError):
            restart.claim(other.session_id, other_scope, "worker-two", BOOT, NOW + 4)
        self.assertEqual(restart.health(NOW + 4)["needs_review"], 1)
        result = restart.reconcile_simulation(lease.session_id, self.binding, NOW + 5)
        self.assertEqual(result.state, "canceled")
        self.assertEqual(self.leg(lease.session_id)["state"], "canceled")
        self.assertEqual(restart.claim(other.session_id, other_scope, "worker-two", BOOT, NOW + 6)
                         .session_id, other.session_id)
        with self.assertRaises(OperatorSessionError):
            restart.claim(lease.session_id, self.binding, "worker-two", BOOT, NOW + 6)

    def test_crash_between_effect_and_confirmation_is_never_replayed(self):
        lease = self.claim()
        self.store.reserve_dispatch(lease, NOW + 2)
        receipt = SimulatedTransport(self.store).start(lease, NOW + 3)
        restart = OperatorSimulationStore(self.path)
        self.assertEqual(restart.recover(NOW + 31)[0].state, "needs_review")
        with self.assertRaises(OperatorSessionError):
            self.store.confirm_start(lease, receipt, NOW + 32)
        self.assertEqual(restart.reconcile_simulation(lease.session_id, self.binding, NOW + 33).state,
                         "canceled")
        self.assertEqual(self.leg(lease.session_id)["state"], "canceled")

    def test_loss_during_active_call_is_not_completed(self):
        lease = self.claim()
        run_simulated_dispatch(self.store, lease, NOW + 2)
        self.store.mark_answered(lease, "sim-" + lease.session_id, NOW + 3)
        self.assertEqual(self.store.recover(NOW + 31)[0].state, "needs_review")
        self.assertEqual(self.store.reconcile_simulation(lease.session_id, self.binding, NOW + 32).state,
                         "canceled")

    def test_pending_limit_and_bounded_maintenance(self):
        for index in range(1, 21):
            self.prepare(binding(occurrence=index), lifetime=60)
        with self.assertRaises(OperatorSessionError):
            self.prepare(binding(occurrence=21))
        self.assertEqual(len(self.store.recover(NOW + 60, limit=5)), 5)
        self.assertEqual(self.store.health(NOW + 60)["pending_sessions"], 15)
        self.assertEqual(len(self.store.recover(NOW + 61)), 15)

    def test_cli_demo_never_uses_network_or_existing_provider(self):
        with patch("socket.create_connection", side_effect=AssertionError("unexpected network")), \
             patch("urllib.request.urlopen", side_effect=AssertionError("unexpected provider")):
            report = exercise()
        self.assertTrue(report["simulation_only"])
        self.assertFalse(report["execution_enabled"])
        self.assertFalse(report["live_certified"])
        self.assertEqual(report["nominal"]["terminal"], "completed")
        self.assertEqual(report["uncertain_dispatch"]["terminal"], "canceled")
        self.assertEqual(report["health"]["pending_sessions"], 0)

    def test_cli_requires_explicit_simulation(self):
        denied = subprocess.run([sys.executable, "-m", "telephony.operator_simulation"],
                                capture_output=True, text=True, timeout=15)
        self.assertNotEqual(denied.returncode, 0)
        accepted = subprocess.run([sys.executable, "-m", "telephony.operator_simulation", "--simulate"],
                                  capture_output=True, text=True, timeout=15, check=True)
        self.assertFalse(json.loads(accepted.stdout)["execution_enabled"])


if __name__ == "__main__":
    unittest.main()
