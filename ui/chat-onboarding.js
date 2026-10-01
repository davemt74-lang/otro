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

  async function api(path,method='GET',body=null) {
    const response = await fetch(path,{
      method,credentials:'same-origin',cache:'no-store',
      headers:{Accept:'application/json',...(method==='POST'?{'X-Requested-With':'XMLHttpRequest'}:{}),...(body?{'Content-Type':'application/json'}:{})},
      ...(body?{body:JSON.stringify(body)}:{})
    });
    let data = {};
    try {data=await response.json();} catch (_) {}
    if (!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'HomeServer could not complete this step.');
    return data;
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
    const paired=Boolean(cloud.paired), online=Boolean(cloud.connected);
    el('onboardCloudState').textContent=online?'Connected ✓':paired?'Paired · establishing connection':code.state==='pending'?'Waiting for Cloud':'Not connected';
    el('onboardCloud').dataset.complete=paired?'true':'false';
    const hasCode=code.state==='pending'&&Boolean(code.code)&&!paired;
    el('onboardPairingCode').hidden=!hasCode;
    if(hasCode){
      el('onboardCodeText').textContent=code.code;
      const expiry=Date.parse(code.expires_at||'');
      el('onboardCodeExpiry').textContent=Number.isNaN(expiry)?'Valid for 15 minutes':('Expires '+new Date(expiry).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}));
      if(snapshot.cloud_url==='https://vp3.me/settings-homeserver.php')el('onboardCloudLink').href=snapshot.cloud_url;
    }
    el('onboardStartCloud').hidden=hasCode||paired;
    el('onboardResetCode').hidden=!hasCode;
    el('onboardStartCloud').disabled=busy;el('onboardResetCode').disabled=busy;
    el('onboardLegacyPairForm').hidden=paired;
    const packages=Array.isArray(voice.packages)?voice.packages:[];
    el('onboardVoicePackages').textContent=packages.length?packages.map(itemText).join('  ·  '):'No compatible voice packages detected.';
    const active=voice.phase==='running';
    el('onboardVoiceState').textContent=voice.supported_count===0?'Not supported on this computer':active?'Installing…':voice.all_ready?'Installed & verified ✓':
      voice.phase==='attention'?'Needs attention':voice.phase==='interrupted'?'Resuming':'Optional setup';
    el('onboardVoice').dataset.complete=voice.all_ready?'true':'false';
    el('onboardInstallVoice').disabled=busy||active||Boolean(voice.all_ready)||voice.supported_count===0;
    el('onboardInstallVoice').textContent=active?'Preparing securely…':voice.all_ready?'Voice ready ✓':
      voice.phase==='attention'?'Retry voice preparation →':'Prepare local voice →';
    el('onboardFinish').disabled=busy;
    if(voice.reason&&voice.phase==='attention'&&!lastError)feedback(voice.reason,true);
    const toggle=el('chatOnboardingToggle');
    if(toggle)toggle.textContent=visible?'Close setup':'Setup';
  }

  async function refresh(){
    snapshot=await api('/api/v1/control/onboarding/summary');
    render();
  }
  async function act(task,message){
    if(busy)return;
    busy=true;render();
    try{
      await task();
      lastError='';
      await refresh();
      if(message)feedback(message);
    }catch(error){feedback(error.message,true);}
    finally{busy=false;render();}
  }
  function hide(){
    visible=false;canvas.hidden=true;
    const t=el('chatOnboardingToggle');if(t)t.textContent='Setup';
    el('chatInput')?.focus();
  }
  function show(){
    visible=true;canvas.hidden=false;
    refresh().catch(e=>feedback('Could not read setup status: '+e.message,true));
  }

  el('onboardStartCloud').addEventListener('click',()=>act(
    ()=>api('/api/v1/control/onboarding/device/start','POST'),
    'Your pairing code is ready. Open VP3 Cloud and enter it there.'));
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
  el('onboardLater').addEventListener('click',hide);
  el('onboardFinish').addEventListener('click',()=>act(async()=>{
    await api('/api/v1/control/system/setup','POST',{complete:true});
    hide();
  },'First-run preferences saved. Your normal Agent Chat remains available.'));

  async function poll(){
    if(!visible||polling||document.visibilityState!=='visible')return;
    polling=true;
    try{
      const code=snapshot?.pairing||{},cloud=snapshot?.cloud||{};
      if(code.state==='pending'&&!cloud.paired){
        const next=await api('/api/v1/control/onboarding/device/poll','POST');
        if(next.cloud?.paired){
          feedback('Cloud pairing is saved. The secure connection is starting.');
          await refresh();
        } else if(next.pairing?.state==='not_started'){
          await refresh();feedback('Your code expired. Generate another code.',true);
        }
      }
      if(snapshot?.provision?.phase==='running'||snapshot?.cloud?.paired)await refresh();
    }catch(e){feedback(e.message,true);}
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
    setInterval(poll,4500);
    document.addEventListener('visibilitychange',()=>{
      if(visible&&document.visibilityState==='visible')refresh().catch(()=>null);
    });
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});
  else init();
})();
