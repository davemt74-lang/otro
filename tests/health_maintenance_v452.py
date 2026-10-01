"""Section 31B acceptance: health alert lifecycle, fail-closed probes and scheduling."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(prefix="hs-maintenance-31b-") as temp:
    os.environ["HOMESERVER_DATA_DIR"]=temp
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.database import db
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import health_repair, health_maintenance, activity_center
    from app.services import tasks as task_service
    from app.services.tasks import scheduler

    state={"issues":[],"snapshot_complete":True}
    original=health_repair.status
    try:
        health_repair.status=lambda: dict(state)
        with TestClient(app) as client:
            scheduler.stop()
            assert client.get("/api/v1/control/activity-center/brain-context").status_code==401
            assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
            issue={
                "key":"storage:disk-pressure",
                "title":"Disk pressure",
                "severity":"attention",
                "repair":{"agent_can_execute":False,"action_key":None},
            }
            state["issues"]=[issue]
            first=health_maintenance.sync_health_notifications()
            assert first=={"created":1,"updated":0,"resolved":0,"snapshot_complete":True},first
            second=health_maintenance.sync_health_notifications()
            assert second["created"]==second["updated"]==second["resolved"]==0,second
            with db() as conn:
                row=conn.execute("SELECT id,occurrence_count,body FROM notifications WHERE dedupe_key='health:storage:disk-pressure'").fetchone()
                assert int(row["occurrence_count"])==1
                assert "no trusted automatic repair" in row["body"]
            # Health warnings belong in proactive attention, even though generic warnings do not.
            projection=activity_center.brain_context()
            assert any(x["title"]=="Disk pressure" and x["level"]=="warning" for x in projection["attention"])
            assert activity_center.summary()["needs_attention"]>=1

            # A failed or truncated snapshot must not silently resolve existing alerts.
            state["issues"]=[]
            state["snapshot_complete"]=False
            partial=health_maintenance.sync_health_notifications()
            assert partial["resolved"]==0 and not partial["snapshot_complete"],partial
            state["snapshot_complete"]=True
            state["issues"]=[issue]
            with patch.object(health_maintenance,"MAX_ISSUES",1):
                state["issues"]=[{"key":"other:one","title":"Other one","severity":"attention","repair":{}},
                                  {"key":"other:two","title":"Other two","severity":"attention","repair":{}}]
                truncated=health_maintenance.sync_health_notifications()
                assert truncated["resolved"]==0 and not truncated["snapshot_complete"],truncated
            state["issues"]=[issue]
            issue["severity"]="critical"
            escalated=health_maintenance.sync_health_notifications()
            assert escalated["updated"]==1
            with db() as conn:
                row=conn.execute("SELECT level,occurrence_count FROM notifications WHERE dedupe_key='health:storage:disk-pressure'").fetchone()
                assert row["level"]=="error"
                assert int(row["occurrence_count"])==2

            # Dismissal is respected; no fresh attention is generated for unchanged issues.
            with db() as conn:
                notification_id=int(conn.execute("SELECT id FROM notifications WHERE dedupe_key='health:storage:disk-pressure'").fetchone()[0])
            activity_center.mark_notification(notification_id,read=True,dismissed=True)
            health_maintenance.sync_health_notifications()
            with db() as conn:
                row=conn.execute("SELECT read_at,dismissed_at,occurrence_count FROM notifications WHERE id=?",(notification_id,)).fetchone()
                assert row["read_at"] and row["dismissed_at"] and row["occurrence_count"]==2

            state["issues"]=[]
            resolved=health_maintenance.sync_health_notifications()
            assert resolved["resolved"]>=1
            with db() as conn:
                assert conn.execute("SELECT archived_at FROM notifications WHERE id=?",(notification_id,)).fetchone()[0]
            state["issues"]=[issue]
            recurrence=health_maintenance.sync_health_notifications()
            assert recurrence["updated"]==1
            with db() as conn:
                row=conn.execute("SELECT COUNT(*),MAX(archived_at),MAX(occurrence_count),MAX(read_at),MAX(dismissed_at) FROM notifications WHERE dedupe_key='health:storage:disk-pressure'").fetchone()
                assert row[0]==1 and row[1] is None and row[2]==3 and row[3] is None and row[4] is None,row
            projection=activity_center.brain_context()
            assert any(x["title"]=="Disk pressure" for x in projection["attention"])

            # An owner-dismissed warning must re-open when it becomes critical.
            new_issue={
                "key":"media:escalating",
                "title":"Media runtime degraded",
                "severity":"warning",
                "repair":{"agent_can_execute":False,"action_key":None},
            }
            state["issues"]=[issue,new_issue]
            health_maintenance.sync_health_notifications()
            with db() as conn:
                new_id=int(conn.execute(
                    "SELECT id FROM notifications WHERE dedupe_key='health:media:escalating'"
                ).fetchone()[0])
            activity_center.mark_notification(new_id, read=True, dismissed=True)
            health_maintenance.sync_health_notifications()
            with db() as conn:
                assert conn.execute("SELECT dismissed_at FROM notifications WHERE id=?",(new_id,)).fetchone()[0]
            new_issue["severity"]="critical"
            health_maintenance.sync_health_notifications()
            with db() as conn:
                row=conn.execute(
                    "SELECT dismissed_at,read_at,level,occurrence_count FROM notifications WHERE id=?",
                    (new_id,),
                ).fetchone()
                assert row["dismissed_at"] is None and row["read_at"] is None
                assert row["level"]=="error" and int(row["occurrence_count"])==2
            assert any(x["title"]=="Media runtime degraded" for x in activity_center.brain_context()["attention"])

            # A real probe failure reports incomplete, without leaking exception text.
            health_repair.status=original
            with patch.object(health_repair.homeserver_app_manager,"inventory",side_effect=RuntimeError("SECRET_PATH_123")):
                broken=health_repair.status()
            assert broken["snapshot_complete"] is False
            assert broken["unavailable_check_count"]>=1
            assert any(x["key"]=="health:incomplete-probes" for x in broken["issues"])
            assert "SECRET_PATH_123" not in repr(broken)
            # Ordinary Activity reads must not perform costly health scans.
            with patch.object(health_maintenance,"sync_health_notifications",side_effect=AssertionError("Unexpected health scan")):
                regular=client.get("/api/v1/control/activity-center/summary")
            assert regular.status_code==200,regular.text
            # Explicit owner refresh does perform the scan and retains existing approvals.
            real=client.post("/api/v1/control/activity-center/sync")
            assert real.status_code==200,real.text
            assert "health" in real.json()
            assert health_repair.status()["contract"]=="vp3.homeserver.health-repair.v1"

        # Existing task scheduler runs maintenance without opening the Brain drawer.
        class FourCycles:
            def __init__(self): self.waits=0
            def is_set(self): return self.waits>=4
            def wait(self,_seconds): self.waits+=1
        local=task_service.TaskScheduler(interval_seconds=1)
        local._stop=FourCycles()
        with patch.object(task_service,"run_due_reminders") as reminders, patch.object(health_maintenance,"sync_health_notifications") as maintenance:
            local._run()
            assert reminders.call_count==4
            maintenance.assert_called_once_with()
    finally:
        health_repair.status=original
print("Section 31B health maintenance integration PASS")
