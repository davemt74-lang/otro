"""Default app install, actual packaged assets, owner-only transport and pairing fences."""
import hashlib
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix="vp3-feed-") as data:
    os.environ["HOMESERVER_DATA_DIR"] = data
    import httpx
    from app.database import initialize_database
    from app.services import homeserver_app_prebuilt as packages, homeserver_apps, social_feed as feed
    from app.services.https_bridge_session import save_https_session
    initialize_database()
    packages.ensure_defaults()
    item = homeserver_apps.get(feed.APP_KEY)
    assert item["installed_version"] == "1.5.0" and item["lifecycle_state"] == "running"
    assert item["app_class"] == "system" and item["protected_system_app"]
    package = packages._package(packages.CATALOG[feed.APP_KEY])
    assert package == packages._package(packages.CATALOG[feed.APP_KEY])
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert b"Create Post" in archive.read("index.html")
        assert b"vp3.social-feed.v1" in archive.read("assets/app.js")
        assert json.loads(archive.read("vp3-app.json"))["routes"]["public"] is False
    installed = item["installed_version"]
    packages.ensure_defaults()
    assert homeserver_apps.get(feed.APP_KEY)["installed_version"] == installed
    endpoint = "https://cloud.example/vp3/api/homeserver-https-poll-v1300.php"
    token = "feed-session-" + "a" * 40
    save_https_session(endpoint, token)
    requests = []
    swap = False
    def cloud(request):
        requests.append(json.loads(request.content))
        assert request.url.path == "/vp3/api/social-feed-v1.php"
        assert request.headers["Authorization"] == "Bearer " + token
        assert request.headers["X-HomeServer-Device"].startswith("hs-")
        if swap:
            save_https_session(endpoint, "new-feed-session-" + "b" * 40)
        return httpx.Response(200, json={"ok": True, "contract": feed.CONTRACT, "viewer_id": 7, "viewer": {"id": 7}})
    with httpx.Client(transport=httpx.MockTransport(cloud)) as client:
        result = feed.request({"action": "bootstrap", "owner_id": 99, "session_token": "injected", "csrf_token": "injected"}, client)
        assert result["viewer_id"] == 7 and requests == [{"action": "bootstrap"}]
        swap = True
        try:
            feed.request({"action": "bootstrap"}, client)
            raise AssertionError("A stale pairing response was returned")
        except feed.FeedError as exc:
            assert exc.status_code == 409
    for body in [{"action": "arbitrary"}, {"action": "post.create", "body": "x" * 65537}]:
        try:
            feed.request(body)
            raise AssertionError("Invalid request accepted")
        except feed.FeedError:
            pass
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    client = TestClient(app)
    assert client.post("/api/v1/control/homeserver-apps/feed/request", json={"action": "bootstrap"}).status_code == 401
    assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200
    preview = client.get("/api/v1/control/homeserver-apps/vp3.feed/preview/")
    assert preview.status_code == 200 and "Create Post" in preview.text
    assert client.get("/api/v1/control/homeserver-apps/vp3.feed/preview/assets/app.js").status_code == 200
    assert client.post("/api/v1/control/homeserver-apps/feed/request", content=b"x" * 65537).status_code == 413
    assert client.post("/api/v1/control/homeserver-apps/feed/request", json=[]).status_code == 422
    homeserver_apps.transition(feed.APP_KEY, "stopped")
    packages.ensure_defaults()
    assert homeserver_apps.get(feed.APP_KEY)["lifecycle_state"] == "stopped", "Startup resumed a deliberately stopped app"
    print("PASS default Feed install/upgrade, deterministic package and assets, owner gateway, filtered transport, pairing changes, size limits and preserved lifecycle choices")
