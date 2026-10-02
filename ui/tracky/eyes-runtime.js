/* One-shot owner-consented browser inference -> canonical HomeServer Tracky provider.
 * No background capture, identity recognition, frame upload, or persistent tokens.
 */
import { HUMAN_ESM_URL, HUMAN_MODEL_BASE } from './src/model-config.js';

const $ = id => document.getElementById(id);
const BASE='/api/v1/control/onboarding/visual/eyes/';
const SCOPE='owner_live_single_observation.v1';
let live=false, generation=0, stream=null, session='', engine=null, heartbeat=null;
let started=false;

async function api(path, body=null) {
  const res=await fetch(BASE+path,{
    method:body===null?'GET':'POST',credentials:'same-origin',cache:'no-store',
    headers:{Accept:'application/json',...(body===null?{}:{
      'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'
    })},
    ...(body===null?{}:{body:JSON.stringify(body)})
  });
  const data=await res.json().catch(()=>({}));
  if(!res.ok)throw new Error(typeof data.detail==='string'?data.detail:'Agent Eyes test unavailable.');
  return data;
}
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function report(message){const e=$('onboardEyesDetails');if(e)e.textContent=message;}
function state(message){const e=$('onboardEyesState');if(e)e.textContent=message;}

function release() {
  generation++;
  live=false;
  if(heartbeat!==null)clearInterval(heartbeat);
  heartbeat=null;
  stream?.getTracks().forEach(t=>t.stop());
  stream=null;
  if($('onboardEyesVideo'))$('onboardEyesVideo').srcObject=null;
  if($('onboardEyesCapture'))$('onboardEyesCapture').hidden=true;
  if($('onboardEyesStop'))$('onboardEyesStop').hidden=true;
  if($('onboardEyesStart'))$('onboardEyesStart').disabled=false;
  engine=null;
}
async function stop(reason='Agent Eyes stopped. Camera released.') {
  const prior=session;
  session='';
  release();
  if(prior)await api('close',{session:prior}).catch(()=>{});
  report(reason);
  state('Camera inactive');
}
async function initializeModel() {
  const module=await import(HUMAN_ESM_URL);
  const Human=module.Human||module.default;
  if(typeof Human!=='function')throw new Error('Pinned Tracky runtime was not loaded.');
  const instance=new Human({
    backend:'webgl',debug:false,modelBasePath:HUMAN_MODEL_BASE,
    face:{enabled:true,detector:{enabled:true,maxDetected:2,minConfidence:0.65},
      mesh:{enabled:false},description:{enabled:false},iris:{enabled:false},
      emotion:{enabled:false},antispoof:{enabled:false},liveness:{enabled:false}},
    body:{enabled:false},hand:{enabled:false},object:{enabled:false},
    gesture:{enabled:false},segmentation:{enabled:false}
  });
  await instance.load();
  return instance;
}

