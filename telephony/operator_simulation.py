"""Non-billable Phase 8A.1 worker lifecycle exercise.

Run only with --simulate. The demo uses a temporary private scratch database
that is deleted at exit; it reads no account configuration or provider keys.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .operator_session_simulation import (
    OperatorSimulationStore, SessionBinding, run_simulated_dispatch,
)


def exercise() -> dict:
    now = 1800000000  # Synthetic clock; no dependency on a real schedule.
    with tempfile.TemporaryDirectory(prefix="vp3-operator-simulation-") as directory:
        path = Path(directory) / "simulation.sqlite3"
        store = OperatorSimulationStore(path)
        binding = SessionBinding(1, 1, 1, 1, "1" * 32, "scheduled", "2" * 32)
        session = store.prepare(binding, now)
        lease = store.claim(session.session_id, binding, "fixture-worker", "3" * 32, now + 1)
        ringing = run_simulated_dispatch(store, lease, now + 2)
        receipt = "sim-" + session.session_id
        store.mark_answered(lease, receipt, now + 3)
        completed = store.finish(lease, receipt, now + 4)
        second = SessionBinding(1, 1, 1, 1, "1" * 32, "scheduled", "4" * 32)
        pending = store.prepare(second, now + 5)
        lost = store.claim(pending.session_id, second, "fixture-worker", "3" * 32, now + 6)
        run_simulated_dispatch(store, lost, now + 7, fault="after_effect")
        # Model a new process reopening the same scratch database. No live
        # transport can be selected and the spent dispatch cannot be reclaimed.
        restarted = OperatorSimulationStore(path)
        closed = restarted.reconcile_simulation(pending.session_id, second, now + 8)
        health = restarted.health(now + 8)
        return {"phase": "8A.1", "simulation_only": True,
                "execution_enabled": False, "live_certified": False,
                "nominal": {"ringing": ringing.state, "terminal": completed.state,
                            "states": [row["state"] for row in store.events(session.session_id, binding)],
                            "dispatch_count": completed.dispatch_count},
                "uncertain_dispatch": {"terminal": closed.state,
                                       "dispatch_count": closed.dispatch_count,
                                       "states": [row["state"] for row in restarted.events(pending.session_id, second)]},
                "health": health}


def main() -> None:
    parser = argparse.ArgumentParser(description="VP3 Operator local transport simulation")
    parser.add_argument("--simulate", action="store_true", required=True,
                        help="Run only the non-networked synthetic lifecycle exercise")
    parser.parse_args()
    report = exercise()
    print(json.dumps(report, indent=2))
    if (report["nominal"]["terminal"] != "completed"
            or report["uncertain_dispatch"]["terminal"] != "canceled"
            or report["health"]["pending_sessions"] != 0):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
