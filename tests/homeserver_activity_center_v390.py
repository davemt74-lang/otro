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

with tempfile.TemporaryDirectory(prefix="homeserver-activity-center-v390-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.database import db
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import (
        activity_center,
        approvals,
        homeserver_app_prebuilt,
        homeserver_apps,
        local_automation,
    )
    from app.services.tasks import scheduler as task_scheduler

    with TestClient(app) as client:
        task_scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        homeserver_app_prebuilt.install("vp3.media-player")
        player=homeserver_apps.get("vp3.media-player")
        app_id=player["app_id"]

        # Safe app event projection must redact secret/path-bearing metadata.
        with db() as connection:
            connection.execute(
                """INSERT INTO homeserver_app_events(
                     app_id,event_type,actor_type,actor_key,metadata_json
                   ) VALUES (?, 'player.test.completed','app','vp3.media-player',?)""",
                (app_id,json.dumps({
                    "result":"ok",
                    "token":"SECRET_TOKEN_932",
                    "filesystem_path":"C:/private/media/file.mp4",
                })),
            )
            connection.execute(
                """INSERT INTO activity_log(
                     actor_type,actor_key,action,resource_type,resource_key,metadata_json
                   ) VALUES ('system','activity-test','system.health','system','homeserver',?)""",
                (json.dumps({"healthy":True,"api_key":"PRIVATE_KEY_221"}),),
            )
            connection.execute(
                """INSERT INTO homeserver_app_ai_runs(
                     run_key,app_id,status,error
                   ) VALUES ('activity_failed_agent',?,'failed','sensitive failure detail')""",
                (app_id,),
            )

        local_automation.upsert_routine(
            "activity-failure-routine",
            "Activity Failure Routine",
            approval_mode="suggest_only",
            steps=[{
                "step_kind":"app_action",
                "app_key":"vp3.media-player",
                "action_key":"player.status",
                "arguments":{},
            }],
        )
        with db() as connection:
            routine_id=connection.execute(
                "SELECT id FROM automation_routines WHERE routine_key='activity-failure-routine'"
            ).fetchone()["id"]
            connection.execute(
                """INSERT INTO automation_rule_executions(
                     rule_id,routine_id,trigger_kind,status,action_count,error
                   ) VALUES (NULL,?,'manual','failed',0,'private automation failure')""",
                (routine_id,),
            )

        approval=approvals.create_app_action_request(
            "activity-test",
            "apps.invoke",
            {"app_key":"vp3.media-player","action":"player.status","arguments":{}},
            owner=True,
        )
        request_id=approval["result"]["request_id"]

        synced=client.post("/api/v1/control/activity-center/sync")
        assert synced.status_code==200,synced.text
        assert synced.json()["created"]>=3

        summary=client.get("/api/v1/control/activity-center/summary")
        assert summary.status_code==200,summary.text
        state=summary.json()
        assert state["pending_approvals"]>=1
        assert state["failed_automations"]>=1
        assert state["failed_agent_runs"]>=1
        assert state["needs_attention"]>=3

        feed=client.get("/api/v1/control/activity",params={"limit":250})
        assert feed.status_code==200,feed.text
        items=feed.json()["items"]
        assert any(row["source_kind"]=="app" and row["action"]=="player.test.completed" for row in items)
        assert any(row["source_kind"]=="notification" and row["action_payload"].get("request_id")==request_id for row in items)
        assert not any(row["source_kind"]=="approval" and row["source_id"]==request_id and row["status"]=="pending" for row in items)
        serialized=json.dumps(items,ensure_ascii=False)
        assert "SECRET_TOKEN_932" not in serialized
        assert "C:/private/media/file.mp4" not in serialized
        assert "PRIVATE_KEY_221" not in serialized
        assert "private automation failure" not in serialized
        assert "sensitive failure detail" not in serialized

        attention=client.get("/api/v1/control/activity",params={"needs_attention":"true","limit":250}).json()["items"]
        assert attention
        assert all(row["needs_attention"] is True for row in attention)

        unread=client.get("/api/v1/control/activity",params={"unread_only":"true","limit":250}).json()["items"]
        assert unread
        assert all(row["source_kind"]=="notification" and row["read"] is False for row in unread)

        # Exact dedupe keys collapse repeated notifications and increment occurrence count.
        first=activity_center.emit_notification(
            source="test",title="Repeated event",level="warning",source_key="repeat-source",
            dedupe_key="repeat:1",event_key="test.repeat",
        )
        second=activity_center.emit_notification(
            source="test",title="Repeated event again",level="warning",source_key="repeat-source",
            dedupe_key="repeat:1",event_key="test.repeat",
        )
        assert first["id"]==second["id"]
        assert int(second["occurrence_count"])==2

        # Preferences suppress noisy levels without disabling urgent events.
        pref=client.put("/api/v1/control/activity-center/preferences/repeat-source",json={
            "minimum_level":"error","enabled":True,"toast_enabled":False
        })
        assert pref.status_code==200,pref.text
        suppressed=activity_center.emit_notification(
            source="test",title="Should suppress",level="info",source_key="repeat-source",
            dedupe_key="repeat:suppressed",
        )
        assert suppressed["suppressed"] is True
        urgent=activity_center.emit_notification(
            source="test",title="Must pass",level="error",source_key="repeat-source",
            dedupe_key="repeat:error",
        )
        assert urgent.get("suppressed") is not True

        # Governance notifications cannot be hidden by source preferences.
        client.put("/api/v1/control/activity-center/preferences/activity-test",json={
            "enabled":False,"minimum_level":"action_required"
        })
        second_approval=approvals.create_app_action_request(
            "activity-test",
            "apps.invoke",
            {"app_key":"vp3.media-player","action":"player.status","arguments":{}},
            owner=True,
        )
        second_request_id=second_approval["result"]["request_id"]
        client.post("/api/v1/control/activity-center/sync")
        approval_items=client.get("/api/v1/control/activity",params={"category":"approvals","limit":250}).json()["items"]
        assert any(
            row["source_kind"]=="notification" and row["action_payload"].get("request_id")==second_request_id
            for row in approval_items
        )

        notification_id=next(
            row["notification_id"] for row in items
            if row["source_kind"]=="notification" and row["notification_id"]
        )
        marked=client.patch(
            f"/api/v1/control/activity-center/notifications/{notification_id}",
            json={"read":True,"dismissed":True},
        )
        assert marked.status_code==200,marked.text
        assert marked.json()["notification"]["read_at"] is not None
        assert marked.json()["notification"]["dismissed_at"] is not None

        brain=client.get("/api/v1/control/activity-center/brain-context")
        assert brain.status_code==200,brain.text
        brain_payload=brain.json()
        assert brain_payload["contract"]=="vp3.homeserver.activity-center.brain-context.v1"
        assert brain_payload["governance"]["raw_secrets_exposed"] is False
        assert brain_payload["governance"]["filesystem_paths_exposed"] is False
        brain_text=json.dumps(brain_payload,ensure_ascii=False)
        assert "SECRET_TOKEN_932" not in brain_text
        assert "C:/private/media/file.mp4" not in brain_text

        # Retention remains bounded and only prunes old dismissed/archived rows.
        with db() as connection:
            connection.execute(
                """INSERT INTO notifications(
                     source,title,body,level,dismissed_at,created_at,category,priority,source_kind
                   ) VALUES ('retention-test','Old dismissed','', 'info',
                             '2025-01-01T00:00:00+00:00','2025-01-01 00:00:00',
                             'system','normal','system')"""
            )
            old_id=connection.execute(
                "SELECT id FROM notifications WHERE source='retention-test' ORDER BY id DESC LIMIT 1"
            ).fetchone()["id"]
            for i in range(1400):
                connection.execute(
                    """INSERT INTO activity_log(actor_type,actor_key,action,resource_type,resource_key,metadata_json)
                       VALUES ('system','stress','stress.event','test',?,'{}')""",
                    (str(i),),
                )
        pruned=activity_center.prune_notifications(retention_days=90,max_rows=5000)
        assert pruned["deleted"]>=1
        with db() as connection:
            assert connection.execute("SELECT id FROM notifications WHERE id=?",(old_id,)).fetchone() is None
        bounded_feed=activity_center.list_activity(limit=250)
        assert bounded_feed["count"]<=250

        capability=client.get("/api/v1/control/activity-center/capability")
        assert capability.status_code==200,capability.text
        cap=capability.json()
        assert cap["unified_activity_feed"] is True
        assert cap["dedupe"] is True
        assert cap["bounded_retention"] is True
        assert cap["default_retention_days"]==90
        assert cap["actionable_approvals"] is True
        assert cap["agent_brain_context"] is True

        ui=(ROOT/"ui"/"index.html").read_text(encoding="utf-8")
        js=(ROOT/"ui"/"app.js").read_text(encoding="utf-8")
        css=(ROOT/"ui"/"activity-center.css").read_text(encoding="utf-8")
        assert 'id="activityFeed"' in ui
        assert 'data-activity-filter="apps"' in ui
        assert 'data-activity-attention="1"' in ui
        assert "activity-center/summary" in js
        assert "data-activity-dismiss" in js
        assert "state.view==='activity'" in js
        assert "30000" in js
        assert ".activity-feed" in css

print("HomeServer Section 25 Unified Notifications & Activity Center: PASS")
