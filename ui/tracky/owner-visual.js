/* HomeServer's integrated Tracky self-enrollment card.
 * Reuses the exact Tracky participant IndexedDB + enrollment decisions at this
 * origin. Browser confirmation is NEVER a trusted server provider attestation.
 */
import { autoEnrollmentDecision, AUTO_ENROLLMENT_TARGET } from './src/auto-enrollment-core.js';
import { faceQuality, normalizeBox } from './src/participant-core.js';
import { listParticipants, saveParticipant, deleteParticipant } from './src/participant-store.js';
import { HUMAN_ESM_URL, HUMAN_MODEL_BASE } from './src/model-config.js';

const el = id => document.getElementById(id);
const SCOPE = 'owner-self-local-recognition-v1';
let active = null;
let stream = null;
let visual = null;
let localOwner = null;
let epoch = 0;
let busy = false;
let privacyCheckedAt = 0;
const MAX_ANGLES = 3;

async function api(path, body = null) {
  const response = await fetch('/api/v1/control/onboarding/visual/' + path, {
    method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
    headers: {Accept:'application/json',...(body?{'X-Requested-With':'XMLHttpRequest','Content-Type':'application/json'}:{})},
    ...(body?{body:JSON.stringify(body)}:{})
  });
  let data = {};
  try {data = await response.json();} catch (_) {}
  if(!response.ok) throw new Error(typeof data.detail==='string' ? data.detail : 'Local visual setup failed.');
  return data;
}

function message(text) {
  const node = el('onboardVisualDetails');
  if(node) node.textContent = text;
}

function closeCamera() {
  epoch += 1;
  active = null;
  if(stream){stream.getTracks().forEach(track=>track.stop());stream=null;}
  const video = el('onboardVisualVideo');
  if(video)video.srcObject = null;
  const stage = el('onboardVisualCapture');
  if(stage)stage.hidden = true;
}

function render(state = visual) {
  if(!el('onboardVisualState') || !state)return;
  visual = state;
  const clientReady=state.phase==='browser_reported';
  const capturing=Boolean(active);
  const missing=state.browser_enrollment_assets!==true;
  el('onboardVisualState').textContent = clientReady
    ? 'Saved in this browser · hardware certification pending'
    : capturing ? 'Collecting local samples…'
    : missing ? 'Integrated assets missing'
    : 'Optional · not enrolled';
  el('onboardVisual').dataset.complete = 'false'; // Never conflate a browser report with live certification.
  el('onboardVisualStart').disabled = busy || capturing || missing || clientReady;
  el('onboardVisualStop').hidden = !capturing;
  el('onboardVisualDelete').hidden = !localOwner;
  if(!busy && !capturing && clientReady)
    message(localOwner
      ? 'Your face profile was saved locally in this browser. HomeServer has only a browser-reported receipt; verified device-wide recognition and contact creation are not enabled.'
      : 'This HomeServer has a browser-reported profile from another browser. Use the original browser to review or delete it.');
  if(!busy && !capturing && !clientReady && localOwner)
    message('Your local Tracky profile already exists. With consent, I can resume reporting it without recapturing your face.');
  if(!busy && !capturing && !clientReady && !localOwner)
    message('Camera off. Three distinct face angles are captured automatically only after you explicitly start.');
}

async function loadLocal() {
  try {
    const records=await listParticipants();
    localOwner=records.find(p=>p.visualEnrollment?.scope==='owner-self')||null;
  }catch(_){localOwner=null;}
  render();
}

function ownerVideoPhoto(video, raw) {
  const box=raw?.box;
  if(!Array.isArray(box)||box.length<4)return null;
  const [x,y,w,h]=box.map(Number);
  if(![x,y,w,h].every(Number.isFinite)||w<=0||h<=0)return null;
  const canvas=document.createElement('canvas');canvas.width=256;canvas.height=256;
  const ctx=canvas.getContext('2d',{willReadFrequently:false});
  const pad=Math.max(w,h)*0.24;
  const sx=Math.max(0,x-pad),sy=Math.max(0,y-pad);
  const sw=Math.min(video.videoWidth-sx,w+2*pad),sh=Math.min(video.videoHeight-sy,h+2*pad);
  if(sw<=0||sh<=0)return null;
  ctx.drawImage(video,sx,sy,sw,sh,0,0,256,256);
  return canvas.toDataURL('image/jpeg',0.85);
}

async function reportLocal(record, session) {
  visual=await api('report',{session,participant_id:record.id,samples:record.embeddings.length});
  localOwner=record;
  await loadLocal();
  message('Local enrollment has finished. The Agent sees a browser-reported profile, not a certified HomeServer perception provider. You can continue without enabling tracking.');
}

