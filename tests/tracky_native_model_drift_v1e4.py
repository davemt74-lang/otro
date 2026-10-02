"""Tracky 1E4: local model integrity fingerprint and fail-closed review gates.

Synthetic fixtures only; a matching local hash is NOT vendor signing or
independent physical hardware certification.
"""
import json
import hashlib
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch
from importlib.machinery import ModuleSpec

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix="tracky-model-drift-") as root:
    os.environ["HOMESERVER_DATA_DIR"]=root
    os.environ["VP3_OS_HARDWARE_ADAPTER"]="disabled"
    from app.database import db,initialize_database
    initialize_database()
    from app.services import tracky_native_model_integrity as integrity
    from app.services import tracky_native_camera as native
    from app.services import tracky_native_diagnosis as diag
    from app.services import tracky_native_certification as cert
    from app.services import tracky_native_managed_session as managed
    from app.services import tracky_physical_context as tracky
    from app.services import health_repair

    model=Path(root)/"haarcascade_frontalface_default.xml"
    baseline_bytes=b"<synthetic-model-v1>"+b"X"*120000
    model.write_bytes(baseline_bytes)
    cv2=types.ModuleType("cv2")
    cv2.__spec__=ModuleSpec("cv2",loader=None)
    cv2.__version__="synthetic-1.0"
    cv2.data=types.SimpleNamespace(haarcascades=root+"/")
    with patch.dict(sys.modules,{"cv2":cv2}), \
         patch.object(integrity,"EXPECTED_HAAR_SHA256",hashlib.sha256(baseline_bytes).hexdigest()):
        baseline=native.model_preflight()
        assert baseline["model_present"] is True
        assert len(baseline["model_sha256"])==64
        assert "haarcascade" not in str(baseline).lower()
        model.write_bytes(b"<synthetic-model-v2>"+b"X"*120000)
        changed=native.model_preflight()
        assert baseline["model_sha256"]!=changed["model_sha256"]
    measurement={"status":"native_detector_completed","driver_worker_exited":True,
        "measurement":{"frame_captured":True,"detector_executed":True,
                       "camera_release_call_completed":True,
                       "capture_and_inference_ms":3,"inference_ms":2}}
    current=[baseline]
    def current_model():
        return current[0]
    with patch.object(native,"model_preflight",side_effect=current_model), \
         patch.object(native,"status",return_value={"last_test":measurement,"running":False,
                                                  "capture_worker_active":False}):
        diag.before_test()
        diag.after_test(status="completed")
        diag._save({"phase":"privacy_reviewed","boot_id":diag._BOOT})
        cert.record_privacy()
        accepted=cert.accept_owner_review(consent=True,installed_device=True,
            camera_release_observed=True,software_privacy_gate_observed=True)
        assert accepted["owner_accepted_current_run"] is True
        assert accepted["installed_model_matches_last_test"] is True
        assert accepted["hardware_certified"] is False

        # A replacement detector invalidates prior owner acceptance immediately,
        # even in the same process and even though test measurements survive.
        current[0]=changed
        drift=cert.status()
        assert drift["owner_accepted_current_run"] is False
        assert drift["review_ready"] is False
        assert drift["requires_new_owner_test_due_model_change"] is True
        assert "tracky:native-model-evidence-expired" in {
            i["key"] for i in health_repair._native_tracky_issues()
        }
        try:
            managed.start(consent=True,scope=managed.SCOPE,
                          camera_index=0,sample_count=1,interval_seconds=5)
            raise AssertionError("Stale model approval was accepted")
        except managed.ManagedSessionError as err:
            assert err.status_code==409
        assert tracky._provider_snapshot()[0] is None

        # Pre-1E4 historic records have no digest. They must NOT be promoted
        # current merely because their original runtime version still matches.
        current[0]=baseline
        with db() as connection:
            row=connection.execute(
                "SELECT id,evidence_json FROM runtime_certification_runs "
                "WHERE test_key=? ORDER BY rowid DESC LIMIT 1",(cert.TEST,)
            ).fetchone()
            prior=json.loads(row["evidence_json"])
            prior.pop("model_sha256")
            connection.execute(
                "UPDATE runtime_certification_runs SET evidence_json=? WHERE id=?",
                (json.dumps(prior),row["id"])
            )
        assert cert.status()["installed_model_matches_last_test"] is False
        assert cert.status()["owner_accepted_current_run"] is False
print("TRACKY_NATIVE_MODEL_DRIFT_V1E4: fingerprint, stale review denial, Agent repair, legacy fail-closed PASS")
