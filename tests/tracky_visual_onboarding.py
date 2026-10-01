"""Integrated Tracky owner visual onboarding: auth, consent, recovery and honest verification."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
js=(ROOT/"ui/tracky/owner-visual.js").read_text(encoding="utf-8")
html=(ROOT/"ui/index.html").read_text(encoding="utf-8")
assert 'id="onboardVisual"' in html
assert 'id="onboardVisualConsent"' in html
assert 'type="module" src="/assets/tracky/owner-visual.js"' in html
assert "getUserMedia" in js and "window.confirm" in js
assert "saveParticipant" in js and "deleteParticipant" in js
assert "enrollment_verified" in (ROOT/"app/services/onboarding_visual.py").read_text(encoding="utf-8")

with tempfile.TemporaryDirectory(prefix="hs-tracky-visual-") as temp:
    os.environ["HOMESERVER_DATA_DIR"]=temp
    os.environ["VP3_OS_HARDWARE_ADAPTER"]="disabled"
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import onboarding_visual
    from app.services.tasks import scheduler

    url="/api/v1/control/onboarding/visual"
    consent={"consent":True,"scope":onboarding_visual.SCOPE}
    header={"X-Requested-With":"XMLHttpRequest"}
    with TestClient(app) as client:
        scheduler.stop()
        assert client.get(url+"/status").status_code==401
        assert client.post(url+"/start",headers=header,json=consent).status_code==401
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
        state=client.get(url+"/status").json()
        assert state["phase"]=="not_started"
        assert state["browser_enrollment_assets"] is True
        assert state["enrollment_verified"] is False
        assert client.get("/assets/tracky/owner-visual.js").status_code==200
        assert client.get("/assets/tracky/src/participant-store.js").status_code==200
        assert client.post(url+"/start",json=consent).status_code==403
        assert client.post(url+"/start",headers=header,json={"consent":False,"scope":onboarding_visual.SCOPE}).status_code==403
        assert client.post(url+"/start",headers=header,json={"consent":True,"scope":"another-person"}).status_code==403
        with patch.object(onboarding_visual,"_privacy_engaged",return_value=True):
            assert client.post(url+"/start",headers=header,json=consent).status_code==403
        issued=client.post(url+"/start",headers=header,json=consent)
        assert issued.status_code==200,issued.text
        token=issued.json()["session"]
        assert "session_hash" not in issued.text
        assert token not in client.get(url+"/status").text
        assert client.post(url+"/report",headers=header,json={
            "session":token,"participant_id":"local-owner-aa11","samples":3,
            "embeddings":[[0.9,0.1]],"portrait":"data:image/jpeg;base64,FORBIDDEN"
        }).status_code==422  # Reject unknown sensitive payload fields, never ignore them.
        assert client.post(url+"/report",headers=header,json={
            "session":"A"*48,"participant_id":"local-owner-aa11","samples":3
        }).status_code==403
        with patch.object(onboarding_visual,"_privacy_engaged",return_value=True):
            assert client.post(url+"/report",headers=header,json={
                "session":token,"participant_id":"local-owner-aa11","samples":3
            }).status_code==403
        accepted=client.post(url+"/report",headers=header,json={
            "session":token,"participant_id":"local-owner-aa11","samples":3
        })
        assert accepted.status_code==200,accepted.text
        receipt=accepted.json()
        assert receipt["phase"]=="browser_reported"
        assert receipt["sample_count"]==3
        assert receipt["evidence"]=="browser_reported_unverified"
        assert receipt["enrollment_verified"] is False
        assert receipt["provider_certified"] is False
        assert receipt["cloud_biometrics"] is False
        assert receipt["tracking_enabled"] is False
        assert receipt["contact_creation_enabled"] is False
        assert "session_hash" not in accepted.text
        assert client.post(url+"/report",headers=header,json={
            "session":token,"participant_id":"local-owner-aa11","samples":3
        }).status_code==403
        assert client.post(url+"/start",headers=header,json=consent).status_code==409
        assert client.post(url+"/delete",headers=header,json={"participant_id":"wrong-owner"}).status_code==404
        assert client.post(url+"/delete",headers=header,json={"participant_id":"local-owner-aa11"}).json()["phase"]=="deleted"
        again=client.post(url+"/start",headers=header,json=consent)
        assert again.status_code==200
        expired=onboarding_visual._read()
        expired["expires_at"]="2000-01-01T00:00:00+00:00"
        onboarding_visual._write(expired)
        assert client.get(url+"/status").json()["phase"]=="interrupted"
        assert client.post(url+"/report",headers=header,json={
            "session":again.json()["session"],"participant_id":"local-owner-aa11","samples":3
        }).status_code==403
        assert client.post(url+"/cancel",headers=header).json()["phase"]=="cancelled"
print("HomeServer visual Chat: owner/session/privacy gates, no biometrics on API, interrupted recovery PASS")
