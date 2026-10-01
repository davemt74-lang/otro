"""First-run welcome: owner security, canonical install path and agent-first handoff."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
html = (ROOT / "ui/welcome.html").read_text(encoding="utf-8")
js = (ROOT / "ui/welcome.js").read_text(encoding="utf-8")
css = (ROOT / "ui/welcome.css").read_text(encoding="utf-8")
launcher = (ROOT / "desktop/launcher.py").read_text(encoding="utf-8")
system = (ROOT / "app/system_api.py").read_text(encoding="utf-8")
gateway = (ROOT / "app/runtime.py").read_text(encoding="utf-8")

assert '"/welcome"' in gateway and '"\/welcome"' not in gateway
assert '"/welcome"' in system and 'no-store' in system
assert '_authorized_path("/welcome")' in launcher
assert 'self.open_welcome).start()' in launcher
assert 'id="startSetup"' in html and 'id="enterAgent"' in html
assert 'prefers-reduced-motion:reduce' in css
assert "addEventListener('click', prepare)" in js
assert "addEventListener('click', openAgent)" in js
assert "REQUIRED_PACKAGES = ['whisper-stt', 'piper-tts']" in js
assert "control/local-apps/" in js and "control/system/setup" in js
assert "window.location.assign('/#chat')" in js
assert "Microphone, camera, and proactive speech remain your choice." in html
assert "Don't enable" not in js  # No misleading silent entitlement state.

with tempfile.TemporaryDirectory(prefix="homeserver-welcome-") as data:
    os.environ["HOMESERVER_DATA_DIR"] = data
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services.tasks import scheduler

    with TestClient(app) as client:
        scheduler.stop()
        assert client.get("/welcome").status_code == 401
        assert client.get("/api/v1/control/system").status_code == 401
        assert client.post("/api/v1/control/local-apps/whisper-stt/install").status_code == 401
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200
        welcome = client.get("/welcome")
        assert welcome.status_code == 200
        assert "no-store" in welcome.headers.get("cache-control", "")
        assert "id=\"welcome-title\"" in welcome.text
        state = client.get("/api/v1/control/system").json()
        assert state["setup"]["complete"] is False
        catalog = client.get("/api/v1/control/local-apps").json()
        assert {"whisper-stt", "piper-tts"}.issubset({x["key"] for x in catalog["packages"]})
        assert client.get("/assets/welcome.css").status_code == 200
        assert client.get("/assets/welcome.js").status_code == 200

print("HomeServer first-run v2: owner gate, existing API and welcome assets PASS")
