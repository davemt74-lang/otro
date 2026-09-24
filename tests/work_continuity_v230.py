from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

static = {
    "migration": (ROOT / "database/migrations/032_cloud_work_continuity.sql").read_text(encoding="utf-8"),
    "service": (ROOT / "app/services/work_continuity.py").read_text(encoding="utf-8"),
    "bridge": (ROOT / "app/services/remote_bridge.py").read_text(encoding="utf-8"),
    "capabilities": (ROOT / "app/bridge.py").read_text(encoding="utf-8"),
    "context": (ROOT / "app/services/canonical_context.py").read_text(encoding="utf-8"),
    "main": (ROOT / "app/main.py").read_text(encoding="utf-8"),
    "config": (ROOT / "app/config.py").read_text(encoding="utf-8"),
    "installer": (ROOT / "installer/HomeServer.iss").read_text(encoding="utf-8"),
}

checks = [
    ("product version is 2.3", 'version: str = "2.3"' in static["config"] and '#define MyAppVersion "2.3"' in static["installer"]),
    ("schema 032 persists stable Cloud run/action receipts", "CREATE TABLE IF NOT EXISTS cloud_work_continuity" in static["migration"] and "continuity_key TEXT PRIMARY KEY" in static["migration"]),
    ("receipt service uses stable v2.3 contract and conservative stale recovery", 'WORK_CONTINUITY_VERSION = "2.3"' in static["service"] and "STALE_RUNNING_SECONDS = 300" in static["service"]),
    ("remote Agent chat consumes continuity metadata", 'body.get("_continuity")' in static["bridge"] and "work_continuity.execute" in static["bridge"]),
    ("remote status and cancel operations are paired-app protected", 'if op == "work.continuity.status"' in static["bridge"] and 'if op == "work.continuity.cancel"' in static["bridge"] and '{"agent.chat"}' in static["bridge"]),
    ("capability registry advertises durable work continuity", '"work_continuity"' in static["capabilities"] and '"work.continuity.v1"' in static["capabilities"] and '"result_replay": True' in static["capabilities"]),
    ("local Agent context can reason over Cloud work receipts", "work_continuity.list_recent" in static["context"] and "work_continuity" in static["context"]),
    ("local owner API exposes continuity history and cancel", "/api/v1/control/work-continuity" in static["main"] and "control_work_continuity_cancel" in static["main"]),
]

for name, ok in checks:
    if not ok:
        raise AssertionError(name)
    print("PASS:", name)

with tempfile.TemporaryDirectory(prefix="homeserver-v230-work-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.config import settings
    from app.database import db, initialize_database
    from app.services import work_continuity

    initialize_database()
    assert settings.version == "2.3"

    with db() as connection:
        assert connection.execute("SELECT 1 FROM schema_migrations WHERE version=32").fetchone() is not None
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cloud_work_continuity'").fetchone() is not None

    calls = {"count": 0}

    def runner() -> dict:
        calls["count"] += 1
        return {
            "ok": True,
            "reply": "Completed once.",
            "conversation_id": "conv-local-1",
            "provider": "test",
            "model": "test-model",
        }

    meta = {
        "key": "a" * 64,
        "cloud_run_id": 2301,
        "cloud_action_id": 501,
        "conversation_id": "cloud-conversation-1",
    }
    first = work_continuity.execute(meta, "agent.chat", runner, source_app_key="vp3")
    assert first["continuity"]["status"] == "completed"
    assert first["continuity"]["replayed"] is False
    assert calls["count"] == 1

    replay = work_continuity.execute(meta, "agent.chat", runner, source_app_key="vp3")
    assert replay["continuity"]["status"] == "completed"
    assert replay["continuity"]["replayed"] is True
    assert replay["reply"] == "Completed once."
    assert calls["count"] == 1, "duplicate Cloud retry executed local work twice"

    with db() as connection:
        connection.execute(
            """
            INSERT INTO cloud_work_continuity(
                continuity_key,cloud_run_id,cloud_action_id,source_app_key,operation,status,
                attempt_count,started_at,heartbeat_at,updated_at
            ) VALUES (?,?,?,?,?,'running',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
            """,
            ("b" * 64, 2302, 502, "vp3", "agent.chat"),
        )
    pending_calls = {"count": 0}

    def pending_runner() -> dict:
        pending_calls["count"] += 1
        return {"ok": True, "reply": "should not run"}

    pending = work_continuity.execute(
        {"key": "b" * 64, "cloud_run_id": 2302, "cloud_action_id": 502},
        "agent.chat",
        pending_runner,
    )
    assert pending["continuity"]["status"] == "running"
    assert pending_calls["count"] == 0

    with db() as connection:
        connection.execute(
            """
            INSERT INTO cloud_work_continuity(
                continuity_key,cloud_run_id,cloud_action_id,source_app_key,operation,status,
                attempt_count,started_at,heartbeat_at,updated_at
            ) VALUES (?,?,?,?,?,'running',1,'2000-01-01 00:00:00','2000-01-01 00:00:00','2000-01-01 00:00:00')
            """,
            ("c" * 64, 2303, 503, "vp3", "agent.chat"),
        )
    stale_calls = {"count": 0}

    def stale_runner() -> dict:
        stale_calls["count"] += 1
        return {"ok": True, "reply": "Recovered after restart."}

    recovered = work_continuity.execute(
        {"key": "c" * 64, "cloud_run_id": 2303, "cloud_action_id": 503},
        "agent.chat",
        stale_runner,
    )
    assert recovered["continuity"]["status"] == "completed"
    assert stale_calls["count"] == 1

    with db() as connection:
        connection.execute(
            """
            INSERT INTO cloud_work_continuity(
                continuity_key,cloud_run_id,cloud_action_id,source_app_key,operation,status,
                attempt_count,updated_at
            ) VALUES (?,?,?,?,?,'queued',0,CURRENT_TIMESTAMP)
            """,
            ("d" * 64, 2304, 504, "vp3", "agent.chat"),
        )
    cancelled = work_continuity.cancel("d" * 64)
    assert cancelled["status"] == "cancelled"
    try:
        work_continuity.execute(
            {"key": "d" * 64, "cloud_run_id": 2304, "cloud_action_id": 504},
            "agent.chat",
            runner,
        )
        raise AssertionError("cancelled durable work executed")
    except work_continuity.WorkContinuityError:
        pass

    recent = work_continuity.list_recent(20)
    keys = {str(item.get("continuity_key") or "") for item in recent}
    assert {"a" * 64, "b" * 64, "c" * 64, "d" * 64}.issubset(keys)

print(f"HomeServer v2.3 work continuity: {len(checks)}/{len(checks)} static + runtime replay/recovery passed")