async function scan(engine, generation, session, captures) {
  if(generation!==epoch || !stream || !active)return;
  const video=el('onboardVisualVideo');
  try {
    // Fail closed if hardware privacy engages or status cannot be read.
    if(Date.now()-privacyCheckedAt>2300){
      let state;
      try{state=await api('status');}
      catch(_){
        closeCamera();await api('cancel',{}).catch(()=>{});
        message('Camera stopped: HomeServer privacy status is unavailable.');return;
      }
      privacyCheckedAt=Date.now();
      if(state.privacy_engaged){
        closeCamera();await api('cancel',{}).catch(()=>{});
        message('Camera stopped: physical privacy is engaged.');return;
      }
    }
    if(generation!==epoch || !stream || !active)return;
    if(video?.readyState >= 2) {
      const raw=await engine.detect(video);
      if(generation!==epoch || !active)return;
      const faces=Array.isArray(raw.face)?raw.face:[];
      const first=faces.length===1?faces[0]:null;
      const w=video.videoWidth||1,h=video.videoHeight||1;
      const box=first?.box ? normalizeBox(first.box,w,h):{};
      const rotation=first?.rotation||first?.angle||{};
      const face=first?{
        quality:faceQuality(first,w,h),
        embedding:Array.isArray(first.embedding)?first.embedding:Array.from(first.embedding||[]),
        rotation:{
          // Human face rotation may be in radians; normalize for pose spacing.
          yaw:Math.abs(Number(rotation.yaw||0))<=Math.PI ? Number(rotation.yaw||0)*180/Math.PI : Number(rotation.yaw||0),
          pitch:Math.abs(Number(rotation.pitch||0))<=Math.PI ? Number(rotation.pitch||0)*180/Math.PI : Number(rotation.pitch||0)
        },
        box:{cx:box.cx||0,cy:box.cy||0}
      }:null;
      const decision=autoEnrollmentDecision(
        {consent:true,active:true,samples:captures,lastCaptureAt:active.lastCaptureAt},
        {faceCount:faces.length,face,now:Date.now()}
      );
      if(decision.capture) {
        const photo=ownerVideoPhoto(video,first);
        captures.push({pose:decision.pose,embedding:Array.from(face.embedding),photo});
        active.lastCaptureAt=Date.now();
        el('onboardVisualProgress').textContent='Saved angle '+captures.length+'/'+AUTO_ENROLLMENT_TARGET
          +(captures.length===1?' · gently turn your head':'');
        if(captures.length>=MAX_ANGLES) {
          closeCamera();
          busy=true;
          message('Saving your participant securely inside this browser…');
          try {
            const record=await saveParticipant({
              name:'My visual profile',embeddings:captures.map(x=>x.embedding),
              primaryPhoto:captures[0].photo,latestPhoto:captures[captures.length-1].photo||captures[0].photo,
              recognitionEnabled:true,voiceRecognitionEnabled:false,
              visualEnrollment:{scope:'owner-self',consentedAt:new Date().toISOString(),
                automatic:true,trackingEnabled:false,cloudSync:false,
                contactCreation:'requires_owner_approval'}
            });
            await reportLocal(record,session);
          } catch(error) {
            // Browser profile may be durable even when semantic reporting fails;
            // starting again offers owner-approved, capture-free recovery.
            message('Local save/report needs attention: '+error.message+'. You can resume from your existing local profile.');
            await loadLocal();
          } finally {busy=false;render();}
          return;
        }
      } else {
        const hints={
          'exactly-one-face-required':'Keep only your own face in the frame.',
          'turn-slightly-for-distinct-angle':'Turn or tilt slightly for another distinct angle.',
          'hold-for-next-angle':'Hold steady while spacing the captures.',
          'move-closer-and-hold-steady':'Center your face in better light and hold still.'
        };
        el('onboardVisualProgress').textContent=hints[decision.reason]||'Checking quality locally…';
      }
    }
  }catch(_){
    el('onboardVisualProgress').textContent='Recognition paused; adjust light or stop to retry.';
  }
  if(generation===epoch && stream && active)
    active.timer=window.setTimeout(()=>{void scan(engine,generation,session,captures);},500);
}

