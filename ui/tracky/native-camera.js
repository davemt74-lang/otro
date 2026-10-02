/* Native HomeServer camera test: owner gesture -> local detector -> canonical Tracky.
 * This page NEVER captures the camera itself for the native test.
 */
(()=>{
'use strict';
const $=id=>document.getElementById(id);
const API='/api/v1/control/onboarding/visual/native/';
let busy=false, running=false;
function say(message){if($('onboardNativeDetails'))$('onboardNativeDetails').textContent=message;}
function label(message){if($('onboardNativeStatus'))$('onboardNativeStatus').textContent=message;}
async function call(path,body){
  const response=await fetch(API+path,{
    method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',
    headers:{Accept:'application/json',...(body===undefined?{}:{
      'X-Requested-With':'XMLHttpRequest','Content-Type':'application/json'
    })},
    ...(body===undefined?{}:{body:JSON.stringify(body)})
  });
  const result=await response.json().catch(()=>({}));
  if(!response.ok)throw Error(result.detail||'Native camera test could not complete.');
  return result;
}
async function refresh(){
  const current=await call('diagnose');
  const ready=Boolean(current.model?.installed&&current.model?.model_present);
  const recovery=current.issues?.map(issue=>issue.code).join(', ')||'';
  const cert=await call('certification');
  if($('onboardNativeReview')) $('onboardNativeReview').hidden=!cert.review_ready||cert.owner_accepted_current_run;
  if($('onboardNativeCertState'))$('onboardNativeCertState').textContent=cert.owner_accepted_current_run
    ? 'Owner-attested installed-device test · independent physical certification pending'
    : 'Independent hardware certification pending · '+(cert.review_ready?'Owner review available':'Complete camera and privacy steps first');
  label(current.running?'Native camera test running':ready?'Native detector installed · not certified':'Local detector unavailable');
  if(!running){
    if(!ready)say('Runtime or model missing. Install the signed HomeServer upgrade, then rerun diagnosis.');
    else if(current.last_test_status==='native_detector_completed')
      say('Previous local test completed; on-device owner review is still required for hardware certification.');
    else say(recovery
      ? 'Diagnosis: '+recovery+'. The Agent can explain safe repair steps; camera testing still requires your approval.'
      : 'Ready for your owner-approved test. Hardware certification requires installed-device review.');
  }
}
async function run(){
  if(busy || running)return;
  if(!($('onboardNativeConsent')?.checked)){
    say('Authorize the one-time local camera test before proceeding.');return;
  }
  if(window.TrackyOwnerEyes?.isActive()||window.HomeServerVisualEnrollment?.isCapturing()){
    say('Finish the existing Tracky camera activity first.');return;
  }
  const index=Number($('onboardNativeIndex')?.value);
  if(![0,1,2].includes(index)){say('Select a supported local camera.');return;}
  if(!window.confirm(
    'Allow this HomeServer to open camera '+index+
    ' and locally examine ONE frame? No photo or face template is retained or uploaded. You may stop the test.'
  ))return;
  busy=running=true;
  $('onboardNativeStart').disabled=true;
  $('onboardNativeCancel').hidden=false;
  label('One-shot test running…');
  say('HomeServer is opening the selected camera and executing the locally installed detector.');
  try{
    const result=await call('test',{
      consent:true,scope:'owner-native-single-camera-test.v1',camera_index:index
    });
    const status=result.request?.status||'unknown';
    if(status==='completed'){
      label('Local test completed · owner review required');
      say('HomeServer executed a local camera/detector observation. This is NOT face identification or production hardware certification.');
    }else{
      label('Test not verified');
      say('The test did not complete: '+(result.request?.reason||status)+'. Check camera access and HomeServer continuity before retrying.');
    }
  }catch(error){label('Not verified');say('Camera test stopped or unavailable: '+String(error.message));}
  finally{
    running=busy=false;
    $('onboardNativeStart').disabled=false;
    $('onboardNativeCancel').hidden=true;
  }
}
async function cancel(){
  if(!running)return;
  $('onboardNativeCancel').disabled=true;
  try{await call('cancel',{});say('Cancellation requested. HomeServer releases the camera as soon as the driver returns.');}
  catch(error){say('Cancellation needs attention: '+String(error.message));}
  finally{$('onboardNativeCancel').disabled=false;}
}
async function checkPrivacy(){
  if(!window.confirm('Engage your HomeServer physical privacy disconnect before proceeding. This check will NOT open the camera. Confirm you want to inspect its reported state.'))return;
  const button=$('onboardNativePrivacy');
  button.disabled=true;
  try{
    const review=await call('privacy-review',{consent:true});
    await refresh();
    if(review.privacy_check==='reported_software_gate_engaged')
      say('HomeServer reports its camera software gate engaged by the privacy switch. No camera was opened; physical camera disconnect and installed-device certification remain unverified.');
    else say(review.instruction||'Privacy switch not verified. Engage it, then retry this read-only check.');
  }catch(error){say('Privacy review unavailable: '+String(error.message));}
  finally{button.disabled=false;}
}
async function ownerReview(){
  const review=$('onboardNativeReview');
  if(!review)return;
  if(!window.confirm('Did YOU run the camera test on the installed HomeServer, observe the camera release after it completed, and verify the reported privacy software gate? This records your review, NOT independent proof of physical camera disconnect or biometric identity.'))return;
  review.disabled=true;
  try{
    const result=await call('owner-review',{consent:true,installed_device:true,
      camera_release_observed:true,software_privacy_gate_observed:true});
    await refresh();
    say(result.owner_accepted_current_run?'Local owner review recorded. Physical camera disconnect and independent certification remain unverified.':'Review not accepted.');
  }catch(error){say('Owner review unavailable: '+String(error.message));}
  finally{review.disabled=false;}
}
async function init(){
  if(!$('onboardNativeCamera'))return;
  $('onboardNativeStart').addEventListener('click',()=>{void run();});
  $('onboardNativeCancel').addEventListener('click',()=>{void cancel();});
  $('onboardNativePrivacy').addEventListener('click',()=>{void checkPrivacy();});
  $('onboardNativeReview').addEventListener('click',()=>{void ownerReview();});
  $('onboardNativeConsent').addEventListener('change',()=>{
    if(!$('onboardNativeConsent').checked && running)void cancel();
  });
  window.addEventListener('homeserver:onboarding-hidden',()=>{if(running)void cancel();});
  window.addEventListener('pagehide',()=>{if(running)void cancel();});
  await refresh().catch(()=>label('Native detector status unavailable'));
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>{void init();},{once:true});
else void init();
})();