from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-members-v400-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.database import db
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import homeserver_app_prebuilt, members
    from app.services.tasks import scheduler as task_scheduler

    with TestClient(app) as client:
        task_scheduler.stop()

        # Owner management remains behind the existing owner gateway.
        assert client.get("/api/v1/control/members").status_code==401
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        homeserver_app_prebuilt.install("vp3.media-player")

        alice=client.post("/api/v1/control/members",json={
            "username":"alice",
            "display_name":"Alice",
            "password":"Alice-local-pass-2026",
            "role":"member",
        })
        assert alice.status_code==200,alice.text
        alice_id=alice.json()["member"]["member_id"]

        bob=client.post("/api/v1/control/members",json={
            "username":"bob",
            "display_name":"Bob",
            "password":"Bob-local-pass-2026",
            "role":"guest",
        })
        assert bob.status_code==200,bob.text
        bob_id=bob.json()["member"]["member_id"]

        duplicate=client.post("/api/v1/control/members",json={
            "username":"alice","display_name":"Other Alice",
            "password":"Other-local-pass-2026","role":"member",
        })
        assert duplicate.status_code==409

        with db() as connection:
            rows=connection.execute(
                """SELECT member_id,username,password_salt,password_hash
                   FROM homeserver_members ORDER BY username"""
            ).fetchall()
        assert len(rows)==2
        serialized_rows=json.dumps([dict(row) for row in rows])
        assert "Alice-local-pass-2026" not in serialized_rows
        assert "Bob-local-pass-2026" not in serialized_rows
        assert all(len(str(row["password_salt"]))>=32 for row in rows)
        assert all(len(str(row["password_hash"]))==64 for row in rows)

        grant=client.put(
            f"/api/v1/control/members/{alice_id}/apps/vp3.media-player",
            json={"allowed":True},
        )
        assert grant.status_code==200,grant.text
        alice_matrix=client.get(f"/api/v1/control/members/{alice_id}/apps").json()
        assert any(row["app_key"]=="vp3.media-player" and row["allowed"] is True for row in alice_matrix["items"])
        bob_matrix=client.get(f"/api/v1/control/members/{bob_id}/apps").json()
        assert any(row["app_key"]=="vp3.media-player" and row["allowed"] is False for row in bob_matrix["items"])

        # Owner global memory must never appear in a member Agent projection.
        owner_secret="OWNER_GLOBAL_MEMORY_PRIVATE_72618"
        memory=client.post("/api/v1/control/memory",json={
            "memory_key":"owner.private.test",
            "content":owner_secret,
            "importance":0.5,
        })
        assert memory.status_code==200,memory.text

        # Remove owner session: member cookie alone must not satisfy /control.
        try:
            client.cookies.delete("homeserver_owner")
        except KeyError:
            pass
        login=client.post("/api/v1/member/session",json={
            "username":"alice","password":"Alice-local-pass-2026"
        })
        assert login.status_code==200,login.text
        assert "session_token" not in login.json()
        assert client.get("/api/v1/member/me").json()["member"]["username"]=="alice"
        assert client.get("/api/v1/control/members").status_code==401

        alice_apps=client.get("/api/v1/member/apps")
        assert alice_apps.status_code==200,alice_apps.text
        assert [row["app_key"] for row in alice_apps.json()["items"]]==["vp3.media-player"]

        alice_private="ALICE_PRIVATE_CONTEXT_91573"
        saved=client.put("/api/v1/member/context/preferences.note",json={"value":alice_private})
        assert saved.status_code==200,saved.text
        alice_ctx=client.get("/api/v1/member/context").json()
        assert any(row["value"]==alice_private for row in alice_ctx["items"])
        alice_brain=client.get("/api/v1/member/agent-context")
        assert alice_brain.status_code==200,alice_brain.text
        alice_brain_text=json.dumps(alice_brain.json(),ensure_ascii=False)
        assert alice_private in alice_brain_text
        assert owner_secret not in alice_brain_text
        assert alice_brain.json()["isolation"]["owner_memory_included"] is False
        assert alice_brain.json()["isolation"]["other_member_context_included"] is False
        assert alice_brain.json()["isolation"]["unassigned_apps_included"] is False

        alice_activity=client.get("/api/v1/member/activity").json()["items"]
        assert any(row["action"]=="member.context.updated" for row in alice_activity)
        alice_activity_text=json.dumps(alice_activity)
        assert alice_private not in alice_activity_text

        # Sign out Alice and authenticate Bob in the same browser session.
        assert client.delete("/api/v1/member/session").status_code==200
        bob_login=client.post("/api/v1/member/session",json={
            "username":"bob","password":"Bob-local-pass-2026"
        })
        assert bob_login.status_code==200,bob_login.text
        assert client.get("/api/v1/member/apps").json()["items"]==[]
        bob_ctx=client.get("/api/v1/member/context").json()
        assert bob_ctx["items"]==[]
        guest_write=client.put("/api/v1/member/context/preferences.note",json={"value":"guest-private"})
        assert guest_write.status_code==403
        bob_brain_text=json.dumps(client.get("/api/v1/member/agent-context").json(),ensure_ascii=False)
        assert alice_private not in bob_brain_text
        assert owner_secret not in bob_brain_text
        bob_activity_text=json.dumps(client.get("/api/v1/member/activity").json(),ensure_ascii=False)
        assert alice_private not in bob_activity_text

        # Five bad credentials lock the account; malformed usernames stay generic.
        assert client.delete("/api/v1/member/session").status_code==200
        malformed=client.post("/api/v1/member/session",json={"username":"BAD USER!","password":"wrong-password"})
        assert malformed.status_code==401
        assert malformed.json()["detail"]=="Invalid member credentials."
        for attempt in range(5):
            bad=client.post("/api/v1/member/session",json={
                "username":"bob","password":"wrong-password-123"
            })
            assert bad.status_code==401,(attempt,bad.text)
        locked=client.post("/api/v1/member/session",json={
            "username":"bob","password":"Bob-local-pass-2026"
        })
        assert locked.status_code==429

        # Owner can reset Bob, which clears lockout and revokes all sessions.
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
        reset_bob=client.put(f"/api/v1/control/members/{bob_id}/password",json={
            "password":"Bob-new-local-pass-2026"
        })
        assert reset_bob.status_code==200,reset_bob.text
        assert reset_bob.json()["sessions_revoked"] is True

        # Alice's existing session is revoked by a password reset.
        try:
            client.cookies.delete("homeserver_owner")
        except KeyError:
            pass
        alice_login_again=client.post("/api/v1/member/session",json={
            "username":"alice","password":"Alice-local-pass-2026"
        })
        assert alice_login_again.status_code==200
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
        reset_alice=client.put(f"/api/v1/control/members/{alice_id}/password",json={
            "password":"Alice-new-local-pass-2026"
        })
        assert reset_alice.status_code==200
        assert client.get("/api/v1/member/me").status_code==401

        # Disabling a user also revokes active sessions.
        try:
            client.cookies.delete("homeserver_owner")
        except KeyError:
            pass
        relogin=client.post("/api/v1/member/session",json={
            "username":"alice","password":"Alice-new-local-pass-2026"
        })
        assert relogin.status_code==200
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
        disabled=client.patch(f"/api/v1/control/members/{alice_id}",json={"status":"disabled"})
        assert disabled.status_code==200
        assert client.get("/api/v1/member/me").status_code==401

        cap=client.get("/api/v1/control/members/capability")
        assert cap.status_code==200,cap.text
        capability=cap.json()
        assert capability["hashed_credentials"] is True
        assert capability["hashed_sessions"] is True
        assert capability["per_member_app_access"] is True
        assert capability["private_member_context"] is True
        assert capability["member_agent_context_isolation"] is True
        assert capability["member_admin_is_not_owner"] is True
        assert capability["role_semantics"]["guest"]=="read-only-no-context-write"
        assert capability["owner_control_inherited_by_members"] is False

        ui=(ROOT/"ui"/"index.html").read_text(encoding="utf-8")
        owner_js=(ROOT/"ui"/"members.js").read_text(encoding="utf-8")
        member_ui=(ROOT/"ui"/"member.html").read_text(encoding="utf-8")
        member_js=(ROOT/"ui"/"member.js").read_text(encoding="utf-8")
        assert 'data-view="members"' in ui
        assert 'id="view-members"' in ui
        assert "loadHomeServerMembers" in owner_js
        assert "Owner control is never inherited" in ui
        assert 'id="memberLoginForm"' in member_ui
        assert "owner controls" in member_ui.lower()
        assert "/api/v1/member/agent-context" in member_js
        assert "/api/v1/control/" not in member_js

print("HomeServer Section 26 Multi-user & Context Isolation: PASS")
