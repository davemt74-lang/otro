"""Opt-in, non-networked worker registration exercise (Phase 8A.2)."""
from __future__ import annotations

import argparse
import json
import secrets
import tempfile
from pathlib import Path

from .operator_worker_protocol import (
    OperatorWorkerClient, WorkerCredential, WorkerRegistrationError,
)
from .operator_worker_registry import ReferenceWorkerRegistry


def exercise() -> dict:
    now = 1800000000
    caps = ("session.recover", "session.simulate")
    # Ephemeral synthetic credential; no account, environment or provider key.
    credential = WorkerCredential("1" * 32, 1, "2" * 32, "fixture-worker",
                                  secrets.token_bytes(32))
    with tempfile.TemporaryDirectory(prefix="vp3-operator-registration-") as directory:
        path = Path(directory) / "reference.sqlite3"
        authority = ReferenceWorkerRegistry(path, (credential,))
        worker = OperatorWorkerClient(credential)
        frame = worker.request("register", now, caps)
        ack = authority.process(frame, now)
        # Simulate a response lost on the first delivery; exact retry does
        # not change the original lease's expiry or registration generation.
        recovered = authority.process(worker.retry(now + 5), now + 5)
        if ack != recovered:
            raise WorkerRegistrationError("Reference replay changed a receipt")
        registered = worker.accept(recovered, now + 5)
        heartbeat = worker.request("heartbeat", now + 20, caps)
        reporting = worker.accept(authority.process(heartbeat, now + 20), now + 20)
        expired_at = reporting["lease_until"]
        stale = authority.list_workers(1, expired_at)[0]
        authority = ReferenceWorkerRegistry(path, (credential,))
        replacement = OperatorWorkerClient(credential)
        frame = replacement.request("register", expired_at + 1, caps)
        registered_again = replacement.accept(authority.process(frame, expired_at + 1),
                                              expired_at + 1)
        retiring = replacement.request("retire", expired_at + 2)
        retired = replacement.accept(authority.process(retiring, expired_at + 2), expired_at + 2)
        return {"phase": "8A.2", "simulation_only": True,
                "execution_enabled": False, "live_certified": False,
                "registration": registered, "heartbeat": reporting,
                "stale": {"state": stale["state"], "current": stale["current"]},
                "replacement": registered_again, "retired": retired}


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Operator worker registration reference")
    parser.add_argument("--simulate", action="store_true", required=True,
                        help="Use synthetic credentials and a temporary local reference authority")
    parser.parse_args()
    report = exercise()
    print(json.dumps(report, indent=2))
    if (report["registration"]["generation"] != 1
            or report["replacement"]["generation"] != 2
            or report["stale"]["current"]
            or report["retired"]["current"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
