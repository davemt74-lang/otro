/* Owner local model review; capture and consent remain in Agent Eyes. */
(()=>{
'use strict';
const $=id=>document.getElementById('trackyScene'+id);
let busy=false, reviewed=false, epoch=0, expiry=null;
const visible=()=>document.visibilityState==='visible'&&document.getElementById('view-tracky')?.classList.contains('active');
async function request(path,payload){
 const response=await fetch('/api/v1/control/onboarding/visual/agent-eyes/scene/'+path,{
  method:payload===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',
  headers:{Accept:'application/json',...(payload===undefined?{}:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'})},
  ...(payload===undefined?{}:{body:JSON.stringify(payload)})});
 const data=await response.json();if(!response.ok)throw Error(data.detail||'Scene review unavailable');return data;
}
function render(data, started=Date.now()){
 clearTimeout(expiry);
 reviewed=data.reviewed===true;
 const selected=$('Room').value;
 $('Room').replaceChildren();
 for(const room of data.rooms||[]){const option=document.createElement('option');option.value=room.room_id;option.textContent=room.name;$('Room').append(option);}
 $('Room').value=data.room_id||selected;
 if(data.model)$('Model').value=data.model;
 $('Accept').disabled=busy||!data.test_completed||!$('Observed').checked;
 $('Status').textContent=data.review_expired?'Scene review expired. Repeat model selection and the installed scene test.':reviewed?'Local scene review accepted for this process. Start a new permitted observation to include scenes in chat.':data.configured?'Model selected. Enable scene inference, choose one observation and start Agent Eyes to test it. Inspect output and camera release before accepting.':'Scene inference is off. Select an installed local vision model and room after camera acceptance.';
 const p=data.preview;
 const age=p?.age_seconds+(Date.now()-started)/1000;
 const fresh=p&&Number.isFinite(age)&&age>=0&&age<=60&&visible();
 $('Preview').textContent=fresh?'Possible objects: '+(p.objects.join(', ')||'none reported')+' · setting '+p.setting+' · lighting '+p.lighting+' · '+p.age_seconds+'s old · uncalibrated':'No recent scene preview available.';
 if(fresh)expiry=setTimeout(()=>{$('Preview').textContent='No recent scene preview available.';},Math.max(0,(60-age)*1000));
}
async function refresh(){if(busy)return;const current=++epoch, started=Date.now();$('Preview').textContent='';try{const data=await request('status');if(current===epoch)render(data,started);}catch(_){if(current===epoch){reviewed=false;$('Preview').textContent='';$('Status').textContent='Scene status unavailable.';}}}
async function change(path,payload){if(busy||!visible())return;busy=true;const current=++epoch, started=Date.now();try{const data=await request(path,payload);if(current===epoch&&visible())render(data,started);}catch(e){$('Status').textContent=e.message;$('Preview').textContent='';}finally{busy=false;}}
function init(){if(!$('Panel'))return;
 $('Configure').addEventListener('click',()=>{if(window.confirm('Review this installed local vision model for the selected room? No capture starts.'))void change('configure',{model:$('Model').value.trim(),room_id:$('Room').value,consent:true});});
 $('Accept').addEventListener('click',()=>{if($('Observed').checked&&window.confirm('Accept the scene output and camera release you personally inspected on this installed device?'))void change('accept',{consent:true,output_observed:true,release_observed:true});});
 $('Disable').addEventListener('click',()=>{reviewed=false;$('Enabled').checked=false;void change('disable',{});});
 $('Refresh').addEventListener('click',()=>void refresh());
 $('Observed').addEventListener('change',()=>void refresh());
 document.addEventListener('visibilitychange',()=>{if(!visible()){epoch++;$('Preview').textContent='';}});
 setInterval(()=>{if(visible())void refresh();else $('Preview').textContent='';},2500);
 void refresh();
}
window.TrackyAgentEyesScene={options:()=>!$('Enabled')?.checked?{}:{include_scene:true,scene_test:!reviewed}};
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
