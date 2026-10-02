"""Section 1C: installed-device diagnosis, consent and restart recovery contracts.

Hardware status here is synthetic. This test NEVER certifies a CI runner camera.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(prefix="tracky-native-diagnosis-v1c-") as root:
    os.environ["HOMESERVER_DATA_DIR"]=root
    os.environ["VP3_OS_HARDWARE_ADAPTER"]="disabled"
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import (
        tracky_native_model_integrity as integrity,
        tracky_native_diagnosis as diag, tracky_native_camera as native,
        vp3_os, health_repair, federated_data
    )
    from app.services.tasks import scheduler

    base="/api/v1/control/onboarding/visual/native/"
    headers={"X-Requested-With":"XMLHttpRequest"}
    with TestClient(app) as client:
        scheduler.stop()
        assert client.get(base+"diagnose").status_code==401
        assert client.post(base+"privacy-review",headers=headers,json={"consent":True}).status_code==401
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
        assert client.post(base+"privacy-review",json={"consent":True}).status_code==403
        assert client.post(base+"privacy-review",headers=headers,json={"consent":False}).status_code==403

        with patch.object(diag,"_model_preflight",return_value={
            "installed":False,"model_present":False,"runtime_loaded":False,"reason":"opencv_missing"
        }):
            report=client.get(base+"diagnose").json()
            assert report["model"]["reason"]=="opencv_missing"
            assert report["hardware_certified"] is False
            assert report["face_recognition_certified"] is False
            assert "opencv_missing" in {row["code"] for row in report["issues"]}
            assert all(not item["automated"] and item["owner_approval"] for item in report["recommendations"])

        # Passive model verification inspects installed files but never opens a camera.
        class FakeCV:
            data=SimpleNamespace(haarcascades=root+"/")
        cascade=Path(root)/"haarcascade_frontalface_default.xml"
        with patch.object(diag.importlib.util,"find_spec",return_value=object()), \
             patch.object(diag.importlib,"import_module",return_value=FakeCV()):
            missing=diag._model_preflight()
            assert missing["reason"]=="face_model_missing"
            cascade.write_text("<synthetic-test-model>")
            present=diag._model_preflight()
            assert present["reason"]=="ready" and present["model_present"]

        diag.before_test()
        saved=diag._saved()
        assert saved["phase"]=="running"
        with patch.object(diag,"_BOOT","simulated-new-boot"):
            state=diag.diagnose()
            assert state["interrupted_prior_attempt"] is True
            assert "interrupted_prior_attempt" in {row["code"] for row in state["issues"]}
        diag.after_test(status="failed")
        assert diag._saved()["phase"]=="failed"

        # New native health problems become CANONICAL repair guidance, never
        # arbitrary package-install commands executed by Agent Brain.
        with patch.object(diag,"_model_preflight",return_value={
            "installed":False,"model_present":False,"runtime_loaded":False,"reason":"opencv_missing"
        }):
            issues=health_repair._native_tracky_issues()
            assert any(item["key"]=="tracky:native-runtime-missing" for item in issues)
            runtime=next(item for item in issues if item["key"]=="tracky:native-runtime-missing")
            assert runtime["repair"]["agent_can_execute"] is False
            assert runtime["repair"]["owner_approval_required"] is True
            assert runtime["repair"]["action_key"] is None

        # Privacy review is never a camera-open challenge; only reported
        # physical-disconnect evidence may be acknowledged.
        assert client.post(base+"privacy-review",headers=headers,json={"consent":True}).json()["privacy_check"]=="not_verified"
        vp3_os.report_hardware_state("privacy_switch",present=True,ready=True,metadata={
            "engaged":True,"physical_disconnect":False,"microphone_powered":True
        })
        approved=client.post(base+"privacy-review",headers=headers,json={"consent":True})
        assert approved.status_code==200,approved.text
        assert approved.json()["privacy_check"]=="reported_software_gate_engaged"
        assert approved.json()["hardware_certified"] is False
        assert approved.json()["camera_opened"] is False
        assert not native.status()["hardware_certified"]
        vp3_os.clear_reported_hardware()
        diag.after_test(status="completed")
        with patch.object(native,"_LAST",{}),patch.object(diag,"_BOOT","other-process"):
            report=diag.diagnose()
            assert "prior_test_requires_repeat_after_restart" in {row["code"] for row in report["issues"]}
            issues=health_repair._native_tracky_issues()
            assert "tracky:native-test-interrupted" in {item["key"] for item in issues}

        # Exercise the owner HTTP test with a controlled fake capture. These
        # measurements are local process evidence, NOT physical certification.
        import types
        class SimCamera:
            def __init__(self,index):self.index=index;self.released=False
            def isOpened(self):return self.index==0
            def set(self,*args):return True
            def read(self):return True,object()
            def release(self):self.released=True
        class SimDetector:
            def __init__(self,path):self.path=path
            def empty(self):return False
            def detectMultiScale(self,*args,**kwargs):return [(0,0,70,70)]
        fakecv=types.ModuleType("cv2")
        fakecv.__spec__=__import__("importlib.machinery",fromlist=["ModuleSpec"]).ModuleSpec("cv2",loader=None)
        fakecv.data=SimpleNamespace(haarcascades=root+"/")
        fakecv.VideoCapture=SimCamera
        fakecv.CascadeClassifier=SimDetector
        fakecv.CAP_PROP_FRAME_WIDTH=3
        fakecv.CAP_PROP_FRAME_HEIGHT=4
        fakecv.COLOR_BGR2GRAY=6
        fakecv.cvtColor=lambda frame,flag:object()
        # The existing Tracky provider never observes while canonical
        # HomeServer/Cloud reconciliation remains pending. Seed the same
        # legitimate baseline used by native v1 acceptance, not a bypass.
        datasets={key:[] for key in federated_data.DATASETS}
        reconciled=federated_data.reconcile_snapshot({
            "version":"2.2","federation_version":"2.4",
            "authoritative_source":"vp3_cloud","snapshot_mode":"full",
            "covered_datasets":list(datasets),"revision":"tracky-native-diagnosis-fixture",
            "datasets":datasets
        },observed_source="homeserver",trigger_reason="tracky-native-diagnosis-fixture")
        assert reconciled["status"]=="completed"
        with patch.dict(sys.modules,{"cv2":fakecv}), \
             patch.object(integrity,"verify_file",return_value=True):
            completed=client.post(base+"test",headers=headers,json={
                "consent":True,"scope":native.SCOPE,"camera_index":0
            })
        assert completed.status_code==200,completed.text
        evidence=completed.json()["native"]["last_test"]
        assert evidence["status"]=="native_detector_completed"
        assert evidence["driver_worker_exited"] is True
        assert evidence["measurement"]["frame_captured"] is True
        assert evidence["measurement"]["camera_release_completed"] is True
        assert evidence["measurement"]["capture_and_inference_ms"]>=0
        assert evidence["hardware_certified"] is False
        assert diag._saved()["phase"]=="completed"
        assert "raw_frame" not in completed.text and "embedding" not in completed.text
        # A status read after an installed-device test cannot certify the
        # camera without owner review and the distinct physical acceptance.
        current=client.get(base+"diagnose").json()
        assert current["last_test_current_process"] is True
        assert current["hardware_certified"] is False

        # API projects recovery into same Agent-owned onboarding state.
        summary=client.get("/api/v1/control/onboarding/summary")
        assert summary.status_code==200,summary.text
        assert summary.json()["native_camera_diagnosis"]["hardware_certified"] is False
        assert summary.json()["native_camera_diagnosis"]["owner_review_required"] is True
        assert "opencv" not in str(diag._saved()).lower()
print("TRACKY_NATIVE_DIAGNOSIS_V1C: live model preflight, owner gate, restart recovery, governed repair and privacy PASS")
