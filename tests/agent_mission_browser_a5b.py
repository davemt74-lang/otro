"""A5B: test source isolation, public URL policy, consent, expiry and leases."""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(prefix="vp3-browser-a5b-") as data:
    os.environ["HOMESERVER_DATA_DIR"]=data
    from app.database import db,initialize_database
    from app.services import agent_browser_policy as policy
    from app.services import agent_browser_capture as capture_runner
    from app.services import agent_mission_browser as browser
    from app.services import agent_mission_runtime as runtime

    initialize_database()
    with db() as conn:
        pid=int(conn.execute("SELECT id FROM agents WHERE is_primary=1").fetchone()["id"])
        conn.execute("INSERT INTO conversations(id,agent_id,source_app_key,title) "
                     "VALUES(?,?,?,?)",("a5b-owner",pid,"owner","Browser worker acceptance"))
    runtime._route=lambda source,conversation:("openai","fixture",False)
    runtime._infer=lambda source,conversation,messages:("Review complete","openai","fixture")

    def make(n):
        return runtime.create_mission(
            "owner",conversation_id="a5b-owner",parent_agent_id=pid,owner=True,
            objective="Review public evidence",client_request_id="browser-"+str(n),
            tasks=[{"role":"reviewer","title":"Site evidence","objective":"Review page evidence",
                    "depends_on":[]}])
    first=make(1)
    mid=first["id"]
    tid=first["tasks"][0]["id"]
    for url in (
        "http://example.com", "https://localhost/", "https://127.0.0.1",
        "https://10.1.1.1", "https://intranet.local", "https://example.com:8080",
        "https://user:pass@example.com", "file:///etc/passwd",
        "javascript:alert(1)", "https://example.com/#fragment",
    ):
        try:
            policy.parse_url(url)
            raise AssertionError("Forbidden URL permitted: "+url)
        except runtime.MissionError:
            pass

    original_dns=policy.pinned_public_ipv4
    policy.pinned_public_ipv4=lambda host:"93.184.215.14"
    fake_pages=[]
    def fake_capture(url,origin,ip):
        fake_pages.append((url,origin,ip))
        return {"url":url,"title":"Test evidence","text":"A public research excerpt.",
                "image_base64":"/9j/"+"a"*180}
    capture_runner.capture=fake_capture
    assert browser.inspect("owner",mid,tid) is None
    approved=browser.authorize("owner",mid,tid,"https://example.com/source")
    assert approved["status"]=="approved" and approved["approved_origin"]=="https://example.com"
    assert approved["visit_count"]==0
    try:
        browser.authorize("owner",mid,tid,"https://example.com/source")
        raise AssertionError("Re-granted an active worker browser")
    except runtime.MissionError as exc:
        assert exc.status_code==409
    try:
        browser.authorize("app:unrelated",mid,tid,"https://example.com/source")
        raise AssertionError("Cross-source browser grant permitted")
    except runtime.MissionError as exc:
        assert exc.status_code==404
    for target in ("https://other.example.org/path","https://example.com.evil.org/"):
        try:
            browser.capture("owner",mid,tid,target)
            raise AssertionError("Escaped approved origin: "+target)
        except runtime.MissionError as exc:
            assert exc.status_code==403

    screenshot=browser.capture("owner",mid,tid,"https://example.com/path")
    assert screenshot["visit_count"]==1
    assert screenshot["page_title"]=="Test evidence"
    assert screenshot["image_base64"].startswith("/9j/")
    assert fake_pages==[("https://example.com/path","https://example.com","93.184.215.14")]
    assert "public research excerpt" in browser.evidence_for_worker("owner",mid,tid)
    assert len(fake_pages)==1,"Existing capture should be reused by model"
    assert browser.inspect("owner",mid,tid,image=False).get("image_base64") is None

    # Failed capture consumes a budget slot, but never creates false evidence.
    def failed_capture(*args):
        raise runtime.MissionError("Injected browser failure.",503)
    capture_runner.capture=failed_capture
    try:
        browser.capture("owner",mid,tid,"https://example.com/path2")
        raise AssertionError("Browser failure reported as success")
    except runtime.MissionError as exc:
        assert exc.status_code==503
    assert browser.inspect("owner",mid,tid)["status"]=="approved"
    assert browser.inspect("owner",mid,tid)["visit_count"]==2
    capture_runner.capture=fake_capture

    # Revoke in the middle of a capture; late screenshot must not reappear.
    def revoke_while_capturing(url,origin,ip):
        browser.revoke("owner",mid,tid)
        return fake_capture(url,origin,ip)
    capture_runner.capture=revoke_while_capturing
    try:
        browser.capture("owner",mid,tid)
        raise AssertionError("Stale capture committed after revocation")
    except runtime.MissionError as exc:
        assert exc.status_code==409
    closed=browser.inspect("owner",mid,tid,image=True)
    assert closed["status"]=="closed" and not closed.get("image_base64")
    capture_runner.capture=fake_capture
    # Closed grants can be reauthorized, but only for workers not yet started.
    renewed=browser.authorize("owner",mid,tid,"https://example.com/renewed")
    assert renewed["visit_count"]==0
    with db() as conn:
        conn.execute("UPDATE agent_mission_browser_v1 SET expires_at=datetime('now','-1 minute') "
                     "WHERE task_id=?",(tid,))
    expired=browser.inspect("owner",mid,tid,image=True)
    assert expired["status"]=="closed" and not expired.get("image_base64")
    browser.authorize("owner",mid,tid,"https://example.com/reapproved")

    # Privacy changes must deny web work even with a previously approved grant.
    with db() as conn:
        conn.execute("UPDATE conversation_context_settings SET cloud_allowed=0 "
                     "WHERE conversation_id=?",("a5b-owner",))
    try:
        browser.capture("owner",mid,tid)
        raise AssertionError("Local-only mission used external browser")
    except runtime.MissionError as exc:
        assert exc.status_code==403
    assert browser.revoke("owner",mid,tid)["status"]=="closed"
    with db() as conn:
        conn.execute("UPDATE conversation_context_settings SET cloud_allowed=1 "
                     "WHERE conversation_id=?",("a5b-owner",))
    again=make(2)
    next_tid=again["tasks"][0]["id"]
    browser.authorize("owner",again["id"],next_tid,"https://example.com/another")
    with db() as conn:
        conn.execute("UPDATE agent_mission_browser_v1 SET status='capturing',"
                     "capture_token='interrupted' WHERE task_id=?",(next_tid,))
    assert browser.recover_interrupted()==1
    assert browser.inspect("owner",again["id"],next_tid)["status"]=="closed"
    runtime.shutdown()
    print("MISSION_A5B PASS: SSRF checks, source isolation, approval, domain pin, capture, consent, revocation race, expiry, privacy and restart")
