import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {fileURLToPath} from 'node:url';

const root=fileURLToPath(new URL('../',import.meta.url));
const cloud=fs.existsSync(root+'chat-voice.js');
const source=name=>fs.readFileSync(root+(cloud?'':'ui/')+name,'utf8');
const checks=[];
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const response=data=>({ok:true,json:async()=>data});
class Element {
 constructor(id=''){this.id=id;this.value='';this.checked=false;this.dataset={};this.attributes={};this.children=[];this.childNodes=[];this.textContent='';this.hidden=false;this.listeners={};this.options=[];this.style={setProperty(){},removeProperty(){}};const classes=new Set();this.classList={add:n=>classes.add(n),remove:n=>classes.delete(n),contains:n=>classes.has(n),toggle(n,v){if(v??!classes.has(n))classes.add(n);else classes.delete(n);}};}
 addEventListener(n,f){(this.listeners[n]??=[]).push(f);}
 setAttribute(n,v){this.attributes[n]=String(v);}
 getAttribute(n){return this.attributes[n]??null;}
 removeAttribute(n){delete this.attributes[n];}
 querySelector(){return new Element();}
 querySelectorAll(){return [];}
 replaceChildren(...children){this.children=children;}
 appendChild(child){this.children.push(child);}
 insertBefore(child){this.children.push(child);}
 append(...children){this.children.push(...children);}
 insertAdjacentHTML(){}
 dispatchEvent(){return true;}
 closest(){return null;}
 remove(){}
 focus(){}
}
function browser({storage=new Map(),recognition=true}={}){
 const elements=new Map(),listeners=new Map(),events=[],timers=[],intervals=[];
 const element=id=>{if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id);};
 const document={readyState:'loading',currentScript:{dataset:{voiceCaptureScope:'test-device'}},documentElement:{lang:'en-US'},body:new Element('body'),head:new Element('head'),getElementById:element,querySelector:element,querySelectorAll:()=>[],createElement:()=>new Element(),addEventListener(){},removeEventListener(){}};
 class Recognition {start(){}stop(){queueMicrotask(()=>this.onend?.({type:'end'}));}abort(){queueMicrotask(()=>this.onend?.({type:'end'}));}}
 class Recorder {static isTypeSupported(){return true;}constructor(){this.state='inactive';this.mimeType='audio/webm';this.listeners={};}addEventListener(n,f){(this.listeners[n]??=[]).push(f);}start(){this.state='recording';}requestData(){}stop(){this.state='inactive';queueMicrotask(()=>{this.onstop?.();for(const f of this.listeners.stop||[])f();});}}
 class AudioContext {constructor(){this.state='running';}createMediaStreamSource(){return {connect(){},disconnect(){}};}createAnalyser(){return {fftSize:512,frequencyBinCount:256,getByteTimeDomainData(){},getByteFrequencyData(){}};}close(){this.state='closed';return Promise.resolve();}resume(){return Promise.resolve();}}
 const window={document,SpeechRecognition:recognition?Recognition:null,MediaRecorder:Recorder,AudioContext,addEventListener:(n,f)=>{if(!listeners.has(n))listeners.set(n,[]);listeners.get(n).push(f);},removeEventListener(){},dispatchEvent:e=>{events.push(e);for(const f of listeners.get(e.type)||[])f(e);return true;},speechSynthesis:{cancel(){},speak(){}},SpeechSynthesisUtterance:class{},fetch:async()=>response({ok:true,session:{id:41,status:'draft',segments:[]},sessions:[]})};
 const localStorage={getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,String(v)),removeItem:k=>storage.delete(k)};
 const context={window,document,localStorage,navigator:{language:'en-US',userActivation:{isActive:true},mediaDevices:{getSupportedConstraints:()=>({})}},location:{href:'https://vp3.test/chat.php',pathname:'/chat.php',search:'',origin:'https://vp3.test'},console,URL,URLSearchParams,Response,Blob,FormData,AbortController,DOMException,Date,performance,crypto:webcrypto,Uint8Array,Float32Array,ArrayBuffer,DataView,Event,CustomEvent:class{constructor(type,init={}){this.type=type;this.detail=init.detail||{};}},queueMicrotask,setTimeout:f=>{timers.push(f);return timers.length;},clearTimeout(){},setInterval:f=>{intervals.push(f);return intervals.length;},clearInterval(){},requestAnimationFrame:()=>1,cancelAnimationFrame(){},fetch:(...args)=>window.fetch(...args)};
 Object.assign(window,{setTimeout:context.setTimeout,clearTimeout:context.clearTimeout,setInterval:context.setInterval,clearInterval:context.clearInterval});
 context.MediaRecorder=Recorder;context.SpeechSynthesisUtterance=window.SpeechSynthesisUtterance;
 return {context,window,document,element,events,timers,intervals,storage,emit:(name,event={})=>window.dispatchEvent({type:name,...event})};
}
function load(name,b,hook=''){
 let code=source(name);if(hook){const at=code.lastIndexOf('})();');assert(at>=0);code=code.slice(0,at)+hook+'\n'+code.slice(at);}
 vm.runInNewContext(code,b.context,{filename:name});
}
function stream(){const track={readyState:'live',stops:0,stop(){this.stops++;this.readyState='ended';},addEventListener(){},getSettings(){return {};},getConstraints(){return {};}};return {track,getTracks:()=>[track],getAudioTracks:()=>[track]};}
async function test(name,run){await run();checks.push(name);console.log('PASS '+name);}

