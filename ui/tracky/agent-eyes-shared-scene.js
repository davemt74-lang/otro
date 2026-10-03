/* Separate semantic-sharing consent and observation-bound owner reports. */
(()=>{'use strict';
const $=id=>document.getElementById(id),objects=['chair','table','sofa','bed','cup','bottle','book','screen','keyboard','phone','lamp','door','window','plant','bag','box'];
const visible=view=>document.visibilityState==='visible'&&$('view-'+view)?.classList.contains('active');
const chatId=()=>document.querySelector('[data-brain-conversation].active')?.dataset.brainConversation;
let shareEpoch=0,shareBusy=false,correctionEpoch=0,correction=null,expires=null;
async function api(path,payload){const response=await fetch(path,{method:payload===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json',...(payload===undefined?{}:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'})},...(payload===undefined?{}:{body:JSON.stringify(payload)})});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Scene request unavailable.');return data;}
function shareRender(data){$('trackySceneShareStatus').textContent=(data.enabled?'Sharing enabled for this process. ':'Sharing is off. ')+(data.delivery==='pending'?'Current state is waiting for Cloud acknowledgment.':data.delivery==='acknowledged'?'Cloud acknowledged this state.':'No sharing requested.');}
async function shareRefresh(){if(shareBusy||!visible('tracky'))return;const epoch=++shareEpoch;try{const data=await api('/api/v1/control/onboarding/visual/agent-eyes/scene/share');if(epoch===shareEpoch&&visible('tracky'))shareRender(data);}catch(_){if(epoch===shareEpoch)$('trackySceneShareStatus').textContent='Sharing status unavailable. Refresh to check current consent.';}}
async function sharing(enabled){if(shareBusy||!visible('tracky'))return;if(enabled&&!window.confirm('Share only possible scene categories and your typed owner reports with your Cloud Agent? Images and chat history stay local. Consent resets off when HomeServer restarts.'))return;shareBusy=true;const epoch=++shareEpoch;try{const data=await api('/api/v1/control/onboarding/visual/agent-eyes/scene/share',{enabled,consent:true});if(epoch===shareEpoch&&visible('tracky'))shareRender(data);}catch(e){if(epoch===shareEpoch&&visible('tracky'))$('trackySceneShareStatus').textContent=e.message;}finally{shareBusy=false;}}
function clear(){correctionEpoch++;clearTimeout(expires);correction=null;if($('contextSceneCorrection'))$('contextSceneCorrection').hidden=true;}
function panel(){if($('contextSceneCorrection'))return $('contextSceneCorrection');const anchor=$('contextAgentEyesEvidence');if(!anchor)return null;
const root=document.createElement('div');root.id='contextSceneCorrection';root.hidden=true;
const title=document.createElement('p');title.textContent='Correct this checked observation with a separate owner report. Camera suggestions remain visible as uncertain evidence.';
const object=document.createElement('select');object.id='contextSceneObject';object.setAttribute('aria-label','Object class');for(const label of objects){const option=document.createElement('option');option.value=label;option.textContent=label;object.append(option);}
const state=document.createElement('select');state.id='contextScenePresent';state.setAttribute('aria-label','Owner reported presence');for(const [value,label] of [['true','Present'],['false','Absent']]){const option=document.createElement('option');option.value=value;option.textContent=label;state.append(option);}
const submit=document.createElement('button');submit.id='contextSceneReport';submit.type='button';submit.className='button secondary';submit.textContent='Record owner report';submit.addEventListener('click',()=>void report());
const status=document.createElement('p');status.id='contextSceneReportStatus';status.setAttribute('role','status');root.append(title,object,state,submit,status);anchor.insertAdjacentElement('afterend',root);return root;}
function render(data,options){clear();const age=data.age_seconds+(Date.now()-options.requestStarted)/1000;
if(!data.scene||data.state!=='recent_observation'||!Number.isFinite(age)||age<0||age>=60||!visible('chat')||chatId()!==options.conversationId||!$('contextUseAgentEyes')?.checked||!/^[a-f0-9]{16}$/.test(data.request_fingerprint||''))return;
const root=panel();if(!root)return;root.hidden=false;$('contextSceneReportStatus').textContent=(data.scene.owner_corrections||[]).map(r=>r.object+' '+(r.present?'present':'absent')+(r.conflicts_with_camera?' (differs from camera)':'')).join(', ');
correction={id:options.conversationId,fingerprint:data.request_fingerprint,deadline:Date.now()+(60-age)*1000,started:Date.now()};expires=setTimeout(clear,(60-age)*1000);}
async function report(){const current=correction,started=Date.now();if(!current||!visible('chat')||chatId()!==current.id||!$('contextUseAgentEyes')?.checked||started<current.started||started>=current.deadline){clear();return;}
const label=$('contextSceneObject').value,present=$('contextScenePresent').value;if(!objects.includes(label)||!['true','false'].includes(present))return;
clear();const requestEpoch=correctionEpoch;
try{const data=await api('/api/v1/control/conversations/'+encodeURIComponent(current.id)+'/agent-eyes-correction',{object_label:label,present:present==='true',expected_fingerprint:current.fingerprint});if(requestEpoch===correctionEpoch&&visible('chat')&&chatId()===current.id&&$('contextUseAgentEyes')?.checked)render(data.agent_eyes_context,{conversationId:current.id,requestStarted:started});}catch(_){if(requestEpoch===correctionEpoch&&visible('chat')&&chatId()===current.id)$('contextSceneReportStatus').textContent='Observation changed or report unavailable. Refresh Agent Eyes status.';}}
window.TrackySceneCorrections={clear,render};
function init(){if(!$('trackySceneSharePanel'))return;$('trackySceneShareEnable').addEventListener('click',()=>void sharing(true));$('trackySceneShareDisable').addEventListener('click',()=>void sharing(false));setInterval(()=>void shareRefresh(),5000);void shareRefresh();}
document.addEventListener('visibilitychange',()=>{if(document.visibilityState!=='visible'){shareEpoch++;clear();}});
document.addEventListener('click',e=>{if(e.target.closest('[data-view]')){shareEpoch++;clear();}});
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
