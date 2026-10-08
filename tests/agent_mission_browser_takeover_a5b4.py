"""A5B4 owner takeover acceptance: control exclusivity and exact-once GET search."""
import json, os, sys, tempfile, threading
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
        if kind=='review_search':
            return {'id':payload['review_id'],'index':0,'fingerprint':'b'*24,
                    'action':'https://example.com/search','method':'GET',
                    'payload_hash':'c'*64,'query':'approved',
                    'destination':'https://example.com/search?q=approved'}
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
        events=str(conn.execute("SELECT group_concat(metadata_json) FROM agent_mission_events_v1 WHERE mission_id=?",
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
    before=len(calls)
    owner.submit_search("owner",mid,tid,proposal_id=proposal["id"])
    assert len(calls)==before,'Search retry executed twice'
    assert owner.release("owner",mid,tid)["owner_takeover"]["mode"]=="agent"
    assert live.propose("owner",mid,tid)["proposed_link"]["id"],"Agent did not regain control"
    # Takeover while the provider is responding must invalidate the pending
    # page revision, not merely clear a proposal that has not arrived yet.
    original_infer=runtime._infer
    def late_proposal(*args):
        owner.acquire('owner',mid,tid)
        return '{"index":0,"reason":"Useful"}','openai','fixture'
    runtime._infer=late_proposal
    for propose in (live.propose,actions.suggest):
        try:
            propose('owner',mid,tid)
            raise AssertionError('Late proposal survived takeover')
        except runtime.MissionError as exc: assert exc.status_code==409
        with db() as conn:
            assert conn.execute('SELECT pending_proposal_json FROM agent_mission_live_browser_v2 WHERE task_id=?',(tid,)).fetchone()[0]=='{}'
            assert conn.execute('SELECT pending_action_json FROM agent_mission_browser_controls_v3 WHERE task_id=?',(tid,)).fetchone()[0]=='{}'
        owner.release('owner',mid,tid)
    runtime._infer=original_infer
    with db() as conn: conn.execute("UPDATE agent_mission_live_browser_v2 SET status='navigating' WHERE task_id=?",(tid,))
    try:
        owner.acquire('owner',mid,tid)
        raise AssertionError('Owner acquired an in-flight action')
    except runtime.MissionError as exc: assert exc.status_code==409
    with db() as conn: conn.execute("UPDATE agent_mission_live_browser_v2 SET status='live' WHERE task_id=?",(tid,))
    owner.acquire('owner',mid,tid)
    operation_id='12345678-1234-4234-8234-123456789012'
    before=len(calls)
    owner.manual('owner',mid,tid,index=0,fingerprint='a'*24,kind='fill',value='private',request_id=operation_id)
    owner.manual('owner',mid,tid,index=0,fingerprint='a'*24,kind='fill',value='private',request_id=operation_id)
    assert len(calls)==before+1,'Duplicate manual request reached actor'
    try:
        owner.manual('owner',mid,tid,index=0,fingerprint='a'*24,kind='fill',value='different',request_id=operation_id)
        raise AssertionError('Receipt permitted changed payload')
    except runtime.MissionError as exc: assert exc.status_code==409
    reviewed=owner.review_search('owner',mid,tid,index=0,fingerprint='b'*24)
    reviewed_id=reviewed['owner_takeover']['pending_form']['id']
    with db() as conn: conn.execute('UPDATE agent_mission_browser_v1 SET visit_count=5 WHERE task_id=?',(tid,))
    before=len(calls)
    try:
        owner.submit_search('owner',mid,tid,proposal_id=reviewed_id)
        raise AssertionError('Exhausted budget reached actor')
    except runtime.MissionError as exc: assert exc.status_code==409
    assert len(calls)==before,'Search dispatched beyond budget'
    with db() as conn: conn.execute('UPDATE agent_mission_browser_v1 SET visit_count=2 WHERE task_id=?',(tid,))
    old_lease=owner.status('owner',mid,tid)['lease_id']
    owner.release('owner',mid,tid,lease_id=old_lease)
    owner.acquire('owner',mid,tid)
    try:
        owner.release('owner',mid,tid,lease_id=old_lease)
        raise AssertionError('Stale release ended new lease')
    except runtime.MissionError as exc: assert exc.status_code==409
    owner.release('owner',mid,tid)
    owner.acquire('owner',mid,tid)
    entered=threading.Event();finish=threading.Event();errors=[]
    def blocked_actor(*args):
        entered.set()
        assert finish.wait(10),'Fixture never released browser command'
        return executed(*args)
    actor.execute=blocked_actor
    rid='12345678-1234-4234-8234-123456789013'
    def first_operation():
        try:owner.manual('owner',mid,tid,index=0,fingerprint='a'*24,kind='fill',value='threaded',request_id=rid)
        except Exception as exc:errors.append(exc)
    before=len(calls);worker=threading.Thread(target=first_operation);worker.start()
    assert entered.wait(10)
    try:
        for attempt in (lambda:owner.acquire('owner',mid,tid),lambda:owner.release('owner',mid,tid),
                        lambda:owner.manual('owner',mid,tid,index=0,fingerprint='a'*24,kind='fill',value='threaded',request_id=rid)):
            try:attempt();raise AssertionError('In-flight operation allowed duplicate dispatch or handover')
            except runtime.MissionError as exc:assert exc.status_code==409
    finally:finish.set();worker.join(10)
    assert not worker.is_alive() and not errors and len(calls)==before+1,errors
    actor.execute=executed
    owner.release('owner',mid,tid)
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
