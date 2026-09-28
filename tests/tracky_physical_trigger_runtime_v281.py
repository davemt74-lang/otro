from __future__ import annotations
import json, tempfile
from datetime import datetime, timezone
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.database import db, initialize_database
from app.services import tracky_federated_automation as fa

HOME="11111111-1111-4111-8111-111111111111"
OFFICE="22222222-2222-4222-8222-222222222222"

def main():
    import os
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["HOMESERVER_DATA_DIR"]=tmp
        initialize_database();initialize_database()
        fa.tracky_federation_sync.local_site_id=lambda auto_pin=False: HOME
        fa.tracky_site_topology.current_topology=lambda:{"sites":[{"id":HOME},{"id":OFFICE}]}
        fa.federated_data.reconciliation_state=lambda peer:{"needs_reconciliation":False}
        payload={"automation_id":"fa:arrival","idempotency_key":"fa:arrival:1","origin_site_id":HOME,"state":"active",
          "trigger":{"kind":"presence","source_site_id":HOME,"event_key":"person.arrived","debounce_ms":10000,"config":{"min_confidence":0.8,"conditions":[{"field":"room_id","op":"eq","value":"kitchen"}]}},
          "steps":[{"step_id":"context","action_type":"data_operation","authority_site_id":HOME,"target_site_id":HOME,"action_key":"world.validate"}]}
        d=fa.create_definition(payload,actor={"actor_type":"owner","actor_id":"owner"})
        event={"event_id":"evt-100","sequence":1,"event_type":"person.arrived","severity":"notable","confidence":0.92,"privacy_class":"user_approved","occurred_at":datetime.now(timezone.utc).isoformat(),"room_id":"kitchen"}
        with db() as c:c.execute("INSERT INTO tracky_physical_events(event_id,sequence_no,event_type,severity,confidence,privacy_class,occurred_at,event_json) VALUES (?,?,?,?,?,?,?,?)",("evt-100",1,"person.arrived","notable",0.92,"user_approved",event["occurred_at"],json.dumps(event,separators=(",",":"),sort_keys=True)))
        result=fa.process_physical_trigger_events(["evt-100"])
        assert result[0]["decision"]=="accepted" and result[0]["run_id"]
        run=fa.get_run(result[0]["run_id"]);assert run["state"] in {"ready","waiting"} and run["safety"]["execution_enabled"] is False
        replay=fa.process_physical_trigger_events(["evt-100"]);assert replay[0]["decision"]=="duplicate"
        cap=fa.public_capability();assert cap["section"]==2 and cap["schema_version"]==55 and cap["trigger_execution_enabled"] is False
        with db() as c:
            assert c.execute("SELECT COUNT(*) FROM tracky_federated_automation_trigger_receipts").fetchone()[0]==1
            try:c.execute("DELETE FROM tracky_federated_automation_trigger_receipts");raise AssertionError("mutable trigger receipt")
            except Exception as exc:assert "trigger receipts are immutable" in str(exc)
    print("TRACKY_V281_PHYSICAL_WORLD_TRIGGER_RUNTIME=PASS")
if __name__=="__main__":main()
