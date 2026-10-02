import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const retained=[
  'contacts_continuity_v241.py',
  'knowledge_continuity_v242.py',
  'task_calendar_continuity_v243.py',
  'file_document_continuity_v244.py',
  'agent_brain_memory_continuity_v245.py',
  'reconnect_reconciliation_v246.py',
  'homeserver_v24_release_acceptance.py'
];
for(const filename of retained){
  const source=readFileSync('tests/'+filename,'utf8');
  assert.match(source,/migration_files/,`Migration registry absent in ${filename}`);
  assert.match(source,/assert declared == list\(range\(1, declared\[-1\] \+ 1\)\)/,
    `Contiguous migration guard missing in ${filename}`);
  assert.match(source,/assert versions == declared/,
    `Applied-vs-declared comparison missing in ${filename}`);
  assert.doesNotMatch(source,/assert versions == list\(range\(1,\s*61\)\)/);
}
const launcher=readFileSync('desktop/launcher.py','utf8');
const test=readFileSync('tests/single_instance.py','utf8');
assert.match(launcher,/threading.Timer\(0\.6, self.open_welcome\)\.start\(\)/);
assert.match(test,/threading.Timer\(0\.6, self.open_welcome\)\.start\(\)/);
assert.match(test,/mark_first_run_prompted\(\)/);
assert.match(test,/self\.open_control_center/);
console.log('HOMESERVER_V24_HISTORICAL_BASELINES_V1F8: future-safe contiguous migrations and current launcher PASS');
