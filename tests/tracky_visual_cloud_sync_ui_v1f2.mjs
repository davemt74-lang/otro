import assert from 'node:assert/strict';
import fs from 'node:fs';
const html=fs.readFileSync('ui/index.html','utf8');
const ui=fs.readFileSync('ui/tracky/owner-contact-link.js','utf8');
const service=fs.readFileSync('app/services/tracky_visual_contact_link.py','utf8');
const physical=fs.readFileSync('app/services/tracky_physical_context.py','utf8');
const api=fs.readFileSync('app/onboarding_api.py','utf8');
for(const id of ['onboardVisualCloud','onboardVisualCloudConsent','onboardVisualCloudEnable',
 'onboardVisualCloudDisable','onboardVisualCloudSync','onboardVisualCloudState']){
 assert.match(html,new RegExp('id="'+id+'"'));
}
assert.match(ui,/CLOUD_SCOPE = 'owner-self-cloud-status-only.v1'/);
assert.match(ui,/onboardVisualCloudConsent'\)\.checked/);
assert.match(ui,/window\.confirm\(warning\)/);
assert.match(ui,/window\.confirm\('Run your existing governed Tracky HTTPS site sync/);
assert.match(ui,/BASE\+'cloud-sharing'/);
assert.match(ui,/BASE\+'cloud-sync'/);
assert.match(ui,/No photo, face descriptor, contact details, IDs or signed local receipt/);
assert.match(service,/def cloud_projection/);
assert.match(service,/def mark_cloud_delivery/);
assert.match(service,/cloud_share_opt_in/);
assert.match(physical,/"visual_owner_association": visual_owner_projection/);
assert.match(physical,/mark_cloud_delivery\(package\["visual_owner_projection"\]\)/);
assert.match(api,/def visual_contact_cloud_sharing/);
assert.match(api,/def visual_contact_cloud_sync/);
assert.match(api,/_require_ui\(x_requested_with\)/);
const section=physical.slice(physical.indexOf('"health": {'),physical.indexOf('"forecast_calibration": tracky_forecast_calibration.cloud_projection()'));
assert.doesNotMatch(section,/(participant_ref|contact_ref|receipt_signature|local_participant_id|embeddings|portrait)/);
assert.doesNotMatch(ui,/setInterval\([^)]*cloud-sync|getUserMedia|video\.srcObject/);
console.log('TRACKY_VISUAL_CLOUD_UI_V1F2: independent consent, opt-in-only scalar, explicit revocation and no media PASS');