await test('media-only ownership, explicit takeover and stale-release isolation',async()=>{
 const storage=new Map(),a=browser({storage,recognition:false}),b=browser({storage,recognition:false});load('voice-lease-v122.js',a);load('voice-lease-v122.js',b);
 const first=a.window.StonefellowVoiceLeaseV122.acquireCapture('local');assert(first);const audio=stream();assert(first.ownStream(audio));
 assert.equal(b.window.StonefellowVoiceLeaseV122.acquireCapture('local'),null);
 assert.equal(a.window.StonefellowVoiceLeaseV122.acquireCapture('different-surface'),null);
 const next=b.window.StonefellowVoiceLeaseV122.acquireCapture('local',{takeover:true});assert(next);
 a.emit('storage',{key:a.window.STONEFELLOW_VOICE_LEASE_V122.key});assert.equal(audio.track.readyState,'ended');assert.equal(first.isCurrent(),false);
 first.release();assert(next.isCurrent());next.release();assert.equal(storage.size,0);
});
await test('pagehide releases owned streams and invalidates pending adoption',async()=>{
 const b=browser({recognition:false});load('voice-lease-v122.js',b);const ticket=b.window.StonefellowVoiceLeaseV122.acquireCapture('local');const audio=stream();ticket.ownStream(audio);b.emit('pagehide');assert.equal(audio.track.readyState,'ended');const late=stream();assert.equal(ticket.ownStream(late),false);assert.equal(late.track.readyState,'ended');
});
await test('unavailable storage denies acquisition',async()=>{
 const b=browser({recognition:false});b.context.localStorage.setItem=()=>{throw Error('blocked');};load('voice-lease-v122.js',b);assert.equal(b.window.StonefellowVoiceLeaseV122.acquireCapture('local'),null);
});
await test('restarted recognition remains preemptible',async()=>{
 const storage=new Map(),a=browser({storage}),b=browser({storage});load('voice-lease-v122.js',a);load('voice-lease-v122.js',b);
 const recognition=new a.window.SpeechRecognition();let ends=0;recognition.onend=()=>ends++;recognition.start();recognition.stop();await Promise.resolve();recognition.start();
 const next=b.window.StonefellowVoiceLeaseV122.acquireCapture('other',{takeover:true});a.emit('storage',{key:a.window.STONEFELLOW_VOICE_LEASE_V122.key});await Promise.resolve();assert.equal(ends,2);assert(next.isCurrent());next.release();
});

