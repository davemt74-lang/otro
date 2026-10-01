import assert from 'node:assert/strict';
import fs from 'node:fs';
import {autoEnrollmentDecision} from '../ui/tracky/src/auto-enrollment-core.js';
import {participantRecord} from '../ui/tracky/src/participant-core.js';

const source=fs.readFileSync('ui/tracky/owner-visual.js','utf8');
const store=fs.readFileSync('ui/tracky/src/participant-store.js','utf8');
const html=fs.readFileSync('ui/index.html','utf8');
const session={consent:false,active:true,samples:[],lastCaptureAt:0};
const face=(yaw=0,cx=.5)=>({quality:.9,embedding:[.25,.5,.7],
  rotation:{yaw},box:{cx,cy:.5}});
assert.equal(autoEnrollmentDecision(session,{faceCount:1,face:face(),now:1000}).capture,false);
session.consent=true;
assert.equal(autoEnrollmentDecision(session,{faceCount:2,face:face(),now:1000}).capture,false);
let first=autoEnrollmentDecision(session,{faceCount:1,face:face(),now:1000});
assert.equal(first.capture,true);
session.samples.push({pose:first.pose});session.lastCaptureAt=1000;
assert.equal(autoEnrollmentDecision(session,{faceCount:1,face:face(12),now:1100}).capture,false);
assert.equal(autoEnrollmentDecision(session,{faceCount:1,face:face(),now:2600}).capture,false);
assert.equal(autoEnrollmentDecision(session,{faceCount:1,face:face(12),now:2600}).capture,true);
const record=participantRecord({name:'My visual profile',embeddings:[[1],[2],[3]],visualEnrollment:{
  scope:'owner-self',consentedAt:'2026-10-01T20:00:00.000Z',
  automatic:true,cloudSync:true,trackingEnabled:true,contactCreation:'auto'
}});
assert.equal(record.visualEnrollment.scope,'owner-self');
assert.equal(record.visualEnrollment.cloudSync,false);
assert.equal(record.visualEnrollment.trackingEnabled,false);
assert.equal(record.visualEnrollment.contactCreation,'requires_owner_approval');
assert.match(store,/indexedDB.open\(DB_NAME, DB_VERSION\)/);
assert.match(source,/window.confirm\('Enroll only yourself/);
assert.match(source,/navigator.mediaDevices.getUserMedia/);
assert.match(source,/const record=await saveParticipant/);
assert.match(source,/await reportLocal\(record,session\)/);
assert.match(source,/await deleteParticipant\(id\)/);
assert.match(source,/if\(active\)void stop\(\)/);
assert.match(source,/generation!==epoch/);
assert.match(source,/browser.reported|browser-reported/i);
const reportBody=source.slice(source.indexOf('async function reportLocal('),source.indexOf('async function scan('));
assert.match(reportBody, /participant_id:record\.id,samples:record\.embeddings\.length/);
assert.doesNotMatch(reportBody, /embeddings\s*:/, 'semantic report cannot transmit face descriptors');
assert.match(source, /state\.privacy_engaged/, 'capture must observe the physical privacy switch');
assert.match(html,/id="onboardVisualConsent"/);
assert.match(html,/No other person is enrolled/);
console.log('Bundled Tracky browser module, local sample/consent gating and non-biometric reporting PASS');
