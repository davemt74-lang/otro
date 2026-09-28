from __future__ import annotations

import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from app.services import tracky_federation_access_operations as access

HOME="11111111-1111-4111-8111-111111111111"
OFFICE="22222222-2222-4222-8222-222222222222"
PERSON="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DEVICE="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def fixture():
    policy={
        "revision":11,"revocation_epoch":9,
        "sites":[{"site_id":HOME,"revision":3,"mode":"household","allow_federation":True,"allow_remote_observation":True,"allowed_peer_sites":[OFFICE],"origin_role":"local_governed"}],
        "grants":[
            {"source_site_id":HOME,"destination_site_id":OFFICE,"scope":"semantic_world_read","status":"granted","revision":4,"reason":"share world"},
            {"source_site_id":HOME,"destination_site_id":OFFICE,"scope":"identity_continuity_read","status":"granted","revision":3},
            {"source_site_id":HOME,"destination_site_id":OFFICE,"scope":"agent_context_read","status":"granted","revision":2},
            {"source_site_id":HOME,"destination_site_id":OFFICE,"scope":"remote_observation","status":"granted","revision":2},
            {"source_site_id":HOME,"destination_site_id":OFFICE,"scope":"history_query","status":"revoked","revision":5},
        ],
        "consents":[
            {"site_id":HOME,"canonical_identity_id":PERSON,"scope":"person_recognition","status":"granted","revision":4},
            {"site_id":HOME,"canonical_identity_id":PERSON,"scope":"voice_matching","status":"granted","revision":2},
            {"site_id":HOME,"canonical_identity_id":PERSON,"scope":"identity_linking","status":"denied","revision":3},
        ],
        "revocations":[
            {"revocation_key":f"grant:{HOME}|{OFFICE}|semantic_world_read","revision":5,"revocation_epoch":8,"reason":"revoked during partition"},
            {"revocation_key":f"consent:{HOME}|{PERSON}|person_recognition","revision":5,"revocation_epoch":9,"reason":"recognition revoked"},
        ],
    }
    topology={"sites":[
        {"id":HOME,"label":"Home","authority_device_id":DEVICE,"authority_epoch":4},
        {"id":OFFICE,"label":"Office","authority_device_id":"cccccccc-cccc-4ccc-8ccc-cccccccccccc","authority_epoch":2},
    ]}
    sync={"local_site_id":HOME,"sites":[
        {"site_id":HOME,"status":"current","fresh":True},
        {"site_id":OFFICE,"status":"partitioned","fresh":False,"stale_age_ms":300000,"reconciliation_required":True},
    ]}
    history=[
        {"event_id":"h1","event_type":"permission_revoked","governing_site_id":HOME,"revision":10,"revocation_epoch":8,"occurred_at":1000,"detail":{"scope":"semantic_world_read"}},
        {"event_id":"h2","event_type":"recognition_consent_revoked","governing_site_id":HOME,"revision":11,"revocation_epoch":9,"occurred_at":1100,"detail":{"scope":"person_recognition"}},
    ]
    return policy,topology,sync,history


def run():
    policy,topology,sync,history=fixture()
    report=access.build_report(policy,topology,sync,history=history,now_ms=2000)
    assert report["protocol"]==access.FEDERATION_ACCESS_OPERATIONS_PROTOCOL
    grant=next(row for row in report["grants"] if row["scope"]=="semantic_world_read")
    assert grant["status"]=="revoked"
    assert grant["effective_allowed"] is False
    assert grant["stale_grant_suppressed"] is True
    assert report["peers"][0]["sync"]["status"]=="partitioned"
    assert report["peers"][0]["revocation_protection"]["revocation_wins"] is True
    assert report["peers"][0]["revocation_protection"]["stale_remote_grant_can_restore_access"] is False

    consent=next(row for row in report["consents"] if row["scope"]=="person_recognition")
    assert consent["status"]=="revoked"
    assert consent["effective_allowed"] is False
    person=next(row for row in report["identities"] if row["canonical_identity_id"]==PERSON)
    assert person["state"]=="revoked"

    sharing=report["peers"][0]["categories"]["federation_sharing"]
    assert sharing["state"]=="limited"
    assert report["counts"]["stale_grants_suppressed"]==2
    assert report["agent_context"]["revocation_wins"] is True
    assert [row["event_id"] for row in report["history"]]==["h1","h2"]

    cap=access.public_capability()
    assert cap["local_site_policy_operations"] is True
    assert cap["local_grant_revoke_operations"] is True
    assert cap["local_consent_operations"] is True
    assert cap["revocation_wins"] is True
    assert cap["stale_remote_grant_can_restore_access"] is False
    assert cap["cloud_read_only"] is True
    assert cap["paired_apps_read_only"] is True
    assert cap["authority_mutation"] is False
    assert cap["cross_site_identity_merge"] is False

    main_source=(ROOT/"app/main.py").read_text(encoding="utf-8")
    paired_source=(ROOT/"app/tracky_api.py").read_text(encoding="utf-8")
    service_source=(ROOT/"app/services/tracky_federation_access_operations.py").read_text(encoding="utf-8")
    agent_source=(ROOT/"app/services/tracky_federated_agent_context.py").read_text(encoding="utf-8")
    policy_source=(ROOT/"app/services/tracky_federation_policy.py").read_text(encoding="utf-8")
    ui=(ROOT/"ui/federation-access-operations-v280.js").read_text(encoding="utf-8")
    index=(ROOT/"ui/index.html").read_text(encoding="utf-8")
    spec=(ROOT/"HomeServer.spec").read_text(encoding="utf-8")

    assert '/api/v1/control/federation-access/site-policy' in main_source
    assert '/api/v1/control/federation-access/permission' in main_source
    assert '/api/v1/control/federation-access/consent' in main_source
    assert '@router.get("/api/v1/tracky/federation-access")' in paired_source
    assert '@router.post("/api/v1/tracky/federation-access' not in paired_source
    assert "revoke_permission" in service_source and "grant_permission" in service_source
    assert '"federation_access_operations"' in agent_source
    assert "_attach_sync_visibility" in agent_source
    assert "revokedRevision" not in policy_source
    assert "revocation_key" in policy_source
    assert "stale-grant-never-resurrects-access" in service_source
    assert "method:'PUT'" in ui and "method:'POST'" in ui
    assert "Revocation always wins." in index
    assert "federation-access-operations-v280.js" in index
    assert "federation-access-operations-v280.css" in index
    assert "('ui', 'ui')" in spec
    print("TRACKY_V280_FEDERATION_ACCESS_OPERATIONS=PASS")


if __name__=="__main__":
    run()
