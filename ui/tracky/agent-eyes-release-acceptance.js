/* Guided owner reports. No camera, restart, connectivity or consent operation. */
(()=>{'use strict';
const $=name=>document.getElementById('trackyReleaseAcceptance'+name),path='/api/v1/control/onboarding/visual/agent-eyes/release-acceptance';
if(!$('Panel'))return;
const guidance={restart_begin:'Stop the camera after completing Stop/privacy/presence exercises. Save this checkpoint, then restart HomeServer yourself.',restart_reset:'After restarting, inspect that capture did not resume, sharing is off and reviews are reset. Record before accepting a new camera review.',disconnect_seen:'Interrupt connectivity yourself and wait for the existing Tracky sync to report a failure. This button does not disconnect anything.',reconnect_seen:'Restore connectivity yourself; wait for a later successful sync and acknowledgment of any pending sharing state.'};
const visible=()=>document.visibilityState==='visible'&&document.getElementById('view-tracky')?.classList.contains('active');
const conversation=()=>document.querySelector('[data-brain-conversation].active')?.dataset.brainConversation||'';
let current=null,epoch=0,busy=false,controller=null,deadline=0,inspectedConversation='';
function controls(){const stage=$('Stage').value;if(stage==='local_chat'&&inspectedConversation!==conversation())$('Observed').checked=false;$('Record').disabled=busy||!current||!visible()||performance.now()>=deadline||!$('Observed').checked||!current.recordable_stages?.includes(stage)||(stage==='local_chat'&&!conversation());$('Guide').textContent=guidance[stage]||current?.checks?.find(c=>c.key===stage)?.guidance||'Refresh to check prerequisites.';}
function clear(message){current=null;deadline=0;$('Checks').replaceChildren();$('Observed').checked=false;$('Message').textContent=message;controls();}
function render(data){if(current?.inspection_token!==data.inspection_token)$('Observed').checked=false;current=data;deadline=performance.now()+5000;$('Checks').replaceChildren();for(const c of data.checks||[]){const row=document.createElement('p');row.textContent=c.label+': '+(c.state==='recorded'?'Recorded owner/software check':'Pending')+(c.optional?(c.group==='scene'?' (optional scene review)':' (optional Cloud exercise)'):'');$('Checks').append(row);}controls();}
async function request(payload){const response=await fetch(path+(payload?'/record':''),{method:payload?'POST':'GET',credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:{Accept:'application/json',...(payload?{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'}:{})},...(payload?{body:JSON.stringify(payload)}:{})});const data=await response.json();if(!response.ok)throw Error('exercise_unavailable');return data;}
async function refresh(){if(busy||!visible())return;busy=true;const token=++epoch;controller=new AbortController();const own=controller,timer=setTimeout(()=>own.abort(),10000);try{const data=await request();if(token===epoch&&visible())render(data);}catch(_){if(token===epoch)clear('Checklist unavailable. Refresh or review the local camera and pairing controls.');}finally{clearTimeout(timer);if(token===epoch){busy=false;controller=null;controls();}}}
async function record(){controls();if($('Record').disabled)return;const stage=$('Stage').value,id=conversation(),fingerprint=current.expected_fingerprint||'';
const payload={stage,consent:true,owner_observed:true,inspection_token:current.inspection_token,expected_fingerprint:fingerprint,conversation_id:stage==='local_chat'?id:''};
busy=true;const token=++epoch;controller=new AbortController();const own=controller,timer=setTimeout(()=>own.abort(),10000);clear('Recording inspected exercise…');
try{const data=await request(payload);if(token===epoch&&visible()&&(stage!=='local_chat'||conversation()===id)){render(data);$('Message').textContent='Owner report recorded with checked software prerequisites.';}}
catch(_){if(token===epoch&&visible())clear('Prerequisites changed or the exercise could not be recorded. Refresh and follow the selected instructions.');}
finally{clearTimeout(timer);if(token===epoch){busy=false;controller=null;controls();}}}
$('Stage').addEventListener('change',()=>{$('Observed').checked=false;controls();});$('Observed').addEventListener('change',()=>{inspectedConversation=conversation();controls();});$('Refresh').addEventListener('click',()=>void refresh());$('Record').addEventListener('click',()=>void record());
function hide(){epoch++;controller?.abort();controller=null;busy=false;clear('Refresh to inspect current prerequisites.');}
document.addEventListener('visibilitychange',()=>{hide();if(visible())void refresh();});
document.addEventListener('click',e=>{if(e.target.closest('[data-view]'))hide();});
setInterval(()=>{controls();if(visible())void refresh();},4000);if(visible())void refresh();
})();
