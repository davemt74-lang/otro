from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
FIXTURE=json.loads((ROOT/"tests/fixtures/tracky_v280_golden_operational_scenarios.json").read_text(encoding="utf-8"))
HOME="11111111-1111-4111-8111-111111111111"
OFFICE="22222222-2222-4222-8222-222222222222"
HOME_OLD="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
HOME_NEW="dddddddd-dddd-4ddd-8ddd-dddddddddddd"
OFFICE_NODE="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

assert FIXTURE["format"]=="physical_federation_v280_golden_operational_release.v1"
assert FIXTURE["version"]=="2.80"
assert FIXTURE["scenario_count"]==24
assert len(FIXTURE["scenario_ids"])==24
assert len(set(FIXTURE["scenario_ids"]))==24

with tempfile.TemporaryDirectory(prefix="tracky-v280-release-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir
    os.environ["VP3_OS_HARDWARE_ADAPTER"]="disabled"

    from app.database import db, initialize_database
    from app.services import (
        tracky_federation_governed_operations,
        tracky_federation_policy,
        tracky_federation_reconciliation,
        tracky_federation_sync,
        tracky_release_hardening,
        tracky_site_topology,
    )

    initialize_database()
    initialize_database()
    with db() as connection:
        versions=[int(row["version"]) for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()]
    assert versions==list(range(1,54))

    for site_id,label in ((HOME,"Home"),(OFFICE,"Office")):
        tracky_site_topology.register_site(site_id=site_id,label=label)
    for device_id,label,site_id in (
        (HOME_OLD,"Home Node A",HOME),(HOME_NEW,"Home Node B",HOME),(OFFICE_NODE,"Office Node",OFFICE)
    ):
        tracky_site_topology.register_device(
            device_id=device_id,label=label,site_id=site_id,hardware_profile="Node",trust_state="trusted",
            roles=["site_authority","persistence","perception"],
            capabilities={"site_authority_eligible":True,"physical_context":True,"reconciliation":True},
        )
    first=tracky_site_topology.claim_site_authority(site_id=HOME,device_id=HOME_OLD)
    tracky_site_topology.claim_site_authority(site_id=OFFICE,device_id=OFFICE_NODE)
    tracky_federation_sync.set_local_site_id(HOME)

    tracky_federation_policy.set_site_policy(
        mode="private",allow_federation=True,allow_remote_observation=False,
        default_identity_visibility="none",allowed_peer_sites=[OFFICE],
    )
    granted=tracky_federation_policy.grant_permission(OFFICE,"semantic_world_read",reason="golden_release")
    assert granted["allowed"] is True
    revoked=tracky_federation_policy.revoke_permission(OFFICE,"semantic_world_read",reason="golden_release_revocation")
    assert revoked["allowed"] is False and revoked["reason"]=="permission_revoked"
    with db() as connection:
        tombstone=connection.execute(
            "SELECT revision,revocation_epoch FROM tracky_federation_policy_revocations WHERE revocation_key=?",
            (f"grant:{HOME}|{OFFICE}|semantic_world_read",),
        ).fetchone()
    assert tombstone is not None and int(tombstone["revocation_epoch"])>0

    transferred=tracky_site_topology.claim_site_authority(
        site_id=HOME,device_id=HOME_NEW,replace=True,reason="golden_release_transfer"
    )
    assert transferred["authority_epoch"]>first["authority_epoch"]
    with db() as connection:
        active=connection.execute(
            "SELECT device_id,authority_epoch FROM tracky_site_authority WHERE site_id=? AND active=1",(HOME,)
        ).fetchone()
        old=connection.execute(
            "SELECT active FROM tracky_site_authority WHERE site_id=? AND device_id=? ORDER BY authority_epoch DESC LIMIT 1",
            (HOME,HOME_OLD),
        ).fetchone()
    assert active["device_id"]==HOME_NEW and int(active["authority_epoch"])==int(transferred["authority_epoch"])
    assert int(old["active"])==0
    try:
        tracky_site_topology.claim_site_authority(site_id=HOME,device_id=HOME_OLD)
        raise AssertionError("conflicting authority claim was accepted")
    except tracky_site_topology.TrackySiteTopologyError as exc:
        assert int(getattr(exc,"status_code",409))==409

    # A cached policy governed by the old authority must fail closed after epoch change.
    with db() as connection:
        connection.execute(
            """UPDATE tracky_federation_site_policies SET origin_role='cloud_mirror',
               governing_authority_device_id=?,governing_authority_epoch=? WHERE site_id=?""",
            (HOME_OLD,int(first["authority_epoch"]),HOME),
        )
    stale=tracky_federation_policy.permission_decision(HOME,OFFICE,"semantic_world_read")
    assert stale["allowed"] is False and stale["reason"]=="source_policy_authority_stale"

    # Restore a locally governed policy for reconciliation checks.
    tracky_federation_policy.set_site_policy(
        mode="private",allow_federation=True,allow_remote_observation=False,
        default_identity_visibility="none",allowed_peer_sites=[OFFICE],
    )
    with db() as connection:
        connection.execute(
            """INSERT OR REPLACE INTO tracky_federation_sync_peers(
               remote_site_id,status,last_received_revision,last_received_fingerprint,
               last_received_authority_epoch,last_received_at
            ) VALUES (?,'current',4,'same',1,CURRENT_TIMESTAMP)""",(OFFICE,)
        )
    current=tracky_federation_reconciliation.note_peer_contact(
        OFFICE,{"revision":4,"fingerprint":"same","authority_epoch":1}
    )
    assert current["status"]=="current"
    partitioned=tracky_federation_reconciliation.mark_partition(OFFICE,"golden_partition")
    assert partitioned["status"]=="partitioned"
    reconnecting=tracky_federation_reconciliation.schedule_retry(OFFICE,reason="golden_reconnect")
    assert reconnecting["status"]=="reconciling"
    assert reconnecting["status"]!="current"
    completed=tracky_federation_reconciliation.complete_reconciliation(
        OFFICE,{"revision":4,"fingerprint":"same","authority_epoch":1}
    )
    assert completed["status"]=="current"

    cap=tracky_federation_governed_operations.public_capability()
    assert cap["cloud_execution_allowed"] is False
    assert cap["agent_proposal_only"] is True
    assert cap["authority_transfer_requires_epoch_advance"] is True
    assert cap["completion_requires_authoritative_reconciliation"] is True
    try:
        tracky_federation_governed_operations.ingest_cloud_requests({
            "cloud_role":"request_relay_only","remote_command_execution":False,"authority_mutation":False,
            "requests":[{"origin_site_id":OFFICE,"request_id":"wrong-origin","idempotency_key":"wrong-origin",
                         "operation_type":"reconcile","target_site_id":OFFICE}]
        })
        raise AssertionError("wrong-origin Cloud request was accepted")
    except tracky_federation_governed_operations.FederationOperationError as exc:
        assert int(exc.status_code)==409

    release=tracky_release_hardening.public_capability()
    assert release["release_ready"] is True
    assert release["final_section"]==10
    assert release["schema_version"]==53
    assert release["golden_scenarios"]==24
    assert release["split_brain_allowed"] is False
    assert release["stale_current_promotion_allowed"] is False
    assert release["revocation_resurrection_allowed"] is False
    assert release["cloud_execution_allowed"] is False
    assert release["agent_execution_allowed"] is False

main=(ROOT/"app/main.py").read_text(encoding="utf-8")
tracky_api=(ROOT/"app/tracky_api.py").read_text(encoding="utf-8")
physical=(ROOT/"app/services/tracky_physical_context.py").read_text(encoding="utf-8")
ci=(ROOT/".github/workflows/ci.yml").read_text(encoding="utf-8")
release_workflow=(ROOT/".github/workflows/homeserver-v24-release.yml").read_text(encoding="utf-8")
assert main.count('@app.get("/api/v1/control/federation-operations")')==1
assert main.count('@app.get("/api/v1/control/federation-governed-operations")')==1
assert '@router.get("/api/v1/tracky/release-v280")' in tracky_api
assert '"release_hardening": tracky_release_hardening.public_capability()' in physical
assert "tracky_v280_golden_operational_release.py" in ci
assert "tracky_v280_golden_operational_release.py" in release_workflow
print("TRACKY_V280_GOLDEN_OPERATIONAL_RELEASE=PASS")
