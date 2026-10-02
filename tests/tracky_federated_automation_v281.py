from __future__ import annotations
import os,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
HOME="11111111-1111-4111-8111-111111111111";OFFICE="22222222-2222-4222-8222-222222222222"
HDEV="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";ODEV="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

with tempfile.TemporaryDirectory(prefix="tracky-v281-fa-") as data_dir:
 os.environ["HOMESERVER_DATA_DIR"]=data_dir;os.environ["VP3_OS_HARDWARE_ADAPTER"]="disabled"
 from app.database import initialize_database,db, migration_files
 from app.services import tracky_federated_automation,tracky_federation_sync,tracky_site_topology
 initialize_database();initialize_database()
 with db() as c:
  versions=[int(x["version"]) for x in c.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()]
 declared = [1] + [version for version, _ in migration_files()]
 assert declared == list(range(1, declared[-1] + 1))
 assert versions == declared
 for site,label in ((HOME,"Home"),(OFFICE,"Office")):tracky_site_topology.register_site(site_id=site,label=label)
 for device,label,site in ((HDEV,"Home Node",HOME),(ODEV,"Office Node",OFFICE)):
  tracky_site_topology.register_device(device_id=device,label=label,site_id=site,hardware_profile="Node",trust_state="trusted",roles=["site_authority"],capabilities={"site_authority_eligible":True})
 tracky_site_topology.claim_site_authority(site_id=HOME,device_id=HDEV);tracky_site_topology.claim_site_authority(site_id=OFFICE,device_id=ODEV)
 tracky_federation_sync.set_local_site_id(HOME)
 payload={"automation_id":"fa:morning-open","revision":1,"idempotency_key":"fa:morning-open:1","name":"Morning Open","origin_site_id":HOME,"state":"active",
  "trigger":{"kind":"world_state","source_site_id":HOME,"event_key":"room.occupied"},
  "steps":[
   {"step_id":"home-check","action_type":"data_operation","authority_site_id":HOME,"target_site_id":HOME,"action_key":"world.validate","required_permissions":["semantic_world_read"]},
   {"step_id":"office-light","action_type":"physical_action","authority_site_id":OFFICE,"target_site_id":OFFICE,"device_id":ODEV,"action_key":"light.on","required_permissions":["device_control"],"depends_on":["home-check"]}
  ]}
 try:tracky_federated_automation.create_definition({**payload,"automation_id":"","idempotency_key":""},actor={"actor_type":"owner","actor_id":"owner"});raise AssertionError("identity-free definition accepted")
 except tracky_federated_automation.FederatedAutomationError:pass
 deterministic={**payload,"automation_id":"","idempotency_key":"fa:deterministic"}
 dd1=tracky_federated_automation.create_definition(deterministic,actor={"actor_type":"owner","actor_id":"owner"})
 dd2=tracky_federated_automation.create_definition(deterministic,actor={"actor_type":"owner","actor_id":"owner"})
 assert dd1["automation_id"]==dd2["automation_id"]
 d=tracky_federated_automation.create_definition(payload,actor={"actor_type":"owner","actor_id":"owner"})
 assert d["protocol"]=="physical_federated_automation.v1" and d["version"]=="2.81"
 assert d["participating_site_ids"]==sorted([HOME,OFFICE]);assert d["safety"]["execution_enabled"] is False
 assert tracky_federated_automation.create_definition(payload,actor={"actor_type":"owner","actor_id":"owner"})["revision"]==1
 try:tracky_federated_automation.create_definition({**payload,"name":"Conflict"},actor={"actor_type":"owner","actor_id":"owner"});raise AssertionError("idempotency conflict not detected")
 except tracky_federated_automation.FederatedAutomationError:pass
 try:tracky_federated_automation.create_run({"automation_id":d["automation_id"]},actor={"actor_type":"owner","actor_id":"owner"});raise AssertionError("identity-free run accepted")
 except tracky_federated_automation.FederatedAutomationError:pass
 run=tracky_federated_automation.create_run({"automation_id":d["automation_id"],"run_id":"run-1","idempotency_key":"run-idem"},actor={"actor_type":"owner","actor_id":"owner"})
 assert run["state"]=="waiting";assert {x["step_id"]:x["state"] for x in run["steps"]}=={"home-check":"ready","office-light":"blocked"}
 assert tracky_federated_automation.create_run({"automation_id":d["automation_id"],"run_id":"run-2","idempotency_key":"run-idem"},actor={"actor_type":"owner","actor_id":"owner"})["run_id"]=="run-1"
 exp=tracky_federated_automation.create_run({"automation_id":d["automation_id"],"run_id":"run-exp","idempotency_key":"run-exp","deadline_at_ms":1},actor={"actor_type":"owner","actor_id":"owner"})
 assert tracky_federated_automation.expire_due_runs(now_ms=2)["expired"]==1
 assert tracky_federated_automation.get_run(exp["run_id"])["state"]=="expired"
 try:tracky_federated_automation.transition_run("run-1","running",actor={"actor_type":"system"});raise AssertionError("Section 1 executed a run")
 except tracky_federated_automation.FederatedAutomationError as exc:assert exc.status_code==409
 try:tracky_federated_automation.cancel_run("run-1",reason="agent_cancel",actor={"actor_type":"agent","actor_id":"agent"});raise AssertionError("agent mutated authoritative run state")
 except tracky_federated_automation.FederatedAutomationError as exc:assert exc.status_code==403
 cancelled=tracky_federated_automation.cancel_run("run-1",reason="operator_cancel",actor={"actor_type":"owner"})
 assert cancelled["state"]=="cancelled" and all(x["state"]=="cancelled" for x in cancelled["steps"])
 try:tracky_federated_automation.transition_run("run-1","ready",actor={"actor_type":"system"});raise AssertionError("terminal run mutated")
 except tracky_federated_automation.FederatedAutomationError as exc:assert exc.status_code==409
 try:tracky_federated_automation.create_definition({**payload,"automation_id":"fa:agent","idempotency_key":"fa:agent:1"},actor={"actor_type":"agent","actor_id":"agent"});raise AssertionError("agent created definition")
 except tracky_federated_automation.FederatedAutomationError as exc:assert exc.status_code==403
 projection=tracky_federated_automation.cloud_projection();assert projection["cloud_read_only"] is True and projection["remote_action_execution"] is False
 cap=tracky_federated_automation.public_capability();assert cap["schema_version"]==56 and cap["execution_enabled"] is True and cap["durable_action_ledger"] is True and cap["deadline_expiration"] is True
 with db() as c:
  assert c.execute("SELECT COUNT(*) FROM tracky_federated_automation_events").fetchone()[0]>=3
  assert c.execute("SELECT COUNT(*) FROM tracky_federated_automation_definitions").fetchone()[0]==2
  assert c.execute("SELECT COUNT(*) FROM tracky_federated_automation_runs").fetchone()[0]==2
 try:
  with db() as c:c.execute("UPDATE tracky_federated_automation_events SET state='tampered' WHERE id=(SELECT MIN(id) FROM tracky_federated_automation_events)")
  raise AssertionError("audit event update was allowed")
 except Exception as exc:assert "audit events are immutable" in str(exc)
 try:
  with db() as c:c.execute("DELETE FROM tracky_federated_automation_events WHERE id=(SELECT MIN(id) FROM tracky_federated_automation_events)")
  raise AssertionError("audit event delete was allowed")
 except Exception as exc:assert "audit events are immutable" in str(exc)


