from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-governed-files-v038-") as temp_root:
    root = Path(temp_root)
    data_dir = root / "data"
    travel_dir = root / "travel-private-root"
    private_dir = root / "personal-private-root"
    travel_dir.mkdir(parents=True)
    private_dir.mkdir(parents=True)

    travel_text = "TRAVEL_FILE_VISIBLE_3801 " + ("Arizona route notes. " * 800)
    private_text = "PRIVATE_FILE_HIDDEN_3802 private financial notes"
    (travel_dir / "trip-plan.txt").write_text(travel_text, encoding="utf-8")
    (private_dir / "private-notes.txt").write_text(private_text, encoding="utf-8")
    os.environ["HOMESERVER_DATA_DIR"] = str(data_dir)

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import agent_tools, app_scopes, local_files, local_files_remote, remote_bridge, tools  # noqa: E402
    from app.services.knowledge_sources import scheduler  # noqa: E402
    from app.services.tasks import scheduler as task_scheduler  # noqa: E402
    from app.database import db  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()
        task_scheduler.stop()
        assert client.post(
            "/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}
        ).status_code == 200

        capabilities = client.get("/api/v1/capabilities")
        assert capabilities.status_code == 200, capabilities.text
        advertised = capabilities.json()
        assert "files.read" in advertised["permissions"]
        assert advertised["files"] == {
            "version": "v0.38",
            "permission": "files.read",
            "read_only": True,
            "indexed_text_only": True,
            "collection_scoped": True,
            "operations": ["files.list", "files.read"],
        }
        assert "files.governed.v1" in advertised["features"]
        assert "files.indexed_text.v1" in advertised["features"]

        for key, name in (("travel", "Travel"), ("private", "Private")):
            created = client.post(
                "/api/v1/control/knowledge/collections",
                json={"collection_key": key, "name": name},
            )
            assert created.status_code == 200, created.text

        source_ids: dict[str, int] = {}
        for key, folder in (("travel", travel_dir), ("private", private_dir)):
            created = client.post(
                "/api/v1/control/knowledge/sources",
                json={"path": str(folder), "label": f"{key.title()} files", "scan_interval_seconds": 60},
            )
            assert created.status_code == 200, created.text
            source_id = int(created.json()["source"]["id"])
            source_ids[key] = source_id
            assigned = client.put(
                f"/api/v1/control/knowledge/sources/{source_id}/collection",
                json={"collection_key": key},
            )
            assert assigned.status_code == 200, assigned.text

        pair = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "governed-files-v038",
                "app_name": "Governed Files",
                "permissions": ["files.read", "tools.execute"],
            },
        ).json()
        assert set(pair["permissions"]) == {"files.read", "tools.execute"}
        assert client.post(
            "/api/v1/pairing/approve", json={"code": pair["code"]}
        ).status_code == 200
        headers = {"Authorization": f"Bearer {pair['claim_token']}"}
        app_row = next(
            item for item in client.get("/api/v1/control/apps").json()["apps"]
            if item["app_key"] == "governed-files-v038"
        )
        app_id = int(app_row["id"])
        restricted = client.put(
            f"/api/v1/control/apps/{app_id}/knowledge-collections",
            json={"collection_keys": ["travel"]},
        )
        assert restricted.status_code == 200, restricted.text

        # Collection scope controls both discovery and direct reads. The API
        # never returns the approved source root or an absolute filesystem path.
        listing = client.get("/api/v1/files", headers=headers)
        assert listing.status_code == 200, listing.text
        listing_json = listing.json()
        assert listing_json["count"] == 1
        visible = listing_json["items"][0]
        assert visible["relative_path"] == "trip-plan.txt"
        assert visible["collection_key"] == "travel"
        assert visible["source_label"] == "Travel files"
        assert visible["ref"].startswith("hsf-")
        assert listing_json["privacy"]["absolute_paths_exposed"] is False
        encoded_listing = json.dumps(listing_json, ensure_ascii=False)
        assert str(travel_dir.resolve()) not in encoded_listing
        assert str(private_dir.resolve()) not in encoded_listing
        assert "path" not in visible or "relative_path" in visible

        hidden_search = client.get(
            "/api/v1/files", params={"q": "private-notes"}, headers=headers
        )
        assert hidden_search.status_code == 200
        assert hidden_search.json()["count"] == 0

        first_read = client.get(
            f"/api/v1/files/{visible['ref']}",
            params={"max_chars": 400},
            headers=headers,
        )
        assert first_read.status_code == 200, first_read.text
        first_json = first_read.json()
        assert "TRAVEL_FILE_VISIBLE_3801" in first_json["text"]
        assert first_json["returned_chars"] <= 400
        assert first_json["truncated"] is True
        assert first_json["next_offset"] == first_json["returned_chars"]
        assert str(travel_dir.resolve()) not in first_read.text

        second_read = client.get(
            f"/api/v1/files/{visible['ref']}",
            params={"offset": first_json["next_offset"], "max_chars": 400},
            headers=headers,
        )
        assert second_read.status_code == 200
        assert second_read.json()["offset"] == first_json["next_offset"]

        with db() as connection:
            private_row = connection.execute(
                "SELECT id, content_hash FROM knowledge_source_files WHERE source_id=? LIMIT 1",
                (source_ids["private"],),
            ).fetchone()
        assert private_row is not None
        private_ref = local_files._file_ref(int(private_row["id"]), private_row["content_hash"])
        denied_private = client.get(f"/api/v1/files/{private_ref}", headers=headers)
        assert denied_private.status_code == 404
        assert "PRIVATE_FILE_HIDDEN_3802" not in denied_private.text

        stale_ref = visible["ref"][:-1] + ("0" if visible["ref"][-1] != "0" else "1")
        stale = client.get(f"/api/v1/files/{stale_ref}", headers=headers)
        assert stale.status_code == 409

        invalid_path_like_ref = client.get("/api/v1/files/..%2F..%2Fsecret.txt", headers=headers)
        assert invalid_path_like_ref.status_code in {404, 422}

        # files.read is independent of knowledge.search, but existing knowledge
        # kind restrictions remain an intersection with the file boundary.
        scoped = app_scopes.save_scope(
            app_id,
            {
                "cloud_allowed": True,
                "memory_key_prefixes": [],
                "knowledge_kinds": ["note"],
                "tool_names": [],
                "plugin_keys": [],
            },
        )
        assert scoped["knowledge_kinds"] == ["note"]
        kind_hidden = client.get("/api/v1/files", headers=headers)
        assert kind_hidden.status_code == 200
        assert kind_hidden.json()["count"] == 0

        app_scopes.save_scope(app_id, dict(app_scopes.DEFAULT_SCOPE))
        listing = client.get("/api/v1/files", headers=headers).json()
        visible_ref = listing["items"][0]["ref"]

        # Capability Registry uses the same scoped count and only advertises
        # relay file operations when files.read is granted.
        registry = client.get("/api/v1/capability-registry", headers=headers)
        assert registry.status_code == 200, registry.text
        registry_json = registry.json()
        assert registry_json["files"]["readable"] is True
        assert registry_json["files"]["visible_files"] == 1
        assert registry_json["files"]["capability_version"] == "v0.38"
        assert "files.list" in registry_json["operations"]
        assert "files.read" in registry_json["operations"]
        assert str(travel_dir.resolve()) not in registry.text

        # Tool execution returns the same safe projection and audits metadata,
        # never the opaque ref or file contents themselves.
        granted = {"files.read", "tools.execute"}
        listed_tool = tools.execute_tool(
            "app:governed-files-v038", "files.list", {"limit": 10}, granted, owner=False
        )
        assert listed_tool["result"]["count"] == 1
        read_tool = tools.execute_tool(
            "app:governed-files-v038",
            "files.read",
            {"ref": visible_ref, "max_chars": 120},
            granted,
            owner=False,
        )
        assert "TRAVEL_FILE_VISIBLE_3801" in read_tool["result"]["text"]
        runs = tools.list_tool_runs(10)
        file_read_run = next(item for item in runs if item["tool_key"] == "files.read")
        assert file_read_run["arguments"]["ref_length"] == len(visible_ref)
        assert "ref" not in file_read_run["arguments"]
        assert "text" not in file_read_run["result"]

        # Model-facing discovery also respects tool-name scope, not merely the
        # final execution check. With only files.list allowed, files.read is not
        # offered to the model.
        app_scopes.save_scope(
            app_id,
            {
                "cloud_allowed": True,
                "memory_key_prefixes": [],
                "knowledge_kinds": [],
                "tool_names": ["files.list"],
                "plugin_keys": [],
            },
        )
        schemas = agent_tools.model_tool_schemas(
            granted,
            owner=False,
            source_app_key="app:governed-files-v038",
        )
        model_names = {item["function"]["name"] for item in schemas}
        assert "homeserver_files_list" in model_names
        assert "homeserver_file_read" not in model_names

        # Coarse permission remains mandatory even when the underlying source
        # exists and is otherwise unrestricted.
        denied_pair = client.post(
            "/api/v1/pairing/request",
            json={"app_key": "no-file-read-v038", "app_name": "No Files", "permissions": []},
        ).json()
        assert client.post(
            "/api/v1/pairing/approve", json={"code": denied_pair["code"]}
        ).status_code == 200
        denied_headers = {"Authorization": f"Bearer {denied_pair['claim_token']}"}
        assert client.get("/api/v1/files", headers=denied_headers).status_code == 403
        denied_registry = client.get("/api/v1/capability-registry", headers=denied_headers).json()
        assert denied_registry["files"]["readable"] is False
        assert denied_registry["files"]["visible_files"] == 0
        assert "files.list" not in denied_registry["operations"]
        assert "files.read" not in denied_registry["operations"]

    # Remote Bridge extension remains fail-closed: no token, invalid refs, or
    # caller paths are accepted; valid operations map only to the loopback APIs.
    calls: list[tuple[str, dict]] = []

    class FakeResponse:
        status_code = 200

        def __init__(self, payload: dict):
            self._payload = payload

        def json(self) -> dict:
            return self._payload

    class FakeHttpClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, path: str, *, params: dict | None = None, headers: dict | None = None, **kwargs):
            calls.append((path, {"params": params or {}, "headers": headers or {}}))
            return FakeResponse({"ok": True})

    original_client = local_files_remote.httpx.Client
    local_files_remote.httpx.Client = FakeHttpClient
    try:
        try:
            remote_bridge.dispatch_remote_request("files.list", {}, None)
            raise AssertionError("files.list accepted a missing token")
        except remote_bridge.RemoteBridgeError:
            pass
        listed = remote_bridge.dispatch_remote_request(
            "files.list", {"query": "trip", "limit": 5}, "t" * 24
        )
        assert listed["ok"] is True
        assert calls[-1][0] == "/api/v1/files"
        assert calls[-1][1]["headers"] == {"Authorization": "Bearer " + ("t" * 24)}
        try:
            remote_bridge.dispatch_remote_request(
                "files.read", {"ref": "../../secret.txt"}, "t" * 24
            )
            raise AssertionError("files.read accepted a caller filesystem path")
        except remote_bridge.RemoteBridgeError:
            pass
        read = remote_bridge.dispatch_remote_request(
            "files.read", {"ref": "hsf-123-0123456789abcdef", "max_chars": 100}, "t" * 24
        )
        assert read["ok"] is True
        assert calls[-1][0] == "/api/v1/files/hsf-123-0123456789abcdef"
    finally:
        local_files_remote.httpx.Client = original_client

print("HomeServer governed local files v0.38 regression passed")
