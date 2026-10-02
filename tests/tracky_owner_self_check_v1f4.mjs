import assert from 'node:assert/strict';
import fs from 'node:fs';
import {evaluateOwnerSelfCheck,OWNER_SELF_CHECK_DURATION_MS} from '../ui/tracky/src/owner-self-check-core.js';

const participant={id:'self-local',
 recognitionEnabled:true,embeddings:[[1,0,0],[.99,.01,0],[.98,.02,0]],
 visualEnrollment:{scope:'owner-self',consentedAt:'2026-10-02T00:00:00Z'}};
const face={score:0.99,box:[360,200,240,240],rotation:{yaw:0,pitch:0},
 embedding:[1,0,0]};
const verify=patch=>evaluateOwnerSelfCheck({
 consent:true,active:true,participant,faces:[face],width:960,height:720,...patch
});
assert.equal(OWNER_SELF_CHECK_DURATION_MS,12000);
assert.equal(verify({consent:false}).state,'approval_required');
assert.equal(verify({active:false}).matched,false);
assert.equal(verify({participant:null}).state,'profile_unavailable');
assert.equal(verify({participant:{...participant,recognitionEnabled:false}}).matched,false);
assert.equal(verify({participant:{...participant,visualEnrollment:{scope:'other'}}}).matched,false);
assert.equal(verify({participant:{...participant,embeddings:[[1,0,0]]}}).matched,false);
assert.equal(verify({faces:[]}).state,'one_face_required');
assert.equal(verify({faces:[face,face]}).state,'multiple_faces');
assert.equal(verify({faces:[{...face,embedding:[0]}]}).state,'descriptor_unavailable');
assert.equal(verify({faces:[{...face,embedding:[Number.NaN,0,0]}]}).state,'descriptor_unavailable');
assert.equal(verify({faces:[{...face,box:[0,0,6,6],score:0.3}]}).state,'improve_lighting_or_center');
assert.equal(verify({faces:[{...face,embedding:[-1,0,0]}]}).state,'local_comparison_not_matched');
const local=verify({});
assert.equal(local.state,'local_similarity_only');
assert.equal(local.matched,true);
assert.equal(local.independently_verified,false);
assert.equal(local.hardware_certified,false);
assert.deepEqual(Object.keys(local).sort(),['hardware_certified','independently_verified','matched','state'].sort());
const code=fs.readFileSync('ui/tracky/owner-self-check.js','utf8');
const html=fs.readFileSync('ui/index.html','utf8');
for(const id of ['onboardSelfCheck','onboardSelfCheckStart','onboardSelfCheckStop',
 'onboardSelfCheckConsent','onboardSelfCheckState','onboardSelfCheckVideo','onboardSelfCheckPreview']){
 assert.match(html,new RegExp('id="'+id+'"'));
}
assert.match(html,/type="module" src="\/assets\/tracky\/owner-self-check\.js"/);
assert.match(code,/listParticipants\(\)/);
assert.match(code,/evaluateOwnerSelfCheck/);
assert.match(code,/state\.privacy_engaged===false/);
assert.match(code,/state\.scope===SCOPE/);
assert.match(code,/window\.confirm\('Compare one live face/);
assert.match(code,/OWNER_SELF_CHECK_DURATION_MS/);
assert.match(code,/getTracks\(\)\)track\.stop\(\)/);
assert.match(code,/document\.hidden\)stop\(\)/);
assert.match(code,/window\.addEventListener\('pagehide',close\)/);
assert.match(code,/local similarity demonstration|similarity demonstration|similarity only|unverified similarity demonstration/);
assert.doesNotMatch(code,/api\('report'|fetch\([^)]*method:\s*'POST'|saveParticipant\(/);
assert.doesNotMatch(code,/setInterval|localStorage|sendBeacon|httpx|requests\.post/);
const other=['ui/tracky/owner-visual.js','ui/tracky/eyes-runtime.js','ui/tracky/native-camera.js']
 .map(f=>fs.readFileSync(f,'utf8'));
for(const source of other)assert.match(source,/TrackyOwnerSelfCheck\?\.isActive\(\)/);
console.log('TRACKY_OWNER_SELF_CHECK_V1F4: consent, quality, one face, local match, ephemeral expiry, competing camera guards PASS');
