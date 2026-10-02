"""Tracky 1F6: ordered Cloud receipt and local invalidation regression.

All HTTPS calls and visual enrollment are synthetic. Physical camera and
independent facial identity certification are NEVER implied by these tests.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix="tracky-ordered-owner-") as root:
    os.environ["HOMESERVER_DATA_DIR"]=root
    os.environ["VP3_OS_HARDWARE_ADAPTER"]="disabled"
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import (contacts, onboarding_visual as visual,
                              tracky_visual_contact_link as link,
                              tracky_physical_context as physical)
    from app.services.tasks import scheduler

    base="/api/v1/control/onboarding/visual/"
    route=base+"contact-link/"
    hdr={"X-Requested-With":"XMLHttpRequest"}
    scope={"consent":True,"scope":link.CLOUD_SCOPE,"enabled":True}
    stop_scope=dict(scope,enabled=False)

    class Reply:
        status_code=200
        def __init__(self,payload,mode):self.payload,self.mode=payload,mode
        def json(self):
            state=self.payload.get("health",{}).get("visual_owner_association")
            revision=self.payload.get("visual_owner_status_revision",0)
            if self.mode=="legacy":return {"ok":True}
            if self.mode=="stale":revision-=1
            if self.mode=="wrong_state":
                state="revoked" if state=="owner_attributed_unverified" else "owner_attributed_unverified"
            return {"ok":True,"visual_owner_status":{
                "accepted":self.mode!="rejected","revision":revision,
                "state":state or "",
            }}
    class HTTPS:
        mode="accepted"
        on_post=None
        payloads=[]
        def __init__(self,*args,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def post(self,url,*,json,headers):
            assert url.startswith("https://")
            assert headers["Authorization"]=="Bearer synthetic-session"
            HTTPS.payloads.append(json)
            if HTTPS.on_post:HTTPS.on_post()
            return Reply(json,HTTPS.mode)
    def sync():
        with patch.multiple(physical,
                 load_https_session=lambda:{
                     "endpoint":"https://cloud.invalid",
                     "session_token":"synthetic-session",
                 },
                 _cloud_sync_url=lambda value:"https://cloud.invalid/api/tracky-sync-v270.php"), \
             patch.object(physical.httpx,"Client",HTTPS):
            return physical.sync_cloud(force=True)

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",
            headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
        cid=contacts.create_contact({"display_name":"Synthetic local owner"})["id"]
        enrollment=client.post(base+"start",headers=hdr,
            json={"consent":True,"scope":visual.SCOPE}).json()
        report=client.post(base+"report",headers=hdr,json={
            "session":enrollment["session"],"participant_id":"self-owner-abcd12",
            "samples":3,
        })
        assert report.status_code==200,report.text
        linked=client.post(route+"associate",headers=hdr,json={
            "consent":True,"scope":link.SCOPE,
            "participant_id":"self-owner-abcd12","contact_id":cid,
        })
        assert linked.status_code==200,linked.text
        local_baseline=link._saved()["cloud_revision"]
        assert local_baseline>=1
        assert "visual_owner_status_revision" not in physical._cloud_payload()["payload"]
        assert client.post(route+"cloud-sharing",headers=hdr,json=scope).status_code==200
        snapshot=link.cloud_snapshot()
        assert snapshot["revision"]>local_baseline
        package=physical._cloud_payload()
        assert package["payload"]["visual_owner_status_revision"]==snapshot["revision"]
        assert package["visual_owner_revision"]==snapshot["revision"]
        assert package["visual_owner_generation"]==snapshot["generation"]
        assert "cloud_generation" not in str(package["payload"]).lower()

        for mode in ("legacy","stale","wrong_state","rejected"):
            HTTPS.mode=mode
            result=sync()
            assert result["ok"] is True
            assert not result["visual_owner_delivery"]["current_generation_acknowledged"]
            assert not link.status()["cloud_current_generation_acknowledged"]
            assert link.status()["cloud_delivery_status"]=="pending_authenticated_sync_and_cloud_consent"

        HTTPS.mode="accepted"
        accepted=sync()
        assert accepted["visual_owner_delivery"]["current_generation_acknowledged"]
        assert link.status()["cloud_current_generation_acknowledged"]
        assert link.status()["cloud_last_accepted_status"]=="owner_attributed_unverified"

        # User revokes during a still-in-flight old active upload; no legacy
        # or correctly revisioned receipt can acknowledge a changed generation.
        def revoke_during_upload():
            changed=client.post(route+"cloud-sharing",headers=hdr,json=stop_scope)
            assert changed.status_code==200,changed.text
        HTTPS.on_post=revoke_during_upload
        late=sync()
        assert late["ok"] is True
        assert late["visual_owner_delivery"]["current_generation_acknowledged"] is False
        HTTPS.on_post=None
        pending=link.cloud_snapshot()
        assert pending["state"]=="revoked"
        assert pending["revision"]>snapshot["revision"]
        assert not link.status()["cloud_current_generation_acknowledged"]
        HTTPS.mode="stale"
        assert not sync()["visual_owner_delivery"]["current_generation_acknowledged"]
        assert link.status()["cloud_revocation_pending"]
        HTTPS.mode="accepted"
        revoked=sync()
        assert revoked["visual_owner_delivery"]["current_generation_acknowledged"]
        assert link.status()["cloud_delivery_status"]=="revocation_delivered"
        assert "visual_owner_status_revision" not in physical._cloud_payload()["payload"]

        assert client.post(route+"cloud-sharing",headers=hdr,json=scope).status_code==200
        changed=link.cloud_snapshot()
        assert changed["revision"]>pending["revision"]
        assert not link.status()["cloud_current_generation_acknowledged"]
        assert link.mark_cloud_delivery("owner_attributed_unverified",
                generation=snapshot["generation"],revision=snapshot["revision"]) is False
        assert sync()["visual_owner_delivery"]["current_generation_acknowledged"]

        # External contact deletion is not a consent revocation API event.
        # Explicit packaging must reconcile to a NEW revocation revision.
        assert contacts.delete_contact(cid) is True
        stale=link.status()
        assert stale["state"]=="needs_review" and stale["cloud_revocation_pending"]
        auto=physical._cloud_payload()
        assert auto["payload"]["health"]["visual_owner_association"]=="revoked"
        assert auto["payload"]["visual_owner_status_revision"]>changed["revision"]
        assert link.cloud_snapshot()["revision"]==auto["payload"]["visual_owner_status_revision"]
        assert not link.status()["cloud_current_generation_acknowledged"]
        assert sync()["visual_owner_delivery"]["current_generation_acknowledged"]
        assert link.status()["cloud_delivery_status"]=="revocation_delivered"
        for item in HTTPS.payloads:
            body=str(item).lower()
            for secret in ("self-owner-abcd12","synthetic local owner",
                           "cloud_generation","receipt_signature",
                           "participant_ref","contact_ref","embeddings"):
                assert secret not in body,secret
print("TRACKY_VISUAL_ORDERED_SENDER_V1F6: Cloud echo, old ACK rejection, live revocation, deleted-contact privacy PASS")
