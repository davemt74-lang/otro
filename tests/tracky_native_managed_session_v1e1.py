"""Section 1E1: supervised camera lease and shared driver tests.
All frames and hardware in these tests are synthetic.
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix="tracky-supervised-") as root:
    os.environ["HOMESERVER_DATA_DIR"] = root
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import federated_data, tracky_native_camera as native
    from app.services import tracky_native_certification as cert
    from app.services import tracky_native_managed_session as managed
    from app.services import tracky_physical_context as tracky
    from app.services.tasks import scheduler

    base = "/api/v1/control/onboarding/visual/native/session/"
    hdr = {"X-Requested-With": "XMLHttpRequest"}
    payload = {"consent": True, "scope": managed.SCOPE, "camera_index": 0,
               "sample_count": 1, "interval_seconds": 5}
    with TestClient(app) as client:
        scheduler.stop()
        assert client.get(base + "status").status_code == 401
        assert client.post(base + "start", json=payload, headers=hdr).status_code == 401
        assert client.post("/__owner/session",
            headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200
        assert client.post(base + "start", json=payload).status_code == 403
        assert client.post(base + "start", json=dict(payload, consent=False),
                           headers=hdr).status_code == 403
        assert client.post(base + "start", json=dict(payload, scope="cloud"),
                           headers=hdr).status_code == 403
        assert client.post(base + "start", json=dict(payload, camera_index=True),
                           headers=hdr).status_code == 422
        assert client.post(base + "start", json=dict(payload, sample_count=13),
                           headers=hdr).status_code == 422
        assert client.post(base + "start", json=payload, headers=hdr).status_code == 409
        assert managed.status()["active"] is False
        with patch.object(cert, "status", return_value={"owner_accepted_current_run": True}), \
             patch.object(native, "_privacy", return_value=True):
            assert client.post(base + "start", json=payload, headers=hdr).status_code == 403
        assert managed.status()["active"] is False

        datasets = {key: [] for key in federated_data.DATASETS}
        reconciled = federated_data.reconcile_snapshot({
            "version": "2.2", "federation_version": "2.4",
            "authoritative_source": "vp3_cloud", "snapshot_mode": "full",
            "covered_datasets": list(datasets), "revision": "managed-v1e1-fixture",
            "datasets": datasets,
        }, observed_source="homeserver", trigger_reason="managed-v1e1-fixture")
        assert reconciled["status"] == "completed"
        fake = {"summary": "One unverified possible face region.",
                "capture_and_inference_ms": 3, "inference_ms": 2}
        model = {"installed": True, "model_present": True, "model_integrity_verified": True, "runtime_version": "synthetic"}
        with patch.object(cert, "status", return_value={"owner_accepted_current_run": True}), \
             patch.object(native, "model_preflight", return_value=model), \
             patch.object(native, "_observe_exclusive", return_value=fake) as observe:
            started = client.post(base + "start", json=payload, headers=hdr)
            assert started.status_code == 200, started.text
            for _ in range(100):
                if not managed.status()["active"]:
                    break
                time.sleep(0.05)
            result = managed.status()
            assert result["active"] is False
            assert result["completed_samples"] == 1, result
            assert result["phase"] == "completed", result
            assert observe.call_count == 1
            assert tracky._provider_snapshot()[0] is None
            assert result["hardware_certified"] is False
            assert result["identity_recognition"] is False
            assert "raw_frame" not in str(result).lower()

            # Shared native camera lock prevents competing capture, including
            # a camera worker that outlives the canonical provider timeout.
            entered = threading.Event()
            release = threading.Event()
            def delayed(index, cancellation):
                entered.set()
                release.wait(timeout=2)
                return fake
            with patch.object(native, "_observe_exclusive", side_effect=delayed):
                worker = threading.Thread(target=lambda: native._observe(0, threading.Event()))
                worker.start()
                assert entered.wait(timeout=1)
                assert native.capture_busy() is True
                try:
                    native._observe(0, threading.Event())
                    raise AssertionError("Driver lock did not reject concurrent capture")
                except native.NativeCameraError as exc:
                    assert exc.status_code == 409
                assert client.post(base + "start", json=payload, headers=hdr).status_code == 409
                release.set()
                worker.join(timeout=3)
                assert not worker.is_alive() and not native.capture_busy()
        assert client.post(base + "stop", headers=hdr).status_code == 200
        assert client.get(base + "status").json()["hardware_certified"] is False

print("TRACKY_NATIVE_MANAGED_V1E1: auth, consent, bounded sample, provider release, driver lock PASS")
