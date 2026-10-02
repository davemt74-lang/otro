import assert from 'node:assert/strict';
import fs from 'node:fs';
const page=fs.readFileSync('ui/index.html','utf8');
const ui=fs.readFileSync('ui/tracky/agent-eyes.js','utf8');
const service=fs.readFileSync('app/services/tracky_agent_eyes_acceptance.py','utf8');
const api=fs.readFileSync('app/onboarding_api.py','utf8');
for(const id of ['AcceptancePanel','AcceptanceStep','AcceptanceObserved','AcceptanceRecord','AcceptanceStatus','TestLease'])
  assert.match(page,new RegExp('id="trackyAgentEyes'+id+'"'));
assert.match(ui,/suspendHeartbeat/);
assert.match(ui,/else if\(!suspendHeartbeat\)/);
assert.match(ui,/installed-exercise\/record/);
assert.match(ui,/inspected_camera_release:true/);
assert.match(ui,/!visible\(\)/);
assert.match(service,/session_binding\.get\("model_sha256"\) == digest/);
assert.match(service,/owner_exercise_complete/);
assert.match(service,/unattended_perception_allowed": False/);
assert.match(api,/@router\.post\("\/visual\/agent-eyes\/installed-exercise\/record"\)/);
assert.doesNotMatch(ui,/getUserMedia|VideoCapture|sessionStorage|localStorage/);
console.log('TRACKY_AGENT_EYES_INSTALLED_EXERCISE_UI_V1G2B3: guided owner exercises, no new camera runtime PASS');
