"""Section 31B notification lifecycle: dedupe, resolution, recurrence and no automatic repair."""
import os
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(prefix="hs-maintenance-31b-") as temp:
    os.environ["HOMESERVER_DATA_DIR"]=temp
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.database import db
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import health_repair, health_maintenance, activity_center
    from app.services.tasks import scheduler

    state={"issues":[]}
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
            assert first=={"created":1,"updated":0,"resolved":0},first
            second=health_maintenance.sync_health_notifications()
            assert second=={"created":0,"updated":0,"resolved":0},second
            with db() as conn:
                row=conn.execute("SELECT id,occurrence_count,body FROM notifications WHERE dedupe_key='health:storage:disk-pressure'").fetchone()
                assert int(row["occurrence_count"])==1
                assert "no trusted automatic repair" in row["body"]
            issue["severity"]="critical"
            escalated=health_maintenance.sync_health_notifications()
            assert escalated["updated"]==1
            with db() as conn:
                row=conn.execute("SELECT level,occurrence_count FROM notifications WHERE dedupe_key='health:storage:disk-pressure'").fetchone()
                assert row["level"]=="error"
                assert int(row["occurrence_count"])==2
            state["issues"]=[]
            resolved=health_maintenance.sync_health_notifications()
            assert resolved["resolved"]==1
            with db() as conn:
                assert conn.execute("SELECT archived_at FROM notifications WHERE dedupe_key='health:storage:disk-pressure'").fetchone()[0]
            state["issues"]=[issue]
            recurrence=health_maintenance.sync_health_notifications()
            assert recurrence["updated"]==1
            with db() as conn:
                row=conn.execute("SELECT COUNT(*),MAX(archived_at),MAX(occurrence_count) FROM notifications WHERE dedupe_key='health:storage:disk-pressure'").fetchone()
                assert row[0]==1 and row[1] is None and row[2]==3,row
            projection=activity_center.brain_context()
            assert any(x["title"]=="Disk pressure" for x in projection["attention"])
            # Exercise the real health/activity coupling, not just a mocked projection.
            health_repair.status=original
            real=client.get("/api/v1/control/activity-center/summary")
            assert real.status_code==200,real.text
            assert health_repair.status()["contract"]=="vp3.homeserver.health-repair.v1"
    finally:
        health_repair.status=original
print("Section 31B health notification lifecycle PASS")
