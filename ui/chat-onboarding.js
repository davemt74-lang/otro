/* The existing Agent Chat is the complete first-run canvas.
   This scripted conversation works before model inference is configured. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const canvas = el('chatOnboardingCanvas');
  if (!canvas) return;
  let snapshot = null;
  let busy = false;
  let visible = false;
  let polling = false;
  let lastError = '';
  let operation = 0;
  let refreshRequest = 0;

  async function api(path,method='GET',body=null) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 45000);
    try {
      const response = await fetch(path,{
        method,credentials:'same-origin',cache:'no-store',signal:controller.signal,
        headers:{Accept:'application/json',...(method==='POST'?{'X-Requested-With':'XMLHttpRequest'}:{}),...(body?{'Content-Type':'application/json'}:{})},
        ...(body?{body:JSON.stringify(body)}:{})
      });
      let data = {};
      try {data=await response.json();} catch (error) {
        if(controller.signal.aborted)throw error;
        throw new Error('HomeServer returned an unreadable response. Check status and retry.');
      }
      if(!data||typeof data!=='object'||Array.isArray(data))throw new Error('HomeServer returned an unreadable response.');
      if (!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'HomeServer could not complete this step.');
      return data;
    } catch(error) {
      if(controller.signal.aborted)throw new Error('This step took too long. Check status before retrying; HomeServer may still be finishing it.');
      throw error;
    } finally {clearTimeout(timeout);}
  }

  function feedback(message,isError=false) {
    lastError=isError?message:'';
    const box=el('onboardFeedback');
    box.hidden=!message;box.textContent=message;box.classList.toggle('error',isError);
  }
  function itemText(p){
    return p.name+(p.ready?' · installed & verified':!p.supported?' · unsupported on this computer':
      p.status==='installing'?' · installing':p.status==='failed'?' · retry needed':' · available');
  }
  function render(){
    if(!snapshot)return;
    const cloud=snapshot.cloud||{}, code=snapshot.pairing||{}, voice=snapshot.provision||{};
    if(snapshot.visual)window.HomeServerVisualEnrollment?.render(snapshot.visual);
    const paired=Boolean(cloud.paired), online=Boolean(cloud.connected);
    const recoveryPending=Boolean(snapshot.pairing_recovery_pending)&&!paired;
    el('onboardCloudState').textContent=online?'Connected ✓':paired?'Paired · establishing connection':code.state==='pending'?'Waiting for Cloud':'Not connected';
    el('onboardCloud').dataset.complete=online?'true':'false';
    el('onboardConnectionHelp').textContent=online?'Live connection verified. You can start chatting.':paired?'Pairing is saved. HomeServer is retrying the live connection automatically. Check status or open Connection settings if it stays offline.':recoveryPending?'A previous pairing needs recovery. Generate a new code to continue.':'Sign in and approve the connection in VP3 Cloud. No camera or microphone permission is needed.';
    el('onboardCheckConnection').disabled=busy;
    el('onboardConnectionSettings').hidden=!paired;
    el('onboardLegacyPairForm').querySelector?.('button')?.toggleAttribute('disabled',busy);
    const hasCode=code.state==='pending'&&Boolean(code.code)&&!paired;
    el('onboardPairingCode').hidden=!hasCode;
    if(hasCode){
      el('onboardCodeText').textContent=code.code;
      const expiry=Date.parse(code.expires_at||'');
      el('onboardCodeExpiry').textContent=Number.isNaN(expiry)?'Valid for 15 minutes':('Expires '+new Date(expiry).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}));
      if(snapshot.cloud_url==='https://vp3.me/settings-homeserver.php')el('onboardCloudLink').href=snapshot.cloud_url+'#hs_code='+encodeURIComponent(code.code);
    }
    el('onboardStartCloud').hidden=hasCode||paired||recoveryPending;
    el('onboardResetCode').hidden=!(hasCode||recoveryPending);
    el('onboardStartCloud').disabled=busy;el('onboardResetCode').disabled=busy;
    el('onboardLegacyPairForm').hidden=paired;
    if(Array.isArray(voice.packages)){
    const packages=voice.packages;
    el('onboardVoicePackages').textContent=packages.length?packages.map(itemText).join('  ·  '):'No compatible voice packages detected.';
    const active=voice.phase==='running';
    el('onboardVoiceState').textContent=voice.supported_count===0?'Not supported on this computer':active?'Installing…':voice.all_ready?'Installed & verified ✓':
      voice.phase==='attention'?'Needs attention':voice.phase==='interrupted'?'Resuming':'Optional setup';
    el('onboardVoice').dataset.complete=voice.all_ready?'true':'false';
    el('onboardInstallVoice').disabled=busy||active||Boolean(voice.all_ready)||voice.supported_count===0;
    el('onboardInstallVoice').textContent=active?'Preparing securely…':voice.all_ready?'Voice ready ✓':
      voice.phase==='attention'?'Retry voice preparation →':'Prepare local voice →';
    }
    el('onboardFinish').hidden=!online;
    el('onboardFinish').disabled=busy;
    el('onboardLater').hidden=online;
    el('onboardLater').disabled=busy;
    el('onboardLater').textContent=paired?'Use HomeServer while Cloud reconnects':'Use HomeServer without Cloud';
    canvas.setAttribute('aria-busy',String(busy));
    if(voice.reason&&voice.phase==='attention'&&!lastError)feedback(voice.reason,true);
    const toggle=el('chatOnboardingToggle');
    if(toggle)toggle.textContent=visible?'Close setup':'Setup';
  }

  async function refresh(){
    const request=++refreshRequest;
    const optional=Boolean(el('onboardOptional')?.open);
    const next=await api('/api/v1/control/onboarding/summary'+(optional?'':'?optional=false'));
    if(request!==refreshRequest)return;
    snapshot=next;render();
  }
  async function act(task,message){
    if(busy)return;
    const generation=++operation;busy=true;render();
    feedback('Working…');
    try{
      const result=await task();
      if(generation!==operation)return false;
      lastError='';
      await refresh();
      if(generation!==operation)return false;
      if(message)feedback(typeof message==='function'?message(result):message);
      return true;
    }catch(error){if(generation===operation)feedback(error.message,true);return false;}
    finally{busy=false;render();}
  }
  function hide(focus=true){
    ++operation;++refreshRequest;visible=false;canvas.hidden=true;
    window.dispatchEvent(new Event('homeserver:onboarding-hidden'));
    const t=el('chatOnboardingToggle');if(t)t.textContent='Setup';
    if(focus)el('chatInput')?.focus({preventScroll:true});
  }
  function show(){
    visible=true;canvas.hidden=false;
    const generation=operation;
    refresh().catch(e=>{if(visible&&generation===operation)feedback('Could not read setup status: '+e.message,true);});
  }

  // Start with one owner action. Cloud remains responsible for sign-in and
  // explicit approval; no pairing verifier or bearer token reaches the browser.
  el('onboardStartCloud').addEventListener('click',async()=>{
    if(busy)return;
    const tab=window.open('about:blank','_blank');
    if(tab)try{tab.opener=null;}catch(_){}
    const generation=++operation;busy=true;render();feedback('Preparing your secure Cloud connection…');
    try{
      await api('/api/v1/control/onboarding/device/start','POST');
      if(generation!==operation){if(tab)tab.close();return;}
      await refresh();
      if(generation!==operation){if(tab)tab.close();return;}
      const code=snapshot?.pairing?.code;
      if(snapshot?.cloud?.paired){if(tab)tab.close();feedback('Your pairing is already saved. Check the live connection below.');return;}
      if(!code)throw new Error('The pairing code is not ready. Try again.');
      const url='https://vp3.me/settings-homeserver.php#hs_code='+encodeURIComponent(code);
      el('onboardCloudLink').href=url;
      if(tab)tab.location.replace(url);
      feedback(tab?'VP3 Cloud is open with your code. Sign in if needed and approve the connection; I’ll finish pairing.':'Your code is ready. Select Approve in VP3 Cloud below to continue.');
    }catch(error){if(tab)tab.close();if(generation===operation)feedback(error.message,true);}
    finally{busy=false;render();}
  });
  el('onboardResetCode').addEventListener('click',()=>act(async()=>{
    await api('/api/v1/control/onboarding/device/reset','POST');
    await api('/api/v1/control/onboarding/device/start','POST');
  },'A new pairing code is ready.'));
  el('onboardInstallVoice').addEventListener('click',()=>act(
    ()=>api('/api/v1/control/onboarding/voice/start','POST'),
    'I started securely preparing voice. I will update the status here.'));
  el('onboardCopyCode').addEventListener('click',async()=>{
    try{await navigator.clipboard.writeText(el('onboardCodeText').textContent||'');feedback('Pairing code copied.');}
    catch(_){feedback('Select the visible code to copy it manually.',true);}
  });
  el('onboardLegacyPairForm').addEventListener('submit',event=>{
    event.preventDefault();
    const input=el('onboardLegacyPairToken');
    const token=String(input.value||'').trim();
    input.value='';
    act(()=>api('/api/v1/control/cloud-connection/pair','POST',{pairing_token:token}),
      'Cloud pairing saved. The secure connection is starting.');
  });
  el('onboardCheckConnection').addEventListener('click',()=>act(()=>Promise.resolve(),()=>
    snapshot?.cloud?.connected?'Live connection verified.':snapshot?.cloud?.paired?'Pairing is saved; the live connection is still retrying.':'Cloud is not connected. Start the connection or choose local use.'));
  async function finish(mode){
    if(mode==='connected'&&!snapshot?.cloud?.connected)return;
    if(await act(()=>api('/api/v1/control/onboarding/finish','POST',{mode})))hide();
  }
  el('onboardLater').addEventListener('click',()=>finish('local'));
  el('onboardFinish').addEventListener('click',()=>finish('connected'));
  el('onboardOptional').addEventListener('toggle',()=>{
    if(!visible)return;
    if(!el('onboardOptional').open)window.dispatchEvent(new Event('homeserver:onboarding-hidden'));
    refresh().catch(e=>feedback(e.message,true));
  });
  canvas.querySelectorAll?.('.onboard-feature').forEach(feature=>feature.addEventListener('toggle',()=>{
    if(!feature.open)window.dispatchEvent(new Event('homeserver:onboarding-hidden'));
  }));

  async function poll(){
    if(!visible||busy||polling||document.visibilityState!=='visible'||!el('view-chat')?.classList.contains('active'))return;
    polling=true;
    const generation=operation;
    try{
      const code=snapshot?.pairing||{},cloud=snapshot?.cloud||{};
      if(code.state==='pending'&&!cloud.paired){
        const next=await api('/api/v1/control/onboarding/device/poll','POST');
        if(generation!==operation||!visible)return;
        if(next.cloud?.paired){
          feedback('Cloud pairing is saved. The secure connection is starting.');
          await refresh();
        } else if(next.pairing?.state==='not_started'){
          await refresh();feedback('Your code expired. Generate another code.',true);
        }
      }
      if(snapshot?.provision?.phase==='running'||snapshot?.cloud?.paired)await refresh();
    }catch(e){if(generation===operation)feedback(e.message,true);}
    finally{polling=false;}
  }

  function init(){
    const head=document.querySelector('#view-chat .chat-head');
    if(!head)return;
    const button=document.createElement('button');
    button.id='chatOnboardingToggle';button.type='button';
    button.className='text-button onboard-toggle';button.textContent='Setup';
    button.addEventListener('click',()=>visible?hide():show());
    head.append(button);
    refresh().then(()=>{if(!snapshot?.setup?.complete)show();})
      .catch(e=>feedback('Setup is unavailable: '+e.message,true));
    const timer=setInterval(poll,4500);
    window.addEventListener('pagehide',()=>{clearInterval(timer);hide(false);},{once:true});
    document.addEventListener('click',event=>{
      const link=event.target.closest?.('[data-view]');
      if(visible&&link&&link.dataset.view!=='chat')hide(false);
    });
    window.addEventListener('hashchange',()=>{if(visible&&location.hash!=='#chat')hide(false);});
    document.addEventListener('visibilitychange',()=>{
      if(visible&&document.visibilityState==='visible'&&!busy&&!polling)refresh().catch(e=>feedback(e.message,true));
    });
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});
  else init();
})();
