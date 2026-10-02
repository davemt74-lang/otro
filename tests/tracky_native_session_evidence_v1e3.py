"""Tracky 1E3: durable non-biometric operational session evidence.

All hardware and detection data are synthetic. A green result does NOT certify
physical camera isolation or autonomous perception.
"""
from __future__ import annotations
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix="tracky-supervised-evidence-") as temp:
    os.environ["HOMESERVER_DATA_DIR"]=temp
    os.environ["VP3_OS_HARDWARE_ADAPTER"]="disabled"
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import tracky_native_session_evidence as evidence
    from app.services import tracky_native_managed_session as managed
    from app.services import tracky_native_camera as native
    from app.services import tracky_native_certification as cert
    from app.services import federated_data
    from app.services.tasks import scheduler
    from app.database import initialize_database
    initialize_database()  # CLI test uses a fresh temporary SQLite path.

    assert evidence.latest()["phase"]=="never_started"
    first=evidence.begin(sample_count=3)
    assert evidence.latest()["phase"]=="running"
    assert evidence.latest()["automatic_resume"] is False
    try:
        evidence.begin(sample_count=1)
        raise AssertionError("prior session overwritten")
    except RuntimeError:
        pass
    with patch.object(evidence,"_BOOT","reboot-synthetic"):
        prior=evidence.latest()
        assert prior["phase"]=="interrupted"
        assert prior["recover_before_new_session"] is True
        assert not evidence.history()
        ended=evidence.recover_prior()
        assert ended["phase"]=="interrupted"
        assert len(evidence.history())==1
        assert evidence.recover_prior()["phase"]=="interrupted"
        assert len(evidence.history())==1
        assert ended["hardware_certified"] is False
    second=evidence.begin(sample_count=1)
    assert second!=first
    ended=evidence.finish(run_id=second,phase="completed",reason="completed",completed_samples=1)
    assert ended["phase"]=="completed" and ended["completed_samples"]==1
    assert evidence.finish(run_id=second,phase="completed",reason="completed",
                           completed_samples=1)["phase"]=="completed"
    assert len(evidence.history())==2
    for row in evidence.history():
        assert row["hardware_certified"] is False
        assert row["evidence"]["physical_hardware_certified"] is False
        assert row["evidence"]["raw_media_retained"] is False
        assert row["evidence"]["identity_recognition_certified"] is False

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",
            headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
        datasets={key:[] for key in federated_data.DATASETS}
        recon=federated_data.reconcile_snapshot({
            "version":"2.2","federation_version":"2.4",
            "authoritative_source":"vp3_cloud","snapshot_mode":"full",
            "covered_datasets":list(datasets),"revision":"session-evidence-v1e3-fixture",
            "datasets":datasets,
        },observed_source="homeserver",trigger_reason="evidence-v1e3-fixture")
        assert recon["status"]=="completed"
        base="/api/v1/control/onboarding/visual/native/session/"
        scope={"consent":True,"scope":managed.SCOPE,"camera_index":0,
               "sample_count":1,"interval_seconds":5}
        fake={"summary":"One possible unverified face region.","confidence":0.0}
        model={"installed":True,"model_present":True,"model_integrity_verified":True,"runtime_version":"synthetic"}
        with patch.object(cert,"status",return_value={"owner_accepted_current_run":True}), \
             patch.object(native,"model_preflight",return_value=model), \
             patch.object(native,"_observe_exclusive",return_value=fake):
            started=client.post(base+"start",headers={"X-Requested-With":"XMLHttpRequest"},
                                json=scope)
            assert started.status_code==200,started.text
            for _ in range(120):
                if not managed.status()["active"]:
                    break
                time.sleep(0.05)
            status=managed.status()
            assert not status["active"] and status["completed_samples"]==1,status
            assert status["durable_evidence"]["phase"]=="completed"
            assert len(evidence.history())==3
            assert evidence.history()[0]["evidence"]["completed_samples"]==1
            assert evidence.history()[0]["evidence"]["phase"]=="completed"
            assert status["durable_evidence"]["hardware_certified"] is False
    report=str(evidence.history()).lower()
    for forbidden in ("embedding","raw_frame","camera_uri","face_identity"):
        assert forbidden not in report
print("TRACKY_SUPERVISED_EVIDENCE_V1E3: atomic records, passive restart, no duplicate, API integration PASS")
