"""A4 acceptance: autonomous recommendation, explicit activation, bounds, privacy."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def until(probe, label, seconds=14):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        result = probe()
        if result:
            return result
        time.sleep(.025)
    raise AssertionError("Timed out waiting for " + label)


with tempfile.TemporaryDirectory(prefix="vp3-cognition-a4-") as temp:
    os.environ["HOMESERVER_DATA_DIR"] = temp
    from app.database import db, initialize_database
    from app.services import agent_mission_runtime as runtime
    from app.services import agent_mission_cognition as cognition

    initialize_database()
    with db() as conn:
        primary = conn.execute("SELECT id FROM agents WHERE is_primary=1").fetchone()
        assert primary
        pid = int(primary["id"])
        conn.execute("INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES(?,?,?,?)",
                     ("cognition-owner",pid,"owner","A4 review"))
    runtime._route = lambda source, conversation: ("openai", "fake", False)
    state = {"reviews": 0, "workers": 0}

    def model(source, conversation, messages):
        system = str(messages[0]["content"])
        if "mission supervisor" in system:
            state["reviews"] += 1
            if state["reviews"] == 1:
                return json.dumps({
                    "decision": "extend", "rationale": "Need an independent validation.",
                    "tasks": [{
                        "role": "independent reviewer", "title": "Validate claims",
                        "objective": "Review existing findings for uncertainty",
                        "instructions": "Read-only, no external tools", "depends_on": [],
                    }]
                }), "openai", "fake"
            return json.dumps({
                "decision": "complete", "rationale": "Evidence is sufficient.",
                "tasks": []
            }), "openai", "fake"
        state["workers"] += 1
        return "Read-only worker result: no external verification performed.", "openai", "fake"

    runtime._infer = model
    mission = runtime.create_mission(
        "owner", conversation_id="cognition-owner", objective="Evaluate three concepts",
        client_request_id="a4-mission-0001", parent_agent_id=pid, owner=True,
        tasks=[{"role":"research","title":"Initial","objective":"Compare concepts","depends_on":[]}],
    )
    mid = mission["id"]
    assert cognition.settings("owner",mid)["enabled"] is False
    assert cognition.configure("owner",mid,enabled=True)["enabled"] is True
    runtime.start_mission("owner",mid)
    until(lambda: cognition.latest("owner",mid) and cognition.latest("owner",mid)["status"]=="proposed",
          "automatic read-only proposal")
    pending = cognition.latest("owner",mid)
    assert pending["round"] == 1
    assert len(pending["tasks"]) == 1
    assert runtime.get_mission("owner",mid)["status"]=="completed"
    assert state["workers"]==1, "Proposing staffing must not start a worker."
    assert cognition.evaluate("owner",mid)["id"]==pending["id"], "Duplicate review must be idempotent."
    try:
        cognition.latest("app:unrelated",mid)
        raise AssertionError("Cross-source cognitive data leaked")
    except runtime.MissionError as exc:
        assert exc.status_code == 404
    try:
        cognition.decide("owner",mid,pending["id"],approve="yes")
        raise AssertionError("Non-boolean approval was accepted")
    except runtime.MissionError as exc:
        assert exc.status_code == 422

    approved=cognition.decide("owner",mid,pending["id"],approve=True)
    assert approved["review"]["status"]=="applied"
    assert cognition.decide("owner",mid,pending["id"],approve=True)["review"]["status"]=="applied"
    until(lambda: runtime.get_mission("owner",mid)["status"]=="completed"
          and len(runtime.get_mission("owner",mid)["tasks"])==2, "added specialist")
    until(lambda: cognition.latest("owner",mid) and cognition.latest("owner",mid)["round"]==2
          and cognition.latest("owner",mid)["status"]=="complete", "second review")
    complete=runtime.get_mission("owner",mid)
    assert len(complete["tasks"])==2
    assert state["workers"]==2, "Approved worker must execute once"
    assert all(t["status"]=="completed" for t in complete["tasks"])
    try:
        cognition.evaluate("owner",mid)
        raise AssertionError("Third review was accepted")
    except runtime.MissionError as exc:
        # complete is idempotent; the rule forbids a new round.
        assert cognition.latest("owner",mid)["round"]==2

    # A user may reject a proposal, but rejected workers never execute.
    state["reviews"]=0
    declined_mission=runtime.create_mission(
        "owner",conversation_id="cognition-owner",objective="Test supervisor decline",
        client_request_id="a4-mission-0002",parent_agent_id=pid,owner=True,
        tasks=[{"role":"analysis","title":"Initial","objective":"Initial insight","depends_on":[]}],
    )
    runtime.start_mission("owner",declined_mission["id"])
    until(lambda:runtime.get_mission("owner",declined_mission["id"])["status"]=="completed","second mission")
    manual=cognition.evaluate("owner",declined_mission["id"])
    assert manual["status"]=="proposed"
    count=state["workers"]
    cognition.decide("owner",declined_mission["id"],manual["id"],approve=False)
    assert len(runtime.get_mission("owner",declined_mission["id"])["tasks"])==1
    assert state["workers"]==count
    assert cognition.decide("owner",declined_mission["id"],manual["id"],approve=False)["review"]["status"]=="declined"
    try:
        cognition.decide("owner",declined_mission["id"],manual["id"],approve=True)
        raise AssertionError("Declined review was approved later")
    except runtime.MissionError as exc:
        assert exc.status_code==409

    # Invalid provider output cannot allocate workers or alter mission status.
    old_model=runtime._infer
    runtime._infer=lambda *args,**kwargs:(
        '{"decision":"extend","rationale":"bad","tasks":['+
        ','.join('{"role":"x","title":"x","objective":"x","depends_on":[]}' for _ in range(4))+']}',
        "openai","fake",
    )
    invalid=runtime.create_mission(
        "owner",conversation_id="cognition-owner",objective="Invalid worker proposal",
        client_request_id="a4-mission-0003",parent_agent_id=pid,owner=True,
        tasks=[{"role":"analysis","title":"Init","objective":"Init","depends_on":[]}],
    )
    # Worker response for this fixture is arbitrary but non-empty; completed pass is sufficient.
    runtime.start_mission("owner",invalid["id"])
    until(lambda:runtime.get_mission("owner",invalid["id"])["status"]=="completed","third mission")
    result=cognition.evaluate("owner",invalid["id"])
    assert result["status"]=="failed"
    assert len(runtime.get_mission("owner",invalid["id"])["tasks"])==1
    runtime._infer=old_model

    # Configuration locked once a mission has run.
    try:
        cognition.configure("owner",mid,enabled=False)
        raise AssertionError("Allowed retroactive cognitive setting change")
    except runtime.MissionError as exc:
        assert exc.status_code==409
    runtime.shutdown()
    print("MISSION_COGNITION_A4 PASS: optional supervisor, review bounds, idempotent consent, worker execution, decline, malformed plan, source isolation")
