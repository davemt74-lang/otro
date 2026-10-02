"""Tracky 1F4: truthful semantic Cloud delivery, fail-closed consent changes.

The paired site transport is replaced by a fake HTTPS client. No hardware,
facial recognition or actual Cloud credentials are used.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix="tracky-visual-delivery-v1f4-") as data:
    os.environ["HOMESERVER_DATA_DIR"]=data
    os.environ["VP3_OS_HARDWARE_ADAPTER"]="disabled"
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import (contacts, onboarding_visual as visual,
                              tracky_physical_context as physical,
                              tracky_visual_contact_link as link)
    from app.services.tasks import scheduler

    base="/api/v1/control/onboarding/visual/"
    route=base+"contact-link/"
    hdr={"X-Requested-With":"XMLHttpRequest"}
    share={"consent":True,"scope":link.CLOUD_SCOPE,"enabled":True}
    unshare=dict(share,enabled=False)

    class Reply:
        status_code=200
        def __init__(self,payload):self.payload=payload
        def json(self):
            state=self.payload.get("health",{}).get("visual_owner_association")
            revision=self.payload.get("visual_owner_status_revision",0)
            return {"ok":True,"visual_owner_status":{
                "accepted":state is not None,"revision":revision if state else 0,
                "state":state or "",
            }}
    class HTTPS:
        on_post=None
        payloads=[]
        def __init__(self,*args,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def post(self,url,*,json,headers):
            assert url.startswith("https://")
            assert headers["Authorization"]=="Bearer synthetic-session"
            assert json["protocol"]==physical.PHYSICAL_CONTEXT_PROTOCOL
            HTTPS.payloads.append(json)
            if HTTPS.on_post:
                HTTPS.on_post()
            return Reply(json)

    def fake_transport():
        return patch.multiple(physical,
            load_https_session=lambda:{"endpoint":"https://cloud.invalid",
                                        "session_token":"synthetic-session"},
            _cloud_sync_url=lambda endpoint:"https://cloud.invalid/api/tracky-sync-v270.php",
            ), patch.object(physical.httpx,"Client",HTTPS)

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",
            headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
        local=contacts.create_contact({"display_name":"Local owner only"})["id"]
        start=client.post(base+"start",headers=hdr,
             json={"consent":True,"scope":visual.SCOPE}).json()
        report=client.post(base+"report",headers=hdr,json={
            "session":start["session"],"participant_id":"owner-self-abc123",
            "samples":3,
        })
        assert report.status_code==200,report.text
        linked=client.post(route+"associate",headers=hdr,json={
            "consent":True,"scope":link.SCOPE,
            "participant_id":"owner-self-abc123","contact_id":local,
        })
        assert linked.status_code==200,linked.text
        assert client.post(route+"cloud-sharing",headers=hdr,
                           json=share).status_code==200
        pending=client.get(route+"status").json()
        assert pending["cloud_delivery_status"]=="pending_authenticated_sync_and_cloud_consent"
        assert pending["cloud_current_generation_acknowledged"] is False

        # The owner revokes while the previous active request is in flight.
        def revoke_during_upload():
            response=client.post(route+"cloud-sharing",headers=hdr,json=unshare)
            assert response.status_code==200,response.text
        HTTPS.on_post=revoke_during_upload
        (session, transport)=fake_transport()
        with session, transport:
            old=physical.sync_cloud(force=True)
        assert old["ok"]
        assert old["visual_owner_delivery"]=={
            "attempted":True,"status_sent":"owner_attributed_unverified",
            "current_generation_acknowledged":False,
        }
        assert HTTPS.payloads[-1]["health"]["visual_owner_association"]=="owner_attributed_unverified"
        assert "visual_owner_generation" not in str(HTTPS.payloads[-1])
        assert link.status()["cloud_delivery_status"]=="revocation_pending_sync"
        assert link.status()["cloud_current_generation_acknowledged"] is False

        # Offline retries cannot clear a revocation or claim delivery.
        with patch.object(physical,"sync_cloud",
             side_effect=physical.TrackyPhysicalError("offline",503)):
            failed=client.post(route+"cloud-sync",headers=hdr,json={"consent":True})
            assert failed.status_code==503
            assert link.status()["cloud_revocation_pending"] is True
        HTTPS.on_post=None
        (session, transport)=fake_transport()
        with session, transport:
            revoked=physical.sync_cloud(force=True)
        assert revoked["visual_owner_delivery"]["current_generation_acknowledged"] is True
        assert revoked["visual_owner_delivery"]["status_sent"]=="revoked"
        final=link.status()
        assert final["cloud_delivery_status"]=="revocation_delivered"
        assert final["cloud_current_generation_acknowledged"] is True
        assert final["cloud_last_accepted_status"]=="revoked"
        assert not final["cloud_revocation_pending"]

        # A later ordinary sync no longer exports a visual association.
        package=physical._cloud_payload()
        assert "visual_owner_association" not in package["payload"]["health"]
        assert "cloud_generation" not in str(package["payload"])

        # New sharing consent must NOT inherit the old revocation ACK.
        assert client.post(route+"cloud-sharing",headers=hdr,json=share).status_code==200
        state=link.status()
        assert state["cloud_current_generation_acknowledged"] is False
        assert state["cloud_last_accepted_status"]==""
        with patch.object(physical,"sync_cloud",return_value={
            "ok":True,"visual_owner_delivery":{
                "attempted":True,"status_sent":"owner_attributed_unverified",
                "current_generation_acknowledged":False,
            },
        }):
            fake=client.post(route+"cloud-sync",headers=hdr,json={"consent":True})
            assert fake.status_code==200
            assert fake.json()["site_sync_accepted"] is True
            assert fake.json()["visual_status_current_generation_acknowledged"] is False
        (session, transport)=fake_transport()
        with session, transport:
            real=client.post(route+"cloud-sync",headers=hdr,json={"consent":True})
        assert real.status_code==200,real.text
        assert real.json()["site_sync_accepted"] is True
        assert real.json()["visual_status_sent"]=="owner_attributed_unverified"
        assert real.json()["visual_status_current_generation_acknowledged"] is True
        assert real.json()["association"]["cloud_current_generation_acknowledged"] is True
        assert real.json()["face_recognition_verified"] is False
        assert real.json()["cloud_biometric_storage"] is False
        for payload in HTTPS.payloads:
            serialized=str(payload)
            for forbidden in ("owner-self-abc123","local owner only",
                              "cloud_generation","receipt_signature",
                              "participant_ref","contact_ref","embeddings"):
                assert forbidden not in serialized.lower(),forbidden

print("TRACKY_VISUAL_DELIVERY_V1F4: same-request revocation, ACK truth, offline retry, fresh consent and zero biometric sync PASS")
