from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-local-apps-v040-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import local_apps  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    # Static catalog must be fully hash pinned. No artifact can activate without
    # a 64-character SHA-256 digest and a bounded expected size.
    assert {"piper-tts", "whisper-stt"}.issubset(local_apps.CATALOG)
    for package in local_apps.CATALOG.values():
        assert package["key"]
        assert package["version"]
        assert package["capabilities"]
        assert package["required_paths"]
        for artifact in package["artifacts"]:
            digest = artifact.get("sha256", "")
            assert len(digest) == 64 and all(ch in "0123456789abcdef" for ch in digest)
            assert int(artifact.get("size_bytes") or 0) > 0
            assert int(artifact.get("max_bytes") or 0) >= int(artifact["size_bytes"])
            local_apps._assert_trusted_url(artifact["url"])

    # URL and path checks fail closed, including suffix tricks and traversal.
    for bad_url in (
        "http://github.com/example/file.zip",
        "https://github.com.evil.example/file.zip",
        "https://example.com/file.zip",
        "file:///tmp/package.zip",
    ):
        try:
            local_apps._assert_trusted_url(bad_url)
            raise AssertionError(f"unsafe URL accepted: {bad_url}")
        except local_apps.LocalAppError:
            pass

    for bad_path in ("../escape", "/absolute", "C:/windows/system32", "a/../../escape"):
        try:
            local_apps._safe_rel_path(bad_path)
            raise AssertionError(f"unsafe path accepted: {bad_path}")
        except local_apps.LocalAppError:
            pass

    archive_path = Path(data_dir) / "bad.zip"
    with zipfile.ZipFile(archive_path, "w") as bundle:
        bundle.writestr("../escape.txt", "blocked")
    try:
        local_apps._extract_zip(archive_path, Path(data_dir) / "extract", 1024 * 1024)
        raise AssertionError("zip traversal was accepted")
    except local_apps.LocalAppError:
        pass
    assert not (Path(data_dir) / "escape.txt").exists()

    fixture_payload = b"homeserver-local-app-v1"
    fixture_hash = hashlib.sha256(fixture_payload).hexdigest()
    test_package = {
        "key": "test-local-app",
        "name": "Test Local App",
        "version": "1.0.0",
        "category": "Test",
        "description": "Synthetic package used only by the regression suite.",
        "runtime": "test",
        "source_label": "test fixture",
        "license": "test",
        "capabilities": ["test.local.capability"],
        "requirements": {},
        "artifacts": [{
            "name": "fixture",
            "kind": "file",
            "url": "https://github.com/homeserver/test/fixture.bin",
            "target": "runtime/fixture.bin",
            "sha256": fixture_hash,
            "size_bytes": len(fixture_payload),
            "max_bytes": 1024,
        }],
        "required_paths": ["runtime/fixture.bin"],
    }
    local_apps.CATALOG[test_package["key"]] = test_package

    original_download = local_apps._download

    def fake_download(artifact: dict, destination: Path) -> dict:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(fixture_payload)
        return {
            "name": artifact["name"],
            "sha256": hashlib.sha256(fixture_payload).hexdigest(),
            "size_bytes": len(fixture_payload),
        }

    local_apps._download = fake_download
    try:
        with TestClient(app) as client:
            scheduler.stop()

            # Owner control middleware protects the package manager; paired-app
            # bearer tokens never gain install/uninstall authority.
            assert client.get("/api/v1/control/local-apps").status_code in {401, 403}
            assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200

            listing = client.get("/api/v1/control/local-apps")
            assert listing.status_code == 200, listing.text
            payload = listing.json()
            assert payload["version"] == "v0.40"
            assert any(item["key"] == "piper-tts" for item in payload["packages"])
            assert any(item["key"] == "whisper-stt" for item in payload["packages"])
            assert "url" not in listing.text.lower()
            assert "sha256" in listing.text.lower()  # integrity label, never raw digest/source URL
            assert fixture_hash not in listing.text

            installed = client.post("/api/v1/control/local-apps/test-local-app/install")
            assert installed.status_code == 200, installed.text
            assert installed.json()["changed"] is True
            active_file = Path(data_dir) / "local-apps" / "test-local-app" / "runtime" / "fixture.bin"
            assert active_file.read_bytes() == fixture_payload
            manifest = json.loads((active_file.parents[1] / "homeserver-app.json").read_text(encoding="utf-8"))
            assert manifest["capabilities"] == ["test.local.capability"]

            current = client.post("/api/v1/control/local-apps/test-local-app/install")
            assert current.status_code == 200
            assert current.json()["changed"] is False
            assert current.json()["reason"] == "already_current"

            # A failed update must leave both the old files and the old installed
            # version intact. Only the last_error field is updated.
            test_package["version"] = "2.0.0"

            def failing_download(artifact: dict, destination: Path) -> dict:
                raise local_apps.LocalAppError("synthetic integrity failure", 502)

            local_apps._download = failing_download
            failed = client.post("/api/v1/control/local-apps/test-local-app/update")
            assert failed.status_code == 502, failed.text
            assert active_file.read_bytes() == fixture_payload
            state = client.get("/api/v1/control/local-apps").json()
            test_state = next(item for item in state["packages"] if item["key"] == "test-local-app")
            assert test_state["installed"]["installed_version"] == "1.0.0"
            assert test_state["installed"]["status"] == "installed"
            assert "synthetic integrity failure" in test_state["installed"]["last_error"]
            assert test_state["update_available"] is True

            # Installed capabilities are exposed to paired wrappers without any
            # private install path or artifact URL/hash disclosure.
            pair = client.post(
                "/api/v1/pairing/request",
                json={"app_key": "local-app-registry-test", "app_name": "Local App Registry Test", "permissions": ["agent.chat"]},
            ).json()
            assert client.post("/api/v1/pairing/approve", json={"code": pair["code"]}).status_code == 200
            registry = client.get(
                "/api/v1/capability-registry",
                headers={"Authorization": f"Bearer {pair['claim_token']}"},
            )
            assert registry.status_code == 200, registry.text
            registry_payload = registry.json()
            local_entry = next(item for item in registry_payload["local_apps"] if item["key"] == "test-local-app")
            assert local_entry["capabilities"] == ["test.local.capability"]
            assert registry_payload["counts"]["local_apps"] >= 1
            for forbidden in ("install_rel_path", "artifact_manifest_json", "github.com/homeserver/test", fixture_hash):
                assert forbidden not in registry.text

            local_apps._download = fake_download
            removed = client.delete("/api/v1/control/local-apps/test-local-app")
            assert removed.status_code == 200, removed.text
            assert removed.json()["changed"] is True
            assert not (Path(data_dir) / "local-apps" / "test-local-app").exists()

            activity = client.get("/api/v1/control/activity?limit=200").json()["items"]
            actions = {item["action"] for item in activity}
            assert "local_app.installed" in actions
            assert "local_app.update.failed" in actions
            assert "local_app.uninstalled" in actions
    finally:
        local_apps._download = original_download
        local_apps.CATALOG.pop("test-local-app", None)

print("HomeServer v0.40 Local Apps one-click installer regression passed")
