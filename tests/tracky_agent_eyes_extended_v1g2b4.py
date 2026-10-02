"""Tracky 1G2B4 conditional extended owner-supervised canonical native worker."""
from __future__ import annotations
import os,sys,tempfile,time,threading
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix="eyes-extended-") as root:
    os.environ["HOMESERVER_DATA_DIR"]=root
    os.environ["VP3_OS_HARDWARE_ADAPTER"]="disabled"
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import tracky_agent_eyes as eyes
    from app.services import tracky_agent_eyes_acceptance as acceptance
    from app.services import tracky_native_managed_session as managed
    from app.services import tracky_native_session_evidence as evidence
    from app.services import tracky_native_camera as native
    from app.services import tracky_native_certification as cert
    from app.services import tracky_physical_context as physical
    from app.services import federated_data
    from app.services.tasks import scheduler
    base="/api/v1/control/onboarding/visual/agent-eyes/"
    headers={"X-Requested-With":"XMLHttpRequest"}
    model={"installed":True,"model_present":True,"model_integrity_verified":True,
           "model_sha256":"a"*64,"runtime_version":"synthetic"}
    reviewed={"owner_accepted_current_run":True,
              "requires_new_owner_test_due_model_change":False,
              "latest_owner_review":{"id":"synthetic-current-owner-review"}}
    payload={"consent":True,"scope":eyes.SCOPE,"camera_index":0,
             "sample_count":30,"interval_seconds":10,
             "max_session_seconds":300,"max_cpu_seconds":20}
    with TestClient(app) as client:
        scheduler.stop()
        assert client.post(base+"start",headers=headers,json=payload).status_code==401
        assert client.post("/__owner/session",
            headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
        assert client.post(base+"start",json=payload).status_code==403
        datasets={key:[] for key in federated_data.DATASETS}
        assert federated_data.reconcile_snapshot({
            "version":"2.2","federation_version":"2.4",
            "authoritative_source":"vp3_cloud","snapshot_mode":"full",
            "covered_datasets":list(datasets),"revision":"eyes-extended-v1g2b4",
            "datasets":datasets,
        },observed_source="homeserver",trigger_reason="eyes-extended-v1g2b4")["status"]=="completed"
        with patch.object(cert,"status",return_value=reviewed), \
             patch.object(native,"model_preflight",return_value=model), \
             patch.object(native,"_privacy",return_value=False):
            baseline={"owner_exercise_complete":False,"pending_steps":["owner_stop"]}
            accepted={"owner_exercise_complete":True,"pending_steps":[]}
            with patch.object(acceptance,"status",return_value=baseline):
                assert client.get(base+"status").json()["max_observations"]==12
                assert client.post(base+"start",headers=headers,json=payload).status_code==409
                assert physical.provider_exposure()["registered"] is False
                assert client.post(base+"start",headers=headers,json={
                    **payload,"sample_count":61}).status_code==422
                assert client.post(base+"start",headers=headers,json={
                    **payload,"max_session_seconds":120}).status_code==422
                assert client.post(base+"start",headers=headers,json={
                    **payload,"max_session_seconds":700}).status_code==422
            with patch.object(acceptance,"status",return_value=accepted):
                assert client.get(base+"status").json()["max_observations"]==60
                # An ordinary native supervised session cannot borrow the
                # acceptance report or request extended resources.
                try:
                    managed.start(consent=True,scope=managed.SCOPE,camera_index=0,
                        sample_count=30,max_session_seconds=300)
                    raise AssertionError("Generic native runtime gained extended rights")
                except managed.ManagedSessionError as err:
                    assert err.status_code==422
                entered=threading.Event()
                release=threading.Event()
                def observe(_idx,stopped):
                    entered.set()
                    release.wait(3)
                    if stopped.is_set():raise RuntimeError("stopped after owner revoke")
                    return {"summary":"synthetic non-identifying observation"}
                with patch.object(native,"_observe",side_effect=observe):
                    started=client.post(base+"start",headers=headers,json=payload)
                    assert started.status_code==200,started.text
                    assert entered.wait(3)
                    live=client.get(base+"status").json()
                    assert live["active"] is True
                    assert live["requested_observations"]==30
                    assert live["resource_budget"]["wall_limit_seconds"]==300
                    assert live["resource_budget"]["cpu_limit_seconds"]==20
                    assert live["max_seconds"]==600
                    assert live["remotely_requestable"] is False
                    assert physical.provider_exposure()["remote_requestable"] is False
                    assert client.post(base+"stop",headers=headers).status_code==200
                    release.set()
                    for _ in range(100):
                        if not managed.status()["active"]:break
                        time.sleep(.04)
                finished=evidence.latest()
                assert managed.status()["active"] is False
                assert finished["owner_surface"]=="agent_eyes",finished
                assert finished["requested_samples"]==30,finished
                assert finished["reason"]=="owner_stopped",finished
                assert eyes.status()["auto_resume"] is False
                assert eyes.status()["continuous_unattended_tracking"] is False
print("TRACKY_AGENT_EYES_EXTENDED_V1G2B4: conditional budgets, canonical shared runtime, no unattended or remote access PASS")
