from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="tracky-v275-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

    from app.database import db, initialize_database  # noqa: E402
    from app.services import (  # noqa: E402
        federated_data,
        local_automation,
        room_device_automation,
        tracky_governed_actions,
        tracky_physical_context,
    )

    initialize_database()

    with db() as connection:
        versions = [
            int(row["version"])
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
        assert versions == list(range(1, 58))

    # V2.4 reconciliation remains the gate.
    try:
        tracky_governed_actions.propose_device_action(
            intent_id="tracky-action-reconcile",
            correlation_id="tracky-corr-reconcile",
            site_id=None,
            origin_kind="agent_suggestion",
            requested_mode="suggest_only",
            device_key="office-light",
            command="off",
            arguments={},
            reason="test",
            requested_by="vp3",
            granted_permissions={"awareness.read"},
        )
        raise AssertionError("action proposal bypassed v2.4 reconciliation")
    except tracky_governed_actions.TrackyActionError as exc:
        assert exc.status_code == 409
        assert "v2.4 reconciliation" in str(exc)

    datasets = {name: [] for name in federated_data.DATASETS}
    reconciled = federated_data.reconcile_snapshot(
        {
            "version": "2.2",
            "federation_version": "2.4",
            "authoritative_source": "vp3_cloud",
            "snapshot_mode": "full",
            "covered_datasets": list(datasets),
            "revision": "tracky-v275-test",
            "datasets": datasets,
        },
        observed_source="homeserver",
        trigger_reason="tracky-v275-test",
    )
    assert reconciled["status"] == "completed"
    assert federated_data.reconciliation_state("vp3_cloud")["needs_reconciliation"] is False

    room_device_automation.upsert_room("office", "Office", enabled=True)
    room_device_automation.upsert_provider(
        "test-provider",
        "Test Provider",
        "test",
        enabled=True,
        executable=False,
        status="connected",
    )
    light = room_device_automation.upsert_device(
        "office-light",
        "test-provider",
        "light-1",
        "Office Light",
        "light",
        room_key="office",
        enabled=True,
        controllable=True,
        state={"on": True},
    )
    assert light["controllable"] is True

    lock = room_device_automation.upsert_device(
        "front-lock",
        "test-provider",
        "lock-1",
        "Front Lock",
        "lock",
        room_key="office",
        enabled=True,
        controllable=True,
        state={"locked": True},
    )
    assert lock["controllable"] is False

    cap = tracky_governed_actions.public_capability()
    assert cap["protocol"] == "physical_action.v1"
    assert cap["remote_direct_execution"] is False
    assert cap["remote_approval_allowed"] is False
    assert cap["default_cloud_device_control_permission"] is False
    assert set(cap["safe_control_categories"]) == {"light", "outlet", "fan", "thermostat"}
    assert {"camera", "lock", "garage", "security", "appliance", "scene"}.issubset(
        set(cap["blocked_control_categories"])
    )

    # Without a separately granted devices.control permission, request_approval
    # downgrades to a local suggestion instead of widening Cloud authority.
    downgraded = tracky_governed_actions.propose_device_action(
        intent_id="tracky-action-downgraded",
        correlation_id="tracky-corr-downgraded",
        site_id=None,
        origin_kind="agent_suggestion",
        requested_mode="request_approval",
        device_key="office-light",
        command="off",
        arguments={},
        reason="The office appears empty; suggest turning off the light.",
        requested_by="vp3",
        granted_permissions={"awareness.read", "tools.execute"},
    )
    assert downgraded["intent"]["status"] == "suggested"
    assert downgraded["intent"]["effective_mode"] == "suggest_only"
    assert downgraded["permission_upgrade_required"] is True
    assert downgraded["approval_required"] is False
    assert downgraded["remote_approval_allowed"] is False
    assert downgraded["direct_execution"] is False
    assert int(downgraded["intent"]["suggestion_id"]) > 0
    assert downgraded["intent"]["action_request_id"] == ""

    # With explicitly granted control permission, Tracky may create a durable
    # approval request, but the command still has not executed.
    requested = tracky_governed_actions.propose_device_action(
        intent_id="tracky-action-requested",
        correlation_id="tracky-corr-requested",
        site_id=None,
        origin_kind="user_request",
        requested_mode="request_approval",
        device_key="office-light",
        command="off",
        arguments={},
        reason="User asked to turn off the office light.",
        requested_by="vp3",
        granted_permissions={"awareness.read", "tools.execute", "devices.control"},
    )
    assert requested["intent"]["status"] == "requested"
    assert requested["approval_required"] is True
    assert requested["local_owner_approval_required"] is True
    assert requested["remote_approval_allowed"] is False
    action_request_id = requested["intent"]["action_request_id"]
    assert action_request_id
    with db() as connection:
        action = connection.execute(
            "SELECT action_key,status FROM action_requests WHERE id=?",
            (action_request_id,),
        ).fetchone()
        physical_runs = connection.execute(
            "SELECT COUNT(*) FROM automation_device_actions"
        ).fetchone()[0]
    assert dict(action) == {"action_key": "devices.command", "status": "pending"}
    assert int(physical_runs) == 0

    # Idempotent intent replay must not create another suggestion/request.
    replayed = tracky_governed_actions.propose_device_action(
        intent_id="tracky-action-requested",
        correlation_id="tracky-corr-requested",
        site_id=None,
        origin_kind="user_request",
        requested_mode="request_approval",
        device_key="office-light",
        command="off",
        arguments={},
        reason="User asked to turn off the office light.",
        requested_by="vp3",
        granted_permissions={"awareness.read", "tools.execute", "devices.control"},
    )
    assert replayed["idempotent"] is True
    assert replayed["intent"]["action_request_id"] == action_request_id

    try:
        tracky_governed_actions.propose_device_action(
            intent_id="tracky-action-lock",
            correlation_id="tracky-corr-lock",
            site_id=None,
            origin_kind="user_request",
            requested_mode="suggest_only",
            device_key="front-lock",
            command="off",
            arguments={},
            reason="Unsafe category test.",
            requested_by="vp3",
            granted_permissions={"awareness.read"},
        )
        raise AssertionError("blocked category received a Tracky action proposal")
    except tracky_governed_actions.TrackyActionError as exc:
        assert exc.status_code in {403, 422}
        assert "controllable" in str(exc).lower() or "discovery-only" in str(exc).lower()

    # Owner-defined Tracky event rule → existing local routine → suggestion.
    local_automation.upsert_routine(
        "empty-office-lights",
        "Empty office lights",
        approval_mode="suggest_only",
        steps=[{"device_key": "office-light", "command": "off", "arguments": {}}],
    )
    rule = tracky_governed_actions.upsert_event_rule(
        "tracky-empty-office",
        "Tracky empty office",
        event_type="room.empty",
        routine_key="empty-office-lights",
        enabled=True,
        room_id="office",
        min_confidence=0.90,
        cooldown_seconds=0,
    )
    assert rule["enabled"] is True
    assert rule["routine"]["approval_mode"] == "suggest_only"

    empty_event = {
        "events": [{
            "event_id": "tracky-v275-room-empty",
            "sequence": 10,
            "event_type": "room.empty",
            "severity": "notable",
            "confidence": 0.97,
            "privacy_class": "cloud_derived",
            "occurred_at": tracky_governed_actions._now_iso(),
            "summary": "Office became empty",
            "room_id": "office",
        }],
        "world_state": [],
        "context": {
            "current_room": "Office",
            "people_present": [],
            "recent_changes": ["Office became empty"],
            "environment_status": "normal",
            "confidence": 0.97,
            "exceptions": [],
        },
        "context_sequence": 10,
        "context_observed_at": tracky_governed_actions._now_iso(),
    }
    ingested = tracky_physical_context.ingest_semantic_projection(empty_event, source="v275-test")
    assert ingested["inserted_events"] == 1
    fired = [r for r in ingested["automation_results"] if r.get("rule_key") == "tracky-empty-office"]
    assert len(fired) == 1 and fired[0]["fired"] is True
    assert fired[0]["status"] == "suggested"

    with db() as connection:
        suggestion_count = int(connection.execute(
            "SELECT COUNT(*) FROM automation_suggestions WHERE source_kind='tracky-rule:tracky-empty-office'"
        ).fetchone()[0])
    assert suggestion_count == 1

    replay = tracky_physical_context.ingest_semantic_projection(empty_event, source="v275-test")
    assert replay["duplicate_events"] == 1
    with db() as connection:
        replay_count = int(connection.execute(
            "SELECT COUNT(*) FROM automation_suggestions WHERE source_kind='tracky-rule:tracky-empty-office'"
        ).fetchone()[0])
        claim_count = int(connection.execute(
            """
            SELECT COUNT(*) FROM tracky_event_automation_claims c
            JOIN tracky_event_automation_rules r ON r.id=c.rule_id
            WHERE r.rule_key='tracky-empty-office' AND c.event_id='tracky-v275-room-empty'
            """
        ).fetchone()[0])
    assert replay_count == 1
    assert claim_count == 1

    # Safety-like events may never promote an ask-every-time routine into a
    # physical approval request; they remain suggestion-only by policy.
    local_automation.upsert_routine(
        "safety-light-routine",
        "Safety light routine",
        approval_mode="ask_every_time",
        steps=[{"device_key": "office-light", "command": "on", "arguments": {}}],
    )
    tracky_governed_actions.upsert_event_rule(
        "tracky-safety-rule",
        "Tracky safety rule",
        event_type="safety.smoke_detected",
        routine_key="safety-light-routine",
        enabled=True,
        min_confidence=0.95,
        cooldown_seconds=0,
    )
    safety_event = {
        "events": [{
            "event_id": "tracky-v275-safety",
            "sequence": 11,
            "event_type": "safety.smoke_detected",
            "severity": "urgent",
            "confidence": 0.99,
            "privacy_class": "cloud_derived",
            "occurred_at": tracky_governed_actions._now_iso(),
            "summary": "Smoke-like condition observed",
        }],
        "world_state": [],
        "context": {
            "current_room": "Office",
            "people_present": [],
            "recent_changes": ["Smoke-like condition observed"],
            "environment_status": "exception",
            "confidence": 0.99,
            "exceptions": ["Smoke-like condition observed"],
        },
        "context_sequence": 11,
        "context_observed_at": tracky_governed_actions._now_iso(),
    }
    safety_ingest = tracky_physical_context.ingest_semantic_projection(safety_event, source="v275-test")
    safety_results = [r for r in safety_ingest["automation_results"] if r.get("rule_key") == "tracky-safety-rule"]
    assert len(safety_results) == 1
    assert safety_results[0]["fired"] is False
    assert safety_results[0]["reason"] == "safety_event_requires_suggest_only"

    # Default pairing must remain least privilege.
    pairing_source = (ROOT / "app" / "services" / "cloud_pairing.py").read_text(encoding="utf-8")
    permissions_block = pairing_source.split("_VP3_PERMISSIONS = [", 1)[1].split("]", 1)[0]
    assert '"devices.control"' not in permissions_block

    # Architecture: Tracky proposes; existing approvals/devices execute.
    service_source = (ROOT / "app" / "services" / "tracky_governed_actions.py").read_text(encoding="utf-8")
    tracky_api_source = (ROOT / "app" / "tracky_api.py").read_text(encoding="utf-8")
    relay_source = (ROOT / "app" / "services" / "remote_bridge.py").read_text(encoding="utf-8")
    migration_source = (ROOT / "database" / "migrations" / "041_tracky_governed_actions.sql").read_text(encoding="utf-8")

    assert "approvals.create_device_command_request" in service_source
    assert "room_device_automation.create_suggestion" in service_source
    assert "local_automation.run_routine" in service_source
    assert "execute_command(" not in service_source
    assert "approve_request(" not in service_source
    assert '"remote_approval_allowed": False' in service_source
    assert "physical_context.action.propose" in relay_source
    assert "physical_context.action.status" in relay_source
    assert "/api/v1/tracky/actions/propose" in tracky_api_source

    for table in (
        "federated_record_links",
        "federated_sync_cursors",
        "federated_reconciliation_state",
        "federated_reconciliation_runs",
        "action_requests",
        "automation_suggestions",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" not in migration_source

print("Tracky V2.75 governed physical actions + automation execution: PASS")
