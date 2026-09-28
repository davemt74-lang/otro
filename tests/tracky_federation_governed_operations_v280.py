from __future__ import annotations
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from app.services import tracky_federation_governed_operations as ops

def run():
    source=(ROOT/"app/services/tracky_federation_governed_operations.py").read_text(encoding="utf-8")
    main=(ROOT/"app/main.py").read_text(encoding="utf-8")
    api=(ROOT/"app/tracky_api.py").read_text(encoding="utf-8")
    physical=(ROOT/"app/services/tracky_physical_context.py").read_text(encoding="utf-8")
    ui=(ROOT/"ui/index.html").read_text(encoding="utf-8")
    js=(ROOT/"ui/federation-governed-operations-v280.js").read_text(encoding="utf-8")
    migration=(ROOT/"database/migrations/053_tracky_federation_governed_operations.sql").read_text(encoding="utf-8")
    assert ops.PROTOCOL=="physical_federation_governed_operations.v1"
    cap=ops.public_capability()
    assert cap["cloud_execution_allowed"] is False
    assert cap["agent_proposal_only"] is True
    assert cap["completion_requires_authoritative_reconciliation"] is True
    assert cap["authority_transfer_requires_epoch_advance"] is True
    assert cap["operation_expiration"] is True
    assert cap["queued_before_running"] is True
    assert cap["revocation_wins"] is True
    assert "tracky_federation_operation_ledger" in migration and "idempotency_key" in migration
    assert "tracky_federation_reconciliation.schedule_retry" in source
    assert '"revoke_site"' in source and "_revoke_site_access" in source
    assert "revocation_wins" in source
    assert "_set(request_id, \"queued\"" in source
    assert "expires_at_ms" in source and "expires_at_ms" in migration
    assert "claim_site_authority" in source and "authority_epoch_after" in source
    assert "fleet_management.request_update" in source and "remove_inventory_device" in source
    assert 'trust_state="revoked"' in source
    assert "hardware_adapters.stop()" in source and "hardware_adapters.start()" in source
    assert '@app.post("/api/v1/control/federation-governed-operations/propose")' in main
    assert '@app.post("/api/v1/control/federation-governed-operations/{request_id}/execute")' in main
    assert '@router.get("/api/v1/tracky/federation-governed-operations")' in api
    assert '@router.post("/api/v1/tracky/federation-governed-operations' not in api
    assert '"federation_governed_operations"' in physical
    assert main.count('@app.get("/api/v1/control/federation-operations")') == 1
    assert main.count('@app.get("/api/v1/control/federation-governed-operations")') == 1
    assert "ingest_cloud_requests" in source
    assert "cloud_request_requires_local_approval" in source
    assert "operation_requires_origin_local" in source
    assert "wrong origin HomeServer" in source
    assert "update_package_required" in source
    assert 'body.get("federation_operation_requests")' in physical
    assert "\\n" not in physical
    assert 'id="governedFederationOperationsV280"' in ui
    assert "/api/v1/control/federation-governed-operations" in js
    assert "data-fgo-action" in js and "data-fgo-create" in ui
    assert "package_sha256" in ui
    print("TRACKY_V280_FEDERATION_GOVERNED_OPERATIONS=PASS")

if __name__=="__main__": run()
