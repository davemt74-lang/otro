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
 const window={document,SpeechRecognition:recognition?Recognition:null,MediaRecorder:Recorder,AudioContext,addEventListener:(n,f)=>{if(!listeners.has(n))listeners.set(n,[]);listeners.get(n).push(f);},removeEventListener(){},dispatchEvent:e=>{events.push(e);for(const f of listeners.get(e.type)||[])f(e);return true;},speechSynthesis:{cancel(){},speak(){}},SpeechSynthesisUtterance:class{constructor(text){this.text=text;}},fetch:async()=>response({ok:true,session:{id:41,status:'draft',segments:[]},sessions:[]})};
 const localStorage={getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,String(v)),removeItem:k=>storage.delete(k)};
 const context={window,document,localStorage,navigator:{language:'en-US',userActivation:{isActive:true},mediaDevices:{getSupportedConstraints:()=>({})}},location:{href:'https://vp3.test/chat.php',pathname:'/chat.php',search:'',origin:'https://vp3.test'},console,URL,URLSearchParams,Response,ReadableStream,TextEncoder,TextDecoder,Blob,FormData,AbortController,DOMException,Date,performance,crypto:webcrypto,Uint8Array,Float32Array,ArrayBuffer,DataView,Event,CustomEvent:class{constructor(type,init={}){this.type=type;this.detail=init.detail||{};}},queueMicrotask,setTimeout:f=>{timers.push(f);return timers.length;},clearTimeout(){},setInterval:f=>{intervals.push(f);return intervals.length;},clearInterval(){},requestAnimationFrame:()=>1,cancelAnimationFrame(){},fetch:(...args)=>window.fetch(...args)};
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


const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
function clock(b){
 let time=0,sequence=0;const pending=new Map();
 const set=(fn,delay=0)=>{const id=++sequence;pending.set(id,{fn,at:time+Number(delay)});return id;};
 const clear=id=>pending.delete(id);
 Object.assign(b.context,{setTimeout:set,clearTimeout:clear});Object.assign(b.window,{setTimeout:set,clearTimeout:clear});
 return {pending,async advance(ms){const end=time+ms;let count=0;while(true){let next;for(const [id,job] of pending)if(job.at<=end&&(!next||job.at<next[1].at))next=[id,job];if(!next)break;assert(++count<1000,'bounded timer execution');time=next[1].at;pending.delete(next[0]);next[1].fn();await flush();}time=end;await flush();}};
}
const result=(text,index=0)=>({resultIndex:index,results:Array.from({length:index+1},(_,i)=>({0:{transcript:i===index?text:'earlier',confidence:.9},isFinal:true}))});

