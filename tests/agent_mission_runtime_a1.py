from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="vp3-mission-runtime-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db, initialize_database
    from app.services import agent_mission_runtime as runtime

    initialize_database()
    from app.services import agent_routing

    # Authoritative owner path: no pre-created specialist personas required.
    with db() as conn:
        primary = conn.execute("SELECT id FROM agents WHERE is_primary=1").fetchone()
        assert primary is not None
        parent_id = int(primary["id"])
        conn.execute(
            "INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES(?,?,?,?)",
            ("mission-test-thread", parent_id, "owner", "Mission regression"),
        )

    runtime._route = lambda source, conversation: ("openai", "fixture-model", False)
    gate = threading.Barrier(3, timeout=10)
    counts = {"running": 0, "max": 0}
    lock = threading.Lock()

    def fake_infer(source, conversation, messages):
        if str(messages[-1]["content"]).startswith("Goal"):
            return json.dumps({"tasks": [
                {"role": "research", "title": "A", "objective": "A", "depends_on": []},
                {"role": "research", "title": "B", "objective": "B", "depends_on": []},
                {"role": "synthesis", "title": "C", "objective": "C", "depends_on": [0, 1]}
            ]}), "openai", "fixture-model"
        with lock:
            counts["running"] += 1
            counts["max"] = max(counts["max"], counts["running"])
        if messages[-1]["content"] in ("A", "B"):
            gate.wait()
        with lock:
            counts["running"] -= 1
        return "RESULT " + messages[-1]["content"], "openai", "fixture-model"

    runtime._infer = fake_infer
    mission = runtime.create_mission(
        "owner", conversation_id="mission-test-thread", objective="Goal: parallel tasks",
        client_request_id="same-key", parent_agent_id=parent_id, owner=True,
    )
    mid = mission["id"]
    assert len(mission["tasks"]) == 3
    assert len({t["worker_id"] for t in mission["tasks"]}) == 3
    assert runtime.create_mission(
        "owner", conversation_id="mission-test-thread", objective="Goal: parallel tasks",
        client_request_id="same-key", parent_agent_id=parent_id, owner=True,
    )["id"] == mid
    try:
        runtime.get_mission("app:unauthorized", mid)
        assert False, "Source isolation failed"
    except runtime.MissionError as exc:
        assert exc.status_code == 404

    runtime.start_mission("owner", mid)
    gate.wait()
    runtime.shutdown()
    finished = runtime.get_mission("owner", mid)
    assert finished["status"] == "completed", finished
    assert all(t["status"] == "completed" for t in finished["tasks"])
    assert counts["max"] >= 2, "Independent tasks must actually overlap."
    assert finished["verified"] is False
    assert finished["tools_enabled"] is False

    # Cancellation is terminal; stale task completions cannot reopen the mission.
    second = runtime.create_mission(
        "owner", conversation_id="mission-test-thread", objective="Cancel me",
        client_request_id="second", parent_agent_id=parent_id, owner=True,
        tasks=[{"role": "research", "title": "Wait", "objective": "Wait", "depends_on": []}],
    )
    runtime.cancel_mission("owner", second["id"])
    assert runtime.get_mission("owner", second["id"])["status"] == "cancelled"

    try:
        runtime.validate_plan([
            {"role": "a", "title": "a", "objective": "a", "depends_on": [1]},
            {"role": "b", "title": "b", "objective": "b", "depends_on": []},
        ])
        assert False, "Forward dependencies must be rejected."
    except runtime.MissionError:
        pass

    with db() as conn:
        conn.execute("UPDATE agent_missions_v1 SET status='running' WHERE id=?", (second["id"],))
        conn.execute("UPDATE agent_mission_tasks_v1 SET status='running' WHERE mission_id=?", (second["id"],))
    assert runtime.recover_interrupted() == 1
    recovered = runtime.get_mission("owner", second["id"])
    assert recovered["status"] == "waiting_review"
    assert recovered["tasks"][0]["status"] == "interrupted"
    print("MISSION_RUNTIME_A1 PASS: idempotency, isolation, parallelism, DAG, cancellation, recovery")
