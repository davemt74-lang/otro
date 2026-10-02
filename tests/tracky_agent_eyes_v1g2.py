"""Section 1G2: real canonical Agent Eyes path with synthetic camera.
Owner-only API and camera provider tests; no actual hardware certification.
"""
from __future__ import annotations
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix="tracky-agent-eyes-v1g2-") as root:
    os.environ["HOMESERVER_DATA_DIR"]=root
    os.environ["VP3_OS_HARDWARE_ADAPTER"]="disabled"
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.database import db
    from app.services import (tracky_agent_eyes as eyes,
                              tracky_native_camera as native,
                              tracky_native_certification as cert,
                              tracky_native_managed_session as managed,
                              tracky_physical_context as physical,
                              onboarding_chat as chat, federated_data)
    from app.services.tasks import scheduler

    base="/api/v1/control/onboarding/visual/agent-eyes/"
    native_base="/api/v1/control/onboarding/visual/native/session/"
    headers={"X-Requested-With":"XMLHttpRequest"}
    payload={"consent":True,"scope":eyes.SCOPE,
             "camera_index":0,"sample_count":1,"interval_seconds":5}
    model={"installed":True,"model_present":True,
           "model_integrity_verified":True,"runtime_version":"synthetic"}
    with TestClient(app) as client:
        scheduler.stop()
        assert client.get(base+"status").status_code==401
        assert client.post(base+"start",json=payload,headers=headers).status_code==401
        auth=client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN})
        assert auth.status_code==200
        assert client.post(base+"start",json=payload).status_code==403
        for change,expected in (
            ({"consent":False},403),
            ({"scope":managed.SCOPE},403),
            ({"camera_index":True},422),
            ({"sample_count":13},422),
            ({"unexpected_field":True},422),
        ):
            assert client.post(base+"start",headers=headers,
                json=dict(payload,**change)).status_code==expected,change
        initial=client.get(base+"status").json()
        assert initial["active"] is False and initial["auto_resume"] is False
        assert initial["remotely_requestable"] is False
        assert initial["hardware_certified"] is False
        assert initial["identity_recognition"] is False
        assert client.post(base+"heartbeat",headers=headers).status_code==409
        # Agent Brain read never starts capture or extends any lease.
        with patch.object(chat,"device_status",return_value={}), \
             patch.object(chat,"provision_status",return_value={}):
            assert chat.summary()["agent_eyes"]["active"] is False

        with patch.object(cert,"status",return_value={
                 "owner_accepted_current_run":True,
                 "requires_new_owner_test_due_model_change":False,
             }),patch.object(native,"model_preflight",return_value=model):
            with patch.object(native,"_privacy",return_value=True):
                assert client.post(base+"start",json=payload,headers=headers).status_code==403

            # The same native provider cannot be borrowed as a second Agent Eyes
            # instance; all observation requests use canonical local Tracky.
            datasets={key:[] for key in federated_data.DATASETS}
            result=federated_data.reconcile_snapshot({
                "version":"2.2","federation_version":"2.4",
                "authoritative_source":"vp3_cloud","snapshot_mode":"full",
                "covered_datasets":list(datasets),"revision":"eyes-v1g2-synthetic",
                "datasets":datasets,
            },observed_source="homeserver",trigger_reason="eyes-v1g2-synthetic")
            assert result["status"]=="completed"
            entered=threading.Event()
            release=threading.Event()
            fake={"summary":"Synthetic one unverified possible face region.",
                  "capture_and_inference_ms":3,"inference_ms":2}
            def observation(_index,_cancel):
                entered.set()
                assert release.wait(timeout=5)
                return fake
            with patch.object(native,"_observe_exclusive",side_effect=observation):
                started=client.post(base+"start",json=payload,headers=headers)
                assert started.status_code==200,started.text
                assert entered.wait(timeout=3),"Canonical native provider not reached"
                exposure=physical.provider_exposure()
                assert exposure["registered"] is True
                assert exposure["local_owner_session_provider"] is True
                assert exposure["remote_requestable"] is False
                assert exposure["cloud_active_perception_advertised"] is False
                assert physical.public_capability()["provider"]["remote_requestable"] is False
                status=client.get(base+"status").json()
                assert status["active"] is True and status["max_observations"]==12
                assert status["owner_review_current"] is True
                assert status["continuous_unattended_tracking"] is False
                assert client.post(native_base+"start",json={
                    "consent":True,"scope":managed.SCOPE,"camera_index":0
                },headers=headers).status_code==409
                # Read-only Agent context cannot renew lease or spoof evidence.
                with patch.object(chat,"device_status",return_value={}), \
                     patch.object(chat,"provision_status",return_value={}):
                    assert chat.summary()["agent_eyes"]["active"] is True
                assert client.post(base+"heartbeat",headers=headers).status_code==200
                release.set()
                for _ in range(100):
                    if not managed.status()["active"]:
                        break
                    time.sleep(.04)
            done=client.get(base+"status").json()
            assert not done["active"] and done["phase"]=="completed",done
            assert done["completed_observations"]==1
            assert done["durable_evidence"]["phase"]=="completed"
            assert physical.provider_exposure()["registered"] is False
            with db() as conn:
                rows=conn.execute(
                    "SELECT requested_by,status FROM tracky_active_perception_requests "
                    "WHERE requested_by=?",
                    ("homeserver_owner_agent_eyes",)
                ).fetchall()
            assert len(rows)==1 and rows[0]["status"]=="completed",rows
            assert "raw_frame" not in str(done).lower()
            assert "embedding" not in str(done).lower()
            # Never stop somebody else's non-Eyes supervised camera session.
            with patch.object(managed,"status",return_value={
                 "owner_surface":"native_supervised","active":True
            }), patch.object(managed,"stop",side_effect=AssertionError("cross-stop")):
                assert eyes.stop()["active"] is False
            # No auto-restart even after previous successful observation.
            assert client.get(base+"status").json()["auto_resume"] is False
            assert client.post(base+"heartbeat",headers=headers).status_code==409
            assert client.post(base+"stop",headers=headers).status_code==200
            assert physical.provider_exposure()["remote_requestable"] is False

print("TRACKY_AGENT_EYES_V1G2: owner auth, canonical driver, lease, agent context, local-only, evidence PASS")
