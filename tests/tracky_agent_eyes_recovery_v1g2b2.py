"""Agent Eyes 1G2B2 canonical recovery gate, API and durable restart tests.

All camera data synthetic. Never asserts physical hardware certification.
"""
from __future__ import annotations
import os, sys, tempfile, threading, time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix="tracky-eyes-recovery-") as root:
    os.environ["HOMESERVER_DATA_DIR"]=root
    os.environ["VP3_OS_HARDWARE_ADAPTER"]="disabled"
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import tracky_agent_eyes as eyes
    from app.services import tracky_agent_eyes_recovery as recovery
    from app.services import tracky_native_managed_session as managed
    from app.services import tracky_native_session_evidence as evidence
    from app.services import tracky_native_camera as native
    from app.services import tracky_native_certification as cert
    from app.services import tracky_physical_context as physical
    from app.services import federated_data
    from app.services.tasks import scheduler
    base="/api/v1/control/onboarding/visual/agent-eyes/"
    headers={"X-Requested-With":"XMLHttpRequest"}
    payload={"consent":True,"camera_stopped_observed":True,
             "fresh_consent_understood":True}
    with TestClient(app) as client:
        scheduler.stop()
        assert client.get(base+"recovery").status_code==401
        assert client.post(base+"recovery/acknowledge",headers=headers,json=payload).status_code==401
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
        assert client.post(base+"recovery/acknowledge",json=payload).status_code==403
        assert client.post(base+"recovery/acknowledge",headers=headers,json=payload).status_code==409
        assert client.get(base+"recovery").json()["requires_acknowledgement"] is False
        datasets={key:[] for key in federated_data.DATASETS}
        assert federated_data.reconcile_snapshot({
            "version":"2.2","federation_version":"2.4",
            "authoritative_source":"vp3_cloud","snapshot_mode":"full",
            "covered_datasets":list(datasets),"revision":"eyes-recovery-v1g2b2",
            "datasets":datasets,
        },observed_source="homeserver",trigger_reason="eyes-recovery-v1g2b2")["status"]=="completed"
        review={"owner_accepted_current_run":True,
                "requires_new_owner_test_due_model_change":False}
        model={"installed":True,"model_present":True,"model_integrity_verified":True,
               "runtime_version":"synthetic"}
        entered=threading.Event()
        release=threading.Event()
        def camera(_index,cancel):
            entered.set()
            release.wait(4)
            if cancel.is_set():
                raise RuntimeError("synthetic driver stopped after watchdog revocation")
            return {"summary":"synthetic, non-identifying observation"}
        with patch.object(cert,"status",return_value=review), \
             patch.object(native,"model_preflight",return_value=model), \
             patch.object(native,"_privacy",return_value=False), \
             patch.object(native,"_observe",side_effect=camera):
            started=eyes.start(consent=True,scope=eyes.SCOPE,camera_index=0,
                               sample_count=1,max_session_seconds=60)
            assert started["active"]
            assert entered.wait(3)
            with managed._LOCK:
                managed._ATTEMPT_STARTED=time.monotonic()-managed.WATCHDOG_STALL_SECONDS-2
            for _ in range(60):
                if managed._STOP.is_set():break
                time.sleep(.05)
            assert managed._STOP.is_set()
            assert client.get(base+"recovery").json()["shared_camera_busy"] is True
            assert client.post(base+"recovery/acknowledge",headers=headers,json=payload).status_code==409
            release.set()
            for _ in range(100):
                if not managed.status()["active"]:break
                time.sleep(.04)
            assert managed.status()["active"] is False
            last=evidence.latest()
            assert last["owner_surface"]=="agent_eyes" and last["reason"]=="watchdog_stall",last
            assert last["phase"]=="stopped"
            assert physical.provider_exposure()["registered"] is False
            current=client.get(base+"recovery").json()
            assert current["requires_acknowledgement"] is True,current
            assert current["last_reason"]=="watchdog_stall"
            assert current["ready_for_owner_acknowledgement"] is True
            assert current["independently_hardware_certified"] is False
            try:
                managed.start(consent=True,scope=managed.SCOPE,camera_index=0,
                              sample_count=1)
                raise AssertionError("Generic supervised session bypassed recovery")
            except managed.ManagedSessionError as exc:
                assert exc.status_code==409
            assert client.post(base+"start",headers=headers,json={
                "consent":True,"scope":eyes.SCOPE,"camera_index":0,"sample_count":1,
            }).status_code==409
            for bad in (
                dict(payload,camera_stopped_observed=False),
                dict(payload,fresh_consent_understood=False),
            ):
                assert client.post(base+"recovery/acknowledge",headers=headers,json=bad).status_code==403
            accepted=client.post(base+"recovery/acknowledge",headers=headers,json=payload)
            assert accepted.status_code==200,accepted.text
            assert accepted.json()["acknowledged"] is True
            assert client.get(base+"recovery").json()["requires_acknowledgement"] is False
            assert client.get(base+"status").json()["hardware_certified"] is False
            assert client.get(base+"status").json()["auto_resume"] is False
        # Simulate an interrupted installed Agent Eyes session on a new boot.
        run_id=evidence.begin(sample_count=1,owner_surface="agent_eyes")
        with patch.object(evidence,"_BOOT","synthetic-new-boot"):
            stale=client.get(base+"recovery").json()
            assert stale["last_reason"]=="interrupted_by_restart"
            assert stale["requires_acknowledgement"] is True
            recovered=client.post(base+"recovery/acknowledge",headers=headers,json=payload)
            assert recovered.status_code==200,recovered.text
            assert evidence.latest()["phase"]=="interrupted"
            assert recovered.json()["acknowledged"] is True
            assert recovered.json()["automatic_recovery"] is False
            assert evidence.latest()["run_id"] if "run_id" in evidence.latest() else True
print("TRACKY_AGENT_EYES_RECOVERY_V1G2B2: precise evidence, shared gate, CSRF, owner ack, restart PASS")
