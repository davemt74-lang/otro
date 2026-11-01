"""PH3I-B optional signed check-in from the LiveKit worker *launcher* process.

Reporting only proves that this process and its local preflight are running.
It does not verify LiveKit room connectivity, Twilio, audio or user identity.
There is no inbound HomeServer listener or new permission grant.
"""

from __future__ import annotations

import os
import re
import threading
import uuid
from collections.abc import Callable, Mapping

from .livekit_voice import WorkerSettings
from .preflight import inspect_worker_config
from .voice_turns import VoiceTurnError, signed_json_post


WORKER_ID = re.compile(r"^[A-Za-z0-9_-]{3,64}$")
INTERVAL_SECONDS = 30


class WorkerHealthPublisher:
    def __init__(self, settings: WorkerSettings, environment: Mapping[str, str], *,
                 sender: Callable = signed_json_post,
                 nonce_factory: Callable[[], str] = lambda: uuid.uuid4().hex) -> None:
        worker_id = environment.get("VP3_PHONE_WORKER_ID", "vp3-phone")
        if not WORKER_ID.fullmatch(worker_id):
            raise VoiceTurnError("Invalid private phone worker identifier")
        if settings.resolve_backend != "cloud":
            raise VoiceTurnError("Cloud worker health requires Cloud resolve mode")
        self.settings = settings
        self.worker_id = worker_id
        self.boot_id = uuid.uuid4().hex
        self.preflight_ready = inspect_worker_config(settings, environment)["ready_for_staging"]
        self.sender = sender
        self.nonce_factory = nonce_factory

    def publish_once(self) -> bool:
        """Single attempt, no automatic replay after an uncertain network result."""
        payload = {"worker_id": self.worker_id, "boot_id": self.boot_id,
                   "nonce": self.nonce_factory(), "phase": "process_reporting",
                   "preflight_ready": self.preflight_ready}
        try:
            result = self.sender(
                self.settings.edge_url.rstrip("/") + "/api/phone-worker-heartbeat-v171.php",
                self.settings.worker_secret, payload, allow_local=False, timeout=5)
            return result.get("ok") is True
        except (VoiceTurnError, OSError, TimeoutError):
            # Absence of check-ins is the only reliable offline indicator.
            return False

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.publish_once()
            if stop.wait(INTERVAL_SECONDS):
                break


def start_worker_health(settings: WorkerSettings, environment: Mapping[str, str]
                        ) -> tuple[threading.Event, threading.Thread] | None:
    """Explicitly opt in: no accounts, network activity or costs by default."""
    if environment.get("VP3_PHONE_WORKER_HEALTH_ENABLED") != "1":
        return None
    monitor = WorkerHealthPublisher(settings, environment)
    stop = threading.Event()
    thread = threading.Thread(target=monitor.run, args=(stop,),
                              name="vp3_phone_worker_health", daemon=True)
    thread.start()
    return stop, thread
