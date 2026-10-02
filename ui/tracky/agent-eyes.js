/* Tracky 1G2: native Agent Eyes on the existing HomeServer Tracky dashboard.
 * The browser does not acquire a camera or run vision. Only explicit owner
 * action arms the local reviewed worker; an invisible view cannot keep it alive.
 */
(()=>{
'use strict';
const $=id=>document.getElementById(id);
const BASE='/api/v1/control/onboarding/visual/agent-eyes/';
let active=false, busy=false, timer=null, pending=false, armedHere=false, suspendHeartbeat=false;

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
  if(!active){armedHere=false;suspendHeartbeat=false;}
  const recovery=state?.recovery;
  const recoveryRequired=recovery?.requires_acknowledgement===true;
  const panel=$('trackyAgentEyesRecoveryPanel');
  if(panel)panel.hidden=!recoveryRequired;
  const recoveryText={
    watchdog_stall:'The last camera request stalled. Verify the device is no longer capturing before acknowledging recovery.',
    observation_unavailable:'A camera observation failed. Check local camera permissions and inspect the device.',
    interrupted_by_restart:'HomeServer restarted while Agent Eyes was active. Check that camera capture stopped.',
    startup_failed:'A previous Agent Eyes startup failed. Check the camera and native runtime before retrying.'
  };
  if($('trackyAgentEyesRecoveryMessage')&&recoveryRequired){
    const message=recoveryText[recovery.last_reason]||'Inspect the installed camera before a new session.';
    $('trackyAgentEyesRecoveryMessage').textContent=recovery.shared_camera_busy
      ? message+' The camera worker or provider is still busy; wait for it to release.'
      : message+' Confirm both items below to clear the operator recovery gate.';
  }
  const ack=$('trackyAgentEyesRecoveryAck');
  if(ack)ack.disabled=busy||!recovery?.ready_for_owner_acknowledgement||
    !$('trackyAgentEyesReleaseObserved')?.checked||
    !$('trackyAgentEyesFreshConsent')?.checked||!visible();
  const ready=state?.owner_review_current===true &&
    !state?.model_changed_requires_review && !state?.privacy_engaged && !recoveryRequired;
  const budget=state?.resource_budget;
  const usage=$('trackyAgentEyesBudgetStatus');
  if(usage)usage.textContent=budget
    ? 'Elapsed '+budget.wall_elapsed_seconds+'/'+budget.wall_limit_seconds+
      's · CPU '+budget.cpu_used_seconds+'/'+budget.cpu_limit_seconds+
      's · watchdog '+(budget.watchdog_running?'active':'stopped')
    : 'Owner-configured budgets; no unattended operation.';
  const exercise=state?.installed_exercise;
  const completed=exercise?.completed_steps||[];
  const names={owner_stop:'Owner stop',privacy_revocation:'Privacy revocation',
               presence_lease:'Presence lease expiry'};
  const progress=$('trackyAgentEyesAcceptanceStatus');
  if(progress)progress.textContent=exercise?.owner_exercise_complete
    ? 'All three current-install owner exercises reported. This is not independent hardware certification.'
    : 'Current-install exercise reports: '+(completed.map(step=>names[step]||step).join(', ')||'none')+
      '. Next: '+(exercise?.pending_steps?.map(step=>names[step]||step).join(', ')||'repeat after review')+
      '. No unattended operation permitted.';
  const step=$('trackyAgentEyesAcceptanceStep')?.value;
  const record=$('trackyAgentEyesAcceptanceRecord');
  if(record)record.disabled=busy||!visible()||!$('trackyAgentEyesAcceptanceObserved')?.checked||
    !exercise?.ready_to_record||exercise?.current_session_step!==step;
  const expiry=$('trackyAgentEyesTestLease');
  if(expiry)expiry.disabled=busy||!active||!armedHere||suspendHeartbeat||!visible();
  const label=$('trackyAgentEyesState');
  if(label)label.textContent=active
    ? 'Supervised local camera active'
    : (state?.phase==='interrupted'?'Previous session interrupted':'Camera inactive');
  const start=$('trackyAgentEyesStart');
  if(start)start.disabled=busy||active||!ready||!$('trackyAgentEyesConsent')?.checked;
  if($('trackyAgentEyesStop'))$('trackyAgentEyesStop').hidden=!active;
  if($('trackyAgentEyesCamera'))$('trackyAgentEyesCamera').disabled=busy||active;
  for(const id of ['trackyAgentEyesSamples','trackyAgentEyesWall','trackyAgentEyesCPU'])
    if($(id))$(id).disabled=busy||active;
  if(active){
    details((armedHere?'Owner-supervised':'Another local owner view has')+' observations: '+state.completed_observations+'/'+
      state.requested_observations+'. No recording or identity recognition. Leave this view to stop.');
  }else if(recoveryRequired){
    details('Owner recovery required. No camera restart is permitted until the previous failure is inspected and acknowledged.');
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
    if(active&&armedHere){
      if(!visible()){
        await stop();
      }else if(!suspendHeartbeat){
        try{render(await request('heartbeat',{}));}
        catch(_){render(await request('status'));}
      }
    }
  }catch(_){details('HomeServer Agent Eyes status is unavailable; no automatic restart.');}
  finally{pending=false;}
}
async function recordInstalledExercise(){
  const step=$('trackyAgentEyesAcceptanceStep')?.value;
  if(busy||!visible()||!['owner_stop','privacy_revocation','presence_lease'].includes(step)||
     !$('trackyAgentEyesAcceptanceObserved')?.checked)return;
  if(!window.confirm('Record your direct installed-device observation for this completed exercise? This is an owner report, not physical certification.'))return;
  busy=true;
  try{
    await request('installed-exercise/record',{
      step,consent:true,inspected_camera_release:true,
    });
    $('trackyAgentEyesAcceptanceObserved').checked=false;
    await refresh();
  }catch(error){details('Exercise report was not recorded: '+String(error.message));}
  finally{busy=false;}
}
function testLeaseExpiry(){
  if(!active||!armedHere||!visible()||suspendHeartbeat)return;
  if(!window.confirm('Test the 15-second presence lease? HomeServer will stop accepting observations after the lease expires. Stay at this device, and use Stop if needed.'))return;
  suspendHeartbeat=true;
  details('Presence-lease test: heartbeats paused intentionally. HomeServer should end this session within 15 seconds. No automatic restart.');
}
async function acknowledgeRecovery(){
  if(busy||!visible()||!$('trackyAgentEyesReleaseObserved')?.checked||
     !$('trackyAgentEyesFreshConsent')?.checked)return;
  if(!window.confirm('Confirm your installed camera has stopped and acknowledge this recovery. This does NOT certify hardware or start capture.'))return;
  busy=true;
  try{
    await request('recovery/acknowledge',{
      consent:true,camera_stopped_observed:true,fresh_consent_understood:true,
    });
    $('trackyAgentEyesReleaseObserved').checked=false;
    $('trackyAgentEyesFreshConsent').checked=false;
    await refresh();
  }catch(error){details('Recovery could not be acknowledged: '+String(error.message));}
  finally{busy=false;}
}
async function start(){
  if(busy||active||!visible()||!$('trackyAgentEyesConsent')?.checked||
     !$('trackyAgentEyesRecoveryPanel')?.hidden)return;
  if(window.TrackyOwnerEyes?.isActive()||
     window.HomeServerVisualEnrollment?.isCapturing()||
     window.TrackyOwnerSelfCheck?.isActive()){
    details('Finish the other browser camera activity before starting Agent Eyes.');return;
  }
  const camera=Number($('trackyAgentEyesCamera')?.value);
  const samples=Number($('trackyAgentEyesSamples')?.value);
  const wall=Number($('trackyAgentEyesWall')?.value);
  const cpu=Number($('trackyAgentEyesCPU')?.value);
  if(![0,1,2].includes(camera)||![3,6,9,12].includes(samples)
     ||![60,120].includes(wall)||![4,8,12].includes(cpu))return;
  if((samples-1)*5>wall-7){
    details('Choose fewer observations or a longer approved time budget.');
    return;
  }
  if(!window.confirm('Start '+samples+' owner-supervised local observations using camera '+
    camera+'? No images are saved, people are not identified, and leaving this view ends the lease.'))return;
  busy=true;
  try{
    const state=await request('start',{
      consent:true,scope:'owner-agent-eyes-supervised-live.v1',
      camera_index:camera,sample_count:samples,interval_seconds:5,
      max_session_seconds:wall,max_cpu_seconds:cpu,
    });
    $('trackyAgentEyesConsent').checked=false;
    armedHere=state.active===true;
    suspendHeartbeat=false;
    render(state);
  }catch(error){details('Agent Eyes could not start: '+String(error.message));}
  finally{busy=false;}
}
async function stop(){
  suspendHeartbeat=false;
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
  $('trackyAgentEyesRecoveryAck').addEventListener('click',()=>{void acknowledgeRecovery();});
  $('trackyAgentEyesTestLease').addEventListener('click',testLeaseExpiry);
  $('trackyAgentEyesAcceptanceRecord').addEventListener('click',()=>{void recordInstalledExercise();});
  for(const id of ['trackyAgentEyesAcceptanceStep','trackyAgentEyesAcceptanceObserved'])
    $(id).addEventListener('change',()=>{void refresh();});
  for(const id of ['trackyAgentEyesReleaseObserved','trackyAgentEyesFreshConsent'])
    $(id).addEventListener('change',()=>{void refresh();});
  document.addEventListener('visibilitychange',()=>{
    if(document.visibilityState==='hidden'&&active&&armedHere)void stop();
  });
  window.addEventListener('pagehide',()=>{
    if(active&&armedHere){
      active=false;
      armedHere=false;
      void fetch(BASE+'stop',{
        method:'POST',credentials:'same-origin',keepalive:true,
        headers:{'X-Requested-With':'XMLHttpRequest','Content-Type':'application/json'},
        body:'{}',
      }).catch(()=>{});
    }
    if(timer){clearInterval(timer);timer=null;}
  });
  timer=window.setInterval(()=>{
    if(active&&armedHere&&!visible())void stop();
    else if(visible())void refresh();
  },2500);
  void refresh();
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});
else init();
})();