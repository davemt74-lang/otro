from __future__ import annotations
import os,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
HOME="11111111-1111-4111-8111-111111111111";DEV="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
with tempfile.TemporaryDirectory(prefix="tracky-v281-exec-") as data_dir:
 os.environ["HOMESERVER_DATA_DIR"]=data_dir;os.environ["VP3_OS_HARDWARE_ADAPTER"]="disabled"
 from app.database import initialize_database,db
 from app.services import tracky_federated_automation as fa,tracky_federation_sync,tracky_site_topology
 initialize_database();initialize_database()
 tracky_site_topology.register_site(site_id=HOME,label="Home")
 tracky_site_topology.register_device(device_id=DEV,label="Node",site_id=HOME,hardware_profile="Node",trust_state="trusted",roles=["site_authority"],capabilities={"site_authority_eligible":True})
 tracky_site_topology.claim_site_authority(site_id=HOME,device_id=DEV);tracky_federation_sync.set_local_site_id(HOME)
 fa.federated_data.reconciliation_state=lambda peer:{"needs_reconciliation":False}
 payload={"automation_id":"fa:exec","idempotency_key":"fa:exec","origin_site_id":HOME,"state":"active","trigger":{"kind":"manual","source_site_id":HOME},
  "steps":[{"step_id":"routine","action_type":"local_routine","authority_site_id":HOME,"target_site_id":HOME,"action_key":"morning","required_permissions":["automation.execute"],"approval_mode":"inherit"}]}
 fa.create_definition(payload,actor={"actor_type":"owner","actor_id":"owner"})
 run=fa.create_run({"automation_id":"fa:exec","idempotency_key":"run:exec"},actor={"actor_type":"owner","actor_id":"owner"})
 dispatch=fa.create_step_dispatch(run["run_id"],"routine",actor={"actor_type":"system","actor_id":"orchestrator"})
 assert dispatch["authority_epoch"]==1 and dispatch["dispatch_id"].startswith("fad-")
 fa.local_automation.run_routine=lambda *a,**k:{"execution_id":77,"status":"completed"}
 receipt=fa.execute_step_dispatch(dispatch,executor_device_id=DEV,permission_grants=["automation.execute"],actor={"actor_type":"system","actor_id":"authority"})
 assert receipt["status"]=="completed"
 assert fa.execute_step_dispatch(dispatch,executor_device_id=DEV,permission_grants=["automation.execute"])["receipt_id"]==receipt["receipt_id"]
 completed=fa.apply_execution_receipt(receipt,actor={"actor_type":"system","actor_id":"orchestrator"})
 assert completed["state"]=="completed" and completed["steps"][0]["state"]=="completed"
 cap=fa.public_capability();assert cap["section"]==3 and cap["schema_version"]==56 and cap["execution_enabled"] is True and cap["cloud_execution_allowed"] is False
 with db() as c:
  assert c.execute("SELECT COUNT(*) FROM tracky_federated_automation_execution_receipts").fetchone()[0]==1
  try:c.execute("DELETE FROM tracky_federated_automation_execution_receipts");raise AssertionError("mutable receipt")
  except Exception as exc:assert "execution receipts are immutable" in str(exc)
 print("TRACKY_V281_DISTRIBUTED_ACTION_EXECUTION=PASS")
