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
 function owner(b){b.window.STONEFELLOW_ARTIST_LISTENING_V172={endpoint:'/transcription',csrf:'synthetic',userId:1};load('artist-listening.js',b,'window.Test={state,nextSegment,flushPending,finalizeStop,startAudioRecording,stopAudioRecording,seed:()=>{state.active=true;state.session={id:41,status:"active",segments:[]};}};');return b.window.Test;}
 await test('failed append backs off and retries the exact ordered batch once',async()=>{
  const b=browser(),timer=clock(b),requests=[];b.window.fetch=async(_url,init)=>{const data=JSON.parse(init.body);requests.push(data);if(requests.length===1)throw Error('offline');return response({ok:true,session:{id:41,status:'active',segments:[]}});};
  const api=owner(b);api.seed();api.nextSegment('transcript','first');api.nextSegment('transcript','second');await api.flushPending();await timer.advance(3999);assert.equal(requests.length,1);assert.equal(api.state.pending.length,2);await timer.advance(1);assert.equal(requests.length,2);assert.deepEqual(requests[0].segments,requests[1].segments);assert.equal(api.state.pending.length,0);
 });
 await test('stalled response body times out, aborts transport and retains text',async()=>{
  const b=browser(),timer=clock(b);let signal;b.window.fetch=async(_url,init)=>{signal=init.signal;return {ok:true,json:()=>new Promise(()=>{})};};const api=owner(b);api.seed();api.nextSegment('transcript','keep me');const saving=api.flushPending();await timer.advance(30000);await saving;assert(signal.aborted);assert.equal(api.state.syncing,false);assert.equal(api.state.pending[0].text,'keep me');
 });
 await test('queue saturation stops capture and preserves all accepted segments',async()=>{
  const b=browser();clock(b);b.window.fetch=()=>new Promise(()=>{});const api=owner(b);api.seed();for(let i=0;i<500;i++)api.nextSegment('transcript','segment '+i);assert.equal(api.state.active,false);assert.equal(api.state.pending.length,500);assert.equal(api.state.pending[0].text,'segment 0');assert.equal(JSON.parse(b.storage.values().next().value).pending.length,500);assert.equal(api.nextSegment('transcript','excess'),null);
 });
 await test('backup quota failure releases capture while retaining text in memory',async()=>{
  const b=browser();clock(b);b.context.localStorage.setItem=()=>{throw Error('quota');};b.window.fetch=()=>new Promise(()=>{});const api=owner(b);api.seed();api.nextSegment('transcript','unsaved text');assert.equal(api.state.active,false);assert.equal(api.state.pending[0].text,'unsaved text');assert(api.state.lastError.includes('Keep this page open'));
 });
 await test('retained clip size cap closes clip without stopping transcription',async()=>{
  const b=browser();clock(b);const audio=stream();b.context.navigator.mediaDevices.getUserMedia=async()=>audio;const api=owner(b);api.seed();await api.startAudioRecording();const rec=api.state.mediaRecorder;
  for(const f of rec.listeners.dataavailable)f({data:new Blob([new Uint8Array(31*1024*1024)])});await flush();assert.equal(api.state.recordingActive,false);assert.equal(api.state.active,true);assert.equal(audio.track.readyState,'ended');assert(api.state.recordingBytes<=32*1024*1024);
 });
 await test('missing recorder stop event settles and releases memory without false save',async()=>{
  const b=browser(),timer=clock(b),audio=stream();b.context.navigator.mediaDevices.getUserMedia=async()=>audio;const api=owner(b);api.seed();await api.startAudioRecording();api.state.recordingChunks.push(new Blob(['clip']));api.state.mediaRecorder.stop=()=>{};const stopping=api.stopAudioRecording().catch(e=>e);await timer.advance(5000);assert((await stopping).message.includes('did not finish'));assert.equal(api.state.recordingUploading,false);assert.equal(api.state.recordingChunks.length,0);assert.equal(audio.track.readyState,'ended');
 });
}else{
 function dictation(b){load('chat-dictation.js',b,'recordingToWav=async blob=>blob;window.Test={startLocalDictation,startBrowserDictation,stop:stopDictation,enqueueSegment,seed:(session=true,path="local")=>{active=true;transcriptionSession=session;mode=path;},state:()=>({active,generation,recognition,recorder,stream,segmentQueue,queuedBytes})};');return b.window.Test;}
 function local(b){const audio=stream(),recorders=[];let opens=0;b.context.navigator.mediaDevices.getUserMedia=async()=>{opens++;return audio;};const R=b.window.MediaRecorder;b.context.MediaRecorder=b.window.MediaRecorder=class extends R{constructor(...args){super(...args);recorders.push(this);}};return {audio,recorders,opens:()=>opens};}
 await test('quiet session rotates recording using one microphone and remains active',async()=>{
  const b=browser();clock(b);const capture=local(b),api=dictation(b);api.seed();await api.startLocalDictation();const first=capture.recorders[0];first.stop();await flush();assert.equal(capture.recorders.length,2);assert.equal(capture.opens(),1);assert.equal(capture.audio.track.readyState,'live');assert(api.state().active);api.stop();assert.equal(capture.audio.track.readyState,'ended');
 });
 await test('capture continues during slow Whisper and queued segments process in order',async()=>{
  const b=browser();clock(b);const capture=local(b),first=deferred(),second=deferred(),requests=[];b.window.fetch=(_url,init)=>{requests.push(init);return requests.length===1?first.promise:second.promise;};const api=dictation(b);api.seed();await api.startLocalDictation();api.enqueueSegment(new Blob(['one']),0);api.enqueueSegment(new Blob(['two']),0);await flush();assert.equal(requests.length,1);assert.equal(api.state().recorder.state,'recording');assert.equal(capture.audio.track.readyState,'live');first.resolve(response({text:'first'}));await flush();assert.equal(requests.length,2);second.resolve(response({text:'second'}));await flush();assert.deepEqual(b.events.filter(e=>e.type==='homeserver:transcription-segment').map(e=>e.detail.text),['first','second']);assert.equal(api.state().queuedBytes,0);api.stop();
 });
 await test('Stop clears audio backlog and late response never enters a restarted session',async()=>{
  const b=browser();clock(b);const waiting=deferred();let signal;b.window.fetch=(_url,init)=>{signal=init.signal;return waiting.promise;};const api=dictation(b);api.seed();api.enqueueSegment(new Blob(['one']),0);api.enqueueSegment(new Blob(['two']),0);await flush();api.stop();assert(signal.aborted);assert.equal(api.state().segmentQueue.length,0);api.seed();waiting.resolve(response({text:'obsolete'}));await flush();assert.equal(b.events.filter(e=>e.type==='homeserver:transcription-segment').length,0);api.stop();
 });
 await test('slow processor overload stops capture and clears bounded audio queue',async()=>{
  const b=browser();clock(b);const capture=local(b);b.window.fetch=()=>new Promise(()=>{});const api=dictation(b);api.seed();await api.startLocalDictation();for(let i=0;i<6;i++)api.enqueueSegment(new Blob(['audio']),0);assert.equal(api.state().active,false);assert.equal(api.state().queuedBytes,0);assert.equal(capture.audio.track.readyState,'ended');
 });
 await test('Whisper deadline settles stalled transport and stops private session',async()=>{
  const b=browser(),timer=clock(b);b.window.fetch=()=>new Promise(()=>{});const api=dictation(b);api.seed();api.enqueueSegment(new Blob(['audio']),0);await flush();await timer.advance(100000);assert.equal(api.state().active,false);assert.equal(api.state().queuedBytes,0);
 });
 await test('browser session survives silence, deduplicates indices and ignores obsolete callbacks',async()=>{
  const b=browser(),timer=clock(b);const api=dictation(b);api.seed(true,'browser');api.startBrowserDictation();const first=api.state().recognition;first.onresult(result('repeat'));first.onresult(result('repeat'));first.onerror({error:'no-speech'});first.onend();await timer.advance(200);const next=api.state().recognition;assert.notEqual(next,first);next.onresult(result('repeat'));first.onresult(result('obsolete'));assert.deepEqual(b.events.filter(e=>e.type==='homeserver:transcription-segment').map(e=>e.detail.text),['repeat','repeat']);api.stop();await timer.advance(1000);assert.equal(api.state().recognition,null);
 });
 await test('workspace retains failed save and retries its key before the next segment',async()=>{
  const b=browser(),timer=clock(b),requests=[];b.window.HomeServerDictation={stopTranscription(){}};b.window.fetch=async(_url,init)=>{const item=JSON.parse(init.body);requests.push(item);if(requests.length===1)throw Error('offline');return response({session:{id:'s1',segment_count:requests.length}});};load('transcription-workspace.js',b,'window.Test={onSegment,saveQueue,seed:()=>{active={id:"s1"};listening=true;},queue:()=>queue};');const api=b.window.Test;api.seed();api.onSegment({detail:{text:'one'}});api.onSegment({detail:{text:'two'}});await flush();assert.equal(api.queue().length,2);await timer.advance(4000);assert.equal(api.queue().length,0);assert.equal(requests[0].client_key,requests[1].client_key);assert.equal(requests[2].text,'two');
 });
 await test('workspace never completes a document with unsaved text',async()=>{
  const b=browser();clock(b);const paths=[];b.window.HomeServerDictation={stopTranscription(){}};b.window.fetch=async url=>{paths.push(url);throw Error('offline');};load('transcription-workspace.js',b,'window.Test={onSegment,finish,seed:()=>{active={id:"s1"};listening=true;},state:()=>({active,queue,finishRequested})};');const api=b.window.Test;api.seed();api.onSegment({detail:{text:'keep'}});await flush();await api.finish().catch(()=>{});assert.equal(api.state().queue.length,1);assert(api.state().active);assert(!paths.some(p=>p.endsWith('/stop')));
 });
}
console.log(`TRANSCRIPTION_SECTION3=PASS (${checks.length} behavioral cases, ${cloud?'Cloud':'HomeServer'})`);
