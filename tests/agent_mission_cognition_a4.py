"""A4: reviewed adaptive staffing and provider-neutral supervisor regression."""
from __future__ import annotations
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def settled(runtime, source, mid, timeout=12):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        data=runtime.get_mission(source,mid)
        if data["status"] in ("completed","partial","failed"):
            return data
        time.sleep(.025)
    raise AssertionError("Mission never settled: "+str(data["status"]))

with tempfile.TemporaryDirectory(prefix="vp3-mission-a4-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir
    from app.database import db,initialize_database
    from app.services import agent_mission_runtime as runtime
    from app.services import agent_mission_cognition as a4
    from app.services import agent_mission_cloud_v1 as cloud
    from app.services import providers
    initialize_database()
    with db() as conn:
        parent=conn.execute("SELECT id FROM agents WHERE is_primary=1").fetchone()
        assert parent
        aid=int(parent["id"])
        conn.execute(
            "INSERT INTO paired_apps(app_key,name,token_hash) VALUES(?,?,?)",
            ("vp3","VP3 Cloud",hashlib.sha256(b"a4-paired-vp3").hexdigest()),
        )
        app_id=conn.execute("SELECT id FROM paired_apps WHERE app_key='vp3'").fetchone()["id"]
        conn.execute("INSERT INTO app_permissions(paired_app_id,permission,allowed) VALUES(?,?,1)",
                     (app_id,"agent.chat"))
        conn.execute("INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES(?,?,?,?)",
                     ("a4-vp3",aid,"app:vp3","A4 adaptive staffing"))

    providers.inference_status=lambda:{"available":True,"selected_provider":"anthropic","model":"claude-fixture"}
    counts={"supervisor":0,"worker":0}
    mode={"decision":"staff","invalid_dep":False}
    def generate(messages,model_override=None):
        system=str(messages[0]["content"]).lower()
        if "mission supervisor" in system:
            counts["supervisor"]+=1
            evidence=json.loads(messages[-1]["content"].split("\n",1)[-1])
            deps=[e["task_id"] for e in evidence["successful_workers"][:1]]
            if mode["invalid_dep"]:deps=["wrong-task-reference"]
            payload={
                "decision":mode["decision"],"reason":"Additional independent cross-check is useful.",
                "confidence":81,
                "tasks":[] if mode["decision"]=="finish" else [
                    {"role":"auditor","title":"Cross-check","objective":"Compare unresolved assumptions",
                     "instructions":"Only reason over available authorized context.",
                     "depends_on":deps},
                    {"role":"critic","title":"Independent review","objective":"Look for contradiction",
                     "instructions":"Provide uncertainty clearly.","depends_on":[]},
                ],
            }
            return {"content":json.dumps(payload)}
        counts["worker"]+=1
        return {"content":"Unverified read-only specialist analysis "+str(counts["worker"])}
    providers.generate=generate

    first=runtime.create_mission(
        "app:vp3",conversation_id="a4-vp3",parent_agent_id=aid,owner=False,
        objective="Compare strategies",client_request_id="a4-mission-1",
        tasks=[
            {"role":"analyst","title":"A","objective":"First pass","depends_on":[]},
            {"role":"analyst","title":"B","objective":"Independent first pass","depends_on":[]},
        ],
    )
    mid=first["id"]
    assert len(first["tasks"])==2
    runtime.start_mission("app:vp3",mid)
    original=settled(runtime,"app:vp3",mid)
    assert original["status"]=="completed"
    old_tasks={t["id"]:t["result"] for t in original["tasks"]}
    assert counts["worker"]==2
    p=a4.propose("app:vp3",mid,"a4-review-00001")
    assert p["status"]=="proposed" and len(p["tasks"])==2 and p["confidence"]==81
    assert len(runtime.get_mission("app:vp3",mid)["tasks"])==2, "Unapproved proposal started work"
    assert a4.propose("app:vp3",mid,"a4-review-00001")["id"]==p["id"],"Proposal not idempotent"
    assert counts["supervisor"]==1
    try:
        a4.propose("app:vp3",mid,"a4-review-00002")
        raise AssertionError("Allowed duplicate pending supervisor decisions")
    except runtime.MissionError as e:
        assert e.status_code==409
    try:
        a4.list_decisions("app:unrelated",mid)
        raise AssertionError("Cross-source supervisor decision exposure")
    except runtime.MissionError as e:
        assert e.status_code==404
    rejected=a4.decide("app:vp3",mid,p["id"],approve=False)
    assert rejected["status"]=="rejected"
    assert len(runtime.get_mission("app:vp3",mid)["tasks"])==2
    try:
        a4.decide("app:vp3",mid,p["id"],approve=True)
        raise AssertionError("Reversed a rejected proposal")
    except runtime.MissionError as e:
        assert e.status_code==409

    mode["invalid_dep"]=True
    try:
        a4.propose("app:vp3",mid,"a4-invalid-dep")
        raise AssertionError("Supervisor dependency escaped completed-task scope")
    except runtime.MissionError as e:
        assert e.status_code==422
    mode["invalid_dep"]=False

    approved_plan=a4.propose("app:vp3",mid,"a4-review-00003")
    assert len(a4.list_decisions("app:vp3",mid)["items"])==2
    approved=a4.decide("app:vp3",mid,approved_plan["id"],approve=True)
    assert approved["status"]=="approved"
    after=settled(runtime,"app:vp3",mid)
    assert len(after["tasks"])==4 and after["status"]=="completed"
    assert {t["id"]:t["result"] for t in after["tasks"][:2]}==old_tasks
    assert counts["worker"]==4
    assert a4.decide("app:vp3",mid,approved_plan["id"],approve=True)["id"]==approved_plan["id"]
    assert len(runtime.get_mission("app:vp3",mid)["tasks"])==4, "Duplicate approval created duplicate workers"

    mode["decision"]="finish"
    finished=a4.propose("app:vp3",mid,"a4-finish-review")
    assert finished["status"]=="no_changes" and not finished["tasks"]
    assert runtime.get_mission("app:vp3",mid)["status"]=="completed"
    mode["decision"]="staff"
    second=a4.propose("app:vp3",mid,"a4-review-round-two")
    assert second["status"]=="proposed"
    # A pending plan becomes stale if prior work changes, and cannot be applied.
    with db() as conn:
        conn.execute("UPDATE agent_missions_v1 SET status='running' WHERE id=?",(mid,))
    try:
        a4.decide("app:vp3",mid,second["id"],approve=True)
        raise AssertionError("Accepted staffing against changed mission")
    except runtime.MissionError as e:
        assert e.status_code==409
    with db() as conn:
        conn.execute("UPDATE agent_missions_v1 SET status='completed' WHERE id=?",(mid,))
    assert a4.decide("app:vp3",mid,second["id"],approve=True)["status"]=="approved"
    second_result=settled(runtime,"app:vp3",mid)
    assert len(second_result["tasks"])==6
    try:
        a4.propose("app:vp3",mid,"a4-review-third-round")
        raise AssertionError("Staffing round budget exceeded")
    except runtime.MissionError as e:
        assert e.status_code==409

    # In Cloud, local-only mode must hide not just results but supervisor reasoning.
    with db() as conn:
        conn.execute("UPDATE conversation_context_settings SET cloud_allowed=0 WHERE conversation_id=?",
                     ("a4-vp3",))
    hidden=cloud.execute("decisions",{"mission_id":mid})
    assert hidden["items"] and all(x["private"] for x in hidden["items"])
    assert all(not x["reason"] and not x["tasks"] for x in hidden["items"])
    with db() as conn:
        conn.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='agent.chat'",
                     (app_id,))
    try:
        a4.propose("app:vp3",mid,"a4-revoked-review")
        raise AssertionError("Revoked paired app planned a new mission")
    except runtime.MissionError:
        pass
    runtime.shutdown()
    print("MISSION_A4 PASS: Claude-compatible supervisor, idempotence, explicit approval, DAG, immutable completed work, adaptive rounds, stale review, source scope, privacy")