async function start() {
  if(busy || active)return;
  if(!el('onboardVisualConsent').checked){
    message('Choose the explicit local self-enrollment consent before starting.');
    return;
  }
  if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia){
    message('Your camera requires localhost or HTTPS and a compatible browser.');
    return;
  }
  if(!window.confirm('Enroll only yourself on this browser? The face model downloads from a pinned HTTPS distribution, while your profile stays in local Tracky storage. This does not enable other-person tracking, Cloud biometric upload, or contact creation.'))return;
  busy=true;render();
  let session=null;
  const generation=++epoch;
  try {
    const started=await api('start',{consent:true,scope:SCOPE});
    session=started.session;
    visual=started.visual;
    if(generation!==epoch || !el('onboardVisualConsent').checked){
      await api('cancel',{}).catch(()=>{});
      return;
    }
    if(localOwner && localOwner.embeddings?.length>=MAX_ANGLES){
      message('Recovering your existing local Tracky profile without reopening the camera…');
      await reportLocal(localOwner,session);
      return;
    }
    active={loading:true,lastCaptureAt:0,timer:0};
    render();
    message('Preparing the face model. The camera will request permission afterward.');
    // Original Tracky @vladmandic/human version is pinned; fully offline/
    // cryptographically verified model packaging is a separate release gate.
    const mod=await import(HUMAN_ESM_URL);
    if(generation!==epoch)return;
    const Human=mod.Human||mod.default;
    const engine=new Human({
      backend:'webgl',debug:false,modelBasePath:HUMAN_MODEL_BASE,
      face:{enabled:true,detector:{enabled:true,maxDetected:2,minConfidence:0.65},
        mesh:{enabled:true},description:{enabled:true},iris:{enabled:false},
        emotion:{enabled:false},antispoof:{enabled:false},liveness:{enabled:false}},
      body:{enabled:false},hand:{enabled:false},object:{enabled:false},
      gesture:{enabled:false},segmentation:{enabled:false}
    });
    await engine.load();
    if(generation!==epoch)return;
    stream=await navigator.mediaDevices.getUserMedia({
      video:{width:{ideal:960},height:{ideal:720},facingMode:{ideal:'user'}},audio:false
    });
    if(generation!==epoch){stream.getTracks().forEach(t=>t.stop());stream=null;return;}
    const video=el('onboardVisualVideo');
    video.srcObject=stream;await video.play();
    privacyCheckedAt=0;
    active={lastCaptureAt:0,timer:0};
    el('onboardVisualCapture').hidden=false;
    const captures=[];
    message('Camera is active only for this consented self-enrollment. Look forward, then turn slightly for the next angle.');
    busy=false;render();
    await scan(engine,generation,session,captures);
  }catch(error){
    closeCamera();
    message('Enrollment could not start: '+String(error.message||'Camera/model unavailable')+'. No automatic background capture will continue.');
    if(session)try{visual=await api('cancel',{});}catch(_){}
  }finally{busy=false;render();}
}

async function stop() {
  closeCamera();
  try{visual=await api('cancel',{});}catch(_){}
  message('Camera stopped and this HomeServer enrollment session was cancelled. You can resume later.');
  render();
}

async function remove() {
  if(busy||active||!localOwner)return;
  if(!window.confirm('Permanently delete your local Tracky face samples and portrait from this browser? HomeServer will then clear its non-biometric status report.'))return;
  busy=true;render();
  const id=localOwner.id;
  try {
    // Actual biometric deletion precedes clearing the HomeServer status.
    await deleteParticipant(id);
    localOwner=null;
    if(visual?.local_participant_id===id)visual=await api('delete',{participant_id:id});
    await loadLocal();
    message('Local face samples and portrait deleted from this browser. This does not delete profiles independently stored on other devices.');
  }catch(error){message('Deletion needs attention: '+error.message+'. Check the original browser before retrying.');}
  finally{busy=false;render();}
}

function init() {
  if(!el('onboardVisual'))return;
  window.HomeServerVisualEnrollment={render};
  el('onboardVisualConsent').addEventListener('change',()=>{
    if(el('onboardVisualConsent').checked)return;
    // Unchecking revokes the session even during async model initialization.
    closeCamera();
    void api('cancel',{}).catch(()=>{});
    message(localOwner
      ? 'Local enrollment stopped. Your existing local profile remains until you explicitly delete it.'
      : 'Visual consent withdrawn and camera stopped.');
    render();
  });
  el('onboardVisualStart').addEventListener('click',()=>{void start();});
  el('onboardVisualStop').addEventListener('click',()=>{void stop();});
  el('onboardVisualDelete').addEventListener('click',()=>{void remove();});
  window.addEventListener('homeserver:onboarding-hidden',()=>{if(active)void stop();});
  document.addEventListener('click',event=>{
    if(active && event.target.closest('[data-view]') && !event.target.closest('[data-view="chat"]'))void stop();
  });
  document.addEventListener('visibilitychange',()=>{if(document.hidden&&active)void stop();});
  window.addEventListener('pagehide',closeCamera);
  Promise.all([api('status'),loadLocal()]).then(([state])=>{visual=state;render();})
    .catch(()=>message('Local Tracky onboarding status is not available.'));
}

if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});
else init();
