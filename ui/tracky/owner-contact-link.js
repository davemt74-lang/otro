/* Tracky 1F1: owner-confirmed link from this browser's local self-profile
 * to an EXISTING local HomeServer contact. No camera, embeddings, portrait,
 * automatic contact creation, Cloud identity assertion or biometric transport.
 */
import { listParticipants } from './src/participant-store.js';

const el = id => document.getElementById(id);
const BASE = '/api/v1/control/onboarding/visual/contact-link/';
const SCOPE = 'owner-self-existing-contact-association.v1';
const CLOUD_SCOPE = 'owner-self-cloud-status-only.v1';
let busy = false;
let state = null;
let visual = null;
let owner = null;
let searchTimer = null;
let cloudNotice = '';

async function json(url, body) {
  const resp = await fetch(url, {
    method: body === undefined ? 'GET' : 'POST',
    credentials:'same-origin', cache:'no-store',
    headers:{Accept:'application/json',...(body === undefined ? {} :
      {'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'})},
    ...(body === undefined ? {} : {body:JSON.stringify(body)})
  });
  const result = await resp.json().catch(()=>({}));
  if (!resp.ok) throw new Error(String(result.detail || 'Local association unavailable.'));
  return result;
}

function message(value) {
  if (el('onboardVisualLinkState')) el('onboardVisualLinkState').textContent = value;
}
function render() {
  const link = state || {};
  const available = visual?.phase === 'browser_reported' && visual?.consented === true &&
      owner?.id === visual.local_participant_id && owner.embeddings?.length >= 3;
  const active = link.active === true;
  const needsReview = link.state === 'needs_review';
  el('onboardVisualLinkStart').hidden = active;
  el('onboardVisualLinkRevoke').hidden = !active && !needsReview;
  el('onboardVisualLinkStart').disabled = busy || needsReview || !available ||
      !el('onboardVisualLinkConsent').checked || !el('onboardVisualLinkTarget').value;
  el('onboardVisualLinkRevoke').disabled = busy;
  el('onboardVisualLinkTarget').disabled = busy || active || needsReview;
  const opted=link.cloud_sharing_opted_in===true;
  const pending=link.cloud_revocation_pending===true;
  el('onboardVisualCloudEnable').hidden=opted;
  el('onboardVisualCloudDisable').hidden=!opted;
  el('onboardVisualCloudEnable').disabled=busy||!active||!el('onboardVisualCloudConsent').checked;
  el('onboardVisualCloudDisable').disabled=busy;
  el('onboardVisualCloudSync').hidden=!opted&&!pending;
  el('onboardVisualCloudSync').disabled=busy;
  const acknowledged=link.cloud_current_generation_acknowledged===true;
  const delivery=opted
    ? acknowledged
      ? 'Paired Cloud site accepted the current unverified attribution. Cloud account consent is still separate.'
      : 'Sharing approved locally; current Cloud delivery is NOT confirmed. Approve an explicit sync to retry.'
    : pending
      ? 'Sharing revoked locally; Cloud revocation has NOT been acknowledged. Retry an explicit sync when online.'
      : link.cloud_delivery_status==='revocation_delivered'
        ? 'Current revocation was accepted by the paired Cloud site. No local sharing is active.'
        : 'Cloud status sharing is off. Neither profile nor contact data are shared.';
  el('onboardVisualCloudState').textContent=cloudNotice || delivery;
  if(active) {
    message('Owner-associated with '+link.contact.display_name+
      ' · browser report only; facial identity not independently verified.');
  } else if(link.state==='revoked') {
    message('Association revoked. Local browser face samples remain until separately deleted.');
  } else if(link.state==='needs_review') {
    message('Existing attribution is stale or its contact was deleted. Review your local profile before linking.');
  } else if(!available) {
    message('Create your self-profile in this browser first. Your local contact is never created automatically.');
  } else {
    message('Optional: select an existing local contact and explicitly approve the attribution. This does not verify a face or enable tracking.');
  }
}

async function refresh(){
  if(!el('onboardVisualLink')) return;
  const query=el('onboardVisualLinkSearch').value.trim().slice(0,120);
  const [link, report, local, contacts] = await Promise.all([
    json(BASE+'status'), json('/api/v1/control/onboarding/visual/status'),
    listParticipants(), json(BASE+'contacts?q='+encodeURIComponent(query))
  ]);
  if(query!==el('onboardVisualLinkSearch').value.trim().slice(0,120))return;
  state = link; visual = report;
  owner = local.find(x=>x.visualEnrollment?.scope==='owner-self' &&
    x.id===report.local_participant_id) || null;
  const picker = el('onboardVisualLinkTarget');
  const chosen = picker.value;
  picker.replaceChildren();
  const prompt = document.createElement('option');
  prompt.value='';prompt.textContent='Choose existing local contact';picker.append(prompt);
  for(const contact of contacts.items || []){
    const option=document.createElement('option');
    option.value=String(contact.id);
    option.textContent=String(contact.display_name || 'Unnamed local contact');
    picker.append(option);
  }
  if(Array.from(picker.options).some(x=>x.value===chosen))picker.value=chosen;
  render();
}

