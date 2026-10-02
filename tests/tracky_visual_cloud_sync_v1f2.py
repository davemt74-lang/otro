"""Tracky 1F2: local owner opt-in and scalar-only canonical HTTPS site health.

Synthetic browser report; no physical camera use, biometric recognition or
real Cloud delivery. Cloud receiver is independently tested in software #511.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(prefix="tracky-visual-cloud-v1f2-") as folder:
    os.environ["HOMESERVER_DATA_DIR"]=folder
    os.environ["VP3_OS_HARDWARE_ADAPTER"]="disabled"
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import contacts,onboarding_visual as visual
    from app.services import tracky_visual_contact_link as link
    from app.services import tracky_physical_context as physical
    from app.services.tasks import scheduler

    base="/api/v1/control/onboarding/visual/"
    route=base+"contact-link/"
    headers={"X-Requested-With":"XMLHttpRequest"}
    enable={"consent":True,"scope":link.CLOUD_SCOPE,"enabled":True}
    disable={"consent":True,"scope":link.CLOUD_SCOPE,"enabled":False}
    with TestClient(app) as client:
        scheduler.stop()
        assert client.post(route+"cloud-sharing",json=enable,headers=headers).status_code==401
        assert client.post(route+"cloud-sync",json={"consent":True},headers=headers).status_code==401
        assert client.post("/__owner/session",
            headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
        assert client.post(route+"cloud-sharing",json=enable).status_code==403
        assert client.post(route+"cloud-sharing",json=enable,headers=headers).status_code==409
        assert client.post(route+"cloud-sharing",json=dict(enable,consent=False),
                           headers=headers).status_code==403
        assert client.post(route+"cloud-sharing",json=dict(enable,scope="other"),
                           headers=headers).status_code==403
        assert client.post(route+"cloud-sharing",json=dict(enable,enabled=1),
                           headers=headers).status_code==422
        assert client.post(route+"cloud-sharing",json=dict(enable,embeddings=[[1,2,3]]),
                           headers=headers).status_code==422
        assert client.post(route+"cloud-sync",json={"consent":False},headers=headers).status_code==403
        assert client.post(route+"cloud-sync",json={"consent":True},headers=headers).status_code==409
        assert link.cloud_projection() is None
        # A status poll must not start an HTTPS request or share a default.
        with patch.object(physical,"sync_cloud",side_effect=AssertionError("Unsolicited Cloud sync")):
            assert client.get(route+"status").status_code==200

        contact_id=contacts.create_contact({"display_name":"Local self","notes":"secret notes"})["id"]
        enroll=client.post(base+"start",json={"consent":True,"scope":visual.SCOPE},headers=headers)
        assert enroll.status_code==200,enroll.text
        token=enroll.json()["session"]
        receipt=client.post(base+"report",json={
            "session":token,"participant_id":"self-owner-xyz123","samples":3
        },headers=headers)
        assert receipt.status_code==200,receipt.text
        linked=client.post(route+"associate",json={
            "consent":True,"scope":link.SCOPE,"participant_id":"self-owner-xyz123",
            "contact_id":contact_id
        },headers=headers)
        assert linked.status_code==200,linked.text
        assert link.cloud_projection() is None
        # Existing site payload must not contain local signed receipt.
        before=physical._cloud_payload()
        assert "visual_owner_association" not in before["payload"]["health"]

        opted=client.post(route+"cloud-sharing",json=enable,headers=headers)
        assert opted.status_code==200,opted.text
        assert opted.json()["cloud_sharing_opted_in"] is True
        assert opted.json()["identity_verified"] is False
        assert link.cloud_projection()=="owner_attributed_unverified"
        package=physical._cloud_payload()
        assert package["visual_owner_projection"]=="owner_attributed_unverified"
        assert package["payload"]["health"]["visual_owner_association"]=="owner_attributed_unverified"
        assert "owner_attributed_unverified" in str(package["payload"]["health"])
        health=package["payload"]["health"]
        for private in ("contact_id","local_participant_id","participant_ref",
                        "contact_ref","receipt_id","receipt_signature","embeddings",
                        "photo","portrait","biometric","local self","secret notes"):
            assert private not in str(health).lower(),private
        # Concurrent explicit revocation wins over an in-flight HTTPS ACK.
        assert client.post(route+"cloud-sharing",json=disable,headers=headers).status_code==200
        assert link.cloud_projection()=="revoked"
        assert link.mark_cloud_delivery("owner_attributed_unverified", generation=package["visual_owner_generation"],
            revision=package["visual_owner_revision"]) is False
        assert link.cloud_projection()=="revoked"
        assert physical._cloud_payload()["payload"]["health"]["visual_owner_association"]=="revoked"
        revoke_snapshot=link.cloud_snapshot()
        assert revoke_snapshot["generation"] != package["visual_owner_generation"]
        assert link.mark_cloud_delivery("revoked") is False  # Unfenced acknowledgements forbidden.
        assert link.mark_cloud_delivery("revoked", generation=revoke_snapshot["generation"],
            revision=revoke_snapshot["revision"]) is True
        assert link.cloud_projection() is None
        assert link.status()["cloud_sharing_opted_in"] is False

        # A brand-new opt-in is required for every reactivation.
        assert client.post(route+"cloud-sharing",json=enable,headers=headers).status_code==200
        assert link.cloud_projection()=="owner_attributed_unverified"
        # A revoked-and-reauthorized link can regain the SAME public scalar.
        # An old ACK must not confirm the new consent generation.
        assert link.mark_cloud_delivery("owner_attributed_unverified",
            generation=package["visual_owner_generation"], revision=package["visual_owner_revision"]) is False
        newer=link.cloud_snapshot()
        assert newer["generation"] != package["visual_owner_generation"]
        assert newer["state"]=="owner_attributed_unverified"
        assert link.mark_cloud_delivery("owner_attributed_unverified") is False
        assert link.mark_cloud_delivery("owner_attributed_unverified",
            generation=newer["generation"],revision=newer["revision"]) is True
        assert link.status()["cloud_delivery_status"]=="authenticated_site_accepted_cloud_account_consent_separate"
        assert "visual_owner_generation" not in physical._cloud_payload()["payload"]
        with patch.object(physical,"sync_cloud",return_value={"ok":True}):
            sent=client.post(route+"cloud-sync",json={"consent":True},headers=headers)
            assert sent.status_code==200
            assert sent.json()["face_recognition_verified"] is False
        assert client.post(base+"cancel",headers=headers).status_code==200
        assert link.cloud_projection()=="revoked"
        assert client.get(route+"status").json()["cloud_revocation_pending"] is True
        assert client.post(route+"cloud-sharing",json=enable,headers=headers).status_code==409
        assert client.post(route+"revoke",json={"consent":True},headers=headers).status_code==200
        assert link.cloud_projection()=="revoked"
        final_snapshot=link.cloud_snapshot()
        assert link.mark_cloud_delivery("revoked",generation=final_snapshot["generation"],
            revision=final_snapshot["revision"]) is True
        assert link.cloud_projection() is None
        assert not client.get(route+"receipt").json()["available"]

print("TRACKY_VISUAL_CLOUD_V1F2: separate local consent, semantic-only transport, stale ACK denial and tombstones PASS")
