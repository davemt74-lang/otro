"""Section A2 state-machine and security regression, no external model calls."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def wait_state(runtime, mid, wanted, limit=12):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        value = runtime.get_mission("owner", mid)
        if value["status"] == wanted:
            return value
        time.sleep(0.02)
    raise AssertionError(f"Expected {wanted}; current: {runtime.get_mission('owner', mid)}")


with tempfile.TemporaryDirectory(prefix="vp3-missions-a2-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir
    from app.database import db, initialize_database
    from app.services import agent_mission_runtime as runtime
    from app.services import agent_mission_control as control

    initialize_database()
    with db() as conn:
        primary = conn.execute("SELECT id FROM agents WHERE is_primary=1").fetchone()
        assert primary, "Missing primary Agent"
        pid = int(primary["id"])
        conn.execute(
            "INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES(?,?,?,?)",
            ("a2-owner-thread", pid, "owner", "Mission control A2"),
        )

    runtime._route = lambda source, conversation: ("openai", "fixture", False)
    entered = threading.Event()
    release = threading.Event()
    calls = {"slow": 0, "flaky": 0}

    def fake_infer(source, conversation, messages):
        objective = str(messages[-1]["content"])
        if objective.startswith("slow"):
            calls["slow"] += 1
            if calls["slow"] == 1:
                entered.set()
                assert release.wait(8), "Timed out in paused model fixture."
        if objective.startswith("flaky"):
            calls["flaky"] += 1
            if calls["flaky"] == 1:
                raise RuntimeError("Injected transient provider error")
        return "success: " + objective, "openai", "fixture"

    runtime._infer = fake_infer

    definition = [
        {"role": "analysis", "title": "A", "objective": "slow", "depends_on": []},
        {"role": "synthesis", "title": "B", "objective": "after A", "depends_on": [0]},
    ]
    mission = runtime.create_mission(
        "owner", conversation_id="a2-owner-thread", parent_agent_id=pid, owner=True,
        objective="Audit and summarize", client_request_id="pause-001", tasks=definition,
    )
    mid = mission["id"]
    assert len(mission["tasks"]) == 2

    try:
        runtime.create_mission(
            "owner", conversation_id="a2-owner-thread", parent_agent_id=pid, owner=True,
            objective="Changed objective", client_request_id="pause-001", tasks=definition,
        )
        raise AssertionError("Conflicting idempotency key accepted.")
    except runtime.MissionError as exc:
        assert exc.status_code == 409

    try:
        runtime.create_mission(
            "owner", conversation_id="not-a-conversation", parent_agent_id=pid,
            owner=True, objective="Invalid", client_request_id="bad-conversation",
            tasks=definition,
        )
        raise AssertionError("Invalid conversation accepted.")
    except runtime.MissionError as exc:
        assert exc.status_code == 404

    runtime.start_mission("owner", mid)
    assert entered.wait(6), "First worker did not start"
    paused = control.pause("owner", mid)
    assert paused["status"] == "waiting_review"
    assert paused["tasks"][0]["status"] == "interrupted"
    assert paused["tasks"][1]["status"] == "queued"
    release.set()

    try:
        control.resume("owner", mid)
        raise AssertionError("Replayed interrupted model call without approval.")
    except runtime.MissionError as exc:
        assert exc.status_code == 409

    assert control.pause("owner", mid)["status"] == "waiting_review"  # idempotent
    assert runtime.get_mission("owner", mid)["tasks"][0]["status"] == "interrupted"
    resumed = control.resume("owner", mid, allow_reexecution=True)
    assert resumed["status"] in ("running", "completed")
    result = wait_state(runtime, mid, "completed")
    assert all(t["status"] == "completed" for t in result["tasks"]), result
    assert calls["slow"] == 2
    assert result["tasks"][0]["attempt"] == 2

    first_page = control.events("owner", mid, after=0, limit=2)
    assert len(first_page["items"]) == 2
    assert first_page["has_more"] is True
    next_page = control.events("owner", mid, after=first_page["next_cursor"], limit=100)
    assert next_page["items"] and next_page["items"][0]["id"] > first_page["next_cursor"]
    ids = [e["id"] for e in first_page["items"] + next_page["items"]]
    assert len(ids) == len(set(ids))
    try:
        control.events("app:other", mid)
        raise AssertionError("Cross-app event leakage.")
    except runtime.MissionError as exc:
        assert exc.status_code == 404

    retry_plan = [
        {"role": "analysis", "title": "Flaky", "objective": "flaky", "depends_on": []},
        {"role": "synthesis", "title": "Dependent", "objective": "after flaky", "depends_on": [0]},
    ]
    broken = runtime.create_mission(
        "owner", conversation_id="a2-owner-thread", parent_agent_id=pid, owner=True,
        objective="Recover worker failure", client_request_id="retry-001", tasks=retry_plan,
    )
    runtime.start_mission("owner", broken["id"])
    failed = wait_state(runtime, broken["id"], "failed")
    assert failed["tasks"][0]["status"] == "failed"
    assert failed["tasks"][1]["status"] == "failed"
    assert failed["tasks"][1]["error"] == "Dependency failed"
    try:
        control.retry("owner", broken["id"], failed["tasks"][1]["id"])
        raise AssertionError("Blocked dependent retried without prerequisite.")
    except runtime.MissionError as exc:
        assert exc.status_code == 409

    control.retry("owner", broken["id"], failed["tasks"][0]["id"])
    recovered = wait_state(runtime, broken["id"], "completed")
    assert all(t["status"] == "completed" for t in recovered["tasks"]), recovered
    assert calls["flaky"] == 2
    try:
        control.retry("owner", broken["id"], recovered["tasks"][0]["id"])
        raise AssertionError("Successful task was allowed to run again.")
    except runtime.MissionError as exc:
        assert exc.status_code == 409

    # Attempt caps must prevent unlimited repeat model calls.
    capped = runtime.create_mission(
        "owner", conversation_id="a2-owner-thread", parent_agent_id=pid, owner=True,
        objective="Cap attempts", client_request_id="capped-001",
        tasks=[{"role": "audit", "title": "Cap", "objective": "unused", "depends_on": []}],
    )
    with db() as conn:
        conn.execute("UPDATE agent_missions_v1 SET status='failed' WHERE id=?", (capped["id"],))
        conn.execute("UPDATE agent_mission_tasks_v1 SET status='failed',attempt=3 "
                     "WHERE mission_id=?", (capped["id"],))
    try:
        control.retry("owner", capped["id"], capped["tasks"][0]["id"])
        raise AssertionError("Retry cap was not enforced.")
    except runtime.MissionError as exc:
        assert exc.status_code == 409

    runtime.shutdown()
    print("AGENT_MISSION_A2 PASS: pause, explicit replay, event cursors, idempotency, isolation, dependency repair, bounded retry")