async function associate(){
  if(busy || !owner || !visual || !el('onboardVisualLinkConsent').checked)return;
  const id=Number(el('onboardVisualLinkTarget').value);
  if(!Number.isSafeInteger(id)||id<1)return;
  const display=el('onboardVisualLinkTarget').selectedOptions[0]?.textContent || 'this contact';
  if(!window.confirm('Attribute your browser-reported self-profile to '+display+
    '? This records your explicit assertion only. No biometric verification, automated tracking, or Cloud sharing will be enabled.'))return;
  busy=true;render();
  try{
    state=await json(BASE+'associate',{
      consent:true,scope:SCOPE,participant_id:owner.id,contact_id:id
    });
    cloudNotice='';
    el('onboardVisualLinkConsent').checked=false;
    await refresh();
  }catch(error){message(String(error.message));}
  finally{busy=false;render();}
}
async function revoke(){
  if(busy || (!state?.active && state?.state!=='needs_review'))return;
  if(!window.confirm('Revoke the visual-to-contact association? Your local profile and contact will remain until you delete them separately.'))return;
  busy=true;render();
  try{
    state=await json(BASE+'revoke',{consent:true});
    cloudNotice='';
    el('onboardVisualLinkConsent').checked=false;
    await refresh();
  }catch(error){message(String(error.message));}
  finally{busy=false;render();}
}

async function toggleCloud(enabled){
  if(busy || (enabled && (!state?.active || !el('onboardVisualCloudConsent').checked)))return;
  const warning=enabled
    ? 'Share only your unverified owner-attribution STATUS with paired VP3 Cloud? Your Cloud account requires its own visual consent. No photo, face descriptor, contact details, IDs or signed local receipt will be sent.'
    : 'Disable status sharing locally and queue a non-biometric revocation status for Cloud?';
  if(!window.confirm(warning))return;
  busy=true;render();
  try{
    state=await json(BASE+'cloud-sharing',{consent:true,scope:CLOUD_SCOPE,enabled});
    cloudNotice='';
    el('onboardVisualCloudConsent').checked=false;
    await refresh();
  }catch(error){message(String(error.message));}
  finally{busy=false;render();}
}
async function syncCloud(){
  if(busy||(!state?.cloud_sharing_opted_in&&!state?.cloud_revocation_pending))return;
  if(!window.confirm('Run your existing governed Tracky HTTPS site sync with your unverified owner status or its revocation? The normal permitted Tracky site data may synchronize too. No visual biometrics, contact details or local receipt will be sent.'))return;
  busy=true;render();
  try{
    const result=await json(BASE+'cloud-sync',{consent:true});
    cloudNotice=result.visual_status_current_generation_acknowledged
      ? result.visual_status_sent==='revoked'
        ? 'Current revocation accepted by the paired Cloud site. Face identity remains unverified.'
        : 'Current unverified attribution accepted by the paired Cloud site. Separate Cloud account consent is required.'
      : result.site_sync_accepted
        ? 'Site sync succeeded, but the CURRENT visual status was not acknowledged. Consent may have changed during upload; retry if still approved.'
        : 'Cloud status was not delivered. Retry after checking your paired connection.';
    await refresh();
  }catch(error){cloudNotice='Cloud status not confirmed. Review pairing and retry while online.';}
  finally{busy=false;render();}
}
function init(){
  if(!el('onboardVisualLink'))return;
  el('onboardVisualLinkConsent').addEventListener('change',render);
  el('onboardVisualCloudConsent').addEventListener('change',render);
  el('onboardVisualCloudEnable').addEventListener('click',()=>{void toggleCloud(true);});
  el('onboardVisualCloudDisable').addEventListener('click',()=>{void toggleCloud(false);});
  el('onboardVisualCloudSync').addEventListener('click',()=>{void syncCloud();});
  el('onboardVisualLinkTarget').addEventListener('change',render);
  el('onboardVisualLinkSearch').addEventListener('input',()=>{
    if(searchTimer)window.clearTimeout(searchTimer);
    searchTimer=window.setTimeout(()=>{
      searchTimer=null;
      void refresh().catch(error=>message(String(error.message)));
    },250);
  });
  el('onboardVisualLinkStart').addEventListener('click',()=>{void associate();});
  el('onboardVisualLinkRevoke').addEventListener('click',()=>{void revoke();});
  window.addEventListener('tracky:visual-state-changed',()=>{
    cloudNotice='';
    el('onboardVisualLinkConsent').checked=false;
    el('onboardVisualCloudConsent').checked=false;
    void refresh().catch(error=>message(String(error.message)));
  });
  window.addEventListener('homeserver:onboarding-hidden',()=>{
    el('onboardVisualLinkConsent').checked=false;
    el('onboardVisualCloudConsent').checked=false;
    render();
  });
  void refresh().catch(()=>message('Local contact attribution is unavailable.'));
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});
else init();
