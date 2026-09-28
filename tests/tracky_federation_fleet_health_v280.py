from __future__ import annotations
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from app.services import tracky_federation_fleet_health as fleet

HOME="11111111-1111-4111-8111-111111111111"
OFFICE="22222222-2222-4222-8222-222222222222"

def run():
    source=(ROOT/"app/services/tracky_federation_fleet_health.py").read_text(encoding="utf-8")
    physical=(ROOT/"app/services/tracky_physical_context.py").read_text(encoding="utf-8")
    api=(ROOT/"app/tracky_api.py").read_text(encoding="utf-8")
    main=(ROOT/"app/main.py").read_text(encoding="utf-8")
    assert fleet.FEDERATION_FLEET_HEALTH_PROTOCOL=="physical_federation_fleet_health.v1"
    cap=fleet.public_capability()
    assert cap["section7_health_is_authoritative"] is True
    assert cap["diagnostics_never_promote_federation_freshness"] is True
    assert cap["cloud_read_only"] is True
    assert cap["remote_command_execution"] is False
    assert cap["authority_mutation"] is False
    assert "fleet_management.remote_diagnostics_summary()" in source
    assert '"diagnostic_content_included":False' in source
    assert '"network_endpoint_details_included":False' in source
    assert "federation_fleet_health" in physical
    assert "FEDERATION_FLEET_HEALTH_PROTOCOL" in physical
    assert '@router.get("/api/v1/tracky/federation-fleet-health")' in api
    assert '/api/v1/control/federation-fleet-health' in main
    assert '@router.post("/api/v1/tracky/federation-fleet-health' not in api
    print("TRACKY_V280_FEDERATION_FLEET_HEALTH=PASS")

if __name__=="__main__":
    run()