if(cloud){
 await test('distinct repeated utterances, result replay and document isolation',async()=>{
  const b=browser();load('artist-listening-realtime.js',b);const C=b.window.STONEFELLOW_ARTIST_LISTENING_REALTIME.TranscriptContinuity;
  const doc=new C('Earlier words');assert.equal(doc.finalize('say it again',1,{runId:'a',resultIndex:0}).delta,'say it again');
  assert.equal(doc.finalize('say it again',2,{runId:'a',resultIndex:1}).delta,'say it again');
  assert(doc.finalize('changed wording on replay',3,{runId:'a',resultIndex:1}).duplicate);
  assert.equal(doc.finalize('say it again',4,{runId:'b',resultIndex:0}).delta,'say it again');
  assert.equal(new C().finalize('say it again',4,{runId:'a',resultIndex:0}).delta,'say it again');
  assert.equal(doc.finalize('say it again').delta,'say it again');
 });
 for(const text of ['你好 世界','今日は','مرحبا بالعالم','हिन्दी आवाज़','cafe\u0301 résumé','中','I','!!!'])await test('transcript text preserved: '+text,async()=>{
  const b=browser();load('artist-listening-realtime.js',b);const api=b.window.STONEFELLOW_ARTIST_LISTENING_REALTIME;const doc=new api.TranscriptContinuity();assert.equal(doc.finalize(text).delta,text);assert.equal(doc.committed,text);
 });
 await test('overlap reconciliation requires explicit cumulative provider text',async()=>{
  const b=browser();load('artist-listening-realtime.js',b);const api=b.window.STONEFELLOW_ARTIST_LISTENING_REALTIME;
  const separate=new api.TranscriptContinuity('move the release date');assert.equal(separate.finalize('the release date to Friday').delta,'the release date to Friday');
  const cumulative=new api.TranscriptContinuity('move the release date');assert.equal(cumulative.finalize('the release date to Friday',1,{cumulative:true}).delta,'to Friday');
  assert.equal(api.reconcileFinal('cafe\u0301 résumé','café résumé demain').delta,'demain');
  assert.equal(api.reconcileFinal('!!!','???').delta,'???');
 });
 await test('native restart gives index-zero finals a fresh identity',async()=>{
  const b=browser(),timer=clock(b),native=[];b.window.SpeechRecognition=class{constructor(){native.push(this);}start(){this.onstart?.({});}stop(){}abort(){}};
  load('artist-listening-recognition.js',b);load('artist-listening-realtime.js',b);const continuity=new b.window.STONEFELLOW_ARTIST_LISTENING_REALTIME.TranscriptContinuity();
  const owner=new b.window.SpeechRecognition();owner.onresult=event=>continuity.finalize(event.results[0][0].transcript,1,{runId:event.stonefellowRecognitionRunId,resultIndex:0});owner.start();
  native[0].onresult(result('repeat'));native[0].onresult(result('repeat'));native[0].onend({});await timer.advance(35);native[1].onresult(result('repeat'));native[0].onresult(result('obsolete'));
  assert.equal(continuity.committed,'repeat repeat');owner.abort();
 });
 function chat(b){
  b.window.STONEFELLOW_CHAT={userId:1,endpoint:'/api/chat.php'};b.window.STONEFELLOW_CHAT_VOICE_BOOT={button:b.element('chatVoiceButton'),intro:null};
  load('chat-voice.js',b,'window.Test={createRecognition,handleVoiceApiFetch,interruptResponse,disableVoice,submitVoiceTranscript,speakAnswer,seed:()=>{voiceOn=true;},recognize:()=>{recognition=createRecognition();return recognition;},state:()=>({voiceOn,processing,speaking,activeRequest,pendingFinalTranscript,lastConversationId,proof})};');return b.window.Test;
 }
 await test('Chat accepts non-Latin speech and distinct repeated native finals',async()=>{
  const b=browser();clock(b);const api=chat(b);api.seed();const a=api.recognize();a.onresult(result('你好 世界'));a.onresult(result('你好 世界'));assert.equal(api.state().pendingFinalTranscript,'你好 世界');
  const next=api.recognize();next.onresult(result('你好 世界'));assert.equal(api.state().pendingFinalTranscript,'你好 世界 你好 世界');api.disableVoice();
 });
 await test('cancelled Chat request cannot clear, speak or change a newer turn',async()=>{
  const b=browser();clock(b);const a=deferred(),next=deferred();let calls=0;b.window.fetch=()=>++calls===1?a.promise:next.promise;const api=chat(b);api.seed();
  const first=api.handleVoiceApiFetch('/chat',{},{});api.interruptResponse();const second=api.handleVoiceApiFetch('/chat',{},{});const owned=api.state().activeRequest;
  assert.equal((await first).status,499);a.resolve(new Response(JSON.stringify({ok:true,answer:'obsolete',conversation_id:900})));await flush();assert.equal(api.state().activeRequest,owned);assert(api.state().processing);assert.equal(api.state().lastConversationId,0);
  next.resolve(new Response(JSON.stringify({ok:true,answer:'',conversation_id:42})));await second;assert.equal(api.state().lastConversationId,42);assert.equal(api.state().processing,false);api.disableVoice();
 });
 await test('failed and timed-out Chat requests release processing for retry',async()=>{
  for(const timeout of [false,true]){const b=browser(),timer=clock(b);b.window.fetch=timeout?()=>new Promise(()=>{}):()=>Promise.reject(Error('offline'));const api=chat(b);api.seed();const turn=api.handleVoiceApiFetch('/chat',{},{});if(timeout)await timer.advance(120000);assert.equal((await turn).status,502);assert.equal(api.state().activeRequest,null);assert.equal(api.state().processing,false);api.disableVoice();}
 });
 await test('Chat submit exception resumes and browser speech watchdog settles once',async()=>{
  const b=browser(),timer=clock(b),spoken=[];b.window.speechSynthesis.speak=u=>spoken.push(u);const api=chat(b);api.seed();b.element('chatForm').requestSubmit=()=>{throw Error('form unavailable');};api.submitVoiceTranscript('مرحبا');assert.equal(api.state().processing,false);assert(timer.pending.size>0);
  api.speakAnswer('short answer');assert.equal(spoken.length,1);spoken[0].onstart();await timer.advance(15000);assert.equal(api.state().speaking,false);const ends=api.state().proof.speechEnds;spoken[0].onend();assert.equal(api.state().proof.speechEnds,ends);api.disableVoice();
 });
 await test('stream cancellation settles a stalled reader and ignores late deltas',async()=>{
  const b=browser();clock(b);let stream,cancelled=0;b.window.fetch=async()=>new Response(new ReadableStream({start(controller){stream=controller;},cancel(){cancelled++;}}));
  b.window.StonefellowPremiumVoiceV122=()=>({warm:async()=>false,createStream(){},stop(){}});const api=chat(b);api.seed();const turn=api.handleVoiceApiFetch('/chat',{},{});await flush();api.interruptResponse();assert.equal((await turn).status,499);assert.equal(cancelled,1);assert.equal(api.state().activeRequest,null);api.disableVoice();
 });
 await test('stream completion, provider failure and stalled deadline settle the current turn',async()=>{
  for(const kind of ['success','failure','deadline']){const b=browser(),timer=clock(b);b.window.StonefellowPremiumVoiceV122=()=>({warm:async()=>false,createStream(){},stop(){}});b.window.fetch=async()=>kind==='deadline'?new Response(new ReadableStream({start(){}})):new Response(JSON.stringify({type:'done',data:{ok:kind==='success',error:'provider offline',answer:'',conversation_id:42}})+'\n');const api=chat(b);api.seed();const turn=api.handleVoiceApiFetch('/chat',{},{});if(kind==='deadline')await timer.advance(120000);const response=await turn;assert.equal(response.status,kind==='success'?200:502);assert.equal(api.state().activeRequest,null);assert.equal(api.state().processing,false);api.disableVoice();}
 });
 await test('silent form rejection recovers; Stop during context preparation never sends',async()=>{
  const b=browser(),timer=clock(b),context=deferred();let requests=0;b.window.fetch=async()=>{requests++;return new Response('{}');};b.window.StonefellowAgentContext={refresh:()=>context.promise,snapshot:()=>({})};const api=chat(b);api.seed();b.element('chatForm').requestSubmit=()=>{};api.submitVoiceTranscript('hello again');await timer.advance(2200);assert.equal(api.state().processing,false);
  b.element('chatForm').requestSubmit=()=>{b.pendingSend=b.window.fetch('/api/chat.php',{method:'POST',body:JSON.stringify({action:'send',message:'hello'})});};api.submitVoiceTranscript('hello');api.disableVoice();context.resolve({});assert.equal((await b.pendingSend).status,499);assert.equal(requests,0);
 });
 await test('capture controller preserves repeats and Unicode while rejecting replay',async()=>{
  const b=browser();clock(b);b.window.STONEFELLOW_ARTIST_LISTENING_V172={endpoint:'/api/artist-listening-v172.php',csrf:'test',userId:1};load('artist-listening-realtime.js',b);load('artist-listening.js',b,'window.Test={state,handleFinal,seed:()=>{state.active=true;state.session={id:41,status:"active",segments:[]};state.captureStartedAt=Date.now();}};');const api=b.window.Test;api.seed();api.handleFinal('مرحبا بالعالم',.9,{runId:'a',resultIndex:0});api.handleFinal('مرحبا بالعالم',.9,{runId:'a',resultIndex:1});api.handleFinal('مرحبا بالعالم',.9,{runId:'a',resultIndex:1});assert.equal(api.state.pending.length,2);assert.equal(api.state.pending[0].text,'مرحبا بالعالم');
 });
 await test('editor accepts single-character Unicode and distinct repeated turns',async()=>{
  const b=browser(),timer=clock(b),recognizers=[],heard=[];b.window.SpeechRecognition=class{constructor(){recognizers.push(this);}start(){this.onstart?.();}stop(){}abort(){}};
  b.context.navigator.mediaDevices.getUserMedia=async()=>stream();load('conversation-voice-v122.js',b);const owner=b.window.StonefellowConversationVoiceV122.create({userId:1,onTranscript:text=>heard.push(text)});owner.setEnabled(true,{persist:false,immediate:true});
  const first=recognizers[0];first.onresult(result('中'));first.onresult(result('中'));await timer.advance(1800);assert.equal(heard[0],'中');await timer.advance(200);recognizers.at(-1).onresult(result('中'));await timer.advance(1800);assert.deepEqual(heard,['中','中']);owner.destroy();
 });
}else{
 function voice(b){load('chat-enhancements.js',b,'window.Test={autoSubmitSpeech,speakAgentReply,stopConversationMode,startBrowserListening,recognition:()=>recognition,seed:(path="browser")=>{conversationMode=true;voicePath={stt:"browser",tts:path};},context:value=>{playbackContext=value;},state:()=>({awaitingAgent,waitingRequestId,speaking,conversationMode,outputGeneration})};');return b.window.Test;}
 function brain(b){load('brain.js',b,'window.BrainTest={submitChatTurn,leaveConversation,state:()=>({pendingChatTurn,activeConversationId})};');return b.window.BrainTest;}
 const submitEvent=b=>({preventDefault(){},target:b.element('chatForm')});
 const emitTurn=(b,requestId,status,reply='')=>b.emit('homeserver:chat-turn',{detail:{requestId,status,reply}});
 function standaloneTurn(b,api,id){b.element('chatForm').requestSubmit=()=>emitTurn(b,id,'started');api.autoSubmitSpeech('new speech');}
 await test('identical replies on distinct turns speak; duplicate completion is ignored',async()=>{
  const b=browser(),timer=clock(b),spoken=[];b.window.speechSynthesis.speak=u=>spoken.push(u);const api=voice(b);api.seed();
  standaloneTurn(b,api,1);emitTurn(b,1,'completed','same reply');emitTurn(b,1,'completed','same reply');assert.equal(spoken.length,1);spoken[0].onend();
  standaloneTurn(b,api,2);emitTurn(b,2,'completed','same reply');assert.equal(spoken.length,2);assert(api.state().speaking);assert.equal(api.state().awaitingAgent,false);api.stopConversationMode('');
 });
 await test('failed, empty and rejected submissions release waiting for retry',async()=>{
  const b=browser(),timer=clock(b);const api=voice(b);api.seed();for(const [id,status] of [[1,'failed'],[2,'completed'],[3,'cancelled']]){standaloneTurn(b,api,id);emitTurn(b,id,status);assert.equal(api.state().awaitingAgent,false);}
  b.element('chatForm').requestSubmit=()=>{throw Error('blocked');};api.autoSubmitSpeech('again');assert.equal(api.state().awaitingAgent,false);b.element('chatForm').requestSubmit=()=>{};api.autoSubmitSpeech('again');assert.equal(api.state().awaitingAgent,false);assert(timer.pending.size>0);api.stopConversationMode('');
 });
 await test('old completion and old speech callbacks are inert after Stop and restart',async()=>{
  const b=browser();clock(b);const spoken=[];b.window.speechSynthesis.speak=u=>spoken.push(u);const api=voice(b);api.seed();standaloneTurn(b,api,1);api.stopConversationMode('');api.seed();standaloneTurn(b,api,2);emitTurn(b,1,'completed','obsolete');assert.equal(spoken.length,0);emitTurn(b,2,'completed','current');const old=spoken[0];api.stopConversationMode('');api.seed();api.speakAgentReply('fresh');old.onend();old.onerror();assert(api.state().speaking);api.stopConversationMode('');
 });
 await test('late local TTS success and failure cannot change a newer output',async()=>{
  for(const fail of [false,true]){const b=browser();clock(b);const waiting=deferred(),spoken=[];let signal;b.window.fetch=(_url,init)=>{signal=init.signal;return waiting.promise;};b.window.speechSynthesis.speak=u=>spoken.push(u);const api=voice(b);api.seed('local');api.speakAgentReply('old');api.stopConversationMode('');assert(signal.aborted);api.seed('browser');api.speakAgentReply('fresh');if(fail)waiting.reject(Error('old failure'));else waiting.resolve({ok:true,arrayBuffer:async()=>new ArrayBuffer(8)});await flush();assert.equal(spoken.length,1);assert(api.state().speaking);api.stopConversationMode('');}
 });
 await test('Strict Local blocks fallback and playback watchdog resumes listening',async()=>{
  const b=browser(),timer=clock(b),spoken=[];b.window.speechSynthesis.speak=u=>spoken.push(u);const api=voice(b);api.seed();b.element('strictLocalVoice').checked=true;api.speakAgentReply('private');assert.equal(spoken.length,0);assert.equal(api.state().conversationMode,false);
  b.element('strictLocalVoice').checked=false;api.seed();api.speakAgentReply('hello');await timer.advance(15000);assert.equal(api.state().speaking,false);api.stopConversationMode('');
 });
 await test('browser recognition aggregates new final indices and stops on startup/device failure',async()=>{
  const b=browser(),timer=clock(b);const api=voice(b);api.seed();const starts=[];b.element('chatForm').requestSubmit=()=>{starts.push(b.element('chatInput').value);emitTurn(b,1,'started');};api.startBrowserListening();const listener=api.recognition();listener.onstart();listener.onresult(result('你好',0));listener.onresult(result('世界',1));await flush();await timer.advance(0);assert.equal(starts[0],'你好 世界');api.stopConversationMode('');api.seed();api.startBrowserListening();api.recognition().onerror({error:'audio-capture'});assert.equal(api.state().conversationMode,false);api.seed();api.startBrowserListening();await timer.advance(5000);assert.equal(api.state().conversationMode,false);
 });
 await test('canonical owner emits actual reply despite failed history refresh',async()=>{
  const b=browser();clock(b);const spoken=[];b.window.speechSynthesis.speak=u=>spoken.push(u);b.window.fetch=async url=>url==='/api/v1/control/chat'?response({reply:'actual answer',conversation_id:'c1',run_id:'r1'}):Promise.reject(Error('history offline'));
  const requestOwner=brain(b),api=voice(b);api.seed();b.element('chatForm').requestSubmit=()=>requestOwner.submitChatTurn(submitEvent(b));api.autoSubmitSpeech('hello');await flush();assert.equal(spoken[0].text,'actual answer');assert.equal(requestOwner.state().pendingChatTurn,null);assert.equal(api.state().awaitingAgent,false);assert.equal(b.element('chatInput').disabled,false);assert.equal(b.events.filter(e=>e.type==='homeserver:chat-turn'&&e.detail.status==='failed').length,0);api.stopConversationMode('');
 });
 await test('canonical owner rejects parallel submits and settles cancellation despite late transport',async()=>{
  const b=browser();clock(b);const a=deferred(),next=deferred();let calls=0;b.window.fetch=url=>url==='/api/v1/control/chat'?(++calls===1?a.promise:next.promise):Promise.reject(Error('history offline'));const owner=brain(b);b.element('chatInput').value='one';const first=owner.submitChatTurn(submitEvent(b));b.element('chatInput').value='duplicate';await owner.submitChatTurn(submitEvent(b));assert.equal(calls,1);b.window.HomeServerChatTurn.cancel(1);await first;b.element('chatInput').value='next';const second=owner.submitChatTurn(submitEvent(b));a.resolve(response({reply:'obsolete',conversation_id:'old'}));await flush();assert.equal(owner.state().pendingChatTurn.id,2);next.resolve(response({reply:'new',conversation_id:'new'}));await second;assert.equal(owner.state().activeConversationId,'new');assert.equal(b.events.filter(e=>e.type==='homeserver:chat-turn'&&e.detail.status==='completed').length,1);
 });
 await test('canonical owner failure and timeout enable composer and emit one failure',async()=>{
  for(const timeout of [false,true]){const b=browser(),timer=clock(b);b.window.fetch=timeout?()=>new Promise(()=>{}):()=>Promise.reject(Error('offline'));const owner=brain(b);b.element('chatInput').value='hello';const turn=owner.submitChatTurn(submitEvent(b));if(timeout)await timer.advance(120000);await turn;assert.equal(owner.state().pendingChatTurn,null);assert.equal(b.element('chatInput').disabled,false);assert.equal(b.events.filter(e=>e.type==='homeserver:chat-turn'&&e.detail.status==='failed').length,1);}
 });
 await test('older history refresh cannot overwrite a later reply',async()=>{
  const b=browser();clock(b);const history=deferred();let calls=0;b.window.fetch=async url=>{if(url==='/api/v1/control/chat')return response({reply:'reply '+(++calls),conversation_id:'c1'});if(url==='/api/v1/control/conversations/c1')return calls===1?history.promise:response({conversation:{title:'new'},messages:[{role:'assistant',content:'new history'}]});return response({items:[]});};const owner=brain(b);b.element('chatInput').value='one';await owner.submitChatTurn(submitEvent(b));b.element('chatInput').value='two';await owner.submitChatTurn(submitEvent(b));await flush();const current=b.element('chatMessages').innerHTML;assert(current.includes('new history'));history.resolve(response({conversation:{title:'old'},messages:[{role:'assistant',content:'old history'}]}));await flush();assert.equal(b.element('chatMessages').innerHTML,current);
 });
 await test('voice Stop cancels its owned turn and navigation prevents late completion',async()=>{
  const b=browser();clock(b);const waiting=deferred();let signal;b.window.fetch=(_url,init)=>{signal=init.signal;return waiting.promise;};const owner=brain(b),api=voice(b);api.seed();b.element('chatForm').requestSubmit=()=>owner.submitChatTurn(submitEvent(b));api.autoSubmitSpeech('hello');assert(api.state().awaitingAgent);api.stopConversationMode('');assert(signal.aborted);await flush();assert.equal(owner.state().pendingChatTurn,null);waiting.resolve(response({reply:'late',conversation_id:'old'}));await flush();assert.equal(owner.state().activeConversationId,null);
 });
}
console.log(`LISTENING_SECTION2=PASS (${checks.length} behavioral cases, ${cloud?'Cloud':'HomeServer'})`);
