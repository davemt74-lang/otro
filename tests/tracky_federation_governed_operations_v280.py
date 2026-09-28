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
    assert "tracky_federation_operation_ledger" in migration and "idempotency_key" in migration
    assert "tracky_federation_reconciliation.schedule_retry" in source
    assert "claim_site_authority" in source and "authority_epoch_after" in source
    assert "fleet_management.request_update" in source and "remove_inventory_device" in source
    assert "hardware_adapters.stop(); hardware_adapters.start()" in source
    assert '@app.post("/api/v1/control/federation-operations/propose")' in main
    assert '@app.post("/api/v1/control/federation-operations/{request_id}/execute")' in main
    assert '@router.get("/api/v1/tracky/federation-governed-operations")' in api
    assert '@router.post("/api/v1/tracky/federation-governed-operations' not in api
    assert '"federation_governed_operations"' in physical
    assert 'id="governedFederationOperationsV280"' in ui
    assert "/api/v1/control/federation-operations" in js
    print("TRACKY_V280_FEDERATION_GOVERNED_OPERATIONS=PASS")

if __name__=="__main__": run()
