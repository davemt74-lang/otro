/* Optional, bounded browser-only self-comparison with the enrolled owner.
 * No new participant, no server recognition report, no photo/embedding export.
 * A similarity result is not identity proof or HomeServer certification.
 */
import {listParticipants,getParticipant} from './src/participant-store.js';
import {HUMAN_ESM_URL,HUMAN_MODEL_BASE} from './src/model-config.js';
import {evaluateOwnerSelfCheck,OWNER_SELF_CHECK_DURATION_MS} from './src/owner-self-check-core.js';

const el=id=>document.getElementById(id);
let generation=0,stream=null,deadlineTimer=null,scanTimer=null,loading=false;
const SCOPE='owner-self-local-recognition-v1';

function say(value){if(el('onboardSelfCheckState'))el('onboardSelfCheckState').textContent=value;}
function close(){
  generation+=1;
  if(scanTimer!==null){clearTimeout(scanTimer);scanTimer=null;}
  if(deadlineTimer!==null){clearTimeout(deadlineTimer);deadlineTimer=null;}
  if(stream){for(const track of stream.getTracks())track.stop();stream=null;}
  const video=el('onboardSelfCheckVideo');
  if(video)video.srcObject=null;
  if(el('onboardSelfCheckPreview'))el('onboardSelfCheckPreview').hidden=true;
  loading=false;
  if(el('onboardSelfCheckStart'))el('onboardSelfCheckStart').disabled=false;
  if(el('onboardSelfCheckStop'))el('onboardSelfCheckStop').hidden=true;
}
function stop(){
  const wasActive=loading||Boolean(stream);
  close();
  if(wasActive)say('Self-check stopped. No live descriptor or result was saved.');
}
async function ownerReady(token){
  const response=await fetch('/api/v1/control/onboarding/visual/status',{
    credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}
  });
  if(token!==generation) return false;
  if(!response.ok)throw new Error('HomeServer privacy status is unavailable');
  const state=await response.json();
  return token===generation&&state.privacy_engaged===false
    &&state.phase==='browser_reported'&&state.consented===true
    &&state.scope===SCOPE;
}
async function scan(engine,participant,token,started){
  if(token!==generation||!stream)return;
  if(Date.now()-started>=OWNER_SELF_CHECK_DURATION_MS){
    close();say('Self-check timed out without a usable comparison. No result saved.');return;
  }
  try{
    // Every observation requires live, fail-closed HomeServer privacy status.
    if(!await ownerReady(token)){
      if(token===generation){close();say('Stopped: local consent or privacy status changed.');}
      return;
    }
    const video=el('onboardSelfCheckVideo');
    if(token!==generation||!stream)return;
    if(video?.readyState>=2){
      const result=await engine.detect(video);
      if(token!==generation||!stream)return;
      // Enrollment can be revoked/deleted while inference is in flight.
      const current=await getParticipant(participant.id);
      if(token!==generation||!stream)return;
      if(!await ownerReady(token)){if(token===generation){close();say('Stopped: consent or privacy changed.');}return;}
      const decision=evaluateOwnerSelfCheck({
        consent:el('onboardSelfCheckConsent').checked,
        active:true,participant:current,
        faces:Array.isArray(result?.face)?result.face:[],
        width:video.videoWidth||1,height:video.videoHeight||1
      });
      if(decision.state==='local_similarity_only'){
        close();
        say('Local browser comparison matched your stored sample. This is an unverified similarity demonstration, not proof of identity or certified HomeServer recognition.');
        return;
      }
      if(decision.state==='local_comparison_not_matched'){
        close();
        say('Local comparison did not match the stored sample. No identity was asserted or data stored.');
        return;
      }
      if(decision.state==='multiple_faces'){
        close();
        say('Stopped: more than one face was detected. This test never enrolls or identifies bystanders.');
        return;
      }
      if(decision.state==='approval_required'||decision.state==='profile_unavailable'){
        close();say('Stopped: owner approval or local self-profile is unavailable.');return;
      }
      say('Local self-check: center one face in good light. No image is saved.');
    }
  }catch(_){
    if(token===generation){
      close();say('Stopped: model or privacy check is unavailable. No identity was asserted.');
    }
    return;
  }
  if(token===generation&&stream)
    scanTimer=setTimeout(()=>{void scan(engine,participant,token,started);},450);
}
async function start(){
  if(loading||stream)return;
  if(!el('onboardSelfCheckConsent').checked){
    say('Explicitly consent to a one-time browser-only self-comparison.');return;
  }
  if(window.HomeServerVisualEnrollment?.isCapturing()||window.TrackyOwnerEyes?.isActive()||window.TrackyNativeCamera?.isActive?.()) {
    say('Finish the active camera test or enrollment first.');return;
  }
  if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia){
    say('A secure browser and camera permission are required.');return;
  }
  if(!window.confirm('Compare one live face only against your stored local self-profile for at most 12 seconds? No result or biometric data is sent to HomeServer or Cloud. This is not verified identity.'))return;
  loading=true;el('onboardSelfCheckStart').disabled=true;
  el('onboardSelfCheckStop').hidden=false;
  const token=++generation;
  let acquired=null;
  try{
    const rows=await listParticipants();
    if(token!==generation)return;
    const state=await fetch('/api/v1/control/onboarding/visual/status',{
      credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}
    });
    if(!state.ok)throw new Error('Local status unavailable');
    const report=await state.json();
    const participant=rows.find(x=>x.visualEnrollment?.scope==='owner-self'
      &&x.id===report.local_participant_id&&x.recognitionEnabled!==false
      &&Array.isArray(x.embeddings)&&x.embeddings.length>=3);
    if(!participant||!await ownerReady(token))
      throw new Error('Current local self-enrollment or privacy clearance unavailable');
    say('Loading the existing pinned browser model. Camera is still off.');
    const mod=await import(HUMAN_ESM_URL);
    if(token!==generation)return;
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
    if(token!==generation||!el('onboardSelfCheckConsent').checked)return;
    if(!await ownerReady(token))throw new Error('Local privacy or consent changed');
    acquired=await navigator.mediaDevices.getUserMedia({
      video:{width:{ideal:960},height:{ideal:720},facingMode:{ideal:'user'}},audio:false
    });
    if(token!==generation||!el('onboardSelfCheckConsent').checked){
      for(const track of acquired.getTracks())track.stop();return;
    }
    stream=acquired;acquired=null;
    const video=el('onboardSelfCheckVideo');
    video.srcObject=stream;
    await video.play();
    if(token!==generation)return;
    loading=false;
    el('onboardSelfCheckPreview').hidden=false;
    const started=Date.now();
    deadlineTimer=setTimeout(()=>{
      if(token===generation){close();say('Self-check timed out. Camera stopped; no result stored.');}
    },OWNER_SELF_CHECK_DURATION_MS);
    await scan(engine,participant,token,started);
  }catch(_){
    if(token===generation){close();say('Self-check unavailable. Camera is off; no result stored.');}
  }finally{
    if(acquired)for(const track of acquired.getTracks())track.stop();
    if(token===generation){loading=false;el('onboardSelfCheckStart').disabled=false;}
  }
}
function init(){
  if(!el('onboardSelfCheck'))return;
  window.TrackyOwnerSelfCheck={isActive:()=>loading||Boolean(stream),stop};
  el('onboardSelfCheckStart').addEventListener('click',()=>{void start();});
  el('onboardSelfCheckStop').addEventListener('click',stop);
  el('onboardSelfCheckConsent').addEventListener('change',()=>{
    if(!el('onboardSelfCheckConsent').checked)stop();
  });
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
  window.addEventListener('homeserver:onboarding-hidden',stop);
  window.addEventListener('tracky:visual-state-changed',stop);
  window.addEventListener('pagehide',close);
  document.addEventListener('click',event=>{
    if(event.target.closest('[data-view]')&&!event.target.closest('[data-view="chat"]'))stop();
  });
  say('Optional local self-check. Camera off; no identity proof is produced.');
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});
else init();

