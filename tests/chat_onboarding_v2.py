"""Chat-canvas onboarding: owner gate, persistent provisioning, secure Cloud code and restart safety."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

html = (ROOT / "ui/index.html").read_text(encoding="utf-8")
js = (ROOT / "ui/chat-onboarding.js").read_text(encoding="utf-8")
welcome = (ROOT / "ui/welcome.js").read_text(encoding="utf-8")
assert 'id="chatOnboardingCanvas"' in html
assert "onboardStartCloud" in html and "onboardCloudLink" in html
assert "device/start" in js and "device/poll" in js and "voice/start" in js
assert "#hs_code=" in js and "encodeURIComponent(code.code)" in js
assert "Your code will be prefilled" in html
assert "window.location.assign('/#chat')" in welcome
assert "control/local-apps/" not in welcome  # No onboarding outside the chat canvas.
assert "https://vp3.me/settings-homeserver.php" in html

with tempfile.TemporaryDirectory(prefix="hs-chat-onboard-") as path:
    os.environ["HOMESERVER_DATA_DIR"] = path
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import onboarding_chat as onboard
    from app.services.tasks import scheduler

    with TestClient(app) as client:
        scheduler.stop()
        assert client.get("/api/v1/control/onboarding/summary").status_code == 401
        assert client.post("/api/v1/control/onboarding/device/start",
                           headers={"X-Requested-With": "XMLHttpRequest"}).status_code == 401
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200
        status = client.get("/api/v1/control/onboarding/summary")
        assert status.status_code == 200
        assert status.json()["setup"]["complete"] is False
        assert "verifier" not in str(status.json())
        assert client.get("/assets/chat-onboarding.js").status_code == 200
        assert client.post("/api/v1/control/onboarding/device/start").status_code == 403
        assert client.post("/api/v1/control/onboarding/voice/start").status_code == 403

        headers={"X-Requested-With":"XMLHttpRequest"}
        registered = []
        def fake_cloud(action, state):
            registered.append(action)
            if action=="start": return {"ok":True,"state":"pending"}
            if action=="poll": return {"ok":True,"state":"claimed","pairing_token":"VP3-TEST_TOKEN"}
            if action=="complete": return {"ok":True,"state":"complete"}
            raise AssertionError(action)
        connected = [False]
        def cloud_status():
            return {"cloud":{"state":"connected" if connected[0] else "not_connected",
                             "paired":connected[0],"connected":connected[0]}}
        def pair_token(token):
            assert token=="VP3-TEST_TOKEN"
            connected[0]=True
            return {"accepted":True}
        with patch.object(onboard,"_cloud",side_effect=fake_cloud), \
             patch.object(onboard.remote_bridge,"cloud_connection_status",side_effect=cloud_status), \
             patch.object(onboard.cloud_pairing,"redeem_vp3_pairing_token",side_effect=pair_token):
            issued=client.post("/api/v1/control/onboarding/device/start",headers=headers)
            assert issued.status_code==200,issued.text
            code=issued.json()["pairing"]["code"]
            assert len(code.replace("-",""))==12
            assert issued.json()["cloud_url"]=="https://vp3.me/settings-homeserver.php"
            assert "verifier" not in issued.text
            assert onboard._read_device()["verifier"] not in issued.text
            repeat=client.post("/api/v1/control/onboarding/device/start",headers=headers)
            assert repeat.json()["pairing"]["code"]==code and registered==["start"]
            completed=client.post("/api/v1/control/onboarding/device/poll",headers=headers)
            assert completed.status_code==200,completed.text
            assert completed.json()["cloud"]["paired"] is True
            assert completed.json()["pairing"]["code"] is None
            assert onboard._read_device() is None
            assert registered==["start","poll","complete"]

        installed=set()
        def packages():
            return {"packages":[
                {"key":key,"name":key,"supported":True,"download_bytes":2048,
                 "installed":{"status":"installed","healthy":True} if key in installed else None}
                for key in onboard.VOICE_PACKAGES]}
        def install(key):
            installed.add(key)
            return {"changed":True}
        with patch.object(onboard.local_apps,"catalog",side_effect=packages), \
             patch.object(onboard.local_apps,"install",side_effect=install):
            provision=client.post("/api/v1/control/onboarding/voice/start",headers=headers)
            assert provision.status_code==200,provision.text
            if onboard._WORKER: onboard._WORKER.join(10)
            state=client.get("/api/v1/control/onboarding/summary").json()["provision"]
            assert state["phase"]=="complete" and state["all_ready"]
            # A restart continues previously approved work, never starts unapproved work.
            installed.clear()
            onboard._WORKER=None
            onboard._save_progress({"phase":"running","approved":True})
            onboard.resume_approved()
            if onboard._WORKER: onboard._WORKER.join(10)
            assert onboard.provision_status()["all_ready"]
            assert set(installed)==set(onboard.VOICE_PACKAGES)

print("HomeServer scripted chat onboarding: owner gate, code privacy, reuse, resume PASS")
