(() => {
  'use strict';

  const BUILD='interactive-capture-section1-20261003';
  const TTL_MS=6500;
  const RENEW_MS=1800;
  const RETRY_MS=1100;
  const userId=Number(window.STONEFELLOW_CHAT?.userId||window.STONEFELLOW_STUDIO_AGENT?.userId||window.STONEFELLOW_VIDEO_EDITOR?.userId||window.STONEFELLOW_ARTIST_LISTENING_CONFIG?.userId||window.STONEFELLOW_AGENT_CONTEXT?.userId||window.VP3Meeting?.userId||0);
  const scope=String(window.STONEFELLOW_VOICE_CAPTURE_SCOPE||document.currentScript?.dataset?.voiceCaptureScope|| (userId>0?userId:window.VP3Meeting?'guest':''));
  if(!scope)return;

  const NativeRecognition=window.SpeechRecognition||window.webkitSpeechRecognition||null;
  if(window.STONEFELLOW_VOICE_LEASE_V122?.loaded)return;

  const key=`stonefellow:voice-lease:${scope}`;
  const channelName=`stonefellow-voice-lease:${scope}`;
  const tabId=(()=>{try{return crypto.randomUUID();}catch(error){return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;}})();
  let channel=null;try{if(typeof BroadcastChannel==='function')channel=new BroadcastChannel(channelName);}catch(error){}
  let activeCount=0,mediaHolds=0,renewTimer=0,destroyed=false,yielding=false,leaseGeneration=0;
  const captureTickets=new Set();const legacyMedia=[];
  const instances=new Set();

  const proof=window.STONEFELLOW_VOICE_LEASE_V122={build:BUILD,loaded:true,userId,tabId,key,claims:0,mediaClaims:0,renewals:0,denials:0,preemptions:0,releases:0,standbyEnds:0,owner:false,lastOwner:''};

  function readLease(){try{const value=JSON.parse(localStorage.getItem(key)||'null');return value&&typeof value==='object'?value:null;}catch(error){return null;}}
  function valid(lease){return !!lease&&String(lease.owner||'')!==''&&Number(lease.expiresAt||0)>Date.now();}
  function ownLease(){const lease=readLease();return valid(lease)&&lease.owner===tabId;}
  function holds(){return activeCount+mediaHolds;}
  function writeLease(expiresAt=Date.now()+TTL_MS){
    const lease={owner:tabId,expiresAt,updatedAt:Date.now(),path:location.pathname,build:BUILD};
    try{localStorage.setItem(key,JSON.stringify(lease));}catch(error){}try{channel?.postMessage({type:'lease',lease});}catch(error){}
    return lease;
  }
  function claim(takeover=false){
    if(destroyed||yielding)return false;const current=readLease();
    if(valid(current)&&current.owner!==tabId&&!takeover){proof.denials+=1;proof.owner=false;proof.lastOwner=String(current.owner||'');return false;}
    proof.claims+=1;writeLease();const verified=readLease();const ok=valid(verified)&&verified.owner===tabId;
    proof.owner=ok;proof.lastOwner=String(verified?.owner||'');if(ok)startRenewal();else proof.denials+=1;return ok;
  }
  function acquireCapture(owner='media',{takeover=false}={}){
    const other=[...captureTickets].find(ticket=>ticket.owner!==owner);
    if(other){proof.denials+=1;return null;}
    if(!claim(takeover))return null;
    const generation=leaseGeneration;let live=true;const streams=new Set();
    const stopStream=stream=>{for(const track of stream?.getTracks?.()||[]){try{track.stop();}catch(error){}}};
    const ticket={owner,isCurrent:()=>live&&!destroyed&&generation===leaseGeneration&&ownLease(),ownStream:stream=>{
      if(!ticket.isCurrent()){stopStream(stream);return false;}
      for(const previous of streams){if((previous.getTracks?.()||[]).every(track=>track.readyState==='ended'))streams.delete(previous);}
      streams.add(stream);return true;
    },release:()=>{
      if(!live)return;live=false;captureTickets.delete(ticket);
      for(const stream of streams)stopStream(stream);streams.clear();
      if(generation!==leaseGeneration)return;
      mediaHolds=Math.max(0,mediaHolds-1);if(holds()===0)release();
    }};
    captureTickets.add(ticket);mediaHolds+=1;proof.mediaClaims+=1;startRenewal();return ticket;
  }
  function acquireMedia(){const ticket=acquireCapture('media');if(!ticket)return false;legacyMedia.push(ticket);return true;}
  function releaseMedia(){legacyMedia.shift()?.release();}
  function startRenewal(){
    if(renewTimer)return;renewTimer=setInterval(()=>{
      if(destroyed||holds()<1){stopRenewal();return;}
      if(!ownLease()){proof.owner=false;proof.preemptions+=1;abortAll();return;}
      proof.renewals+=1;writeLease();if(!ownLease()){proof.owner=false;abortAll();}
    },RENEW_MS);
  }
  function stopRenewal(){if(renewTimer){clearInterval(renewTimer);renewTimer=0;}}
  function release(){
    if(!ownLease()){proof.owner=false;return;}
    try{localStorage.removeItem(key);}catch(error){}try{channel?.postMessage({type:'release',owner:tabId});}catch(error){}
    proof.releases+=1;proof.owner=false;stopRenewal();
  }
  function abortAll(){leaseGeneration+=1;for(const ticket of [...captureTickets])ticket.release();captureTickets.clear();mediaHolds=0;for(const instance of [...instances]){try{instance.__leaseAbort?.();}catch(error){}}activeCount=0;stopRenewal();try{window.dispatchEvent(new CustomEvent('stonefellow:voice-lease-lost',{detail:{userId,tabId}}));}catch(error){}}
  function handleForeign(){const lease=readLease();if(valid(lease)&&lease.owner===tabId)return;proof.lastOwner=String(lease?.owner||'');if(holds()>0){proof.preemptions+=1;proof.owner=false;abortAll();}}
  function yieldCapture(){yielding=true;abortAll();release();yielding=false;}
  function onStorage(event){if(event.key===key)handleForeign(readLease());}
  window.addEventListener('storage',onStorage);
  if(channel)channel.onmessage=event=>{const data=event.data||{};if(data.type==='lease')handleForeign(data.lease);};

  function LeasedRecognition(){
    const inner=new NativeRecognition();let running=false,deniedTimer=0,pendingEndMeta=null;const wrapper=this;instances.add(wrapper);
    const forward=['onaudiostart','onaudioend','onerror','onnomatch','onresult','onsoundstart','onsoundend','onspeechstart','onspeechend','onstart'];
    for(const name of forward)inner[name]=event=>{try{wrapper[name]?.(event);}catch(error){}};
    inner.onend=event=>{
      if(running){running=false;activeCount=Math.max(0,activeCount-1);if(holds()===0)release();}
      instances.delete(wrapper);const meta=pendingEndMeta;pendingEndMeta=null;
      try{wrapper.onend?.(meta?Object.assign(event||{type:'end'},meta):event);}catch(error){}
    };
    Object.defineProperties(wrapper,{
      continuous:{get:()=>inner.continuous,set:v=>{inner.continuous=v;}},interimResults:{get:()=>inner.interimResults,set:v=>{inner.interimResults=v;}},lang:{get:()=>inner.lang,set:v=>{inner.lang=v;}},maxAlternatives:{get:()=>inner.maxAlternatives,set:v=>{inner.maxAlternatives=v;}},grammars:{get:()=>inner.grammars,set:v=>{inner.grammars=v;}},serviceURI:{get:()=>inner.serviceURI,set:v=>{inner.serviceURI=v;}},
    });
    wrapper.start=()=>{
      if(running)return;instances.add(wrapper);
      if(!claim()){clearTimeout(deniedTimer);deniedTimer=setTimeout(()=>{proof.standbyEnds+=1;instances.delete(wrapper);try{wrapper.onend?.({type:'end',leaseDenied:true});}catch(error){}},RETRY_MS);return;}
      try{running=true;activeCount+=1;inner.start();startRenewal();}
      catch(error){running=false;activeCount=Math.max(0,activeCount-1);instances.delete(wrapper);if(holds()===0)release();throw error;}
    };
    wrapper.stop=()=>{clearTimeout(deniedTimer);if(!running){instances.delete(wrapper);return;}try{inner.stop();}catch(error){}};
    wrapper.abort=()=>{clearTimeout(deniedTimer);if(!running){instances.delete(wrapper);return;}try{inner.abort();}catch(error){}};
    wrapper.__leaseAbort=()=>{
      clearTimeout(deniedTimer);if(!running){instances.delete(wrapper);return;}
      running=false;activeCount=Math.max(0,activeCount-1);pendingEndMeta={leaseLost:true};try{inner.abort();}catch(error){instances.delete(wrapper);try{wrapper.onend?.({type:'end',leaseLost:true});}catch(callbackError){}}
    };
  }
  if(NativeRecognition)LeasedRecognition.prototype=NativeRecognition.prototype;
  if(NativeRecognition&&window.SpeechRecognition===NativeRecognition)window.SpeechRecognition=LeasedRecognition;
  if(NativeRecognition&&window.webkitSpeechRecognition===NativeRecognition)window.webkitSpeechRecognition=LeasedRecognition;

  window.StonefellowVoiceLeaseV122={build:BUILD,claim,owns:ownLease,acquireMedia,releaseMedia,acquireCapture,yield:yieldCapture,proof};

  function destroy(){if(destroyed)return;destroyed=true;abortAll();mediaHolds=0;release();stopRenewal();window.removeEventListener('storage',onStorage);try{channel?.close();}catch(error){}}
  window.addEventListener('pagehide',destroy,{once:true});
})();
