"""Phase 8A.4E: supervised, simulation-only registration lifecycle.

No call/session dispatch, carrier, model, media or LiveKit dependency. Exactly
one local thread owns a handle. Read-only status never waits on network I/O.
Cloud remains the authoritative boot/lease fence across processes.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

from .operator_worker_protocol import WorkerRegistrationError

CAPABILITIES = ("session.recover", "session.simulate")
HEARTBEAT_SECONDS = 20
ACK_FRESH_SECONDS = 25
RESTART_FENCE_SECONDS = 130
RETRY_DELAYS = (2, 4, 8)
MAX_OPEN_BACKOFF = 60


class OperatorWorkerSupervisor:
    """Own one paired handle and never advance an unresolved wire sequence.

    All I/O occurs on the supervisor's calling thread, never the status lock.
    The injectable clock and explicit step() make the state machine testable
    without wall-clock sleeps or network access. Only run() may call step()
    in production. Do not call step()/shutdown() concurrently.
    """

    def __init__(self, open_worker: Callable, *, monotonic: Callable = time.monotonic,
                 wall: Callable = lambda: int(time.time())) -> None:
        self._open = open_worker
        self._mono = monotonic
        self._wall = wall
        self._lock = threading.Lock()
        self._worker = None
        self._mode = "open"
        self._due = self._mono()
        self._backoff = 5
        self._retries = 0
        self._last_ack = None
        self._lease_until = 0
        self._fresh_deadline = 0.0
        self._phase = "starting"
        self._failure = None
        self._closed = False

    def _publish(self, phase: str, reason: str | None = None) -> None:
        with self._lock:
            self._phase = phase
            self._failure = reason

    def report(self) -> dict:
        """Redacted observation, not a live connection or execution grant."""
        with self._lock:
            phase = self._phase
            now = self._wall()
            current = (phase == "reporting" and self._last_ack is not None
                       and self._last_ack <= now < self._lease_until
                       and self._mono() < self._fresh_deadline)
            if phase == "reporting" and not current:
                phase = "stale"
            return {"enabled": True, "state": phase, "current": current,
                    "last_ack_epoch": self._last_ack,
                    "lease_until": self._lease_until if current else 0,
                    "capabilities": list(CAPABILITIES) if current else [],
                    "reason": self._failure,
                    "execution_enabled": False, "live_certified": False}

    def _drop(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            try:
                worker.close()
            except Exception:
                pass

    def _open_failed(self) -> None:
        self._drop()
        self._mode = "open"
        self._due = self._mono() + self._backoff
        self._backoff = min(MAX_OPEN_BACKOFF, self._backoff * 2)
        self._publish("waiting", "paired_credential_unavailable")

    def _fence(self, reason: str = "registration_unresolved") -> None:
        # Even a failed send might have committed a 90-second server lease.
        # Allow freshness/skew in addition to that lease, starting from the
        # end of the last attempt. Cloud independently rejects active boots.
        self._drop()
        self._mode = "open"
        self._due = self._mono() + RESTART_FENCE_SECONDS
        self._publish("cooldown", reason)

    def _pending(self) -> bool:
        if self._worker is None:
            return False
        try:
            return self._worker.status().get("state") == "awaiting_ack"
        except Exception:
            return False

    def _failed_exchange(self) -> None:
        if self._pending() and self._retries < len(RETRY_DELAYS):
            self._mode = "retry"
            self._due = self._mono() + RETRY_DELAYS[self._retries]
            self._retries += 1
            self._publish("awaiting_ack", "registration_unresolved")
        else:
            self._fence()

    def _accept_reporting(self, state: dict) -> None:
        now = self._wall()
        lease = state.get("lease_until")
        if (state.get("state") != "reporting" or state.get("current") is not True
                or state.get("execution_enabled") is not False
                or state.get("live_certified") is not False
                or state.get("capabilities") != list(CAPABILITIES)
                or type(lease) is not int or not now < lease <= now + 95):
            raise WorkerRegistrationError("Registration receipt is not reporting-only")
        mono = self._mono()
        with self._lock:
            self._last_ack = now
            self._lease_until = lease
            self._fresh_deadline = mono + min(lease - now, ACK_FRESH_SECONDS)
            self._phase = "reporting"
            self._failure = None
        self._mode = "heartbeat"
        self._due = mono + HEARTBEAT_SECONDS
        self._backoff = 5
        self._retries = 0

    def step(self, stop: threading.Event | None = None) -> bool:
        """Do at most one scheduled exchange (plus opening for first register).

        Returns False when stopped or not due. An unresolved request is never
        replaced by a new sequence. Closing a handle is not carrier recovery.
        """
        if self._closed or (stop is not None and stop.is_set()) or self._mono() < self._due:
            return False
        if self._mode == "open":
            self._publish("connecting")
            try:
                self._worker = self._open()
            except Exception:
                self._open_failed()
                return True
            if stop is not None and stop.is_set():
                self._drop()
                return False
            self._mode = "register"
            self._retries = 0
        mode = self._mode
        self._publish("connecting" if mode == "register" else
                      "awaiting_ack" if mode == "retry" else "checking")
        try:
            if self._worker is None:
                raise WorkerRegistrationError("No paired handle")
            if mode == "retry":
                state = self._worker.retry_pending()
            else:
                if mode not in ("register", "heartbeat"):
                    raise WorkerRegistrationError("Unexpected supervision state")
                if mode == "heartbeat":
                    old = self._worker.status()
                    if old.get("state") != "reporting" or old.get("current") is not True:
                        raise WorkerRegistrationError("Previous lease is not current")
                state = self._worker.send(mode, CAPABILITIES)
            self._accept_reporting(state)
        except Exception:
            # Fixed reason codes only: exception text may contain local details.
            self._failed_exchange()
        return True

    def shutdown(self) -> None:
        """Retire once only if an exact current handle has no unresolved request."""
        if self._closed:
            return
        self._closed = True
        self._publish("stopping")
        confirmed = False
        had_worker = self._worker is not None
        try:
            if self._worker is not None:
                state = self._worker.status()
                if state.get("current") is True and state.get("state") == "reporting":
                    result = self._worker.send("retire", ())
                    confirmed = (result.get("state") == "retired"
                                 and result.get("current") is False
                                 and result.get("lease_until") == 0
                                 and result.get("execution_enabled") is False
                                 and result.get("live_certified") is False)
        except Exception:
            pass
        finally:
            self._drop()
            self._publish("stopped", None if confirmed or not had_worker
                          else "retirement_unconfirmed")

    def run(self, stop: threading.Event) -> None:
        try:
            while not stop.is_set():
                self.step(stop)
                # Stop is interruptible; no independent timer or second worker.
                remaining = max(0.05, self._due - self._mono())
                stop.wait(min(remaining, 1.0))
        finally:
            self.shutdown()
