"""Tracky 1E5: acceptance gate must remain valid throughout actual sampling.

Synthetic camera, deterministic revocation, and no physical certification.
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix="tracky-live-approval-") as root:
    os.environ["HOMESERVER_DATA_DIR"]=root
    os.environ["VP3_OS_HARDWARE_ADAPTER"]="disabled"
    from app.database import initialize_database
    initialize_database()
    from app.services import federated_data, tracky_native_camera as native
    from app.services import tracky_native_certification as cert
    from app.services import tracky_native_managed_session as managed
    from app.services import tracky_native_session_evidence as evidence
    from app.services import tracky_native_diagnosis as diag
    from app.services import tracky_physical_context as tracky
    from app.services import health_repair

    datasets={key:[] for key in federated_data.DATASETS}
    reconciled=federated_data.reconcile_snapshot({
        "version":"2.2","federation_version":"2.4",
        "authoritative_source":"vp3_cloud","snapshot_mode":"full",
        "covered_datasets":list(datasets),"revision":"live-approval-fixture",
        "datasets":datasets,
    },observed_source="homeserver",trigger_reason="live-approval-fixture")
    assert reconciled["status"]=="completed"

    approved={"value":True}
    def current_approval():
        return {"owner_accepted_current_run":approved["value"]}
    model={"installed":True,"model_present":True,"model_integrity_verified":True,"runtime_version":"synthetic",
           "model_sha256":"a"*64}
    entered=threading.Event()
    release=threading.Event()
    calls=[]
    def blocking_observation(index,stop):
        calls.append(index)
        entered.set()
        release.wait(timeout=5)
        return {"summary":"Unverified possible face region.","confidence":0.0}
    with patch.object(cert,"status",side_effect=current_approval), \
         patch.object(native,"model_preflight",return_value=model), \
         patch.object(native,"_observe_exclusive",side_effect=blocking_observation):
        started=managed.start(consent=True,scope=managed.SCOPE,camera_index=0,
                              sample_count=2,interval_seconds=5)
        assert started["active"],started
        assert entered.wait(timeout=2)
        assert native.capture_busy()
        approved["value"]=False  # Detector update/review revocation during frame.
        release.set()
        for _ in range(120):
            if not managed.status()["active"]:
                break
            time.sleep(0.05)
        result=managed.status()
        assert not result["active"],result
        assert result["phase"]=="stopped",result
        assert result["reason"]=="acceptance_revoked",result
        assert result["completed_samples"]==0,result
        assert len(calls)==1,calls
        assert tracky._provider_snapshot()[0] is None
        assert not native.capture_busy()
        assert evidence.latest()["phase"]=="stopped"
        assert evidence.latest()["reason"]=="acceptance_revoked"
        assert evidence.history()[0]["evidence"]["completed_samples"]==0
        assert evidence.history()[0]["hardware_certified"] is False
        with patch.object(diag,"_saved",return_value={"phase":"completed"}), \
             patch.object(diag,"diagnose",return_value={"issues":[]}):
            issues=health_repair._native_tracky_issues()
            ended=next(i for i in issues if i["key"]=="tracky:managed-session-ended")
            assert ended["repair"]["agent_can_execute"] is False
            assert ended["repair"]["owner_approval_required"] is True

        # The provider also denies a request before touching the camera when
        # approval is already stale.
        class Alive:
            def is_alive(self):return True
        with patch.object(managed,"_WORKER",Alive()), \
             patch.object(managed,"_ALLOWED_REQUEST","test-before-camera"), \
             patch.object(native,"_observe") as capture:
            managed._STOP.clear()
            managed._STATE["phase"]="running"
            try:
                managed._provider({"request_type":"refresh_current_view",
                                   "request_id":"test-before-camera"})
                raise AssertionError("Revoked acceptance opened a camera")
            except tracky.TrackyPhysicalError as exc:
                assert exc.status_code==403
            capture.assert_not_called()
            assert managed._STOP.is_set()
print("TRACKY_LIVE_APPROVAL_V1E5: in-flight revocation, no late result, pre-capture denial, durable reason PASS")
