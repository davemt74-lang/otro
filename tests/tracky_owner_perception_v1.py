"""Integrated HomeServer Tracky one-shot provider; no simulated hardware certification."""
from __future__ import annotations
import os,sys,tempfile,threading,time
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(prefix="tracky-owner-perception-v1-") as data:
    os.environ["HOMESERVER_DATA_DIR"]=data
    os.environ["VP3_OS_HARDWARE_ADAPTER"]="disabled"
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import tracky_owner_perception as eyes,tracky_physical_context as tracky
    from app.services import federated_data,live_certification,tracky_owner_perception
    from app.services.tasks import scheduler

    with TestClient(app) as client:
        scheduler.stop()
        base="/api/v1/control/onboarding/visual/eyes/"
        hdr={"X-Requested-With":"XMLHttpRequest"}
        consent={"consent":True,"scope":eyes.SCOPE,"model_ready":True,"camera_ready":True}
        assert client.get(base+"status").status_code==401
        assert client.post(base+"open",headers=hdr,json=consent).status_code==401
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
        baseline=client.get(base+"status").json()
        assert not baseline["active"] and not baseline["provider_certified"]
        assert not baseline["server_native_camera_certified"]
        assert client.post(base+"open",json=consent).status_code==403
        assert client.post(base+"open",headers=hdr,json=dict(consent,consent=False)).status_code==403
        assert client.post(base+"open",headers=hdr,json=dict(consent,scope="other-person")).status_code==403
        assert client.post(base+"open",headers=hdr,json=dict(consent,model_ready=False)).status_code==422
        with patch.object(eyes,"_privacy",return_value=True):
            assert client.post(base+"open",headers=hdr,json=consent).status_code==403

        opened=client.post(base+"open",headers=hdr,json=consent)
        assert opened.status_code==200,opened.text
        token=opened.json()["session"]
        assert len(token)>=32 and token not in client.get(base+"status").text
        assert tracky._provider_snapshot()[2]=="homeserver-owner-browser-one-shot"
        caps=tracky._provider_snapshot()[1]
        assert caps["requires_camera"] is False and caps["server_native_camera"] is False
        assert client.post(base+"open",headers=hdr,json=consent).status_code==409
        assert client.post(base+"heartbeat",headers=hdr,json={"session":"A"*43}).status_code==403
        assert client.post(base+"heartbeat",headers=hdr,json={"session":token}).status_code==200
        visual=client.get("/api/v1/control/onboarding/visual/status").json()
        assert not visual["trusted_perception_provider_registered"]
        assert not visual["enrollment_verified"]
        assert visual["owner_browser_perception"]["active"]
        status,evidence=live_certification._run_eyes()
        assert status=="unsupported" and evidence["reason"]=="owner_browser_requires_chat_test"

        # A Cloud or ordinary paired app cannot trigger capture in the open owner browser.
        datasets={name:[] for name in federated_data.DATASETS}
        result=federated_data.reconcile_snapshot({
            "version":"2.2","federation_version":"2.4",
            "authoritative_source":"vp3_cloud","snapshot_mode":"full",
            "covered_datasets":list(datasets),"revision":"eyes-owner-test-v1",
            "datasets":datasets,
        },observed_source="homeserver",trigger_reason="eyes-owner-test-v1")
        assert result["status"]=="completed"
        denied=tracky.active_perception(
            "refresh_current_view",request_id="eyes-external-denied",
            correlation_id="eyes-external-correlation",
            requested_by="vp3_cloud",
        )
        assert denied["request"]["status"]=="failed"
        assert not client.post(base+"next",headers=hdr,json={"session":token}).json()["pending"]

        result_holder={}
        def owner_test():
            result_holder["response"]=client.post(base+"test",headers=hdr,json={"session":token})
        thread=threading.Thread(target=owner_test,daemon=True)
        thread.start()
        pending={}
        for _ in range(55):
            pending=client.post(base+"next",headers=hdr,json={"session":token}).json()
            if pending.get("pending"):break
            time.sleep(.06)
        assert pending.get("pending"),"One approved canonical request must reach the owner browser"
        assert pending["request_type"]=="refresh_current_view"
        assert client.post(base+"submit",headers=hdr,json={
            "session":token,"request_id":pending["request_id"],"face_count":1,
            "confidence":.87,"model_ready":True,"camera_ready":True,
            "embedding":[.1,.2],"frame":"forbidden"
        }).status_code==422
        assert client.post(base+"submit",headers=hdr,json={
            "session":token,"request_id":pending["request_id"],"face_count":True,
            "confidence":.87,"model_ready":True,"camera_ready":True
        }).status_code==422
        assert client.post(base+"submit",headers=hdr,json={
            "session":token,"request_id":"wrong-"+pending["request_id"],
            "face_count":1,"confidence":.87,"model_ready":True,"camera_ready":True
        }).status_code==409
        accepted=client.post(base+"submit",headers=hdr,json={
            "session":token,"request_id":pending["request_id"],
            "face_count":1,"confidence":.87,"model_ready":True,"camera_ready":True
        })
        assert accepted.status_code==200,accepted.text
        assert accepted.json()=={"accepted":True,"biometrics_transmitted":False}
        thread.join(4)
        assert not thread.is_alive()
        answer=result_holder["response"]
        assert answer.status_code==200,answer.text
        assert answer.json()["request"]["status"]=="completed",answer.text
        assert not answer.json()["readiness"]["server_native_camera_certified"]
        assert not answer.json()["readiness"]["provider_certified"]
        assert answer.json()["readiness"]["last_observation"]["owner_review_required"]
        assert "embedding" not in answer.text and "frame" not in answer.text

        # Cancellation/heartbeat expiry detach only this provider, never a foreign one.
        assert client.post(base+"close",headers=hdr,json={"session":token}).status_code==200
        assert tracky._provider_snapshot()[0] is None

        # Pending work is also revoked immediately when hardware privacy
        # engages; a result cannot be accepted after that point.
        token=client.post(base+"open",headers=hdr,json=consent).json()["session"]
        revoked={}
        def revoked_test():
            revoked["response"]=client.post(base+"test",headers=hdr,json={"session":token})
        worker=threading.Thread(target=revoked_test,daemon=True);worker.start()
        for _ in range(55):
            check=client.post(base+"next",headers=hdr,json={"session":token}).json()
            if check.get("pending"):break
            time.sleep(.06)
        assert check.get("pending"),"The governed owner-only request must become pending"
        with patch.object(eyes,"_privacy",return_value=True):
            assert client.get(base+"status").json()["active"] is False
        worker.join(4)
        assert not worker.is_alive()
        assert revoked["response"].json()["request"]["status"]=="failed"
        assert tracky._provider_snapshot()[0] is None
        assert client.post(base+"submit",headers=hdr,json={
            "session":token,"request_id":check["request_id"],"face_count":1,
            "confidence":.9,"model_ready":True,"camera_ready":True
        }).status_code==403

        opened=client.post(base+"open",headers=hdr,json=consent)
        token=opened.json()["session"]
        with eyes._COND:
            eyes._SESSION["last_seen"]-=eyes.HEARTBEAT_MAX_SECONDS+1
        assert client.get(base+"status").json()["active"] is False
        assert tracky._provider_snapshot()[0] is None
        assert client.post(base+"heartbeat",headers=hdr,json={"session":token}).status_code==403

        # The physical privacy switch can revoke even a previously valid session.
        token=client.post(base+"open",headers=hdr,json=consent).json()["session"]
        with patch.object(eyes,"_privacy",return_value=True):
            assert client.get(base+"status").json()["active"] is False
        assert tracky._provider_snapshot()[0] is None
        assert client.post(base+"test",headers=hdr,json={"session":token}).status_code==403

        # Never steal an independently registered site perception engine.
        def another_provider(_request):return {"summary":"independent hardware"}
        tracky.register_provider(another_provider,name="existing-Tracky-provider")
        assert client.post(base+"open",headers=hdr,json=consent).status_code==409
        assert tracky._provider_snapshot()[0] is another_provider
        assert tracky.unregister_provider(expected=eyes._provider) is False
        assert tracky._provider_snapshot()[0] is another_provider
        assert tracky.unregister_provider(expected=another_provider) is True

print("TRACKY_OWNER_PERCEPTION_V1: owner gate, governed provider, private detection, live test, expiry and privacy PASS")
