"""Tracky 1E2: heartbeat lease, revocation, camera worker and repair contracts.
All camera responses here are synthetic; physical hardware is not certified.
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix="tracky-session-safety-") as root:
    os.environ["HOMESERVER_DATA_DIR"] = root
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import federated_data, tracky_native_camera as native
    from app.services import tracky_native_certification as cert
    from app.services import tracky_native_managed_session as managed
    from app.services import tracky_physical_context as tracky, health_repair, tracky_native_diagnosis as diag
    from app.services.tasks import scheduler

    base = "/api/v1/control/onboarding/visual/native/session/"
    hdr = {"X-Requested-With": "XMLHttpRequest"}
    scope = {"consent": True, "scope": managed.SCOPE, "camera_index": 0,
             "sample_count": 3, "interval_seconds": 5}
    model = {"installed": True, "model_present": True, "runtime_version": "synthetic"}
    fake = {"summary": "One possible face region; no identity verified.", "confidence": 0.0}
    with TestClient(app) as client:
        scheduler.stop()
        assert client.post(base+"heartbeat", headers=hdr).status_code == 401
        assert client.post("/__owner/session",
            headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code == 200
        assert client.post(base+"heartbeat", headers=hdr).status_code == 409
        assert client.post(base+"heartbeat").status_code == 403
        datasets = {key: [] for key in federated_data.DATASETS}
        result = federated_data.reconcile_snapshot({
            "version": "2.2", "federation_version": "2.4",
            "authoritative_source": "vp3_cloud", "snapshot_mode": "full",
            "covered_datasets": list(datasets), "revision": "session-safety-fixture",
            "datasets": datasets,
        }, observed_source="homeserver", trigger_reason="session-safety-fixture")
        assert result["status"] == "completed"

        with patch.object(cert, "status", return_value={"owner_accepted_current_run": True}), \
             patch.object(native, "model_preflight", return_value=model), \
             patch.object(native, "_observe_exclusive", return_value=fake):
            started = client.post(base+"start", json=scope, headers=hdr)
            assert started.status_code == 200, started.text
            for _ in range(60):
                if managed.status()["completed_samples"] > 0:
                    break
                time.sleep(0.05)
            assert managed.status()["completed_samples"] >= 1
            assert client.post(base+"heartbeat", headers=hdr).status_code == 200
            # Read-only status, including Agent polling, must not refresh lease.
            stale = time.monotonic() - managed.HEARTBEAT_TTL_SECONDS - 1
            with patch.object(managed, "_LAST_HEARTBEAT", stale):
                observed = client.get(base+"status").json()
                assert observed["stop_requested"] is True
                for _ in range(60):
                    if not managed.status()["active"]:
                        break
                    time.sleep(0.05)
            stopped = managed.status()
            assert stopped["active"] is False, stopped
            assert stopped["phase"] == "stopped", stopped
            assert stopped["reason"] == "owner_presence_expired", stopped
            assert stopped["completed_samples"] < 3
            assert tracky._provider_snapshot()[0] is None
            assert client.post(base+"heartbeat", headers=hdr).status_code == 409
            # Only read-only, owner-gated canonical repair guidance is surfaced.
            with patch.object(diag, "_saved", return_value={"phase": "completed"}), \
                 patch.object(diag, "diagnose", return_value={"issues": []}):
                issues = health_repair._native_tracky_issues()
                entry = next(x for x in issues if x["key"] == "tracky:managed-session-ended")
                assert entry["repair"]["owner_approval_required"] is True
                assert entry["repair"]["agent_can_execute"] is False

        # While a synthetic camera operation is blocked, explicit owner
        # revocation suppresses late results, clears provider ownership and
        # does not credit a completed observation.
        entered = threading.Event()
        release = threading.Event()
        def slow_camera(index, stop):
            entered.set()
            release.wait(timeout=5)
            return fake
        with patch.object(cert, "status", return_value={"owner_accepted_current_run": True}), \
             patch.object(native, "model_preflight", return_value=model), \
             patch.object(native, "_observe_exclusive", side_effect=slow_camera):
            started = client.post(base+"start", json=scope, headers=hdr)
            assert started.status_code == 200, started.text
            assert entered.wait(timeout=2)
            assert native.capture_busy() is True
            cancelled = client.post(base+"stop", headers=hdr)
            assert cancelled.status_code == 200
            release.set()
            for _ in range(100):
                if not managed.status()["active"]:
                    break
                time.sleep(0.05)
            assert managed.status()["phase"] == "stopped", managed.status()
            assert managed.status()["completed_samples"] == 0
            assert tracky._provider_snapshot()[0] is None
            assert native.capture_busy() is False
            assert managed.status()["identity_recognition"] is False
            assert managed.status()["hardware_certified"] is False
print("TRACKY_NATIVE_SESSION_SAFETY_V1E2: heartbeat lease, privacy revocation, no late results and canonical repair PASS")
