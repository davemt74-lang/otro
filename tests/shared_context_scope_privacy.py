"""Shared Cloud context obeys the same live paired-app scopes as direct APIs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

with tempfile.TemporaryDirectory(prefix="shared-context-privacy-") as directory:
    root = Path(directory)
    os.environ["HOMESERVER_DATA_DIR"] = str(root / "data")
    from fastapi.testclient import TestClient
    from app.database import initialize_database
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import (app_scopes, cloud_pairing, federated_data, knowledge,
                              knowledge_collections, knowledge_sources, memory_continuity,
                              pairing, remote_bridge, shared_agent_context)

    initialize_database()
    knowledge.ensure_knowledge_index()
    for key in ("work", "private"):
        folder = root / key
        folder.mkdir()
        (folder / f"{key}-file.txt").write_text(f"{key.upper()}_FILE_CONTENT", encoding="utf-8")
        knowledge_collections.create_collection(key, key.title())
        source = knowledge_sources.create_source(str(folder), label=f"{key.title()} files")
        knowledge_collections.assign_source(source["id"], key)
        knowledge_sources.scan_source(source["id"])
        note = knowledge.create_knowledge_item(f"{key.title()} note", "note", f"{key.upper()}_NOTE_CONTENT")
        knowledge_collections.assign_item(note["id"], key)
        memory_continuity.create_federated_memory(
            {"memory_key": f"{key}:context", "content": f"{key.upper()}_MEMORY_CONTENT"},
            source_app_key="owner", owner=True,
        )
    wrong_kind = knowledge.create_knowledge_item("Work document outside kind scope", "document", "KIND_RESTRICTED_CONTENT")
    knowledge_collections.assign_item(wrong_kind["id"], "work")
    request = pairing.create_pairing_request("vp3", "VP3", cloud_pairing._VP3_PERMISSIONS)
    pairing.approve_pairing_request(request["request_id"])
    token = request["claim_token"]
    identity = pairing.authenticate(token)
    app_scopes.save_scope(identity["id"], {"memory_key_prefixes": ["work:"], "knowledge_kinds": ["note", "watched_document"]})
    with TestClient(app) as client:
        knowledge_sources.scheduler.stop()
        from app.services.tasks import scheduler
        scheduler.stop()
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200
        changed = client.put(f"/api/v1/control/apps/{identity['id']}/knowledge-collections", json={"collection_keys": ["work"]})
        assert changed.status_code == 200, changed.text
        headers = {"Authorization": f"Bearer {token}"}
        direct = client.get("/api/v1/files", headers=headers)
        assert direct.status_code == 200 and direct.json()["count"] == 1, direct.text
        assert direct.json()["items"][0]["relative_path"] == "work-file.txt"
        cloud = {"authoritative_source": "vp3_cloud", "snapshot_mode": "full", "revision": "scope-test-cloud",
                 "covered_datasets": ["memory", "knowledge", "contacts", "tasks", "calendar", "files", "notifications"],
                 "datasets": {name: [] for name in ("memory", "knowledge", "contacts", "tasks", "calendar", "files", "notifications")}}
        reply = remote_bridge.dispatch_remote_request("shared.context.exchange", {"cloud_snapshot": cloud}, token)
        assert reply["ok"], reply
        home = reply["payload"]["homeserver_snapshot"]
        encoded = json.dumps(home)
        assert "WORK_MEMORY_CONTENT" in encoded and "WORK_NOTE_CONTENT" in encoded and "work-file.txt" in encoded
        assert "PRIVATE_MEMORY_CONTENT" not in encoded and "PRIVATE_NOTE_CONTENT" not in encoded
        assert "private-file.txt" not in encoded and "KIND_RESTRICTED_CONTENT" not in encoded
        assert {"memory", "knowledge", "files"}.isdisjoint(home["covered_datasets"])
        assert home["snapshot_mode"] == "full" and "contacts" in home["covered_datasets"]
        assert home["unavailable_datasets"] == {"memory": "scope_limited:memory_key_prefixes",
                                                  "knowledge": "scope_limited:knowledge_collections",
                                                  "files": "scope_limited:knowledge_collections"}
        # Narrowing discovery is not deletion of the records outside that scope.
        private = federated_data.envelope("homeserver", "memory", "private-universe-record", title="Outside current scope")
        federated_data.observe(private, observed_source="vp3_cloud")
        result = federated_data.reconcile_snapshot(home, observed_source="vp3_cloud")
        assert result["tombstoned"] == 0
        assert federated_data.resolve_authority_key(private["canonical_id"], authority_source="homeserver", dataset="memory", observed_source="vp3_cloud") == "private-universe-record"
        assert pairing.authenticate(token)["permissions"] == identity["permissions"]
        print("PASS live memory prefixes, Knowledge kind/collection intersection and indexed-file scopes constrain real shared exchange")

        # Generic read privileges do not authorize writing the trusted Cloud peer.
        baseline = shared_agent_context.cloud_snapshot()["revision"]
        for grants in (["memory.read", "knowledge.search", "contacts.read", "tasks.read", "events.read", "files.read", "notifications.read"],
                       list(pairing.DEFAULT_PERMISSIONS)):
            reader = pairing.create_pairing_request("generic-reader", "Reader", grants)
            pairing.approve_pairing_request(reader["request_id"])
            forged = {**cloud, "revision": "forged-reader-revision",
                      "datasets": {**cloud["datasets"], "memory": [{"id": "forged", "title": "Forged trusted memory", "content": "UNAUTHORIZED_CLOUD_CONTEXT"}]}}
            try:
                remote_bridge.dispatch_remote_request("shared.context.exchange", {"cloud_snapshot": forged}, reader["claim_token"])
                raise AssertionError("Generic application impersonated the trusted VP3 Cloud mirror")
            except remote_bridge.RemoteBridgeError:
                pass
            assert shared_agent_context.cloud_snapshot()["revision"] == baseline
            ping = remote_bridge.dispatch_remote_request("system.ping", {}, reader["claim_token"])
            assert ping["ok"] and ping["payload"]["app"] == "generic-reader"
        print("PASS generic read and write grants cannot impersonate the trusted VP3 Cloud peer")
