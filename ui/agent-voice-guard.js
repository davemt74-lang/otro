/* Local microphone features. No raw audio is retained or uploaded by this module. */
(() => {
  'use strict';
  const boot=()=>{
    const adapter=window.VP3_ORB_ADAPTER;if(!adapter||window.VP3VoiceGuard)return;
    let processor=null,source=null,high=null,low=null,mute=null,rate=0,chunks=[],length=0,lastAudio=0,noise=.004,epoch=0,turn=0,learning=null,pendingContext=null,features=null,featureApi=null,cachedProfiles=[],cachedAt=0,attachment=null,refreshing=false,lastChecked=null;
    const voiceSession=crypto.randomUUID();const prefs=`vp3:voice-filter:${adapter.ownerKey}`;let enabled=true,knownOnly=false,invitation='',invitationUntil=0;
    try{const p=JSON.parse(localStorage.getItem(prefs)||'{}');enabled=p.enabled!==false;knownOnly=p.knownOnly===true;}catch(_){}
    const module=()=>features||(features=import(adapter.voiceFeatureUrl).then(lib=>featureApi=lib));
    const panel=document.createElement('details');panel.className='vp3-voice-filter';
    panel.innerHTML='<summary>Voice profiles and background noise</summary><label><input type="checkbox" data-voice-filter> Filter background noise</label><label><input type="checkbox" data-voice-known> Respond only to learned voices</label><label>Participant<select data-voice-person aria-label="Voice participant"></select></label><label><input type="checkbox" data-voice-consent> This participant agrees to save local voice features for recognition.</label><div class="vp3-eyes-actions"><button type="button" data-voice-learn>Learn this voice</button><button type="button" data-voice-forget>Forget voice features</button></div><p data-voice-status role="status">Voice matching is conversational context, not authentication. No raw audio is saved.</p>';
    const q=s=>panel.querySelector(s),say=t=>{q('[data-voice-status]').textContent=t;window.dispatchEvent(new CustomEvent('vp3:voice-filter-status',{detail:{message:t}}));};
    const mount=()=>{const host=document.querySelector('.vp3-eyes-content');if(host&&!panel.isConnected)host.append(panel);};mount();
    const mounting=setInterval(()=>{mount();if(panel.isConnected||new URLSearchParams(location.search).get('orb_embed')==='1')clearInterval(mounting);},100);
    window.addEventListener('vp3:voice-filter-remote-status',event=>{q('[data-voice-status]').textContent=String(event.detail?.message||'');});
    q('[data-voice-filter]').checked=enabled;q('[data-voice-known]').checked=knownOnly;
    function persist(){try{localStorage.setItem(prefs,JSON.stringify({enabled,knownOnly}));}catch(_){}clear();}
    async function profiles(){const rows=await adapter.loadVoiceProfiles();cachedProfiles=rows;cachedAt=Date.now();return rows;}
    async function refresh(){try{const rows=await profiles(),selected=q('[data-voice-person]').value;q('[data-voice-person]').replaceChildren();for(const p of rows){const o=document.createElement('option');o.value=p.id;o.textContent=p.name+(p.voiceProfileReady?' · learned':'');q('[data-voice-person]').append(o);}if(rows.some(p=>String(p.id)===selected))q('[data-voice-person]').value=selected;}catch(_){say('Voice participant list is unavailable.');}}
    panel.addEventListener('toggle',()=>{if(panel.open)void refresh();});
    q('[data-voice-filter]').addEventListener('change',()=>{enabled=q('[data-voice-filter]').checked;if(!enabled){knownOnly=false;q('[data-voice-known]').checked=false;learning=null;}persist();});
    q('[data-voice-known]').addEventListener('change',async()=>{const rows=await profiles().catch(()=>[]);knownOnly=q('[data-voice-known]').checked;if(knownOnly&&!rows.some(p=>window.VP3TrainedVoice?.enabled()?window.VP3TrainedVoice.readyProfile(p):p.voiceRecognitionEnabled&&p.voiceProfileReady)){knownOnly=false;q('[data-voice-known]').checked=false;say('Learn a participant voice before enabling known voices only.');}if(knownOnly){enabled=true;q('[data-voice-filter]').checked=true;}persist();});
    for(const selector of ['[data-voice-person]','[data-voice-consent]'])q(selector).addEventListener('change',()=>{learning=null;epoch++;window.VP3AgentOrb?.cancelVoiceEnrollment?.();if(selector==='[data-voice-person]')q('[data-voice-consent]').checked=false;});
    async function beginEnrollment(id){await refresh();const p=(await profiles()).find(p=>String(p.id)===String(id));if(!p)return say('Choose an existing participant.');q('[data-voice-person]').value=String(id);q('[data-voice-consent]').checked=true;enabled=true;q('[data-voice-filter]').checked=true;persist();learning={id:String(id),epoch};say(window.VP3TrainedVoice?.enabled()?'Speak three clean phrases of at least three seconds each, alone and without music.':'Speak three short phrases, alone and without music. Each clean phrase adds one local sample.');adapter.listen();}
    q('[data-voice-learn]').addEventListener('click',()=>{const id=q('[data-voice-person]').value;if(!id||!q('[data-voice-consent]').checked)return say('Choose a participant and allow local voice features first.');if(window.VP3AgentOrb?.startVoiceEnrollment?.(id)){q('[data-voice-consent]').checked=false;say('Voice learning opened in the existing chat. Speak three clean phrases.');return;}void beginEnrollment(id).catch(()=>say('Voice enrollment is unavailable.'));});
    q('[data-voice-forget]').addEventListener('click',async()=>{const id=q('[data-voice-person]').value;if(!id)return;clear();learning=null;window.VP3AgentOrb?.cancelVoiceEnrollment?.();try{const p=(await profiles()).find(p=>String(p.id)===id);await adapter.forgetVoice(id);window.dispatchEvent(new CustomEvent('vp3:participant-forgotten',{detail:{id,name:p?.name||''}}));await refresh();say('Local voice features removed. Participant names and visual samples are preserved.');}catch(_){say('Voice features could not be removed.');}});
    function clear(){chunks=[];length=0;lastAudio=0;pendingContext=null;epoch++;window.VP3TrainedVoice?.clear();window.VP3ParticipantContinuity?.clearSpeaker?.();}
    function detach(keepLearning=false){clear();attachment=null;if(keepLearning&&learning)learning.epoch=epoch;else learning=null;for(const n of [processor,source,high,low,mute])try{n?.disconnect();}catch(_){}if(processor)processor.onaudioprocess=null;processor=source=high=low=mute=null;}
    function attach(stream,context){
      detach(true);if(!enabled||!stream||!context?.createScriptProcessor)return false;
      void module().catch(()=>{});void profiles().catch(()=>{});
      try{attachment={stream,context};rate=context.sampleRate;source=context.createMediaStreamSource(stream);high=context.createBiquadFilter();high.type='highpass';high.frequency.value=100;low=context.createBiquadFilter();low.type='lowpass';low.frequency.value=Math.min(7600,rate*.45);processor=context.createScriptProcessor(2048,1,1);mute=context.createGain();mute.gain.value=0;source.connect(high);high.connect(low);low.connect(processor);processor.connect(mute);mute.connect(context.destination);
        processor.onaudioprocess=event=>{const data=event.inputBuffer.getChannelData(0),copy=Float32Array.from(data);let power=0;for(const n of copy)power+=n*n;const rms=Math.sqrt(power/copy.length);if(rms<Math.max(.012,noise*1.7))noise=noise*.97+Math.max(.001,rms)*.03;chunks.push({data:copy,rms});length+=copy.length;while(length>rate*10&&chunks.length>1)length-=chunks.shift().data.length;lastAudio=performance.now();};return true;
      }catch(_){detach();say('Local voice filtering is unavailable in this browser.');return false;}
    }
    async function evaluateVoice(text,isCurrent=()=>true){
      const turnSerial=++turn;
      window.VP3ParticipantContinuity?.clearSpeaker?.();
      lastChecked={text:String(text),at:Date.now()};
      if(Date.now()<invitationUntil&&((invitation==='name'&&window.VP3EyesCore?.introductionName(text,true))||(invitation==='visual-consent'&&/^(?:please )?(?:remember me visually|save my local visual samples|recognize me next time)[.!]?$/i.test(String(text).trim())))){invitation='';pendingContext=null;return {allowed:true,reason:'invited-introduction'};}
      pendingContext=null;if(/^(?:that['’]s not me|that is not me|you have the wrong person)[.!]?$/i.test(String(text).trim()))return {allowed:true,reason:'participant-correction'};if(/^(?:stop|cancel|pause|stop listening|stop the microphone)[.!]?$/i.test(String(text).trim()))return {allowed:true};if(window.VP3AgentEyes?.expectingIntroduction?.()&&window.VP3EyesCore?.introductionName(text,true))return {allowed:true,reason:'invited-introduction'};if(!enabled)return {allowed:true};const token=epoch;
      const current=()=>token===epoch&&turnSerial===turn&&isCurrent();
      const captured=chunks;chunks=[];length=0;
      if(!rate||!captured.length||performance.now()-lastAudio>2200){if(knownOnly)say('Speech ignored: voice matching has no current microphone evidence.');return {allowed:!knownOnly,reason:'unavailable'};}
      const clean=captured.filter(c=>c.rms>=Math.max(.008,noise*2.5));const count=clean.reduce((n,c)=>n+c.data.length,0);
      if(count<rate*.12){say('Background noise ignored. Listening continues.');return {allowed:false,reason:'noise'};}
      const pcm=new Float32Array(count);let offset=0;for(const c of clean){pcm.set(c.data,offset);offset+=c.data.length;}
      const trained=window.VP3TrainedVoice?.enabled()===true,lib=await module();if(!current())return {allowed:false,reason:'cancelled'};
      const result=trained?await window.VP3TrainedVoice.extract(pcm,rate,{enrollment:!!learning}):null;
      if(!current())return {allowed:false,reason:'cancelled'};
      if(trained&&!result.embedding){say(result.quality?.message||'No trained voice match is available.');return {allowed:!knownOnly,reason:result.quality?.reason||'unavailable'};}
      const embedding=trained?result.embedding:lib.voiceEmbeddingFromPcm(pcm,rate),rows=await profiles();if(!current())return {allowed:false,reason:'cancelled'};
      if(learning&&learning.epoch===token&&embedding&&q('[data-voice-consent]').checked){
        const selected=learning;const saved=await adapter[trained?'saveTrainedVoiceSample':'saveVoiceSample']({id:selected.id,embedding,isCurrent:()=>current()&&learning===selected&&q('[data-voice-consent]').checked});
        if(!current())return {allowed:false,reason:'cancelled'};
        if(saved.voiceProfileReady){learning=null;q('[data-voice-consent]').checked=false;say(`${saved.name} voice learned. Local matches remain provisional.`);}else say(`${saved.voiceEmbeddings.length} of 3 local voice samples saved.`);
        return {allowed:true,learning:true};
      }
      const match=trained?await window.VP3TrainedVoice.match(embedding,rows):lib.bestVoiceParticipantMatch(embedding,rows);
      if(!current())return {allowed:false,reason:'cancelled'};
      if(!match.matched){say((match.ambiguous||match.reason==='ambiguous')?'Speaker is ambiguous. No participant was assigned.':knownOnly?'Unknown voice ignored. Listening continues.':'Unrecognized speaker. No participant was assigned.');return {allowed:!knownOnly,reason:match.ambiguous?'ambiguous':'unknown'};}
      // Recheck consent after matching and immediately before exposing a name.
      const fresh=(await profiles()).find(p=>p.id===match.participant.id&&(trained?window.VP3TrainedVoice.readyProfile(p):p.voiceRecognitionEnabled&&p.voiceProfileReady));if(!current()||!fresh)return {allowed:!knownOnly,reason:'revoked'};
      const person={participant_id:String(fresh.id),display_name:fresh.name};
      const attribution=window.VP3ParticipantContinuity?.assessVoice(person,{notify:false});
      if(attribution&&!attribution.accepted){say('Speaker attribution is uncertain. No participant assigned.');return {allowed:!knownOnly,reason:attribution.reason};}
      const note=adapter.voiceParticipantNote?await adapter.voiceParticipantNote(String(fresh.id)).catch(()=>''):'';if(!current())return {allowed:false,reason:'cancelled'};
      const finalAttribution=window.VP3ParticipantContinuity?.assessVoice(person);
      if(finalAttribution&&!finalAttribution.accepted)return {allowed:!knownOnly,reason:finalAttribution.reason};
      pendingContext={text:String(text),at:Date.now(),value:{conversation_note:String(note).slice(0,1000),participant_id:String(fresh.id),display_name:String(fresh.name).slice(0,120),source:'local_voice_similarity',confidence:match.similarity,authentication_authority:false}};
      void Promise.resolve(adapter.recordEyes?.({event_id:crypto.randomUUID(),session_id:voiceSession,track_id:crypto.randomUUID(),event_type:'voice_turn',participant_id:String(fresh.id),display_name:fresh.name,identity_source:'local_voice_similarity',authentication_authority:false})).catch(()=>{});say(`Listening to ${fresh.name} · provisional ${trained?'trained':'local'} voice match.`);return {allowed:true,participant:pendingContext.value};
    }
    async function check(text,isCurrent=()=>true){const task=evaluateVoice(text,isCurrent),record=lastChecked;try{return await task;}finally{if(lastChecked===record&&record)record.at=Date.now();}}
    function takeContext(text){const p=pendingContext;pendingContext=null;if(!p||Date.now()-p.at>=5000||p.text!==String(text))return null;return window.VP3ParticipantContinuity&&!window.VP3ParticipantContinuity.assessVoice(p.value,{notify:false}).accepted?null:p.value;}
    function reset(){clear();if(learning)learning.epoch=epoch;}
    function canInterrupt(){if(!enabled||!knownOnly)return true;if(Date.now()-cachedAt>3000&&!refreshing){refreshing=true;void profiles().catch(()=>{}).finally(()=>refreshing=false);}if(!featureApi||Date.now()-cachedAt>5000||performance.now()-lastAudio>700)return false;const clean=chunks.filter(c=>c.rms>=Math.max(.008,noise*2.5)),size=clean.reduce((n,c)=>n+c.data.length,0);if(size<(window.VP3TrainedVoice?.enabled()?rate*1.5:rate*.45))return false;const pcm=new Float32Array(size);let at=0;for(const c of clean){pcm.set(c.data,at);at+=c.data.length;}if(window.VP3TrainedVoice?.enabled()){const token=epoch;return window.VP3TrainedVoice.canInterrupt(pcm,rate,cachedProfiles,()=>token===epoch&&performance.now()-lastAudio<700);}const match=featureApi.bestVoiceParticipantMatch(featureApi.voiceEmbeddingFromPcm(pcm,rate),cachedProfiles);return match.matched&&(!window.VP3ParticipantContinuity||window.VP3ParticipantContinuity.assessVoice({participant_id:match.participant.id,display_name:match.participant.name},{notify:false}).accepted);}
    window.VP3VoiceGuard={attach,detach,check,takeContext,wasVoiceTurn:text=>!!lastChecked&&lastChecked.text===String(text)&&Date.now()-lastChecked.at<5000,clear,beginEnrollment,canInterrupt,inviteIntroduction:mode=>{invitation=['name','visual-consent'].includes(mode)?mode:'';invitationUntil=Date.now()+90000;},cancelEnrollment:()=>{clear();learning=null;q('[data-voice-consent]').checked=false;},reset,isKnownOnly:()=>knownOnly,isEnabled:()=>enabled};
    window.addEventListener('homeserver:voice-playback',event=>{if(event.detail?.active===false)reset();});
    window.addEventListener('stonefellow:agent-proactive-speech',event=>{if(event.detail?.state==='end')reset();});
    window.dispatchEvent(new Event('vp3:voice-guard-ready'));
    window.addEventListener('storage',event=>{if(event.key!==prefs)return;try{const p=JSON.parse(event.newValue||'{}');enabled=p.enabled!==false;knownOnly=p.knownOnly===true;q('[data-voice-filter]').checked=enabled;q('[data-voice-known]').checked=knownOnly;clear();}catch(_){}});
    window.addEventListener('pagehide',()=>{detach();clearInterval(mounting);},{once:true});
    document.addEventListener('visibilitychange',()=>{if(document.hidden){const previous=attachment;detach();attachment=previous;}else if(attachment&&attachment.context.state!=='closed'&&attachment.stream.getTracks?.().some(t=>t.readyState==='live')){const previous=attachment;attach(previous.stream,previous.context);}});
  };
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
