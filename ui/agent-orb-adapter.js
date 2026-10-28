(() => {
  'use strict';
  const $=id=>document.getElementById(id);let audible=false,voiceState='idle';
  window.addEventListener('homeserver:voice-state',event=>{voiceState=String(event.detail?.state||'idle');});
  window.addEventListener('homeserver:voice-playback',event=>{audible=event.detail?.active===true;});
  async function api(url,body,keepalive=false){const r=await fetch(url,{method:body?'POST':'GET',credentials:'same-origin',cache:'no-store',keepalive,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});const data=await r.json();if(!r.ok)throw Error(data.detail||'HomeServer request failed.');return data;}
  const store=()=>import('/assets/tracky/src/participant-store.js?participant=v1344');
  const recording=()=>window.HomeServerDictation?.isTranscribing?.()===true;
  const state=()=>{let value=audible?'speaking':$('voiceInputButton')?.dataset.voiceState||voiceState;if(value==='speaking'&&!audible)value='thinking';if(!audible&&window.HomeServerChatTurn?.isBusy?.())value='thinking';return {state:value,recording:recording()};};
  const adapter={ownerKey:'homeserver-local-owner',chatUrl:'/#chat',alwaysBar:true,state,voiceFeatureUrl:'/assets/tracky/src/voice-profile-core.js',
    conversationRef:()=>window.HomeServerChatTurn?.isReady?.()===false?null:({id:window.HomeServerChatTurn?.conversationId?.()||''}),
    conversationUrl:ref=>{const u=new URL('/#chat',location.href);if(/^[a-f0-9]{32}$/.test(String(ref?.id||'')))u.searchParams.set('conversation_id',ref.id);return u.href;},
    focusChat:()=>{document.querySelector('[data-view="chat"]')?.click();$('chatInput')?.focus();},
    participantDetails:async id=>{const p=await (await store()).getParticipant(id);if(!p)throw Error('Participant unavailable.');return {id:p.id,name:p.name,note:p.agentConversationNote||''};},
    voiceParticipantNote:async id=>(await adapter.participantDetails(id)).note,
    saveParticipantDetails:async({id,name,note,isCurrent})=>{if(!name.trim()||name.length>120||note.length>1000)throw Error('Enter a name up to 120 characters and note up to 1000 characters.');const p=await (await store()).patchParticipant(id,p=>({...p,name:name.trim(),agentConversationNote:note.trim()}),isCurrent);return {id:p.id,name:p.name,note:p.agentConversationNote};},
    busy:()=>Boolean(window.HomeServerChatTurn?.isBusy?.()||['thinking','speaking','connecting'].includes(state().state)||recording()||$('chatInput')?.value.trim()),
    listen:()=>{if(!window.HomeServerConversationVoice?.isEnabled?.())$('voiceInputButton')?.click();},
    stop:()=>{window.HomeServerConversationVoice?.stop();window.HomeServerDictation?.stopTranscription();window.HomeServerChatTurn?.cancel();},
    greet:(text,isCurrent)=>window.HomeServerConversationVoice?.announce(text,isCurrent)||false,
    attachChat:host=>{
      const panel=$('chatForm')?.closest('.chat-panel');if(!panel)return null;
      const marker=document.createComment('VP3 chat panel location');panel.before(marker);host.append(panel);
      return ()=>{marker.replaceWith(panel);};
    },
    submitDraft:text=>{const input=$('chatInput');if(!input||input.value.trim()||adapter.busy())return false;input.value=text;input.dispatchEvent(new Event('input',{bubbles:true}));$('chatForm')?.requestSubmit();return true;},
    cameraAllowed:async()=>{
      if(document.hidden||window.HomeServerSpeakerFusion?.isCameraActive?.()||window.TrackyOwnerEyes?.isActive()||window.HomeServerVisualEnrollment?.isCapturing()||window.TrackyOwnerSelfCheck?.isActive())return false;
      const data=await api('/api/v1/control/onboarding/visual/status');
      const native=await api('/api/v1/control/onboarding/visual/agent-eyes/status');
      return data.privacy_engaged===false&&native.privacy_engaged===false&&native.active!==true;
    },
    loadEyesProfiles:async()=>{
      const rows=await (await store()).listParticipants();return rows.map(p=>({...p,recognitionEnabled:p.recognitionEnabled!==false&&!!p.agentEyesEnrollment?.consentedAt}));
    },
    introduceEyes:async({name,isCurrent=()=>true})=>{
      const data=await store(),rows=await data.listParticipants();if(!isCurrent())throw Error('Introduction stopped.');
      const existing=rows.filter(p=>p.name.normalize('NFKC').toLocaleLowerCase()===name.normalize('NFKC').toLocaleLowerCase());
      if(existing.length>1)throw Error('More than one participant has that name. Choose the participant in Eyes.');
      if(existing.length)return {id:String(existing[0].id),name:existing[0].name};
      const p=await data.saveParticipant({name,embeddings:[],recognitionEnabled:false});return {id:String(p.id),name:p.name};
    },
    enrollEyes:async({id,name,embeddings,isCurrent=()=>true})=>{
      const data=await store();
      if(!isCurrent()||!await adapter.cameraAllowed())throw Error('Enrollment stopped.');
      if(id){
        await data.patchParticipant(id,p=>{if(!isCurrent())throw Error('Enrollment stopped.');return {...p,embeddings,recognitionEnabled:true,agentEyesEnrollment:{consentedAt:new Date().toISOString(),scope:'participant-local'}};});
      }else await data.saveParticipant({name,embeddings,recognitionEnabled:true,agentEyesEnrollment:{consentedAt:new Date().toISOString(),scope:'participant-local'}});
    },
    loadVoiceProfiles:async()=>await (await store()).listParticipants(),
    saveVoiceSample:async({id,embedding,isCurrent})=>{const data=await store();if(!isCurrent())throw Error('Voice enrollment stopped.');return await data.patchParticipant(id,p=>{if(!isCurrent())throw Error('Voice enrollment stopped.');const voiceEmbeddings=[...(p.voiceEmbeddings||[]),embedding].slice(-5);return {...p,voiceEmbeddings,voiceRecognitionEnabled:true,voiceProfileReady:voiceEmbeddings.length>=3};},isCurrent);},
    forgetVoice:async id=>{const data=await store();await data.patchParticipant(id,p=>({...p,voiceEmbeddings:[],voiceProfileSamples:[],voiceRecognitionEnabled:false,voiceProfileReady:false}));},
    forgetEyes:async id=>{const data=await store();await data.patchParticipant(id,p=>({...p,embeddings:[],visualEnrollment:null,agentEyesEnrollment:null,recognitionEnabled:false}));},
    recordEyes:event=>{
      const labels={arrival:'arrived',departure:'left view',identity_changed:'visual attribution changed',greeting:'received an automatic greeting',voice_turn:'spoke in Agent chat',attribution_corrected:'had a conversational match dismissed',participant_updated:'participant details updated',recognition_forgotten:'local recognition samples removed'};
      return api('/api/v1/control/cognition/events',{
        event_id:`eyes:${event.session_id}:${event.event_id}`,event_type:`agent_eyes.${event.event_type}`,
        summary:`${event.display_name||'Unidentified participant'} · ${labels[event.event_type]}. Browser-reported context; participant attribution is provisional and grant no authentication authority.`,
        entity_type:'participant_presence',entity_key:event.track_id,correlation_id:event.session_id,importance:.15,privacy_scope:'private',
        payload:{session_id:event.session_id,track_id:event.track_id,participant_id:event.participant_id,identity_source:event.identity_source,authentication_authority:false},
      },true);
    },
  };
  if(!$('chatForm')){delete adapter.attachChat;delete adapter.submitDraft;}
  window.VP3_ORB_ADAPTER=adapter;
})();
