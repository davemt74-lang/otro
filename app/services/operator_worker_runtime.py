"""Opt-in HomeServer lifespan integration for Operator reporting only."""
from __future__ import annotations

import os
import threading
from collections.abc import Callable, Mapping

from telephony.operator_worker_https import cloud_registration_host
from telephony.operator_worker_pairing import open_current_paired_worker
from telephony.operator_worker_supervisor import OperatorWorkerSupervisor


def inactive_report(state: str, *, enabled: bool = False, reason: str | None = None) -> dict:
    return {"enabled": enabled, "state": state, "current": False, "last_ack_epoch": None,
            "lease_until": 0, "capabilities": [], "reason": reason,
            "execution_enabled": False, "live_certified": False}


class OperatorWorkerRuntime:
    """One thread per app process, started only by explicit local configuration.

    Shutdown joins with a bounded timeout. If OS/DNS/I/O remains blocked, the
    daemon is fenced from new work by its stop event, is not replaced inside
    this process, and status remains non-current until it actually exits.
    """
    def __init__(self, opener: Callable = open_current_paired_worker) -> None:
        self._opener = opener
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop: threading.Event | None = None
        self._supervisor: OperatorWorkerSupervisor | None = None
        self._inactive = inactive_report("disabled")

    def start(self, environment: Mapping[str, str] | None = None) -> bool:
        env = os.environ if environment is None else environment
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._thread = None
            self._stop = None
            self._supervisor = None
            if env.get("VP3_OPERATOR_WORKER_ENABLED") != "1":
                self._inactive = inactive_report("disabled")
                return False
            origin = env.get("VP3_OPERATOR_CLOUD_ORIGIN", "")
            try:
                cloud_registration_host(origin)
            except Exception:
                self._inactive = inactive_report("configuration_required", enabled=True,
                                                reason="explicit_https_origin_required")
                return False
            stop = threading.Event()
            supervisor = OperatorWorkerSupervisor(lambda: self._opener(origin))
            thread = threading.Thread(target=supervisor.run, args=(stop,),
                                      name="vp3-operator-worker-reporting", daemon=True)
            self._stop, self._supervisor, self._thread = stop, supervisor, thread
            self._inactive = inactive_report("starting", enabled=True)
            try:
                thread.start()
            except RuntimeError:
                self._thread = self._stop = self._supervisor = None
                self._inactive = inactive_report("unavailable", enabled=True, reason="worker_start_failed")
                return False
            return True

    def stop(self, timeout: float = 8.0) -> bool:
        with self._lock:
            thread, stop = self._thread, self._stop
            if stop is not None:
                stop.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, min(float(timeout), 8.0)))
        return thread is None or not thread.is_alive()

    def report(self) -> dict:
        with self._lock:
            supervisor, thread, stop = self._supervisor, self._thread, self._stop
            if supervisor is None or thread is None:
                return dict(self._inactive)
            stopping = stop is not None and stop.is_set()
            alive = thread.is_alive()
        # Status never acquires the paired handle lock or waits for a socket.
        report = supervisor.report()
        if stopping and alive:
            report.update(state="stopping", current=False, lease_until=0, capabilities=[])
        elif not alive and report["state"] != "stopped":
            report.update(state="unavailable", current=False, lease_until=0, capabilities=[],
                          reason="worker_thread_exited")
        return report


_runtime = OperatorWorkerRuntime()


def start() -> bool:
    return _runtime.start()


def stop() -> bool:
    return _runtime.stop()


def report() -> dict:
    return _runtime.report()
