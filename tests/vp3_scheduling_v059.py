from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-vp3-scheduling-v059-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import action_policy, pairing, remote_bridge, vp3_scheduling_connector  # noqa: E402
    from app.services.knowledge_sources import scheduler as knowledge_scheduler  # noqa: E402
    from app.services.tasks import scheduler as task_scheduler  # noqa: E402

    assert "scheduling.read" in pairing.DEFAULT_PERMISSIONS
    assert "scheduling.write" in pairing.DEFAULT_PERMISSIONS

    with TestClient(app) as client:
        knowledge_scheduler.stop()
        task_scheduler.stop()
        owner = client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN})
        assert owner.status_code == 200, owner.text

        request = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "vp3",
                "app_name": "VP3",
                "permissions": ["scheduling.read", "scheduling.write", "tools.execute"],
            },
        ).json()
        approved = client.post("/api/v1/pairing/approve", json={"code": request["code"]})
        assert approved.status_code == 200, approved.text
        headers = {"Authorization": f"Bearer {request['claim_token']}"}
        me = client.get("/api/v1/me", headers=headers)
        assert me.status_code == 200, me.text
        app_id = int(me.json()["id"])

        expected = {
            "vp3.schedule.overview",
            "vp3.schedule.availability",
            "vp3.booking.create",
            "vp3.booking.reschedule",
            "vp3.booking.cancel",
        }
        listed = client.get("/api/v1/tools", headers=headers)
        assert listed.status_code == 200, listed.text
        by_key = {item["key"]: item for item in listed.json()["items"]}
        assert expected.isdisjoint(by_key)
        assert "vp3.connector.configure" not in by_key
        owner_catalog = client.get("/api/v1/control/tools")
        assert owner_catalog.status_code == 200, owner_catalog.text
        assert expected.isdisjoint({item["key"] for item in owner_catalog.json()["items"]})
        owner_skills = client.get("/api/v1/control/skills")
        assert owner_skills.status_code == 200, owner_skills.text
        assert all(not str(item["key"]).startswith("vp3.") for item in owner_skills.json()["items"])

        owner_agent_tools = client.get("/api/v1/control/agent-tools")
        assert owner_agent_tools.status_code == 200, owner_agent_tools.text
        assert "homeserver_vp3_calendar_overview" not in owner_agent_tools.json()["available_tools"]
        assert "homeserver_vp3_schedule_availability" not in owner_agent_tools.json()["available_tools"]

        unavailable_proposal = client.post(
            "/api/v1/tools/vp3.booking.create/execute",
            headers=headers,
            json={
                "arguments": {
                    "kind": "personal",
                    "target_id": 7,
                    "start_at_utc": "2026-09-18 16:00:00",
                    "guest_name": "Synthetic Guest",
                }
            },
        )
        assert unavailable_proposal.status_code == 404, unavailable_proposal.text
        with db() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM action_requests WHERE action_key LIKE 'vp3.booking.%'"
            ).fetchone()[0] == 0

        # Relay-only bootstrap: only the paired VP3 identity may provision it.
        wrong = client.post(
            "/api/v1/pairing/request",
            json={"app_key": "not-vp3", "app_name": "Not VP3", "permissions": ["tools.execute"]},
        ).json()
        assert client.post("/api/v1/pairing/approve", json={"code": wrong["code"]}).status_code == 200
        try:
            remote_bridge.dispatch_remote_request(
                "vp3.connector.configure",
                {
                    "endpoint": "http://127.0.0.1/api/homeserver-scheduling-v620.php",
                    "token": "vps_" + "x" * 40,
                    "version": "v6.20",
                },
                wrong["claim_token"],
            )
            raise AssertionError("non-VP3 app configured the connector")
        except remote_bridge.RemoteBridgeError:
            pass

        configured = remote_bridge.dispatch_remote_request(
            "vp3.connector.configure",
            {
                "endpoint": "http://127.0.0.1/api/homeserver-scheduling-v620.php",
                "token": "vps_" + "y" * 40,
                "version": "v6.20",
                "capabilities": ["overview", "availability", "booking.create"],
            },
            request["claim_token"],
        )
        assert configured["ok"] is True
        assert configured["payload"]["configured"] is True
        connector_status = vp3_scheduling_connector.status()
        assert connector_status["configured"] is True
        assert connector_status["pairing_bound"] is True
        assert connector_status["endpoint_host"] == "127.0.0.1"
        assert connector_status["token_suffix"] == "yyyy"

        listed_ready = client.get("/api/v1/tools", headers=headers)
        assert listed_ready.status_code == 200, listed_ready.text
        ready_by_key = {item["key"]: item for item in listed_ready.json()["items"]}
        assert expected.issubset(ready_by_key)
        assert all(ready_by_key[key]["available"] is True for key in expected)
        assert ready_by_key["vp3.schedule.overview"]["mode"] == "read"
        assert ready_by_key["vp3.schedule.availability"]["mode"] == "read"
        owner_catalog_ready = client.get("/api/v1/control/tools")
        assert expected.issubset({item["key"] for item in owner_catalog_ready.json()["items"]})
        owner_skills_ready = client.get("/api/v1/control/skills")
        assert {
            "vp3.calendar-review",
            "vp3.find-time",
            "vp3.booking-management",
        }.issubset({item["key"] for item in owner_skills_ready.json()["items"]})

        owner_agent_tools = client.get("/api/v1/control/agent-tools")
        assert owner_agent_tools.status_code == 200, owner_agent_tools.text
        assert "homeserver_vp3_calendar_overview" in owner_agent_tools.json()["available_tools"]
        assert "homeserver_vp3_schedule_availability" in owner_agent_tools.json()["available_tools"]
        assert "homeserver_vp3_booking_create_request" not in owner_agent_tools.json()["available_tools"]

        for tool_key in ("vp3.booking.create", "vp3.booking.reschedule", "vp3.booking.cancel"):
            policy = ready_by_key[tool_key]["execution_policy"]
            assert policy["policy_mode"] == "approval_required"
            assert set(policy["allowed_modes"]) == {"approval_required", "sensitive_high_impact"}
            rejected = client.put(
                f"/api/v1/control/action-policies/{app_id}/{tool_key}",
                json={"policy_mode": "safe_automatic"},
            )
            assert rejected.status_code == 422, rejected.text

        # Defense in depth: a stale/manual safe-automatic override is ignored.
        with db() as connection:
            connection.execute(
                """
                INSERT INTO app_tool_execution_policies(paired_app_id, tool_key, policy_mode)
                VALUES (?, 'vp3.booking.create', 'safe_automatic')
                ON CONFLICT(paired_app_id, tool_key) DO UPDATE SET
                    policy_mode='safe_automatic', updated_at=CURRENT_TIMESTAMP
                """,
                (app_id,),
            )
        repaired = action_policy.resolve_policy(app_id, "vp3", "vp3.booking.create")
        assert repaired["policy_mode"] == "approval_required"
        assert repaired["inherited"] is True
        assert "safe_automatic" not in repaired["allowed_modes"]

        # A cloud-changing request stops at a local action request. No cloud call
        # occurs before explicit owner approval.
        proposal = client.post(
            "/api/v1/tools/vp3.booking.create/execute",
            headers=headers,
            json={
                "arguments": {
                    "kind": "personal",
                    "target_id": 7,
                    "start_at_utc": "2026-09-18 16:00:00",
                    "guest_name": "Synthetic Guest",
                    "guest_email": "guest@example.invalid",
                    "idempotency_key": "caller-supplied-must-be-ignored",
                }
            },
        )
        assert proposal.status_code == 200, proposal.text
        body = proposal.json()
        assert body["approval_required"] is True
        request_id = str((body.get("result") or {}).get("request_id") or "")
        assert request_id
        with db() as connection:
            row = connection.execute(
                "SELECT status, action_key, arguments_json FROM action_requests WHERE id=?",
                (request_id,),
            ).fetchone()
        assert row is not None
        assert row["status"] == "pending"
        assert row["action_key"] == "vp3.booking.create"
        args = json.loads(row["arguments_json"])
        assert str(args["idempotency_key"]).startswith("hs-action-")
        assert args["idempotency_key"] != "caller-supplied-must-be-ignored"
        assert len(args["idempotency_key"]) <= 160

        # Credential-bearing calls reject non-HTTPS remote endpoints and write
        # calls require a stable idempotency key.
        pairing_hash = hashlib.sha256(request["claim_token"].encode("utf-8")).hexdigest()
        try:
            vp3_scheduling_connector.configure(
                "http://example.com/api/homeserver-scheduling-v620.php",
                "vps_" + "z" * 40,
                pairing_token_hash=pairing_hash,
            )
            raise AssertionError("insecure remote scheduling endpoint accepted")
        except vp3_scheduling_connector.VP3SchedulingConnectorError:
            pass
        try:
            vp3_scheduling_connector.request("booking.create", {}, idempotency_key="")
            raise AssertionError("write operation accepted without idempotency key")
        except vp3_scheduling_connector.VP3SchedulingConnectorError:
            pass

        # Local revocation invalidates the reverse cloud connector immediately.
        # The encrypted connector file may remain for diagnostics, but it cannot
        # be used and the scheduling tools disappear from all normal surfaces.
        with db() as connection:
            connection.execute("UPDATE paired_apps SET status='revoked' WHERE app_key='vp3'")
        revoked_status = vp3_scheduling_connector.status()
        assert revoked_status["configured"] is False
        assert revoked_status["pairing_bound"] is False
        assert expected.isdisjoint({item["key"] for item in client.get("/api/v1/control/tools").json()["items"]})
        assert all(
            not str(item["key"]).startswith("vp3.")
            for item in client.get("/api/v1/control/skills").json()["items"]
        )
        try:
            vp3_scheduling_connector.request("overview", {})
            raise AssertionError("revoked VP3 pairing retained reverse scheduling access")
        except vp3_scheduling_connector.VP3SchedulingConnectorError as exc:
            assert exc.status_code == 409

        # Re-pairing rotates the canonical VP3 bearer. The stale reverse
        # connector must remain disabled until the newly paired VP3 identity
        # explicitly provisions it again.
        repaired_pair = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "vp3",
                "app_name": "VP3",
                "permissions": ["scheduling.read", "scheduling.write", "tools.execute"],
            },
        ).json()
        assert client.post("/api/v1/pairing/approve", json={"code": repaired_pair["code"]}).status_code == 200
        assert vp3_scheduling_connector.status()["configured"] is False
        reprovisioned = remote_bridge.dispatch_remote_request(
            "vp3.connector.configure",
            {
                "endpoint": "http://127.0.0.1/api/homeserver-scheduling-v620.php",
                "token": "vps_" + "r" * 40,
                "version": "v6.20",
                "capabilities": ["overview", "availability", "booking.create"],
            },
            repaired_pair["claim_token"],
        )
        assert reprovisioned["ok"] is True
        assert reprovisioned["payload"]["configured"] is True
        assert vp3_scheduling_connector.status()["pairing_bound"] is True

print("HomeServer VP3 scheduling v0.59 regression passed")
