import assert from 'node:assert/strict';
import fs from 'node:fs';
import {autoEnrollmentReceipt} from '../ui/tracky/src/auto-enrollment-core.js';

const html=fs.readFileSync('ui/index.html','utf8');
const link=fs.readFileSync('ui/tracky/owner-contact-link.js','utf8');
const owner=fs.readFileSync('ui/tracky/owner-visual.js','utf8');
const server=fs.readFileSync('app/services/tracky_visual_contact_link.py','utf8');
for(const id of ['onboardVisualLink','onboardVisualLinkTarget',
                 'onboardVisualLinkConsent','onboardVisualLinkStart',
                 'onboardVisualLinkRevoke','onboardVisualLinkState']){
  assert.match(html,new RegExp('id="'+id+'"'));
}
assert.match(html,/type="module" src="\/assets\/tracky\/owner-contact-link\.js"/);
assert.match(link,/from '\.\/src\/participant-store\.js'/);
assert.match(link,/report\.local_participant_id/);
assert.match(link,/owner\.embeddings\?\.length >= 3/);
assert.match(link,/window\.confirm\('Attribute your browser-reported self-profile/);
assert.match(link,/json\(BASE\+'associate'/);
assert.match(link,/json\(BASE\+'revoke'/);
assert.match(link,/cloud.*sharing.*will be enabled/i);
assert.doesNotMatch(link,/getUserMedia|CanvasRenderingContext|trackingEnabled\s*:\s*true/);
const payload=link.slice(link.indexOf("state=await json(BASE+'associate'"),link.indexOf("el('onboardVisualLinkConsent').checked=false;"));
assert.match(payload,/participant_id:owner\.id,contact_id:id/);
assert.doesNotMatch(payload,/embedding|primaryPhoto|latestPhoto|portrait|base64/i);
assert.match(owner,/window\.dispatchEvent\(new Event\("tracky:visual-state-changed"\)\)/);
assert.match(server,/hmac\.new/);
assert.match(server,/_commit\(row, "tracky\.visual\.association\.approved"\)/);
assert.match(server,/_commit\(row, "tracky\.visual\.association\.revoked"\)/);
assert.doesNotMatch(server,/requests\.post|httpx\.post|requests\.put/);
const receipt=autoEnrollmentReceipt({id:'self-local',visualEnrollment:{
  scope:'owner-self',consentedAt:'2026-10-01T20:00:00Z'},
  recognitionEnabled:true,embeddings:[[1],[2],[3]]});
assert.equal(receipt?.scope,'owner-self');
assert.equal(receipt.cloudSync,'not_enabled');
assert.equal(receipt.contactCreation,'requires_owner_approval');
assert.equal('embeddings' in receipt,false);
console.log('TRACKY_VISUAL_CONTACT_LINK_UI_V1F1: explicit consent, existing contact, local biometric exclusion, revocation PASS');
