"""Section 1D contracts: local evidence, review gates, restart invalidation.
Synthetic tests never certify real physical camera hardware.
"""
import os, sys, tempfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(prefix="tracky-native-v1d-") as home:
    os.environ["HOMESERVER_DATA_DIR"]=home
    os.environ["VP3_OS_HARDWARE_ADAPTER"]="disabled"
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import tracky_native_certification as cert
    from app.services import tracky_native_diagnosis as diag, tracky_native_camera as native
    from app.services.tasks import scheduler
    base="/api/v1/control/onboarding/visual/native/"
    hdr={"X-Requested-With":"XMLHttpRequest"}
    with TestClient(app) as client:
        scheduler.stop()
        assert client.get(base+"certification").status_code==401
        assert client.post("/__owner/session",
            headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
        assert client.post(base+"owner-review",json={},headers=hdr).status_code==422
        assert client.get(base+"certification").json()["hardware_certified"] is False
        full={"consent":True,"installed_device":True,
              "camera_release_observed":True,"software_privacy_gate_observed":True}
        assert client.post(base+"owner-review",json=full).status_code==403
        assert client.post(base+"owner-review",json=full,headers=hdr).status_code==409
        assert not cert.status()["history"]
        model={"installed":True,"model_present":True,"model_integrity_verified":True,"runtime_version":"synthetic"}
        evidence={"status":"native_detector_completed","driver_worker_exited":True,
                  "measurement":{"frame_captured":True,"detector_executed":True,
                                 "camera_release_call_completed":True,
                                 "capture_and_inference_ms":14,"inference_ms":8}}
        diag.before_test()
        with patch.object(native,"model_preflight",return_value=model), \
             patch.object(native,"status",return_value={"last_test":evidence,"running":False,
                                                        "capture_worker_active":False}):
            diag.after_test(status="completed")
            assert cert.status()["review_ready"] is False
            assert client.post(base+"owner-review",json=full,headers=hdr).status_code==409
            diag._save({"phase":"privacy_reviewed","boot_id":diag._BOOT})
            cert.record_privacy()
            assert cert.status()["review_ready"] is True
            assert cert.status()["hardware_certified"] is False
            assert client.post(base+"owner-review",json=full,headers=hdr).status_code==200
            assert cert.status()["owner_accepted_current_run"] is True
            assert cert.status()["hardware_certified"] is False
            with patch.object(diag,"_BOOT","new-process-id"):
                assert cert.status()["owner_accepted_current_run"] is False
                assert cert.status()["hardware_certified"] is False
        history=cert.status()["history"]
        assert len(history)==3
        body=str(history).lower()
        for forbidden in ("embedding","raw_frame","camera_uri","physical_camera_disconnect_verified': true"):
            assert forbidden not in body
print("TRACKY_NATIVE_CERTIFICATION_V1D: auth, evidence, privacy, restart, review PASS")
