"""Tracky 1G2B3 local supervised exercise: synthetic current-model owner reports only."""
from __future__ import annotations
import os, sys, tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix="tracky-eyes-exercise-") as root:
    os.environ["HOMESERVER_DATA_DIR"]=root
    os.environ["VP3_OS_HARDWARE_ADAPTER"]="disabled"
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.database import db
    from app.services import tracky_agent_eyes_acceptance as exercise
    from app.services import tracky_native_session_evidence as evidence
    from app.services import tracky_native_camera as native
    from app.services import tracky_native_certification as cert
    from app.services.tasks import scheduler
    base="/api/v1/control/onboarding/visual/agent-eyes/installed-exercise"
    headers={"X-Requested-With":"XMLHttpRequest"}
    digest="a"*64
    review="review-synthetic-installed"
    approved={"owner_accepted_current_run":True,
              "latest_owner_review":{"id":review},
              "requires_new_owner_test_due_model_change":False}
    model={"installed":True,"model_present":True,"model_integrity_verified":True,
           "model_sha256":digest,"runtime_version":"synthetic"}
    payload={"step":"owner_stop","consent":True,"inspected_camera_release":True}
    with TestClient(app) as client:
        scheduler.stop()
        assert client.get(base).status_code==401
        assert client.post(base+"/record",headers=headers,json=payload).status_code==401
        assert client.post("/__owner/session",
            headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
        assert client.post(base+"/record",json=payload).status_code==403
        with patch.object(cert,"status",return_value=approved), \
             patch.object(native,"model_preflight",return_value=model):
            empty=client.get(base).json()
            assert empty["pending_steps"]==["owner_stop","privacy_revocation","presence_lease"]
            assert empty["owner_exercise_complete"] is False
            assert empty["extended_mode_enabled"] is False
            assert empty["independent_hardware_certified"] is False
            assert client.post(base+"/record",headers=headers,json=payload).status_code==409
            assert client.post(base+"/record",headers=headers,
                json=dict(payload,step="unknown")).status_code==422
            assert client.post(base+"/record",headers=headers,
                json=dict(payload,inspected_camera_release=False)).status_code==403
            reasons=[
                ("owner_stop","owner_stopped"),
                ("privacy_revocation","privacy_engaged"),
                ("presence_lease","owner_presence_expired"),
            ]
            for step,reason in reasons:
                run=evidence.begin(sample_count=1,owner_surface="agent_eyes",
                                   owner_review_id=review,model_sha256=digest)
                evidence.finish(run_id=run,phase="stopped",reason=reason,completed_samples=0)
                state=client.get(base).json()
                assert state["current_session_step"]==step,state
                assert state["ready_to_record"] is True
                assert client.post(base+"/record",headers=headers,
                    json=dict(payload,step="owner_stop" if step!="owner_stop" else "privacy_revocation")
                ).status_code==409
                with patch.object(native,"capture_busy",return_value=True):
                    assert client.get(base).json()["ready_to_record"] is False
                    assert client.post(base+"/record",headers=headers,
                        json=dict(payload,step=step)).status_code==409
                recorded=client.post(base+"/record",headers=headers,
                    json=dict(payload,step=step))
                assert recorded.status_code==200,recorded.text
                assert step in recorded.json()["completed_steps"]
                repeat=client.post(base+"/record",headers=headers,
                    json=dict(payload,step=step))
                assert repeat.status_code==200
            done=client.get(base).json()
            assert done["owner_exercise_complete"] is True
            assert done["physical_camera_release_verified"] is False
            assert done["unattended_perception_allowed"] is False
            with db() as connection:
                rows=connection.execute("SELECT evidence_json FROM runtime_certification_runs "
                    "WHERE test_key=?",(exercise.TEST_KEY,)).fetchall()
            assert len(rows)==3,rows
            assert all("raw_frame" not in r["evidence_json"] for r in rows)
            with patch.object(native,"model_preflight",return_value={**model,"model_sha256":"b"*64}):
                mismatch=client.get(base).json()
                assert mismatch["owner_exercise_complete"] is False
                assert mismatch["ready_to_record"] is False
            with patch.object(cert,"status",return_value={**approved,
                    "latest_owner_review":{"id":"different-review"}}):
                assert client.get(base).json()["owner_exercise_complete"] is False
                assert client.get(base).json()["ready_to_record"] is False
            with patch.object(exercise,"_BOOT","new-process"),patch.object(evidence,"_BOOT","new-process"):
                assert client.get(base).json()["owner_exercise_complete"] is False
                assert client.get(base).json()["ready_to_record"] is False
print("TRACKY_AGENT_EYES_INSTALLED_EXERCISE_V1G2B3: current runtime/review/digest binding, idle lock, CSRF, evidence PASS")
