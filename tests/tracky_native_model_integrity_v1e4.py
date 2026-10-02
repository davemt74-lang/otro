"""Tracky 1E4: approved offline model integrity and tamper/recovery contracts.

A local file hash is not a signature and these tests never open a camera.
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix="tracky-model-integrity-") as root:
    os.environ["HOMESERVER_DATA_DIR"]=root
    os.environ["VP3_OS_HARDWARE_ADAPTER"]="disabled"
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import tracky_native_model_integrity as integrity
    from app.services import tracky_native_camera as native
    from app.services import tracky_native_diagnosis as diag
    from app.services import tracky_native_managed_session as managed
    from app.services import tracky_native_certification as cert
    from app.services.tasks import scheduler

    model=Path(root)/integrity.MODEL_FILE
    content=b"<?xml version='1.0'?>"+b"X"*120000
    model.write_bytes(content)
    expected=hashlib.sha256(content).hexdigest()
    synthetic=SimpleNamespace(__version__="4.10.synthetic",data=SimpleNamespace(haarcascades=root+"/"))
    with patch.object(integrity,"EXPECTED_HAAR_SHA256",expected):
        assert integrity.verify_file(model)
        verified=integrity.inspect_module(synthetic)
        assert verified["model_integrity_verified"] is True
        assert verified["model_load_source"]=="local_filesystem_only"
        assert verified["network_fetch"] is False
        model.write_bytes(content+b"tampered")
        assert not integrity.verify_file(model)
        tampered=integrity.inspect_module(synthetic)
        assert tampered["model_present"] is True
        assert tampered["model_integrity_verified"] is False
        assert tampered["integrity_state"]=="model_digest_mismatch"
        model.unlink()
        assert integrity.inspect_module(synthetic)["integrity_state"]=="model_missing"
        model.write_bytes(b"short")
        assert integrity.verify_file(model) is False

    # The real pinned runtime in CI must bundle the exact reviewed model.
    # A CI runner fixture may never claim native physical certification.
    import cv2
    actual=integrity.inspect_module(cv2)
    assert actual["model_present"] is True
    assert actual["model_integrity_verified"] is True, actual
    assert actual["hardware_certified"] is False

    with TestClient(app) as client:
        scheduler.stop()
        base="/api/v1/control/onboarding/visual/native/"
        hdr={"X-Requested-With":"XMLHttpRequest"}
        assert client.get(base+"diagnose").status_code==401
        assert client.post("/__owner/session",
            headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
        broken={"installed":True,"model_present":True,"model_integrity_verified":False,
                "runtime_version":"synthetic"}
        with patch.object(native,"model_preflight",return_value=broken), \
             patch.object(diag,"_model_preflight",return_value={
               "installed":True,"model_present":True,"runtime_loaded":True,"reason":"ready"
             }):
            status=client.get(base+"diagnose").json()
            assert status["model"]["model_integrity_verified"] is False
            assert "face_model_integrity_mismatch" in {item["code"] for item in status["issues"]}
            result=client.post(base+"test",headers=hdr,json={
                "consent":True,"scope":native.SCOPE,"camera_index":0
            })
            assert result.status_code==503, result.text
            assert native.status()["running"] is False
            assert native.capture_busy() is False
            with patch.object(cert,"status",return_value={"owner_accepted_current_run":True}):
                scope={"consent":True,"scope":managed.SCOPE,"camera_index":0,
                       "sample_count":1,"interval_seconds":5}
                blocked=client.post(base+"session/start",headers=hdr,json=scope)
                assert blocked.status_code==503,blocked.text
                assert managed.status()["active"] is False
print("TRACKY_NATIVE_MODEL_INTEGRITY_V1E4: reviewed digest, tamper, offline and fail-closed PASS")
