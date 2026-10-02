/* Tracky 1F1: owner-confirmed link from this browser's local self-profile
 * to an EXISTING local HomeServer contact. No camera, embeddings, portrait,
 * automatic contact creation, Cloud identity assertion or biometric transport.
 */
import { listParticipants } from './src/participant-store.js';

const el = id => document.getElementById(id);
const BASE = '/api/v1/control/onboarding/visual/contact-link/';
const SCOPE = 'owner-self-existing-contact-association.v1';
let busy = false;
let state = null;
let visual = null;
let owner = null;

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
  el('onboardVisualLinkStart').hidden = active;
  el('onboardVisualLinkRevoke').hidden = !active;
  el('onboardVisualLinkStart').disabled = busy || !available ||
      !el('onboardVisualLinkConsent').checked || !el('onboardVisualLinkTarget').value;
  el('onboardVisualLinkRevoke').disabled = busy;
  el('onboardVisualLinkTarget').disabled = busy || active;
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
  const [link, report, local, contacts] = await Promise.all([
    json(BASE+'status'), json('/api/v1/control/onboarding/visual/status'),
    listParticipants(), json(BASE+'contacts')
  ]);
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
    el('onboardVisualLinkConsent').checked=false;
    await refresh();
  }catch(error){message(String(error.message));}
  finally{busy=false;render();}
}
async function revoke(){
  if(busy || !state?.active)return;
  if(!window.confirm('Revoke the visual-to-contact association? Your local profile and contact will remain until you delete them separately.'))return;
  busy=true;render();
  try{
    state=await json(BASE+'revoke',{consent:true});
    el('onboardVisualLinkConsent').checked=false;
    await refresh();
  }catch(error){message(String(error.message));}
  finally{busy=false;render();}
}

function init(){
  if(!el('onboardVisualLink'))return;
  el('onboardVisualLinkConsent').addEventListener('change',render);
  el('onboardVisualLinkTarget').addEventListener('change',render);
  el('onboardVisualLinkStart').addEventListener('click',()=>{void associate();});
  el('onboardVisualLinkRevoke').addEventListener('click',()=>{void revoke();});
  window.addEventListener('tracky:visual-state-changed',()=>{
    el('onboardVisualLinkConsent').checked=false;
    void refresh().catch(error=>message(String(error.message)));
  });
  window.addEventListener('homeserver:onboarding-hidden',()=>{
    el('onboardVisualLinkConsent').checked=false;
    render();
  });
  void refresh().catch(()=>message('Local contact attribution is unavailable.'));
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});
else init();
