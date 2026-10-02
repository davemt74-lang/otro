/* Tracky 1F3 optional one-frame local self comparison.
 * No remote identification, automatic capture, profile modification or
 * security authentication. Pinned browser model still requires HTTPS download.
 */
import { listParticipants } from './src/participant-store.js';
import { faceQuality } from './src/participant-core.js';
import { evaluateOwnerSelfFrame } from './src/owner-self-check-core.js';
import { HUMAN_ESM_URL, HUMAN_MODEL_BASE } from './src/model-config.js';

const el=id=>document.getElementById(id);
const STATUS='/api/v1/control/onboarding/visual/status';
const NATIVE='/api/v1/control/onboarding/visual/native/status';
const MANAGED='/api/v1/control/onboarding/visual/native/session/status';
const MAX_CAPTURE_MS=18000;
let busy=false, stream=null, epoch=0, watchdog=null;

function state(value){if(el('onboardVisualVerifyState'))el('onboardVisualVerifyState').textContent=value;}
async function safeStatus(url){
  const result=await fetch(url,{credentials:'same-origin',cache:'no-store',
                                 headers:{Accept:'application/json'}});
  if(!result.ok)throw new Error('Local privacy or camera status is unavailable.');
  return result.json();
}
function release(){
  epoch++;
  if(watchdog!==null){window.clearTimeout(watchdog);watchdog=null;}
  stream?.getTracks().forEach(track=>track.stop());
  stream=null;
  const video=el('onboardVisualVerifyVideo');
  if(video)video.srcObject=null;
  if(el('onboardVisualVerifyCapture'))el('onboardVisualVerifyCapture').hidden=true;
  if(el('onboardVisualVerifyStop'))el('onboardVisualVerifyStop').hidden=true;
}
function stop(reason='One-frame self-check stopped. No result was retained.'){
  release();
  el('onboardVisualVerifyConsent').checked=false;
  state(reason);
  if(el('onboardVisualVerifyStart'))el('onboardVisualVerifyStart').disabled=busy;
}
async function verify(){
  if(busy||stream)return;
  if(!el('onboardVisualVerifyConsent')?.checked){
    state('Give separate consent before the one-frame self-check.');return;
  }
  if(window.HomeServerVisualEnrollment?.isCapturing() ||
     window.TrackyOwnerEyes?.isActive()){
    state('Finish any active browser enrollment or Agent Eyes session first.');return;
  }
  if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia){
    state('Camera access requires HTTPS or localhost and a supported browser.');return;
  }
  if(!window.confirm('Compare exactly one new frame of YOUR face with your existing three local browser samples? The model loads from pinned HTTPS; no images or descriptors will be uploaded or saved. A similarity indication is not verified identity or liveness.'))return;
  busy=true;
  const run=++epoch;
  const btn=el('onboardVisualVerifyStart');btn.disabled=true;
  el('onboardVisualVerifyStop').hidden=false;
  try{
    const [report,native,managed,participants]=await Promise.all([
      safeStatus(STATUS),safeStatus(NATIVE),safeStatus(MANAGED),listParticipants()
    ]);
    const owner=participants.find(x=>x.visualEnrollment?.scope==='owner-self' &&
                                    x.id===report.local_participant_id);
    if(report.privacy_engaged||native.privacy_engaged||native.running||
       native.capture_worker_active||managed.active||managed.stop_requested)
      throw new Error('A privacy gate or existing camera session blocks this check.');
    if(!report.consented||report.phase!=='browser_reported'||!owner||
       !Array.isArray(owner.embeddings)||owner.embeddings.length<3)
      throw new Error('Finish consented self-enrollment in THIS browser first.');
    if(run!==epoch||!el('onboardVisualVerifyConsent').checked)return;
    state('Loading the pinned HTTPS face model; camera remains off until ready.');
    const module=await import(HUMAN_ESM_URL);
    if(run!==epoch)return;
    const Human=module.Human||module.default;
    if(typeof Human!=='function')throw new Error('Face model unavailable.');
    const engine=new Human({
      backend:'webgl',debug:false,modelBasePath:HUMAN_MODEL_BASE,
      face:{enabled:true,detector:{enabled:true,maxDetected:2,minConfidence:0.65},
        mesh:{enabled:true},description:{enabled:true},iris:{enabled:false},
        emotion:{enabled:false},antispoof:{enabled:false},liveness:{enabled:false}},
      body:{enabled:false},hand:{enabled:false},object:{enabled:false},
      gesture:{enabled:false},segmentation:{enabled:false}
    });
    await engine.load();
    if(run!==epoch)return;
    const [privacyAgain,nativeAgain,managedAgain]=await Promise.all([
      safeStatus(STATUS),safeStatus(NATIVE),safeStatus(MANAGED)
    ]);
    if(privacyAgain.privacy_engaged||!privacyAgain.consented||
       privacyAgain.local_participant_id!==owner.id||
       nativeAgain.privacy_engaged||nativeAgain.running||
       nativeAgain.capture_worker_active||managedAgain.active)
      throw new Error('The local consent or camera state changed. Restart with fresh approval.');
    if(run!==epoch||!el('onboardVisualVerifyConsent').checked)return;
    const opened=await navigator.mediaDevices.getUserMedia({
      video:{width:{ideal:960},height:{ideal:720},facingMode:{ideal:'user'}},
      audio:false
    });
    if(run!==epoch){opened.getTracks().forEach(t=>t.stop());return;}
    stream=opened;
    const video=el('onboardVisualVerifyVideo');
    video.srcObject=stream;
    el('onboardVisualVerifyCapture').hidden=false;
    watchdog=window.setTimeout(()=>{stop('One-frame self-check timed out; camera stopped.');},MAX_CAPTURE_MS);
    await video.play();
    if(run!==epoch)return;
    state('Checking one frame locally. Ensure only your face is visible.');
    const frame=await engine.detect(video);
    if(run!==epoch)return;
    // Privacy and owner report are checked again after asynchronous inference.
    const after=await safeStatus(STATUS);
    if(run!==epoch)return;
    if(after.privacy_engaged||!after.consented||
       after.local_participant_id!==owner.id)
      throw new Error('Consent or privacy changed. Discarding the local comparison.');
    const faces=Array.isArray(frame.face)?frame.face:[];
    const face=faces.length===1?faces[0]:null;
    const vector=face?.embedding
      ? Array.from(face.embedding) : [];
    const quality=face?faceQuality(face,video.videoWidth||1,video.videoHeight||1):0;
    const assessment=evaluateOwnerSelfFrame({
      consent:true,report:after,owner,faces:faces.length===1?[{embedding:vector}]:faces,
      quality
    });
    if(assessment.matched){
      state('Local similarity candidate found for your saved self-profile. This is NOT proof of identity or liveness, and no Agent action was authorized.');
    }else{
      state('No robust local self-match: '+assessment.reason.replaceAll('_',' ')+
        '. Nothing was saved or sent.');
    }
  }catch(error){
    if(run===epoch)state('Local self-check unavailable: '+String(error.message||'camera/model issue'));
  }finally{
    if(run===epoch)release();
    busy=false;
    el('onboardVisualVerifyConsent').checked=false;
    btn.disabled=false;
  }
}
function init(){
  if(!el('onboardVisualVerify'))return;
  window.TrackyOwnerSelfVerify={isActive:()=>busy||Boolean(stream)};
  el('onboardVisualVerifyStart').addEventListener('click',()=>{void verify();});
  el('onboardVisualVerifyStop').addEventListener('click',()=>stop());
  el('onboardVisualVerifyConsent').addEventListener('change',()=>{
    if(!el('onboardVisualVerifyConsent').checked&&
       (busy||stream))stop('Self-check consent withdrawn; camera stopped.');
  });
  window.addEventListener('tracky:visual-state-changed',()=>{
    if(busy||stream)stop('Visual profile changed; self-check cancelled.');
    else el('onboardVisualVerifyConsent').checked=false;
  });
  window.addEventListener('homeserver:onboarding-hidden',()=>{if(busy||stream)stop();});
  document.addEventListener('visibilitychange',()=>{
    if(document.hidden&&(busy||stream))stop('Page hidden: camera stopped.');
  });
  window.addEventListener('pagehide',release);
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});
else init();
