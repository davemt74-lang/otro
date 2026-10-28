/* Optional trained speaker engine; existing voice owner supplies all microphone PCM. */
(() => {
  'use strict';
  function boot(){
    const adapter=window.VP3_ORB_ADAPTER;if(!adapter?.trainedVoiceUrl||window.VP3TrainedVoice)return;
    const pref='vp3:trained-voice:'+adapter.ownerKey;
    let enabled=false,lib=null,client=null,generation=0,barge=null,bargeBusy=false,installing=false,installAbort=null;
    try{enabled=localStorage.getItem(pref)==='enabled';}catch(_){}
    const panel=document.createElement('details');panel.className='vp3-trained-voice';
    panel.innerHTML='<summary>Trained voice profiles · optional</summary><p>124 MB model and runtime component. Processing stays on this device. Profiles require three clean phrases of at least three seconds each. Existing lightweight profiles are kept separately.</p><div class="vp3-eyes-actions"><button type="button" data-trained-install>Install and enable trained matching</button><button type="button" data-trained-disable>Disable trained matching</button></div><p data-trained-state role="status"></p>';
    const q=s=>panel.querySelector(s),say=text=>q('[data-trained-state]').textContent=text;
    async function load(){if(!lib)lib=await import(adapter.trainedVoiceUrl);if(!client)client=lib.createTrainedVoiceClient({onProgress:say});return lib;}
    function clear(){generation++;barge=null;client?.cancelInference();}
    const mount=()=>{const host=document.querySelector('.vp3-eyes-content');if(host&&!panel.isConnected)host.append(panel);};mount();
    const timer=setInterval(mount,1000);
    q('[data-trained-install]').addEventListener('click',async()=>{
      if(installing)return;const token=++generation;installing=true;installAbort=new AbortController();const deadline=setTimeout(()=>installAbort?.abort(),180000);q('[data-trained-install]').disabled=true;
      try{
        say('Preparing the optional voice component…');await load();
        await adapter.installTrainedVoice?.({signal:installAbort.signal});if(token!==generation)return;
        await client.install(adapter.trainedVoiceSource||'cloud');if(token!==generation)return;
        enabled=true;try{localStorage.setItem(pref,'enabled');}catch(_){}
        window.VP3VoiceGuard?.cancelEnrollment?.();say('Trained matching enabled. Use Learn this voice below to enroll three clean phrases. Match scores are similarities, not accuracy percentages.');
      }catch(error){if(token===generation){enabled=false;client?.cancel();say(String(error.message||'The voice component could not start.'));}}
      finally{clearTimeout(deadline);installAbort=null;installing=false;q('[data-trained-install]').disabled=false;}
    });
    q('[data-trained-disable]').addEventListener('click',()=>{enabled=false;generation++;barge=null;installAbort?.abort();client?.cancel();try{localStorage.removeItem(pref);}catch(_){}window.VP3VoiceGuard?.cancelEnrollment?.();say('Trained matching disabled. Your saved profiles are kept.');});
    if(enabled)void load().catch(()=>{});
    say(enabled?'Trained matching selected. The cached component loads when needed.':'Optional component is off. The existing lightweight matcher remains available.');
    window.VP3TrainedVoice={
      enabled:()=>enabled,
      async extract(pcm,rate,options){await load();try{return await client.extract(pcm,rate,options);}catch(error){say('Trained component unavailable. Install it again or disable trained matching. '+String(error.message||''));throw error;}},
      async match(embedding,people){await load();return lib.profileMatch(embedding,people);},
      readyProfile:person=>!!lib?.cleanProfile(person?.voiceTrainedProfile)?.embeddings?.length&&lib.cleanProfile(person.voiceTrainedProfile).embeddings.length>=lib.MIN_SAMPLES,
      clear,
      canInterrupt(pcm,rate,people,isCurrent){
        if(!bargeBusy){
          const token=generation;bargeBusy=true;
          void (async()=>{try{await load();const r=await client.extract(pcm,rate);if(token!==generation||!isCurrent()||!r.embedding)return;const m=lib.profileMatch(r.embedding,people);barge=m.matched?{person:m.participant,at:performance.now()}:null;}catch(_){barge=null;}finally{bargeBusy=false;}})();
        }
        return !!barge&&performance.now()-barge.at<700&&isCurrent()&&(!window.VP3ParticipantContinuity||window.VP3ParticipantContinuity.assessVoice({participant_id:barge.person.id,display_name:barge.person.name},{notify:false}).accepted);
      },
    };
    window.addEventListener('pagehide',()=>{enabled=false;clearInterval(timer);generation++;installAbort?.abort();client?.cancel();},{once:true});
    document.addEventListener('visibilitychange',()=>{if(document.hidden)clear();});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
