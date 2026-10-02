"""Tracky 1F1: governed, revocable local owner-self/contact association.

Only synthetic browser-reported enrollment. Neither this test nor the service
certifies facial identity, active tracking, or physical camera readiness.
"""
from __future__ import annotations
import hmac
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(prefix="tracky-visual-link-v1f1-") as folder:
    os.environ["HOMESERVER_DATA_DIR"]=folder
    os.environ["VP3_OS_HARDWARE_ADAPTER"]="disabled"
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import onboarding_visual as visual
    from app.services import contacts, tracky_visual_contact_link as link, remote_identity
    from app.services.tasks import scheduler
    base="/api/v1/control/onboarding/visual/"
    api=base+"contact-link/"
    headers={"X-Requested-With":"XMLHttpRequest"}
    with TestClient(app) as client:
        scheduler.stop()
        contact_id=contacts.create_contact({"display_name":"My existing local record",
             "email":"private@example.invalid", "notes":"private local only"})["id"]
        assert client.get(api+"status").status_code==401
        assert client.get(api+"contacts").status_code==401
        assert client.get(api+"receipt").status_code==401
        assert client.post(api+"associate",headers=headers,json={}).status_code==401
        assert client.post("/__owner/session",
            headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
        catalog=client.get(api+"contacts").json()
        assert catalog["items"]==[{"id":contact_id,"display_name":"My existing local record"}]
        assert "private@example.invalid" not in str(catalog)
        assert client.get(api+"status").json()["state"]=="not_linked"
        body={"consent":True,"scope":link.SCOPE,
              "participant_id":"self-owner-a1b2","contact_id":contact_id}
        assert client.post(api+"associate",json=body).status_code==403
        assert client.post(api+"associate",json=body,headers=headers).status_code==409
        assert client.post(api+"associate",json=dict(body,consent=False),headers=headers).status_code==403
        assert client.post(api+"associate",json=dict(body,scope="all-persons"),headers=headers).status_code==403
        assert client.post(api+"associate",json=dict(body,embeddings=[[1.0,0.0]]),headers=headers).status_code==422
        assert client.post(api+"associate",json=dict(body,contact_id="1"),headers=headers).status_code==422
        assert client.post(api+"revoke",json={"consent":False},headers=headers).status_code==403
        enrollment=client.post(base+"start",json={"consent":True,"scope":visual.SCOPE},headers=headers)
        assert enrollment.status_code==200,enrollment.text
        token=enrollment.json()["session"]
        assert client.post(base+"report",json={"session":token,"participant_id":"self-owner-a1b2","samples":3},
                           headers=headers).status_code==200
        assert client.post(api+"associate",json=dict(body,participant_id="wrong-self-id"),
                           headers=headers).status_code==409
        assert client.post(api+"associate",json=dict(body,contact_id=999999),
                           headers=headers).status_code==404
        associated=client.post(api+"associate",json=body,headers=headers)
        assert associated.status_code==200,associated.text
        linked=associated.json()
        assert linked["active"] and linked["state"]=="owner_attributed_unverified"
        assert linked["identity_verified"] is False and linked["cloud_sync_enabled"] is False
        assert linked["contact_creation_automatic"] is False
        assert linked["contact"]=={"id":contact_id,"display_name":"My existing local record"}
        assert "private@example.invalid" not in associated.text
        assert "embedding" not in associated.text.lower()
        assert client.get(base+"status").json()["contact_association"]["active"]
        assert client.post(api+"associate",json=body,headers=headers).status_code==409
        receipt=client.get(api+"receipt").json()
        assert receipt["available"] is True and receipt["cloud_delivery"]=="not_enabled"
        payload=receipt["receipt"]
        assert payload["face_recognition_verified"] is False
        assert payload["native_hardware_certified"] is False
        assert payload["cloud_biometrics"] is False
        assert payload["cloud_delivery"]=="not_enabled"
        assert body["participant_id"] not in str(receipt)
        assert str(contact_id) not in payload["contact_ref"]
        key=remote_identity.load_or_create_remote_identity()["device_secret"].encode()
        expected=hmac.new(key,json.dumps(payload,sort_keys=True,separators=(",",":")).encode(),
                          hashlib.sha256).hexdigest()
        assert hmac.compare_digest(receipt["signature"],expected)
        assert client.post(api+"revoke",headers=headers,json={"consent":True}).status_code==200
        assert not client.get(api+"status").json()["active"]
        assert client.get(api+"receipt").json()["available"] is False
        assert client.post(api+"associate",headers=headers,json=body).status_code==200
        cancelled=client.post(base+"cancel",headers=headers)
        assert cancelled.status_code==200
        assert client.get(api+"status").json()["state"]=="revoked"
        assert client.get(api+"receipt").json()["available"] is False
        # Local browser deletion precedes this API call in the real UI. This
        # API alone cannot independently prove that IndexedDB was deleted.
        assert client.post(base+"delete",headers=headers,json={"participant_id":"self-owner-a1b2"}).status_code==200
        assert client.get(api+"status").json()["active"] is False
    assert link.status()["identity_verified"] is False
    from app.database import db
    with db() as conn:
        rows=conn.execute("SELECT action FROM activity_log WHERE resource_type='tracky_visual_link' ORDER BY id").fetchall()
    assert [r["action"] for r in rows]==[
        "tracky.visual.association.approved","tracky.visual.association.revoked",
        "tracky.visual.association.approved","tracky.visual.association.revoked"
    ]
print("TRACKY_VISUAL_LINK_V1F1: authenticated explicit association, redacted signed receipt, revocation, no biometric sync PASS")
