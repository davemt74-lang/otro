"""A3 HomeServer Cloud mission relay — auth, scoping, projections and lifecycle."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="vp3-missions-cloud-a3-") as data:
    os.environ["HOMESERVER_DATA_DIR"]=data
    from app.database import db,initialize_database
    from app.services import agent_mission_cloud_v1 as cloud
    from app.services import agent_mission_runtime as runtime
    from app.services import providers,remote_bridge
    initialize_database()

    token="vp3-test-token-for-mission-a3-1"
    other_token="other-app-token-for-mission-a3"
    with db() as conn:
        ids={}
        for key,secret in [("vp3",token),("other",other_token)]:
            c=conn.execute("INSERT INTO paired_apps(app_key,name,token_hash) VALUES(?,?,?)",
                (key,key,hashlib.sha256(secret.encode()).hexdigest()))
            ids[key]=int(c.lastrowid)
            conn.execute("INSERT INTO app_permissions(paired_app_id,permission,allowed) VALUES(?,?,1)",
                (ids[key],"agent.chat"))

    providers.inference_status=lambda:{
        "available":True,"selected_provider":"openai","model":"fixture-model",
    }
    providers.generate=lambda messages,model_override=None:{"content":(
        json.dumps({"tasks":[
            {"role":"research","title":"One","objective":"First work","depends_on":[]},
            {"role":"research","title":"Two","objective":"Second work","depends_on":[]}
        ]}) if "mission planner" in str(messages[0].get("content","")).lower()
        else "TASK COMPLETE: "+str(messages[-1].get("content",""))),
        "provider":"openai","model":model_override or "fixture-model"}

    def call(action,body=None,bearer=token):
        return remote_bridge.dispatch_remote_request("agent.missions."+action,body or {},bearer)

    try:
        call("list",{},other_token)
        raise AssertionError("Non-VP3 app unexpectedly accessed missions.")
    except remote_bridge.RemoteBridgeError:
        pass

    first=call("create",{"thread_id":731,"objective":"Compare providers","request_id":"a3-request-00000001"})
    assert first["ok"],first
    mid=first["payload"]["mission"]["id"]
    assert first["payload"]["contract"]==cloud.CONTRACT
    again=call("create",{"thread_id":731,"objective":"Compare providers","request_id":"a3-request-00000001"})
    assert again["payload"]["mission"]["id"]==mid
    assert cloud.cloud_conversation(731,"another-request")==cloud.cloud_conversation(731,"a3-request-00000001")
    assert cloud.cloud_conversation(0,"another-request")!=cloud.cloud_conversation(0,"a3-request-00000001")

    wrong=call("create",{"thread_id":731,"objective":"Different objective","request_id":"a3-request-00000001"})
    assert wrong["status"]==409

    detail=call("get",{"mission_id":mid})
    assert detail["ok"] and len(detail["payload"]["mission"]["tasks"])==2
    assert "instructions" not in detail["payload"]["mission"]["tasks"][0]
    assert "lease_id" not in detail["payload"]["mission"]["tasks"][0]
    assert not detail["payload"]["mission"]["verified"]
    assert detail["payload"]["mission"]["read_only"]

    started=call("start",{"mission_id":mid})
    assert started["ok"],started
    deadline=time.monotonic()+12
    while time.monotonic()<deadline:
        current=call("get",{"mission_id":mid})["payload"]["mission"]
        if current["status"]=="completed":break
        time.sleep(.025)
    assert current["status"]=="completed",current
    assert all(t["status"]=="completed" for t in current["tasks"])

    recent=call("list",{"limit":4})
    assert recent["payload"]["items"][0]["id"]==mid
    events=call("events",{"mission_id":mid,"after":0})
    assert events["ok"] and events["payload"]["items"]
    assert all("id" in e and "kind" in e for e in events["payload"]["items"])

    missing=call("get",{"mission_id":"9b41d4fc-7b6c-4b6a-b7a1-70e567950daa"})
    assert missing["status"]==404

    # A4 supervises completed work but must not spawn another worker until
    # the paired Cloud owner explicitly approves the proposal.
    original_generate=providers.generate
    def cognitive_reply(messages,model_override=None):
        if "mission supervisor" in str(messages[0].get("content","")).lower():
            return {"content":json.dumps({
                "decision":"extend","rationale":"Independent verification is useful.",
                "tasks":[{"role":"reviewer","title":"Recheck findings",
                          "objective":"Review findings without tools",
                          "instructions":"Read-only validation","depends_on":[]}]
            })}
        return original_generate(messages,model_override=model_override)
    providers.generate=cognitive_reply
    proposal=call("cognition.evaluate",{"mission_id":mid})
    assert proposal["ok"] and proposal["payload"]["review"]["status"]=="proposed"
    assert len(call("get",{"mission_id":mid})["payload"]["mission"]["tasks"])==2
    review_id=proposal["payload"]["review"]["id"]
    invalid_decision=call("cognition.decide",{"mission_id":mid,"review_id":review_id,"approve":"yes"})
    assert invalid_decision["status"]==422
    approved=call("cognition.decide",{"mission_id":mid,"review_id":review_id,"approve":True})
    assert approved["ok"] and approved["payload"]["review"]["status"]=="applied"
    deadline=time.monotonic()+12
    while time.monotonic()<deadline:
        current=call("get",{"mission_id":mid})["payload"]["mission"]
        if current["status"]=="completed" and len(current["tasks"])==3:
            break
        time.sleep(.025)
    assert current["status"]=="completed" and len(current["tasks"])==3,current
    providers.generate=original_generate

    # Privacy changes must instantly redact mission text from Cloud responses.
    with db() as conn:
        cid=conn.execute("SELECT conversation_id FROM agent_missions_v1 WHERE id=?",(mid,)).fetchone()["conversation_id"]
        conn.execute("UPDATE conversation_context_settings SET cloud_allowed=0 WHERE conversation_id=?",(cid,))
    private=call("get",{"mission_id":mid})["payload"]["mission"]
    assert private["private"] is True
    assert private["objective"]=="Private HomeServer mission"
    assert not private["result"]
    assert all(not t.get("result") and not t.get("model") for t in private["tasks"])
    assert not private["events"]
    private_events=call("events",{"mission_id":mid,"after":0})["payload"]
    assert private_events["private"] and not private_events["items"]
    # Restore only in the test fixture; the service itself never changes privacy.
    with db() as conn:
        conn.execute("UPDATE conversation_context_settings SET cloud_allowed=1 WHERE conversation_id=?",(cid,))

    with db() as conn:
        conn.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='agent.chat'",(ids["vp3"],))
    try:
        call("list")
        raise AssertionError("Revocation did not block relay.")
    except remote_bridge.RemoteBridgeError:
        pass
    runtime.shutdown()
    print("MISSION_CLOUD_A3 PASS: canonical paired-app authorization, conversation mapping, idempotency, projection, execution, events, revocation")