async function test() {
  if(started||live)return;
  const consent=$('onboardEyesConsent');
  if(!consent?.checked){report('Approve this separate one-time test before using your camera.');return;}
  if(window.TrackyOwnerSelfCheck?.isActive()){
    report('Stop the local self-check before starting Agent Eyes.');return;
  }
  if(window.HomeServerVisualEnrollment?.isCapturing()){
    report('Finish or stop visual profile enrollment before running Agent Eyes.');return;
  }
  if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia){
    report('The local camera needs localhost/HTTPS and a compatible browser.');return;
  }
  if(!window.confirm('Run one owner-approved camera-to-Tracky observation now? No image, face descriptor, or identification will be sent to HomeServer or VP3 Cloud. Stop at any time.'))return;
  started=true;live=true;
  const expected=++generation;
  $('onboardEyesStart').disabled=true;$('onboardEyesStop').hidden=false;
  state('Requesting camera permission…');
  try {
    // Browser permission request must immediately follow the owner gesture.
    stream=await navigator.mediaDevices.getUserMedia({
      audio:false,video:{width:{ideal:640},height:{ideal:480},facingMode:{ideal:'user'}}
    });
    if(expected!==generation){stream.getTracks().forEach(t=>t.stop());stream=null;return;}
    const video=$('onboardEyesVideo');
    video.srcObject=stream;
    $('onboardEyesCapture').hidden=false;
    await video.play();
    state('Loading Tracky detector…');
    engine=await initializeModel();
    if(expected!==generation)return;
    const opened=await api('open',{
      consent:true,scope:SCOPE,model_ready:true,camera_ready:stream.active
    });
    if(expected!==generation){
      await api('close',{session:opened.session}).catch(()=>{});return;
    }
    session=opened.session;
    state('One-time test running…');
    report('Your Agent is passing this camera observation through the existing governed HomeServer Tracky runtime.');
    heartbeat=setInterval(async()=>{
      if(!live||!session)return;
      try {
        const pulse=await api('heartbeat',{session});
        if(pulse.active!==true)throw new Error('Tracky session expired.');
      }catch(_){if(live)void stop('Camera stopped: owner session or privacy status unavailable.');}
    },1500);
    let done=false;
    const testPromise=api('test',{session}).then(
      result=>{done=true;return {result};},
      error=>{done=true;return {error};}
    );
    const until=Date.now()+12500;
    let provided=false;
    while(expected===generation && live && !done && Date.now()<until){
      const pending=await api('next',{session});
      if(!pending.pending){await delay(250);continue;}
      // One local detector pass, no face identity description or biometric export.
      const detected=await engine.detect(video);
      if(expected!==generation||!live)break;
      const faces=Array.isArray(detected.face)?detected.face:[];
      let confidence=faces.length?
        Number(faces[0].score??faces[0].confidence??0):0;
      if(!Number.isFinite(confidence))confidence=0;
      confidence=Math.max(0,Math.min(1,confidence));
      await api('submit',{
        session,request_id:pending.request_id,
        face_count:Math.min(2,faces.length),confidence,
        model_ready:true,camera_ready:stream?.active===true
      });
      provided=true;
      $('onboardEyesResult').textContent='Local inference delivered without media or identifying data.';
      break;
    }
    const answer=await testPromise;
    if(expected!==generation)return;
    if(answer.error)throw answer.error;
    const outcome=answer.result?.request?.status||'unknown';
    const success=provided&&outcome==='completed';
    await stop(success
      ? 'Camera-to-Tracky test completed. This is an owner-browser observation, not native HomeServer camera certification or identity recognition.'
      : 'Test ended without a verified browser observation ('+outcome+'). Check pairing/reconciliation, then retry.');
    state(success?'Local browser test complete · hardware review still required':'Needs attention');
  }catch(error){
    if(expected===generation){
      await stop('Camera released; test could not complete: '+String(error.message||'unknown error'));
      state('Not certified');
    }
  }finally{
    started=false;
    if(expected===generation)$('onboardEyesStart').disabled=false;
  }
}

function init(){
  if(!$('onboardEyes'))return;
  window.TrackyOwnerEyes={isActive:()=>live};
  $('onboardEyesStart').addEventListener('click',()=>{void test();});
  $('onboardEyesStop').addEventListener('click',()=>{void stop();});
  $('onboardEyesConsent').addEventListener('change',()=>{
    if(!$('onboardEyesConsent').checked&&live)void stop('Agent Eyes consent withdrawn; camera stopped.');
  });
  window.addEventListener('homeserver:onboarding-hidden',()=>{if(live)void stop();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden&&live)void stop();});
  window.addEventListener('pagehide',release);
  api('status').then(v=>{
    if(v.last_observation?.status==='browser_observation_completed')
      report('Previous owner-browser test completed. Each new test requires separate consent and camera permission.');
  }).catch(()=>report('Agent Eyes runtime status unavailable.'));
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});
else init();