main=(ROOT/"app/main.py").read_text(encoding="utf-8")
tracky_api=(ROOT/"app/tracky_api.py").read_text(encoding="utf-8")
physical=(ROOT/"app/services/tracky_physical_context.py").read_text(encoding="utf-8")
ci=(ROOT/".github/workflows/ci.yml").read_text(encoding="utf-8")
release_workflow=(ROOT/".github/workflows/homeserver-v24-release.yml").read_text(encoding="utf-8")
migration=(ROOT/"database/migrations/054_tracky_federated_automation.sql").read_text(encoding="utf-8")
assert '@app.get("/api/v1/control/federated-automation")' in main
assert '@app.post("/api/v1/control/federated-automation/definitions")' in main
assert '@app.post("/api/v1/control/federated-automation/runs")' in main
assert '@app.post("/api/v1/control/federated-automation/runs/{run_id}/cancel")' in main
assert '/api/v1/control/federated-automation/runs/{run_id}/execute' not in main
assert '@router.get("/api/v1/tracky/federated-automation")' in tracky_api
assert '"federated_automation": tracky_federated_automation.public_capability()' in physical
assert '"federated_automation": tracky_federated_automation.agent_context()' in physical
assert '"federated_automation": federated_automation_projection' in physical
assert "tracky_federated_automation.recover_incomplete_runs()" in main
assert "tracky_federated_automation_v281.py" in ci
assert "tracky_federated_automation_v281.py" in release_workflow
assert "tracky_federated_automation_definitions" in migration and "tracky_federated_automation_events" in migration
assert "trg_tracky_federated_automation_events_no_update" in migration and "trg_tracky_federated_automation_events_no_delete" in migration
# The package version is calculated from the canonical registry at build time.
assert "current_schema_version = [int]$schema" in ci
assert "from app.database import migration_files;" in ci
assert f"if ($LASTEXITCODE -ne 0 -or -not $schema -or [int]$schema -lt {max([1] + [v for v, _ in migration_files()])})" in ci
assert "feature_track = 'Tracky V2.81'" in ci
assert "feature_section = 3" in ci
assert "federated_automation_execution_enabled = $true" in ci
print("TRACKY_V281_FEDERATED_AUTOMATION_LEDGER=PASS")
