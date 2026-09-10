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


with tempfile.TemporaryDirectory(prefix="homeserver-file-actions-v039-") as temp_root:
    root = Path(temp_root)
    data_dir = root / "data"
    travel_dir = root / "travel-files"
    travel_dir.mkdir(parents=True)
    plan_path = travel_dir / "plan.txt"
    scope_path = travel_dir / "scope.txt"
    revoke_path = travel_dir / "revoke.txt"
    changed_path = travel_dir / "changed.txt"
    delete_path = travel_dir / "delete.txt"
    plan_path.write_text("PLAN_ORIGINAL_3901", encoding="utf-8")
    scope_path.write_text("SCOPE_ORIGINAL_3902", encoding="utf-8")
    revoke_path.write_text("REVOKE_ORIGINAL_3903", encoding="utf-8")
    changed_path.write_text("CHANGED_ORIGINAL_3904", encoding="utf-8")
    delete_path.write_text("DELETE_ORIGINAL_3905", encoding="utf-8")
    os.environ["HOMESERVER_DATA_DIR"] = str(data_dir)

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import agent_tools, app_scopes, tools  # noqa: E402
    from app.services.knowledge_sources import scheduler  # noqa: E402
    from app.services.tasks import scheduler as task_scheduler  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()
        task_scheduler.stop()
        assert client.post(
            "/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}
        ).status_code == 200

        capabilities = client.get("/api/v1/capabilities")
        assert capabilities.status_code == 200, capabilities.text
        cap = capabilities.json()
        assert "files.write" in cap["permissions"]
        assert cap["files"]["version"] == "v0.39"
        assert cap["files"]["write_policy_gated"] is True
        assert cap["files"]["arbitrary_paths"] is False
        assert cap["files"]["operations"] == [
            "files.list", "files.read", "files.update", "files.delete"
        ]
        assert "files.actions.v1" in cap["features"]
        assert "files.approval_gated.v1" in cap["features"]

        for key, name in (("travel", "Travel"), ("private", "Private")):
            created = client.post(
                "/api/v1/control/knowledge/collections",
                json={"collection_key": key, "name": name},
            )
            assert created.status_code == 200, created.text

        source = client.post(
            "/api/v1/control/knowledge/sources",
            json={"path": str(travel_dir), "label": "Travel writable files", "scan_interval_seconds": 60},
        )
        assert source.status_code == 200, source.text
        source_id = int(source.json()["source"]["id"])
        assigned = client.put(
            f"/api/v1/control/knowledge/sources/{source_id}/collection",
            json={"collection_key": "travel"},
        )
        assert assigned.status_code == 200, assigned.text

        pair = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "file-actions-v039",
                "app_name": "File Actions",
                "permissions": ["agent.chat", "files.read", "files.write", "tools.execute"],
            },
        ).json()
        assert set(pair["permissions"]) == {"agent.chat", "files.read", "files.write", "tools.execute"}
        assert client.post(
            "/api/v1/pairing/approve", json={"code": pair["code"]}
        ).status_code == 200
        headers = {"Authorization": f"Bearer {pair['claim_token']}"}

        app_row = next(
            item for item in client.get("/api/v1/control/apps").json()["apps"]
            if item["app_key"] == "file-actions-v039"
        )
        app_id = int(app_row["id"])
        assert client.put(
            f"/api/v1/control/apps/{app_id}/knowledge-collections",
            json={"collection_keys": ["travel"]},
        ).status_code == 200

        listing = client.get("/api/v1/files", headers=headers)
        assert listing.status_code == 200, listing.text
        refs = {item["name"]: item["ref"] for item in listing.json()["items"]}
        assert set(refs) == {"plan.txt", "scope.txt", "revoke.txt", "changed.txt", "delete.txt"}

        client_tools = client.get("/api/v1/tools", headers=headers)
        assert client_tools.status_code == 200, client_tools.text
        by_key = {item["key"]: item for item in client_tools.json()["items"]}
        assert by_key["files.update"]["available"] is True
        assert by_key["files.delete"]["available"] is True
        assert by_key["files.update"]["execution_policy"]["policy_mode"] == "approval_required"
        assert by_key["files.delete"]["execution_policy"]["policy_mode"] == "approval_required"

        registry = client.get("/api/v1/capability-registry", headers=headers)
        assert registry.status_code == 200, registry.text
        registry_json = registry.json()
        assert registry_json["files"]["writable"] is True
        assert registry_json["files"]["action_version"] == "v0.39"
        assert registry_json["files"]["arbitrary_paths"] is False
        assert "files.update" in registry_json["operations"]
        assert "files.delete" in registry_json["operations"]
        assert str(travel_dir.resolve()) not in registry.text

        # Default writes are proposals. No disk or index mutation occurs until
        # an authorized approval executes the request.
        update_text = "PLAN_UPDATED_3901\nNew approved content."
        proposed = client.post(
            "/api/v1/tools/files.update/execute",
            json={"arguments": {"ref": refs["plan.txt"], "content": update_text}},
            headers=headers,
        )
        assert proposed.status_code == 200, proposed.text
        proposed_json = proposed.json()
        assert proposed_json["approval_required"] is True
        update_request_id = proposed_json["result"]["request_id"]
        assert plan_path.read_text(encoding="utf-8") == "PLAN_ORIGINAL_3901"
        before_read = client.get(f"/api/v1/files/{refs['plan.txt']}", headers=headers)
        assert before_read.status_code == 200
        assert "PLAN_ORIGINAL_3901" in before_read.json()["text"]

        approved = client.post(f"/api/v1/control/action-requests/{update_request_id}/approve")
        assert approved.status_code == 200, approved.text
        assert approved.json()["request"]["status"] == "executed"
        assert plan_path.read_text(encoding="utf-8") == update_text
        assert client.get(f"/api/v1/files/{refs['plan.txt']}", headers=headers).status_code == 409
        after_listing = client.get("/api/v1/files", headers=headers).json()["items"]
        new_plan = next(item for item in after_listing if item["name"] == "plan.txt")
        assert new_plan["ref"] != refs["plan.txt"]
        assert client.get(f"/api/v1/files/{new_plan['ref']}", headers=headers).json()["text"] == update_text

        # Caller paths never enter the action surface.
        path_attempt = client.post(
            "/api/v1/tools/files.delete/execute",
            json={"arguments": {"ref": refs["delete.txt"], "path": "../../secret.txt"}},
            headers=headers,
        )
        assert path_attempt.status_code == 422
        assert delete_path.exists()

        # Collection access is rechecked at approval time, not frozen when the
        # request was created.
        scope_proposal = client.post(
            "/api/v1/tools/files.delete/execute",
            json={"arguments": {"ref": refs["scope.txt"]}},
            headers=headers,
        )
        assert scope_proposal.status_code == 200
        scope_request_id = scope_proposal.json()["result"]["request_id"]
        with db() as connection:
            scope_item = connection.execute(
                "SELECT knowledge_item_id FROM knowledge_source_files WHERE relative_path='scope.txt'"
            ).fetchone()
        assert scope_item is not None
        assert client.put(
            f"/api/v1/control/knowledge/{int(scope_item['knowledge_item_id'])}/collection",
            json={"collection_key": "private"},
        ).status_code == 200
        scope_approve = client.post(f"/api/v1/control/action-requests/{scope_request_id}/approve")
        assert scope_approve.status_code in {403, 404}
        assert scope_path.exists()
        scope_status = client.get("/api/v1/control/action-requests").json()["items"]
        scope_request = next(item for item in scope_status if item["id"] == scope_request_id)
        assert scope_request["status"] == "failed"
        assert client.put(
            f"/api/v1/control/knowledge/{int(scope_item['knowledge_item_id'])}/collection",
            json={"collection_key": "travel"},
        ).status_code == 200

        # Permission revocation after proposal also invalidates approval.
        revoke_proposal = client.post(
            "/api/v1/tools/files.update/execute",
            json={"arguments": {"ref": refs["revoke.txt"], "content": "REVOKE_SHOULD_NOT_WRITE"}},
            headers=headers,
        )
        assert revoke_proposal.status_code == 200
        revoke_request_id = revoke_proposal.json()["result"]["request_id"]
        with db() as connection:
            connection.execute(
                "UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='files.write'",
                (app_id,),
            )
        revoke_approve = client.post(f"/api/v1/control/action-requests/{revoke_request_id}/approve")
        assert revoke_approve.status_code == 403
        assert revoke_path.read_text(encoding="utf-8") == "REVOKE_ORIGINAL_3903"
        with db() as connection:
            connection.execute(
                "UPDATE app_permissions SET allowed=1 WHERE paired_app_id=? AND permission='files.write'",
                (app_id,),
            )

        # A changed disk file invalidates the version even if the opaque ref and
        # index have not yet been rescanned; HomeServer must not overwrite it.
        changed_proposal = client.post(
            "/api/v1/tools/files.update/execute",
            json={"arguments": {"ref": refs["changed.txt"], "content": "CHANGED_APPROVED_TARGET"}},
            headers=headers,
        )
        assert changed_proposal.status_code == 200
        changed_request_id = changed_proposal.json()["result"]["request_id"]
        changed_path.write_text("EXTERNAL_EDIT_3904", encoding="utf-8")
        changed_approve = client.post(f"/api/v1/control/action-requests/{changed_request_id}/approve")
        assert changed_approve.status_code == 409
        assert changed_path.read_text(encoding="utf-8") == "EXTERNAL_EDIT_3904"

        # Delete also remains pending until approval, then removes the file and
        # its indexed tracker atomically from the paired-app surface.
        delete_proposal = client.post(
            "/api/v1/tools/files.delete/execute",
            json={"arguments": {"ref": refs["delete.txt"]}},
            headers=headers,
        )
        assert delete_proposal.status_code == 200
        delete_request_id = delete_proposal.json()["result"]["request_id"]
        assert delete_path.exists()
        assert client.get(f"/api/v1/files/{refs['delete.txt']}", headers=headers).status_code == 200
        deleted = client.post(f"/api/v1/control/action-requests/{delete_request_id}/approve")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["request"]["status"] == "executed"
        assert not delete_path.exists()
        assert client.get(f"/api/v1/files/{refs['delete.txt']}", headers=headers).status_code == 404

        # Agent proposal discovery obeys both the global write-proposal switch
        # and per-app tool scope. It never offers a direct path-based mutation.
        assert client.put(
            "/api/v1/control/agent-tools",
            json={"enabled": True, "max_calls": 3, "allow_write_proposals": True},
        ).status_code == 200
        granted = {"agent.chat", "files.read", "files.write", "tools.execute"}
        schemas = agent_tools.model_tool_schemas(
            granted,
            owner=False,
            allow_write_proposals=True,
            source_app_key="app:file-actions-v039",
        )
        model_names = {item["function"]["name"] for item in schemas}
        assert "homeserver_file_update_request" in model_names
        assert "homeserver_file_delete_request" in model_names
        delete_schema = next(
            item["function"] for item in schemas if item["function"]["name"] == "homeserver_file_delete_request"
        )
        assert set(delete_schema["parameters"]["properties"]) == {"ref"}

        app_scopes.save_scope(
            app_id,
            {
                "cloud_allowed": True,
                "memory_key_prefixes": [],
                "knowledge_kinds": [],
                "tool_names": ["files.list", "files.read", "files.update"],
                "plugin_keys": [],
            },
        )
        scoped_schemas = agent_tools.model_tool_schemas(
            granted,
            owner=False,
            allow_write_proposals=True,
            source_app_key="app:file-actions-v039",
        )
        scoped_names = {item["function"]["name"] for item in scoped_schemas}
        assert "homeserver_file_update_request" in scoped_names
        assert "homeserver_file_delete_request" not in scoped_names
        app_scopes.save_scope(app_id, dict(app_scopes.DEFAULT_SCOPE))

        # Sensitive/high-impact policy removes a write from both direct app
        # execution and model-facing proposal discovery.
        sensitive = client.put(
            f"/api/v1/control/action-policies/{app_id}/files.delete",
            json={"policy_mode": "sensitive_high_impact"},
        )
        assert sensitive.status_code == 200, sensitive.text
        blocked = client.post(
            "/api/v1/tools/files.delete/execute",
            json={"arguments": {"ref": refs["scope.txt"]}},
            headers=headers,
        )
        assert blocked.status_code == 403
        blocked_schemas = agent_tools.model_tool_schemas(
            granted,
            owner=False,
            allow_write_proposals=True,
            source_app_key="app:file-actions-v039",
        )
        assert "homeserver_file_delete_request" not in {
            item["function"]["name"] for item in blocked_schemas
        }

        # Tool audit metadata does not duplicate file contents, opaque refs, or
        # local source paths. The private action request itself retains only the
        # normalized arguments required for later execution.
        runs = client.get("/api/v1/control/tool-runs?limit=200").json()["items"]
        serialized_runs = json.dumps(runs, ensure_ascii=False)
        assert update_text not in serialized_runs
        assert refs["plan.txt"] not in serialized_runs
        assert str(travel_dir.resolve()) not in serialized_runs
        file_runs = [item for item in runs if item["tool_key"] in {"files.update", "files.delete"}]
        assert file_runs
        assert all("ref" not in item["arguments"] for item in file_runs)
        assert all("content" not in item["arguments"] for item in file_runs)

        # An app without files.write cannot discover or invoke mutation tools.
        no_write_pair = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "read-only-files-v039",
                "app_name": "Read Only Files",
                "permissions": ["files.read", "tools.execute"],
            },
        ).json()
        assert client.post(
            "/api/v1/pairing/approve", json={"code": no_write_pair["code"]}
        ).status_code == 200
        no_write_headers = {"Authorization": f"Bearer {no_write_pair['claim_token']}"}
        no_write_tools = client.get("/api/v1/tools", headers=no_write_headers).json()["items"]
        no_write_by_key = {item["key"]: item for item in no_write_tools}
        assert no_write_by_key["files.update"]["available"] is False
        assert no_write_by_key["files.delete"]["available"] is False
        denied = client.post(
            "/api/v1/tools/files.update/execute",
            json={"arguments": {"ref": new_plan["ref"], "content": "DENIED"}},
            headers=no_write_headers,
        )
        assert denied.status_code == 403

print("HomeServer governed file actions v0.39 regression passed")