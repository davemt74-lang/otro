"""A5B2 deterministic acceptance: consent, model proposals and live-state fencing."""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="vp3-live-browser-a5b2-") as directory:
    os.environ["HOMESERVER_DATA_DIR"] = directory
    from app.database import db, initialize_database
    from app.services import agent_mission_runtime as runtime
    from app.services import agent_mission_browser as grants
    from app.services import agent_mission_live_browser as live
    from app.services import agent_browser_policy as policy
    from app.services import agent_browser_live_actor as actor

    initialize_database()
    with db() as conn:
        pid = int(conn.execute("SELECT id FROM agents WHERE is_primary=1").fetchone()["id"])
        conn.execute(
            "INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES(?,?,?,?)",
            ("a5b2-owner", pid, "owner", "Persistent browser supervisor")
        )
        conn.execute(
            "INSERT OR IGNORE INTO conversation_context_settings(conversation_id) VALUES(?)",
            ("a5b2-owner",)
        )
    runtime._route = lambda source, cid: ("openai", "fixture", False)
    model_reply = {"index": 0, "reason": "The second page clarifies the findings."}
    runtime._infer = lambda source, cid, messages: (json.dumps(model_reply), "openai", "fixture")
    policy.pinned_public_ipv4 = lambda host: "93.184.215.14"

    def mission(index):
        return runtime.create_mission(
            "owner", conversation_id="a5b2-owner", parent_agent_id=pid, owner=True,
            objective="Research open pages", client_request_id="live-"+str(index),
            tasks=[{"role": "researcher", "title": "Review sources",
                    "objective": "Review approved pages", "depends_on": []}],
        )

    first = mission(1)
    mid, tid = first["id"], first["tasks"][0]["id"]
    grants.authorize("owner", mid, tid, "https://example.com/start")

    sessions = set()
    events = []
    def fake_open(tid, origin, ip, url):
        sessions.add(tid)
        events.append(("open", tid, url))
        return {
            "url": url, "page_title": "Start page",
            "text_snapshot": "Read-only research summary.",
            "image_base64": "/9j/"+"a"*120,
            "links": [
                {"url": "https://example.com/second", "title": "Second page"},
                {"url": "https://example.com/third", "title": "Third page"},
            ],
        }
    def fake_execute(tid, command, url=""):
        events.append((command, tid, url))
        if command == "snapshot":
            return fake_open_snapshot(tid)
        return {
            "url": url, "page_title": "Second page",
            "text_snapshot": "Follow-up research.",
            "image_base64": "/9j/"+"b"*120,
            "links": [{"url": "https://example.com/third", "title": "Third page"}],
        }
    def fake_open_snapshot(tid):
        return {
            "url": "https://example.com/start", "page_title": "Start page",
            "text_snapshot": "Read-only research summary.",
            "image_base64": "/9j/"+"a"*120,
            "links": [{"url": "https://example.com/second", "title": "Second page"}],
        }
    actor.open_session = fake_open
    actor.execute = fake_execute
    actor.active = lambda tid: tid in sessions
    actor.close_session = lambda tid: sessions.discard(tid)
    actor.shutdown = lambda: sessions.clear()

    initial = live.start("owner", mid, tid)
    assert initial["status"] == "live" and initial["session_active"]
    assert initial["visit_count"] == 1
    assert initial["current_url"] == "https://example.com/start"
    assert initial["image_base64"].startswith("/9j/")
    try:
        live.start("owner", mid, tid)
        raise AssertionError("Duplicate live session start permitted")
    except runtime.MissionError as exc:
        assert exc.status_code == 409

    proposal = live.propose("owner", mid, tid)
    suggested = proposal["proposed_link"]
    assert suggested["url"] == "https://example.com/second"
    assert len(events) == 1, "A model proposal must not navigate"
    refreshed = live.refresh("owner", mid, tid)
    assert refreshed["proposed_link"]["id"] == suggested["id"]
    assert refreshed["revision"] == proposal["revision"]
    approval = live.approve_navigation("owner", mid, tid, suggested["id"])
    assert approval["current_url"] == "https://example.com/second"
    assert approval["visit_count"] == 2
    assert approval["proposed_link"] == {}
    assert [x[0] for x in events] == ["open", "snapshot", "navigate"]
    assert events[-1][-1] == suggested["url"]
    try:
        live.approve_navigation("owner", mid, tid, suggested["id"])
        raise AssertionError("Stale navigation proposal was replayed")
    except runtime.MissionError as exc:
        assert exc.status_code == 409
    try:
        live.get("app:unrelated", mid, tid)
        raise AssertionError("Cross-app browser leaked")
    except runtime.MissionError as exc:
        assert exc.status_code == 404
    try:
        live.stop("owner", mid, "00000000-0000-4000-8000-000000000000")
        raise AssertionError("Stopped a browser not owned by the mission")
    except runtime.MissionError as exc:
        assert exc.status_code == 404
    assert actor.active(tid)

    # Unsafe model output is rejected before human approval or navigation.
    model_reply = {"index": 99, "reason": "Invalid"}
    try:
        live.propose("owner", mid, tid)
        raise AssertionError("Agent could select a non-existent link")
    except runtime.MissionError as exc:
        assert exc.status_code == 502
    model_reply = {"index": 0, "reason": "Another source"}
    pending = live.propose("owner", mid, tid)
    prior = pending["proposed_link"]
    # User revokes the grant during the actor's navigation.
    def revoked_during_navigate(tid, command, url=""):
        if command == "navigate":
            grants.revoke("owner", mid, tid)
        return fake_execute(tid, command, url)
    actor.execute = revoked_during_navigate
    try:
        live.approve_navigation("owner", mid, tid, prior["id"])
        raise AssertionError("Revoked browser committed late navigation")
    except runtime.MissionError as exc:
        assert exc.status_code == 409
    hidden = live.get("owner", mid, tid)
    assert hidden["status"] == "stopped" and not hidden.get("image_base64")
    assert not actor.active(tid)

    # Restart must close lingering browser sessions without replaying pages.
    second = mission(2)
    mid2, tid2 = second["id"], second["tasks"][0]["id"]
    grants.authorize("owner", mid2, tid2, "https://example.com/start")
    live.start("owner", mid2, tid2)
    assert live.recover_interrupted() == 1
    assert not sessions
    assert live.get("owner", mid2, tid2)["status"] == "stopped"

    third = mission(3)
    mid3, tid3 = third["id"], third["tasks"][0]["id"]
    grants.authorize("owner", mid3, tid3, "https://example.com/start")
    live.start("owner", mid3, tid3)
    with db() as conn:
        conn.execute(
            "UPDATE agent_mission_browser_v1 SET expires_at=datetime('now','-2 minutes') "
            "WHERE task_id=?", (tid3,)
        )
    expired = live.get("owner", mid3, tid3)
    assert expired["status"] == "stopped"
    assert not expired.get("image_base64")
    assert not actor.active(tid3)

    # Local-only privacy changes must suppress page data without any navigation.
    fourth = mission(4)
    mid4, tid4 = fourth["id"], fourth["tasks"][0]["id"]
    grants.authorize("owner", mid4, tid4, "https://example.com/start")
    live.start("owner", mid4, tid4)
    with db() as conn:
        conn.execute(
            "UPDATE conversation_context_settings SET cloud_allowed=0 "
            "WHERE conversation_id='a5b2-owner'"
        )
    secret = live.get("owner", mid4, tid4)
    assert secret["status"] == "private" and not secret.get("image_base64")
    try:
        live.refresh("owner", mid4, tid4)
        raise AssertionError("Private live mission refreshed browser")
    except runtime.MissionError as exc:
        assert exc.status_code == 403
    live.stop("owner", mid4, tid4)
    runtime.shutdown()
    print("MISSION_A5B2 PASS: persistent session, review-only LLM proposal, explicit navigation approval, stale consent, refresh, revoke race, privacy, expiry and restart")
