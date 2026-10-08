"""A5B3 actor-independent approval, source-isolation and safe-value tests."""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

with tempfile.TemporaryDirectory(prefix="vp3-dom-a5b3-") as directory:
    os.environ["HOMESERVER_DATA_DIR"]=directory
    from app.database import db, initialize_database
    from app.services import agent_mission_runtime as mission
    from app.services import agent_mission_browser as grant
    from app.services import agent_mission_live_browser as live
    from app.services import agent_mission_browser_actions as actions
    from app.services import agent_browser_live_actor as actor
    from app.services import agent_browser_policy as url_policy

    initialize_database()
    with db() as conn:
        pid=int(conn.execute("SELECT id FROM agents WHERE is_primary=1").fetchone()["id"])
        conn.execute("INSERT INTO conversations(id,agent_id,source_app_key,title) "
                     "VALUES(?,?,?,?)",("a5b3-owner",pid,"owner","DOM actions"))
        conn.execute("INSERT OR IGNORE INTO conversation_context_settings(conversation_id) "
                     "VALUES(?)",("a5b3-owner",))
    mission._route=lambda source,cid:("openai","fixture",False)
    decision={"index":0,"reason":"A topic would clarify the research"}
    model_prompts=[]
    def fake_model(source,conversation,messages):
        model_prompts.append(messages)
        return json.dumps(decision),"openai","fixture"
    mission._infer=fake_model
    url_policy.pinned_public_ipv4=lambda host:"93.184.215.14"
    def create(n):
        return mission.create_mission(
            "owner",conversation_id="a5b3-owner",parent_agent_id=pid,owner=True,
            objective="Analyze published information",client_request_id="dom-"+str(n),
            tasks=[{"role":"analyst","title":"Research","objective":"Review the form",
                    "depends_on":[]}])
    first=create(1)
    mid,tid=first["id"],first["tasks"][0]["id"]
    grant.authorize("owner",mid,tid,"https://example.com/reports")
    opened=set()
    applied=[]
    controls=[{"index":0,"kind":"fill","label":"Search topic",
               "fingerprint":"d0f0b0da8cbbcd0022dd99aa"},
              {"index":2,"kind":"check","label":"Acknowledged",
               "fingerprint":"aabbccddeeff001122334455"},
              {"index":3,"kind":"select","label":"Category",
               "fingerprint":"aabbccddeeff001122334456",
               "options":[{"index":0,"label":"Science"},{"index":1,"label":"Art"}]}]
    def frame():
        return {"url":"https://example.com/reports","page_title":"Report",
                "text_snapshot":"Read-only public website.",
                "image_base64":"/9j/"+"a"*120,
                "links":[{"url":"https://example.com/next","title":"Next"}],
                "controls":controls}
    actor.open_session=lambda tid,origin,ip,url:(opened.add(tid),frame())[1]
    actor.active=lambda tid:tid in opened
    actor.close_session=lambda tid:opened.discard(tid)
    actor.shutdown=lambda :opened.clear()
    def fake_exec(tid,command,value=""):
        if command=="interact":
            assert set(value)=={"index","kind","fingerprint","value"}
            applied.append(value)
        return frame()
    actor.execute=fake_exec
    state=live.start("owner",mid,tid)
    assert state["status"]=="live" and state["controls"]==controls
    assert state["actions_used"]==0

    proposed=actions.suggest("owner",mid,tid)
    pending=proposed["proposed_action"]
    assert pending["kind"]=="fill" and pending["label"]=="Search topic"
    assert not applied, "Agent recommendation must never touch the browser."
    assert "CustomerSecret" not in json.dumps(model_prompts)
    for source in ["app:unrelated"]:
        try:
            actions.suggest(source,mid,tid)
            raise AssertionError("Cross-app proposal accepted")
        except mission.MissionError as exc:
            assert exc.status_code==404
    try:
        actions.approve("owner",mid,tid,pending["id"],value=123)
        raise AssertionError("Non-text field input accepted")
    except mission.MissionError as exc:
        assert exc.status_code==422
    text="CustomerSecretPrivateInput"
    outcome=actions.approve("owner",mid,tid,pending["id"],value=text)
    assert outcome["actions_used"]==1
    assert outcome["proposed_action"]=={}
    assert outcome["revision"]==proposed["revision"]+1
    assert applied[0]["value"]==text and applied[0]["kind"]=="fill"
    try:
        actions.approve("owner",mid,tid,pending["id"],value=text)
        raise AssertionError("Replayed action approval accepted")
    except mission.MissionError as exc:
        assert exc.status_code==409
    with db() as conn:
        rows=conn.execute("SELECT * FROM agent_mission_browser_controls_v3 "
                          "WHERE task_id=?",(tid,)).fetchall()
        assert len(rows)==1
        assert text not in str(dict(rows[0]))
        journal=conn.execute("SELECT * FROM agent_mission_events_v1 "
                             "WHERE mission_id=?",(mid,)).fetchall()
        assert text not in str([dict(x) for x in journal])

    decision={"index":99,"reason":"outside list"}
    try:
        actions.suggest("owner",mid,tid)
        raise AssertionError("Model-selected unknown control accepted")
    except mission.MissionError as exc:
        assert exc.status_code==502

    decision={"index":0,"reason":"More research"}
    again=actions.suggest("owner",mid,tid)["proposed_action"]
    def revoking_exec(tid,command,value=""):
        grant.revoke("owner",mid,tid)
        return frame()
    actor.execute=revoking_exec
    try:
        actions.approve("owner",mid,tid,again["id"],value="late")
        raise AssertionError("Late DOM action restored a revoked grant")
    except mission.MissionError as exc:
        assert exc.status_code in (403,409)
    assert not actor.active(tid)
    state=live.get("owner",mid,tid)
    assert state["status"]=="stopped" and not state.get("image_base64")
    mission.shutdown()
    print("MISSION_A5B3_APPROVAL PASS: no implicit action, typed owner value, no stored secrets, source isolation, stale decision, invalid model index, revocation race")
