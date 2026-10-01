from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-storage-v420-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.config import settings
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import (
        backup_protection,
        backups,
        homeserver_app_prebuilt,
        homeserver_app_resources,
        storage_maintenance,
        tools,
    )
    from app.services.tasks import scheduler as task_scheduler

    with TestClient(app) as client:
        task_scheduler.stop()

        assert client.get("/api/v1/control/storage").status_code==401
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        installed=homeserver_app_prebuilt.install("vp3.notes")
        assert installed["app"]["app_key"]=="vp3.notes"

        payload=b"SECTION28_APP_STORAGE_" + (b"x"*4096)
        homeserver_app_resources.write_file("vp3.notes","section28/data.bin",payload)
        status=client.get("/api/v1/control/storage")
        assert status.status_code==200,status.text
        body=status.json()

        assert body["contract"]=="vp3.homeserver.storage-maintenance.v1"
        assert body["automatic_deletion"] is False
        assert body["external_mapped_storage_counted"] is False
        assert body["filesystem_paths_exposed"] is False
        assert body["categories"]["app_data"]>=len(payload)
        notes=next(item for item in body["apps"]["items"] if item["app_key"]=="vp3.notes")
        assert notes["used_bytes"]>=len(payload)
        assert notes["limit_bytes"]>notes["used_bytes"]
        assert notes["filesystem_path_exposed"] is False

        serialized=json.dumps(body,ensure_ascii=False)
        assert str(settings.data_dir) not in serialized
        assert "\\" not in serialized
        assert "/app-data/" not in serialized

        capability=client.get("/api/v1/control/storage/capability")
        assert capability.status_code==200,capability.text
        cap=capability.json()
        assert cap["per_app_quota_rollup"] is True
        assert cap["automatic_deletion"] is False
        assert cap["backup_prune_delegates_existing_retention"] is True

        # Force reserve-breach behavior without filling the disk.
        high_reserve=client.put("/api/v1/control/storage/policy",json={
            "minimum_free_bytes":1099511627776,
            "warning_free_percent":15.0,
            "critical_free_percent":7.5,
            "allow_owner_backup_prune":True,
        })
        assert high_reserve.status_code==200,high_reserve.text
        critical=client.get("/api/v1/control/storage").json()
        assert critical["disk"]["reserve_breached"] is True
        assert critical["disk"]["level"]=="critical"
        plan=client.get("/api/v1/control/storage/maintenance").json()
        assert any(item["key"]=="disk.free-space" for item in plan["recommendations"])
        assert plan["automatic_deletion"] is False

        invalid=client.put("/api/v1/control/storage/policy",json={
            "warning_free_percent":5.0,
            "critical_free_percent":10.0,
        })
        assert invalid.status_code==400

        # App quota pressure is surfaced as recommendation, not auto-cleaned.
        usage=homeserver_app_resources.resource_status("vp3.notes")
        homeserver_app_resources.update_limits(
            "vp3.notes",
            storage_limit_bytes=max(16*1024*1024,usage["storage_used_bytes"]+1024),
        )
        app_plan=client.get("/api/v1/control/storage/maintenance").json()
        assert app_plan["automatic_deletion"] is False

        # Agent storage status is owner-only and path-safe.
        owner_tools={row["key"]:row for row in tools.list_tools(owner=True)}
        assert owner_tools["storage.status"]["available"] is True
        app_tools={row["key"]:row for row in tools.list_tools({"tools.execute"},owner=False)}
        assert app_tools["storage.status"]["available"] is False
        assert "owner.control" in app_tools["storage.status"]["missing_permissions"]
        tool_run=tools.execute_tool("owner","storage.status",{},set(),owner=True)
        assert tool_run["status"]=="completed"
        tool_text=json.dumps(tool_run,ensure_ascii=False)
        assert str(settings.data_dir) not in tool_text
        assert "filesystem_paths_exposed" in tool_text
        assert '"automatic_deletion": false' in tool_text.lower()

        # Backup pruning delegates to Section 27 retention; nothing else is deleted.
        backup_protection.update_policy({"retain_manual":100})
        one=backups.create_backup("manual")
        two=backups.create_backup("manual")
        assert Path(one["path"]).is_file()
        assert Path(two["path"]).is_file()
        backup_protection.update_policy({"retain_manual":1})
        prune=client.post("/api/v1/control/storage/maintenance/prune-backups")
        assert prune.status_code==200,prune.text
        assert prune.json()["delegated_to_backup_retention"] is True
        assert prune.json()["deleted_count"]>=1
        assert Path(two["path"]).is_file()

        # Disabling the delegated prune blocks even the owner action.
        disabled=client.put("/api/v1/control/storage/policy",json={"allow_owner_backup_prune":False})
        assert disabled.status_code==200
        blocked=client.post("/api/v1/control/storage/maintenance/prune-backups")
        assert blocked.status_code==403
        assert homeserver_app_resources.read_file("vp3.notes","section28/data.bin")==payload

        ui=(ROOT/"ui"/"app.js").read_text(encoding="utf-8")
        css=(ROOT/"ui"/"storage.css").read_text(encoding="utf-8")
        assert "view-storage" in ui
        assert "storagePolicyForm" in ui
        assert "storagePruneBackups" in ui
        assert "automatic" not in ui.lower() or "auto-delete" not in ui.lower()
        assert ".storage-health" in css

print("HomeServer Section 28 Storage Quotas & Maintenance: PASS")