if(cloud){
 function chat(b){b.window.STONEFELLOW_CHAT={userId:1,endpoint:'/api/chat.php'};b.window.STONEFELLOW_CHAT_VOICE_BOOT={button:b.element('chatVoiceButton'),intro:null};load('chat-voice.js',b,'window.Test={ensureProcessedMic,disableVoice,seed:()=>{voiceOn=true;},state:()=>({voiceOn,inputStream,inputTrackPromise})};');return b.window.Test;}
 await test('Chat Stop disposes late microphone permission',async()=>{
  const b=browser(),pending=deferred(),audio=stream();b.context.navigator.mediaDevices.getUserMedia=()=>pending.promise;const api=chat(b);api.seed();const opening=api.ensureProcessedMic();api.disableVoice();pending.resolve(audio);await opening;assert.equal(audio.track.readyState,'ended');assert.equal(api.state().inputStream,null);
 });
 await test('Chat Stop prevents a late compatibility fallback',async()=>{
  const b=browser(),pending=deferred();let calls=0;b.context.navigator.mediaDevices.getUserMedia=()=>{calls++;return pending.promise;};const api=chat(b);api.seed();const opening=api.ensureProcessedMic();api.disableVoice();pending.reject(Object.assign(Error('unsupported'),{name:'TypeError'}));await opening;assert.equal(calls,1);
 });
 await test('Chat fallback result is disposed after Stop',async()=>{
  const b=browser(),pending=deferred(),audio=stream();let calls=0;b.context.navigator.mediaDevices.getUserMedia=()=>++calls===1?Promise.reject(Object.assign(Error('unsupported'),{name:'TypeError'})):pending.promise;const api=chat(b);api.seed();const opening=api.ensureProcessedMic();for(let i=0;i<8;i++)await Promise.resolve();assert.equal(calls,2);api.disableVoice();pending.resolve(audio);await opening;assert.equal(audio.track.readyState,'ended');
 });
 await test('old Chat permission completion cannot clear a newer request',async()=>{
  const b=browser(),a=deferred(),c=deferred(),old=stream(),fresh=stream();let calls=0;b.context.navigator.mediaDevices.getUserMedia=()=>++calls===1?a.promise:c.promise;const api=chat(b);api.seed();const first=api.ensureProcessedMic();api.disableVoice();api.seed();const second=api.ensureProcessedMic();a.resolve(old);await first;assert(api.state().inputTrackPromise);c.resolve(fresh);await second;assert.equal(api.state().inputStream,fresh);assert.equal(old.track.readyState,'ended');api.disableVoice();
 });
 function transcription(b){b.window.STONEFELLOW_ARTIST_LISTENING_V172={endpoint:'/api/artist-listening-v172.php',csrf:'synthetic',userId:1};load('artist-listening.js',b,'window.Test={state,startAudioRecording,startMeter,startRecognition,stopRecognition,stopCapture,seed:()=>{state.active=true;state.session={id:41,status:"active",segments:[]};state.captureStartedAt=Date.now();}};');return b.window.Test;}
 for(const method of ['startAudioRecording','startMeter'])await test('transcription Stop disposes late '+method,async()=>{
  const b=browser(),pending=deferred(),audio=stream();b.context.navigator.mediaDevices.getUserMedia=()=>pending.promise;const api=transcription(b);api.seed();const opening=api[method]();await api.stopCapture();pending.resolve(audio);await opening;assert.equal(audio.track.readyState,'ended');assert.equal(api.state.recordingActive,false);assert.equal(api.state.meterStream,null);
 });
 await test('old transcription recognizer end cannot change a newer recognizer',async()=>{
  const b=browser(),api=transcription(b);api.seed();api.startRecognition();const oldEnd=api.state.recognition.onend;api.stopRecognition();api.seed();api.startRecognition();const fresh=api.state.recognition;oldEnd();assert.equal(api.state.recognition,fresh);assert.equal(api.state.recognitionStarting,true);
 });
 await test('foreign voice preference never activates Chat capture',async()=>{
  const b=browser();const api=chat(b);b.emit('storage',{key:'stonefellow:voice-mode:1',newValue:'1'});assert.equal(api.state().voiceOn,false);assert.equal(api.state().inputStream,null);
 });
 await test('retained audio tracks stop before recording upload completes',async()=>{
  const b=browser(),audio=stream(),upload=deferred();b.context.navigator.mediaDevices.getUserMedia=async()=>audio;b.window.fetch=async(_url,init)=>init.body instanceof FormData?upload.promise:response({ok:true,session:{id:41,status:'draft',segments:[]},sessions:[]});const api=transcription(b);api.seed();await api.startAudioRecording();api.state.recordingChunks.push(new Blob(['audio']));const stopping=api.stopCapture();assert.equal(audio.track.readyState,'ended');await Promise.resolve();upload.resolve(response({ok:true,session:{id:41,status:'active',segments:[]}}));await stopping;
 });
 await test('Stop discards preview; Finish includes it before stopping',async()=>{
  for(const finish of [false,true]){const b=browser();const api=transcription(b);api.seed();api.state.interim='unfinished preview';const original=api.state.pending.length;await api.stopCapture(finish?'finish':'button');assert.equal(api.state.active,false);assert.equal(api.state.interim,'');assert.equal(b.window.STONEFELLOW_ARTIST_LISTENING_V172.segments,finish?1:0);assert.equal(original,0);}
 });
 await test('editor barge permission cannot revive after release',async()=>{
  const b=browser(),pending=deferred(),audio=stream();b.context.navigator.mediaDevices.getUserMedia=()=>pending.promise;load('editor-voice-barge-v117.js',b);const barge=b.window.StonefellowEditorVoiceBarge({isSpeaking:()=>true});const opening=barge.ensure();barge.release();pending.resolve(audio);await opening;assert.equal(audio.track.readyState,'ended');
 });
 function meeting(b){b.window.VP3Meeting={userId:1,meeting:'m',tokenEndpoint:'/token',presenceEndpoint:'/presence'};load('video-meetings-v1800.js',b,'renderParticipant=()=>{};populateDevices=async()=>{};window.Test={join,leave,toggleMic,seed:value=>{room=value;connected=true;},state:()=>({room,connected,joining})};');return b.window.Test;}
 await test('leaving during meeting authorization prevents media creation',async()=>{
  const b=browser(),token=deferred();let rooms=0;b.window.fetch=async url=>url==='/token'?token.promise:response({ok:true});b.window.LivekitClient={Room:class{constructor(){rooms++;}}};const api=meeting(b);const joining=api.join();await api.leave();token.resolve(response({ok:true,server_url:'x',participant_token:'t'}));await joining;assert.equal(rooms,0);assert.equal(api.state().connected,false);
 });
 await test('leaving during meeting microphone permission releases late track',async()=>{
  const b=browser(),mic=deferred(),micStarted=deferred(),audio=stream();let cameras=0,disconnects=0;const publications=new Map();b.window.fetch=async()=>response({ok:true,server_url:'x',participant_token:'t'});b.window.LivekitClient={RoomEvent:{},Room:class{constructor(){this.localParticipant={trackPublications:publications,setMicrophoneEnabled:async()=>{micStarted.resolve();await mic.promise;publications.set('mic',{track:audio.track});},setCameraEnabled:async()=>{cameras++;}};this.remoteParticipants=new Map();}on(){}async connect(){}async disconnect(){disconnects++;}async startAudio(){}}};const api=meeting(b);const joining=api.join();await micStarted.promise;await api.leave();mic.resolve();await joining;assert.equal(cameras,0);assert.equal(audio.track.readyState,'ended');assert(disconnects>0);assert.equal(api.state().connected,false);
 });
 await test('late meeting media toggle cannot retain capture after leave',async()=>{
  const b=browser(),mic=deferred(),audio=stream(),publications=new Map();b.window.fetch=async()=>response({ok:true});const target={localParticipant:{trackPublications:publications,isMicrophoneEnabled:false,setMicrophoneEnabled:async()=>{await mic.promise;publications.set('mic',{track:audio.track});}},async disconnect(){}};const api=meeting(b);api.seed(target);const toggling=api.toggleMic();await api.leave();mic.resolve();await toggling;assert.equal(audio.track.readyState,'ended');
 });
}else{
 for(const [file,start,stop,seed,state] of [
  ['chat-dictation.js','startLocalDictation','stopDictation','active=true;mode="local";','stream'],
  ['chat-enhancements.js','startLocalListening','stopConversationMode','conversationMode=true;voicePath={stt:"local",tts:"local"};','mediaStream'],
 ]){
  await test(file+' old permission cannot overwrite newer capture',async()=>{
   const b=browser(),a=deferred(),c=deferred(),old=stream(),fresh=stream();let calls=0;b.context.navigator.mediaDevices.getUserMedia=()=>++calls===1?a.promise:c.promise;load(file,b,`window.Test={start:${start},stop:${stop},seed:()=>{${seed}},state:()=>(${state})};`);const api=b.window.Test;api.seed();const first=api.start();api.stop('');api.seed();const second=api.start();c.resolve(fresh);await second;a.resolve(old);await first;assert.equal(old.track.readyState,'ended');assert.equal(api.state(),fresh);assert.equal(fresh.track.readyState,'live');api.stop('');assert.equal(fresh.track.readyState,'ended');
  });
 }
 await test('cancelled conversation setup cannot resume a newer setup',async()=>{
  const b=browser(),a=deferred(),c=deferred();let loads=0,statusCalls=0;b.window.HomeServerVoiceSettings={load:()=>++loads===1?a.promise:c.promise};b.window.fetch=async()=>{statusCalls++;return response({});};load('chat-enhancements.js',b,'window.Test={start:toggleConversationMode,stop:stopConversationMode,state:()=>({conversationMode,conversationStarting})};');const api=b.window.Test;const first=api.start();api.stop('');const second=api.start();a.resolve();await first;assert.equal(statusCalls,0);assert.equal(api.state().conversationStarting,true);api.stop('');c.resolve();await second;assert.equal(statusCalls,0);assert.equal(api.state().conversationMode,false);
 });
 await test('late browser conversation final cannot submit after Stop',async()=>{
  const b=browser();let submits=0;b.element('chatForm').requestSubmit=()=>submits++;load('chat-enhancements.js',b,'window.Test={start:startBrowserListening,stop:stopConversationMode,seed:()=>{conversationMode=true;voicePath={stt:"browser",tts:"browser"};},recognizer:()=>recognition};');const api=b.window.Test;api.seed();api.start();const current=api.recognizer();current.onresult({results:[Object.assign([{transcript:'old speech'}],{isFinal:true})]});current.onend();api.stop('');api.seed();for(const timer of b.timers)timer();assert.equal(submits,0);
 });
 await test('dictation Stop aborts an in-flight local transcription request',async()=>{
  const b=browser();let signal;const waiting=deferred();b.window.fetch=async(_url,init)=>{signal=init.signal;return waiting.promise;};load('chat-dictation.js',b,'recordingToWav=async()=>new Blob(["wav"]);window.Test={transcribeLocal,stop:stopDictation,seed:()=>{active=true;mode="local";}};');b.window.Test.seed();const transcribing=b.window.Test.transcribeLocal(new Blob(['audio']),0);await Promise.resolve();assert(signal);b.window.Test.stop('');assert(signal.aborted);waiting.resolve(response({text:'late speech'}));await transcribing;
 });
}
console.log(`CAPTURE_OWNERSHIP_SECTION1=PASS (${checks.length} behavioral cases, ${cloud?'Cloud':'HomeServer'})`);
