import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const retained=["tracky_contract_join_v273","tracky_reliability_v274","tracky_governed_actions_v275","tracky_forecast_calibration_v276","tracky_model_lifecycle_v277","tracky_site_topology_v278","tracky_federated_world_v278","tracky_federation_sync_v278","tracky_mobile_transition_v278","tracky_identity_continuity_v278","tracky_federated_agent_context_v278","tracky_federation_policy_v278","tracky_federated_query_v278","tracky_federation_reconciliation_v278","tracky_golden_multisite_release_v278","tracky_v280_golden_operational_release","tracky_federated_automation_v281","hosting_runtime_v100"];
for(const name of retained){
  const source=readFileSync('tests/'+name+'.py','utf8');
  assert.ok(source.includes('migration_files'), name+' must load canonical migrations');
  assert.ok(source.includes('assert declared == list(range(1, declared[-1] + 1))'),
    name+' must require contiguous migration registry');
  assert.ok(source.includes('assert versions == declared'),
    name+' must compare applied and declared versions');
  assert.ok(!/assert versions\\s*==\\s*list\\(range\\(1,\\s*61\\)\\)/.test(source),
    name+' must not freeze the old 060 ceiling');
}
const workflow=readFileSync('.github/workflows/ci.yml','utf8');
assert.ok(workflow.includes('current_schema_version = [int]$schema'));
assert.ok(workflow.includes('from app.database import migration_files;'));
assert.ok(workflow.includes("owner_visual_identity_claim = 'owner_attributed_unverified'"));
assert.ok(workflow.includes('native_hardware_certified = $false'));
assert.ok(workflow.includes('dist/HomeServerSetup.exe'));
assert.ok(workflow.includes("SHA256SUMS.txt"));
console.log('TRACKY_V24_RELEASE_BASELINES_V1F9: 18 current-chain suites, truthful manifest and installer checks PASS');
