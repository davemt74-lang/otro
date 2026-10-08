"""A5B4 owner takeover acceptance: control exclusivity and exact-once GET search."""
import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix="vp3-owner-takeover-") as tmp:
    os.environ["HOMESERVER_DATA_DIR"]=tmp
    from app.database import db,initialize_database
    from app.services import agent_mission_runtime as runtime
    from app.services import agent_mission_browser as browser
    from app.services import agent_mission_live_browser as live
    from app.services import agent_mission_browser_actions as actions
    from app.services import agent_mission_browser_takeover as owner
    from app.services import agent_browser_live_actor as actor
    from app.services import agent_browser_policy as policy
    initialize_database()
    with db() as conn:
        pid=int(conn.execute("SELECT id FROM agents WHERE is_primary=1").fetchone()["id"])
        conn.execute("INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES(?,?,?,?)",
                     ("a5b4-owner",pid,"owner","Owner takeover acceptance"))
        conn.execute("INSERT OR IGNORE INTO conversation_context_settings(conversation_id) VALUES(?)",
                     ("a5b4-owner",))
    runtime._route=lambda source,conversation:("openai","fixture",False)
    runtime._infer=lambda *args:('{"index":0,"reason":"Useful"}',"openai","fixture")
    policy.pinned_public_ipv4=lambda h:"93.184.215.14"
    calls=[];running=set()
    control={"index":0,"kind":"fill","fingerprint":"a"*24,"label":"Search"}
    safe_form={"index":0,"fingerprint":"b"*24,"label":"Search","action":"https://example.com/search","method":"GET"}
    def frame(url="https://example.com/search"):
        return {"url":url,"page_title":"Sample","text_snapshot":"Public evidence",
                "image_base64":"","links":[{"url":"https://example.com/search","title":"Search"}],
                "controls":[control],"search_forms":[safe_form]}
    def opened(tid,origin,ip,url):
        calls.append(("open",url));running.add(tid);return frame()
    def executed(tid,kind,payload=""):
        calls.append((kind,payload))
        return frame("https://example.com/search?q=approved" if kind=="search_get" else "https://example.com/search")
    actor.open_session=opened
    actor.execute=executed
    actor.active=lambda tid:tid in running
    actor.close_session=lambda tid:running.discard(tid)
    actor.shutdown=lambda:running.clear()
    mission=runtime.create_mission("owner",conversation_id="a5b4-owner",
        objective="Research an open search page",client_request_id="a5b4-owner-001",
        parent_agent_id=pid,owner=True,tasks=[
            {"role":"research","title":"Search","objective":"Find public evidence","depends_on":[]}])
    mid=mission["id"];tid=mission["tasks"][0]["id"]
    browser.authorize("owner",mid,tid,"https://example.com/search")
    snap=live.start("owner",mid,tid)
    assert snap["owner_takeover"]["mode"]=="agent"
    assert owner.acquire("owner",mid,tid)["owner_takeover"]["mode"]=="owner"
    assert owner.status("owner",mid,tid)["mode"]=="owner"
    try:
        owner.acquire("owner",mid,tid)
        raise AssertionError("Duplicate owner lease")
    except runtime.MissionError as exc: assert exc.status_code==409
    for fn in (lambda: live.propose("owner",mid,tid),
               lambda: actions.suggest("owner",mid,tid)):
        try:
            fn();raise AssertionError("Agent used browser during owner takeover")
        except runtime.MissionError as exc: assert exc.status_code==409
    try:
        owner.status("app:unrelated",mid,tid)
        raise AssertionError("Cross-source takeover read")
    except runtime.MissionError as exc: assert exc.status_code==404
    before=len(calls)
    manual=owner.manual("owner",mid,tid,index=0,fingerprint="a"*24,kind="fill",value="private")
    assert manual["owner_takeover"]["actions_used"]==1
    assert len(calls)==before+1 and calls[-1][0]=="interact"
    with db() as conn:
        events=str(conn.execute("SELECT group_concat(detail_json) FROM agent_mission_events_v1 WHERE mission_id=?",
                                (mid,)).fetchone()[0] or "")
        assert "private" not in events,"Raw typed browser value leaked into event journal"
    preview=owner.review_search("owner",mid,tid,index=0,fingerprint="b"*24)
    proposal=preview["owner_takeover"]["pending_form"]
    assert proposal["method"]=="GET" and proposal["action"]=="https://example.com/search"
    before=len(calls)
    outcome=owner.submit_search("owner",mid,tid,proposal_id=proposal["id"])
    assert len(calls)==before+1 and calls[-1][0]=="search_get"
    assert outcome["current_url"].startswith("https://example.com/search?")
    assert outcome["owner_takeover"]["actions_used"]==2
    try:
        owner.submit_search("owner",mid,tid,proposal_id=proposal["id"])
        raise AssertionError("Search proposal replayed")
    except runtime.MissionError as exc: assert exc.status_code==409
    assert owner.release("owner",mid,tid)["owner_takeover"]["mode"]=="agent"
    assert live.propose("owner",mid,tid)["proposed_link"]["id"],"Agent did not regain control"
    owner.acquire("owner",mid,tid)
    with db() as conn:
        conn.execute("UPDATE agent_mission_browser_takeover_v4 SET expires_at=datetime('now','-1 minute') WHERE task_id=?",(tid,))
    assert owner.status("owner",mid,tid)["mode"]=="agent"
    try:
        owner.manual("owner",mid,tid,index=0,fingerprint="a"*24,kind="fill",value="late")
        raise AssertionError("Expired owner lease authorized a control")
    except runtime.MissionError as exc: assert exc.status_code==409
    # Revocation cleans up session grants and blocks reuse.
    browser.revoke("owner",mid,tid)
    try:
        owner.acquire("owner",mid,tid)
        raise AssertionError("Owner took over a revoked browser")
    except runtime.MissionError: pass
    runtime.shutdown()
    print("A5B4_TAKEOVER PASS: exclusive control, safe manual action, one-time GET review, no raw value journals, release, expiry and revocation")
