/* Tracky 1G2: native Agent Eyes on the existing HomeServer Tracky dashboard.
 * The browser does not acquire a camera or run vision. Only explicit owner
 * action arms the local reviewed worker; an invisible view cannot keep it alive.
 */
(()=>{
'use strict';
const $=id=>document.getElementById(id);
const BASE='/api/v1/control/onboarding/visual/agent-eyes/';
let active=false, busy=false, timer=null, pending=false;

function visible(){
  return document.visibilityState==='visible' &&
    $('view-tracky')?.classList.contains('active')===true;
}
function details(value){
  if($('trackyAgentEyesDetails'))$('trackyAgentEyesDetails').textContent=value;
}
async function request(route,payload){
  const res=await fetch(BASE+route,{
    method:payload===undefined?'GET':'POST',
    credentials:'same-origin',cache:'no-store',
    headers:{Accept:'application/json',...(payload===undefined?{}:{
      'X-Requested-With':'XMLHttpRequest','Content-Type':'application/json',
    })},
    ...(payload===undefined?{}:{body:JSON.stringify(payload)}),
  });
  const body=await res.json().catch(()=>({}));
  if(!res.ok)throw Error(String(body.detail||'Agent Eyes request unavailable'));
  return body;
}
function render(state){
  active=state?.active===true;
  const ready=state?.owner_review_current===true &&
    !state?.model_changed_requires_review && !state?.privacy_engaged;
  const label=$('trackyAgentEyesState');
  if(label)label.textContent=active
    ? 'Supervised local camera active'
    : (state?.phase==='interrupted'?'Previous session interrupted':'Camera inactive');
  const start=$('trackyAgentEyesStart');
  if(start)start.disabled=busy||active||!ready||!$('trackyAgentEyesConsent')?.checked;
  if($('trackyAgentEyesStop'))$('trackyAgentEyesStop').hidden=!active;
  if($('trackyAgentEyesCamera'))$('trackyAgentEyesCamera').disabled=busy||active;
  if($('trackyAgentEyesSamples'))$('trackyAgentEyesSamples').disabled=busy||active;
  if(active){
    details('Owner-supervised observations: '+state.completed_observations+'/'+
      state.requested_observations+'. No recording or identity recognition. Leave this view to stop.');
  }else if(state?.model_changed_requires_review){
    details('Installed detector changed: repeat the native test, privacy check and owner review in Agent Chat.');
  }else if(state?.privacy_engaged){
    details('Privacy gate engaged. No new camera session is permitted.');
  }else if(!ready){
    details('Complete current installed-camera testing and owner review in Agent Chat first.');
  }else{
    details('Ready for your explicit approval. Prior session: '+(state.reason||'not started')+
      '. Each new session requires fresh consent.');
  }
}
async function refresh(){
  if(pending)return;
  pending=true;
  try{
    const state=await request('status');
    render(state);
    // Passive status never renews permission. A heartbeat comes ONLY
    // from an open, visible Tracky owner view while its session is active.
    if(active){
      if(!visible()){
        await stop();
      }else{
        try{render(await request('heartbeat',{}));}
        catch(_){render(await request('status'));}
      }
    }
  }catch(_){details('HomeServer Agent Eyes status is unavailable; no automatic restart.');}
  finally{pending=false;}
}
async function start(){
  if(busy||active||!visible()||!$('trackyAgentEyesConsent')?.checked)return;
  if(window.TrackyOwnerEyes?.isActive()||
     window.HomeServerVisualEnrollment?.isCapturing()||
     window.TrackyOwnerSelfCheck?.isActive()){
    details('Finish the other browser camera activity before starting Agent Eyes.');return;
  }
  const camera=Number($('trackyAgentEyesCamera')?.value);
  const samples=Number($('trackyAgentEyesSamples')?.value);
  if(![0,1,2].includes(camera)||![3,6,9,12].includes(samples))return;
  if(!window.confirm('Start '+samples+' owner-supervised local observations using camera '+
    camera+'? No images are saved, people are not identified, and leaving this view ends the lease.'))return;
  busy=true;
  try{
    const state=await request('start',{
      consent:true,scope:'owner-agent-eyes-supervised-live.v1',
      camera_index:camera,sample_count:samples,interval_seconds:5,
    });
    $('trackyAgentEyesConsent').checked=false;
    render(state);
  }catch(error){details('Agent Eyes could not start: '+String(error.message));}
  finally{busy=false;}
}
async function stop(){
  // Revocation is best-effort over HTTP; backend's 15-second owner
  // heartbeat lease independently stops all further observations.
  active=false;
  try{render(await request('stop',{}));}
  catch(_){details('Stop request failed. Do not renew the lease; HomeServer will expire it.');}
}
function init(){
  if(!$('trackyAgentEyesPanel'))return;
  $('trackyAgentEyesConsent').addEventListener('change',()=>{
    if($('trackyAgentEyesStart'))
      $('trackyAgentEyesStart').disabled=busy||active||!$('trackyAgentEyesConsent').checked;
  });
  $('trackyAgentEyesStart').addEventListener('click',()=>{void start();});
  $('trackyAgentEyesStop').addEventListener('click',()=>{void stop();});
  $('trackyAgentEyesRefresh').addEventListener('click',()=>{void refresh();});
  document.addEventListener('visibilitychange',()=>{
    if(document.visibilityState==='hidden'&&active)void stop();
  });
  window.addEventListener('pagehide',()=>{
    if(active){
      active=false;
      void fetch(BASE+'stop',{
        method:'POST',credentials:'same-origin',keepalive:true,
        headers:{'X-Requested-With':'XMLHttpRequest','Content-Type':'application/json'},
        body:'{}',
      }).catch(()=>{});
    }
    if(timer){clearInterval(timer);timer=null;}
  });
  timer=window.setInterval(()=>{
    if(active&&!visible())void stop();
    else if(visible())void refresh();
  },2500);
  void refresh();
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});
else init();
})();