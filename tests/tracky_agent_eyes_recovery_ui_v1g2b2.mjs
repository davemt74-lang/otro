import assert from 'node:assert/strict';
import fs from 'node:fs';
const html=fs.readFileSync('ui/index.html','utf8');
const js=fs.readFileSync('ui/tracky/agent-eyes.js','utf8');
const py=fs.readFileSync('app/services/tracky_agent_eyes_recovery.py','utf8');
const ev=fs.readFileSync('app/services/tracky_native_session_evidence.py','utf8');
for(const id of ['RecoveryPanel','RecoveryMessage','ReleaseObserved','FreshConsent','RecoveryAck'])
  assert.match(html,new RegExp('id="trackyAgentEyes'+id+'"'));
assert.match(js,/recovery\/acknowledge/);
assert.match(js,/recoveryRequired/);
assert.match(js,/!visible\(\)/);
assert.match(py,/def acknowledge\(/);
assert.match(py,/ready_for_owner_acknowledgement/);
assert.match(py,/physical_camera_release_verified": False/);
assert.match(ev,/"watchdog_stall"/);
assert.doesNotMatch(js,/getUserMedia|VideoCapture|sessionStorage|localStorage/);
console.log('TRACKY_AGENT_EYES_RECOVERY_UI_V1G2B2: owner report, no auto-resume, no false physical proof PASS');
