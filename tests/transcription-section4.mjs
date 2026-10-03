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
 querySelector(key){this.nodes??=new Map();if(!this.nodes.has(key))this.nodes.set(key,new Element(key));return this.nodes.get(key);}
 querySelectorAll(){return [];}
 replaceChildren(...children){this.children=children;}
 appendChild(child){this.children.push(child);}
 insertBefore(child){this.children.push(child);}
 append(...children){this.children.push(...children);}
 insertAdjacentHTML(_where,html){this.innerHTML=(this.innerHTML||'')+html;}
 insertAdjacentElement(){}
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
 const localStorage={get length(){return storage.size;},key:i=>[...storage.keys()][i]??null,getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,String(v)),removeItem:k=>storage.delete(k)};
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


const flush=async()=>{for(let i=0;i<80;i++)await Promise.resolve();};
function clock(b){
 let time=0,sequence=0;const pending=new Map();
 const set=(fn,delay=0)=>{const id=++sequence;pending.set(id,{fn,at:time+Number(delay)});return id;};
 const clear=id=>pending.delete(id);
 Object.assign(b.context,{setTimeout:set,clearTimeout:clear});Object.assign(b.window,{setTimeout:set,clearTimeout:clear});
 return {pending,async advance(ms){const end=time+ms;let count=0;while(true){let next;for(const [id,job] of pending)if(job.at<=end&&(!next||job.at<next[1].at))next=[id,job];if(!next)break;assert(++count<1000,'bounded timer execution');time=next[1].at;pending.delete(next[0]);next[1].fn();await flush();}time=end;await flush();}};
}
const result=(text,index=0)=>({resultIndex:index,results:Array.from({length:index+1},(_,i)=>({0:{transcript:i===index?text:'earlier',confidence:.9},isFinal:true}))});


if(cloud){
 function owner(b){b.window.STONEFELLOW_ARTIST_LISTENING_V172={endpoint:'/api/artist-listening-v172.php',csrf:'synthetic',userId:1};load('artist-listening.js',b,'window.Test={state,flushPending,finalizeStop,nextSegment,elapsedMs};');return b.window.Test;}
 const key='a'.repeat(32),savedKey='b'.repeat(32),backupKey='stonefellow:artist-listening:v172:1';
 const pending={key,index:1,type:'transcript',text:'retained words',started_ms:50,ended_ms:100};
 await test('reload binds pending capture text to its original owned document without a microphone',async()=>{
  const b=browser({storage:new Map([[backupKey,JSON.stringify({sessionId:41,clientSessionKey:savedKey,pending:[pending],elapsedMs:100})]])});clock(b);const calls=[];
  b.window.fetch=async(url,init)=>{const data=init.method==='GET'?Object.fromEntries(new URL(url).searchParams):JSON.parse(init.body);calls.push(data);return response({ok:true,session:{id:41,client_session_key:savedKey,status:'active',segments:[]},active:{id:99,status:'active'},sessions:[]});};
  const api=owner(b);await flush();assert.equal(api.state.session.id,41);assert.equal(api.state.active,false);assert.equal(api.state.pending.length,0);assert.equal(calls.find(c=>c.action==='append').session_id,41);assert(!api.state.recognition);
 });
 await test('missing original document preserves recovery and prevents online retargeting',async()=>{
  const b=browser({storage:new Map([[backupKey,JSON.stringify({sessionId:41,clientSessionKey:savedKey,pending:[pending]})]])});clock(b);const calls=[];
  b.window.fetch=async(url,init)=>{calls.push(url);return {ok:false,json:async()=>({ok:false,error:'not owned or deleted'})};};const api=owner(b);await flush();b.emit('online');await flush();assert.equal(calls.length,1);assert.equal(api.state.pending[0].text,'retained words');assert(api.state.recoveryBlocked);assert.equal(JSON.parse(b.storage.get(backupKey)).sessionId,41);
 });
 await test('lost start acknowledgement cannot append recovered words to an unrelated active document',async()=>{
  const b=browser({storage:new Map([[backupKey,JSON.stringify({sessionId:0,clientSessionKey:savedKey,pending:[pending]})]])});clock(b);const calls=[];b.window.fetch=async(_url,init)=>{calls.push(JSON.parse(init.body));return response({ok:true,session:{id:99,client_session_key:'c'.repeat(32),status:'active'}});};const api=owner(b);await flush();assert.equal(calls.length,1);assert(api.state.recoveryBlocked);assert.equal(api.state.pending.length,1);assert.equal(api.state.session.id,0);
 });
 await test('document selection cannot discard a pending failed-start queue',async()=>{
  const b=browser();clock(b);const api=owner(b);await flush();api.state.session={id:41,status:'starting',segments:[]};api.state.pending=[pending];b.emit('stonefellow:artist-listening-document-selected',{detail:{session:{id:99,status:'draft',segments:[]}}});assert.equal(api.state.session.id,41);assert.equal(api.state.pending.length,1);assert.equal(b.window.STONEFELLOW_ARTIST_LISTENING_V172.api.getState().pendingSegments,1);
 });
 await test('Stop finalization is serialized and stays bound to its document',async()=>{
  const b=browser();clock(b);const api=owner(b);await flush();const waiting=deferred();let count=0;b.window.fetch=(_url,init)=>{if(init.method==='POST'){count++;return waiting.promise;}return Promise.resolve(response({ok:true,sessions:[]}));};api.state.session={id:41,status:'active',segments:[]};api.state.pendingStop=true;const first=api.finalizeStop();await flush();await api.finalizeStop();assert.equal(count,1);b.emit('stonefellow:artist-listening-document-selected',{detail:{session:{id:99,status:'draft'}}});waiting.resolve(response({ok:true,session:{id:41,status:'draft',duration_ms:200,segments:[]}}));await first;assert.equal(api.state.session.id,41);assert.equal(api.state.pendingStop,false);
 });
 function workspace(b){b.window.STONEFELLOW_ARTIST_LISTENING_V172={endpoint:'/api/artist-listening-v172.php',csrf:'test',userId:1};load('artist-listening-workspace.js',b,'window.WorkspaceTest={state,openSession,showSplash,fillEditor,queueTitleSave,queueTranscriptSave,saveMetadata,transcriptionRenameDocument,loadLibrary};');const api=b.window.WorkspaceTest;api.state.workspace.querySelector=key=>b.element(key);return api;}
 const session=id=>({id,title:'Document '+id,status:'draft',tags:[],segments:[],continuous_text:'Text '+id});
 await test('late document loads including A-B-A cannot replace the latest selection',async()=>{
  const b=browser();clock(b);const requests=[];b.window.fetch=()=>{const d=deferred();requests.push(d);return d.promise;};const api=workspace(b),a=api.openSession(41),other=api.openSession(99),last=api.openSession(41);requests[2].resolve(response({ok:true,session:{...session(41),title:'latest A'}}));await last;requests[0].resolve(response({ok:true,session:{...session(41),title:'old A'}}));requests[1].resolve(response({ok:true,session:session(99)}));await Promise.all([a,other]);assert.equal(api.state.current.title,'latest A');
 });
 await test('a late save applies only to its original document and cannot force library navigation',async()=>{
  const b=browser();clock(b);const saving=deferred(),calls=[];b.window.fetch=(url,init)=>{calls.push(url);return init.method==='POST'?saving.promise:Promise.resolve(response({ok:true,session:session(99)}));};const api=workspace(b);api.fillEditor(session(41));const save=api.transcriptionRenameDocument('new A');await flush();await api.openSession(99);saving.resolve(response({ok:true,session:{...session(41),title:'new A'}}));await save;assert.equal(api.state.current.id,99);assert.equal(b.element('[data-listening-workspace-title]').value,'Document 99');assert.equal(calls.length,2);
 });
 await test('debounced text and title save the original document across navigation',async()=>{
  const b=browser(),timer=clock(b),writes=[];b.window.fetch=async(url,init)=>{if(init.method==='POST'){const data=JSON.parse(init.body);writes.push(data);return response({ok:true,session:session(data.session_id)});}return response({ok:true,session:session(99),sessions:[session(41),session(99)]});};const api=workspace(b);api.fillEditor(session(41));b.element('[data-listening-workspace-title]').value='A title edit';api.queueTitleSave();b.element('[data-listening-workspace-editor]').value='A text edit';api.queueTranscriptSave();await api.openSession(99);await timer.advance(2000);assert.equal(writes.length,2);assert(writes.every(w=>w.session_id===41));assert.equal(writes.find(w=>w.action==='rename').title,'A title edit');assert.equal(writes.find(w=>w.action==='replace_transcript').text,'A text edit');assert.equal(api.state.current.id,99);
 });
 await test('concurrent writes are sent in order and the first response cannot erase a newer edit',async()=>{
  const b=browser();clock(b);const first=deferred(),second=deferred(),writes=[];b.window.fetch=(_url,init)=>{writes.push(JSON.parse(init.body));return writes.length===1?first.promise:second.promise;};const api=workspace(b);api.fillEditor(session(41));const one=api.transcriptionRenameDocument('first');await flush();const two=api.transcriptionRenameDocument('second');await flush();assert.equal(writes.length,1);first.resolve(response({ok:true,session:{...session(41),title:'first'}}));await one;await flush();assert.equal(api.state.current.title,'Document 41');assert.equal(writes.length,2);api.state.selectionEpoch++;api.state.current=session(99);second.resolve(response({ok:true,session:{...session(41),title:'second'}}));await two;assert.equal(api.state.current.id,99);
 });
 await test('reload restores manual editor text only into its authenticated original document',async()=>{
  const b=browser({storage:new Map([['stonefellow:transcription-edit:1:41:text',JSON.stringify({value:'unsaved edit'})]])});clock(b);const api=workspace(b);api.fillEditor(session(99));assert.equal(b.element('[data-listening-workspace-editor]').value,'Text 99');api.fillEditor(session(41));assert.equal(b.element('[data-listening-workspace-editor]').value,'unsaved edit');assert.equal(api.state.pendingText.id,41);
 });
 function pages(b){b.window.STONEFELLOW_ARTIST_LISTENING_V172={endpoint:'/api/artist-listening-v172.php'};load('artist-listening-transcript.js',b,'window.PagesTest={state,loadManifest,loadContinuousPage,enterContinuous};');return b.window.PagesTest;}
 await test('late manifest cannot resurrect a cleared document or overwrite a newer same-document page',async()=>{
  const b=browser();clock(b);const jobs=[];b.window.fetch=()=>{const d=deferred();jobs.push(d);return d.promise;};const api=pages(b);b.emit('stonefellow:artist-listening-document-selected',{detail:{session:{id:41,transcript_page:1}}});b.emit('stonefellow:artist-listening-document-selected',{detail:{session:null}});jobs[0].resolve(response({ok:true,manifest:{page_count:1,title:'obsolete'}}));await flush();assert.equal(api.state.sessionId,0);assert.equal(api.state.manifest,null);api.state.sessionId=41;const one=api.loadManifest(41,1),two=api.loadManifest(41,2);jobs[2].resolve(response({ok:true,manifest:{page_count:2,pages:[]}}));await two;jobs[1].resolve(response({ok:true,manifest:{page_count:2,pages:[]}}));await one;assert.equal(api.state.page,2);
 });
 await test('late continuous page cannot append into another document or release its busy flag',async()=>{
  const b=browser();clock(b);const waiting=deferred();b.window.fetch=()=>waiting.promise;const api=pages(b);api.state.sessionId=41;api.state.manifest={page_count:3};api.state.view='continuous';const loading=api.loadContinuousPage(1);api.state.documentEpoch++;api.state.sessionId=99;api.state.continuousBusy=true;waiting.resolve(response({ok:true,page:{page_number:1,segments:[{segment_type:'transcript',transcript_text:'old text'}]}}));await loading;assert.equal(api.state.continuousLoaded,0);assert.equal(api.state.continuousBusy,true);assert(!b.element('[data-listening-transcript-continuous-pages]').innerHTML);
 });
 await test('separate tabs cannot overwrite another document recovery slot',async()=>{
  const storage=new Map(),a=browser({storage}),b=browser({storage});clock(a);clock(b);a.window.fetch=b.window.fetch=()=>new Promise(()=>{});const one=owner(a),two=owner(b);one.state.active=true;one.state.session={id:41,client_session_key:savedKey,status:'active',segments:[]};two.state.active=true;two.state.session={id:99,client_session_key:'c'.repeat(32),status:'active',segments:[]};one.nextSegment('transcript','A private text');two.nextSegment('transcript','B private text');const records=[...storage.values()].map(value=>JSON.parse(value));assert(records.some(value=>value.sessionId===41&&value.pending?.[0]?.text==='A private text'));assert(records.some(value=>value.sessionId===99&&value.pending?.[0]?.text==='B private text'));const exported=b.window.STONEFELLOW_ARTIST_LISTENING_V172.api.exportRecovery();assert.equal(exported.segments.length,2);assert(exported.segments.some(segment=>segment.sessionId===41));
 });
 await test('recovery rejects a reused numeric document id with a different original client key',async()=>{
  const b=browser({storage:new Map([[backupKey,JSON.stringify({sessionId:41,clientSessionKey:savedKey,pending:[pending]})]])});clock(b);let calls=0;b.window.fetch=async()=>{calls++;return response({ok:true,session:{id:41,client_session_key:'f'.repeat(32),status:'active',segments:[]}});};const api=owner(b);await flush();assert.equal(calls,1);assert(api.state.recoveryBlocked);assert.equal(api.state.pending.length,1);
 });
 await test('monotonic elapsed time survives wall-clock changes and is preserved on pagehide',async()=>{
  const b=browser();clock(b);let elapsed=2000;b.context.performance={now:()=>elapsed};const api=owner(b);await flush();api.state.session={id:41,client_session_key:savedKey,status:'active',segments:[]};api.state.active=true;api.state.captureStartedAt=1000;api.state.elapsedBeforeResume=5000;assert.equal(api.elapsedMs(),6000);elapsed=2500;b.emit('pagehide');assert.equal(api.elapsedMs(),6500);assert.equal(JSON.parse(b.storage.get(backupKey)).elapsedMs,6500);assert.equal(api.state.active,false);
 });
 await test('stalled document save releases its ordered queue at the deadline and retains recovery text',async()=>{
  const b=browser(),timer=clock(b);let signal;b.window.fetch=(_url,init)=>{signal=init.signal;return new Promise(()=>{});};const api=workspace(b);api.fillEditor(session(41));b.element('[data-listening-workspace-editor]').value='keep this edit';api.queueTranscriptSave();await timer.advance(1400);assert.equal(api.state.mutationChains.size,1);await timer.advance(30000);assert(signal.aborted);assert.equal(api.state.mutationChains.size,0);assert.equal(JSON.parse(b.storage.get('stonefellow:transcription-edit:1:41:text')).value,'keep this edit');assert(b.element('[data-listening-workspace-editor-state]').textContent.includes('failed'));
 });
}else{
 const id='a'.repeat(32),other='b'.repeat(32);
 function workspace(b){b.window.HomeServerDictation={stopTranscription(){},startTranscription:async()=>true};load('transcription-workspace.js',b,'window.Test={onSegment,refresh,open,saveQueue,finish,recoverQueue,start,share,state:()=>({active,selected,queue,finishSessionId}),seed:(id)=>{active={id,status:"active",timeline_ms:100,started_at:new Date().toISOString()};listening=true;captureSessionId=id;timelineBase=100;captureClock=1000;},select:value=>renderSession(value),changeActive:value=>{active=value;}};');return b.window.Test;}
 await test('reload restores the exact queue key and original session without starting capture',async()=>{
  const storage=new Map(),b=browser({storage});clock(b);b.window.fetch=()=>new Promise(()=>{});const api=workspace(b);api.seed(id);api.onSegment({detail:{text:'keep across reload',capturedAt:1010}});const key=api.state().queue[0].segment.client_key;
  const fresh=browser({storage}),timer=clock(fresh),writes=[];fresh.window.fetch=async(url,init)=>{if(init.method==='GET')return response({sessions:[{id:other,status:'active'}],session:{id:other,status:'active',segments:[]}});writes.push({url,body:JSON.parse(init.body)});return response({session:{id,segment_count:1}});};const recovered=workspace(fresh);await recovered.refresh();assert.equal(recovered.state().queue.length,1);await timer.advance(4000);assert.equal(writes[0].body.client_key,key);assert(writes[0].url.includes(id));assert.equal(recovered.state().queue.length,0);assert.equal(recovered.state().active.id,other);
 });
 await test('local timing uses capture time and session offset rather than delayed inference time',async()=>{
  const b=browser();clock(b);b.window.fetch=()=>new Promise(()=>{});b.context.performance={now:()=>9000};const api=workspace(b);api.seed(id);api.onSegment({detail:{text:'first',capturedAt:1200}});api.onSegment({detail:{text:'second',capturedAt:1100}});assert.deepEqual(Array.from(api.state().queue,x=>x.segment.started_ms),[300,300]);
 });
 await test('Stop freezes its session before awaiting a failed segment acknowledgement',async()=>{
  const b=browser();clock(b);const waiting=deferred(),paths=[];b.window.fetch=(url,init)=>{paths.push(url);return url.endsWith('/segments')?waiting.promise:Promise.resolve(response({sessions:[],session:{id,status:'completed',segments:[]}}));};const api=workspace(b);api.seed(id);api.onSegment({detail:{text:'last'}});const stopping=api.finish();api.changeActive({id:other,status:'active'});waiting.resolve(response({session:{id,segment_count:1}}));await stopping;assert(paths.includes('/api/v1/control/transcription-sessions/'+id+'/stop'));assert(!paths.includes('/api/v1/control/transcription-sessions/'+other+'/stop'));
 });
 await test('failed authenticated list cannot restore or transmit browser recovery',async()=>{
  const storage=new Map([['homeserver:transcription-outbox:v1:old',JSON.stringify({version:1,queue:[{sessionId:id,segment:{client_key:other,text:'private',started_ms:1}}]})]]),b=browser({storage});clock(b);let calls=0;b.window.fetch=async()=>{calls++;return {ok:false,json:async()=>({detail:'owner access required'})};};const api=workspace(b);await api.refresh();assert.equal(api.state().queue.length,0);assert.equal(calls,1);assert.equal(storage.size,1);
 });
 await test('late local document load is ignored after a newer selection',async()=>{
  const b=browser();clock(b);const requests=[];b.window.fetch=()=>{const d=deferred();requests.push(d);return d.promise;};const api=workspace(b),a=api.open(id),c=api.open(other);requests[1].resolve(response({session:{id:other,title:'latest',segments:[]}}));await c;requests[0].resolve(response({session:{id,title:'old',segments:[]}}));await a;assert.equal(api.state().selected.id,other);
 });
 await test('late Cloud-share acknowledgement cannot mix another document into the shared response',async()=>{
  const b=browser();clock(b);b.window.confirm=()=>true;const waiting=deferred();b.window.fetch=(_url,init)=>init.method==='PUT'?waiting.promise:Promise.resolve(response({sessions:[]}));const api=workspace(b);api.select({id,status:'completed',segments:[{text:'A'}]});const sharing=api.share();await api.open(other).catch(()=>{});api.select({id:other,status:'completed',segments:[{text:'B'}]});waiting.resolve(response({session:{id,status:'completed',cloud_shared:true}}));await sharing;assert.notEqual(api.state().selected?.id,id);
 });
 await test('backup quota failure preserves accepted text and explicitly releases capture',async()=>{
  const b=browser();clock(b);b.context.localStorage.setItem=()=>{throw Error('quota');};b.window.fetch=()=>new Promise(()=>{});const api=workspace(b);let stops=0;b.window.HomeServerDictation.stopTranscription=()=>stops++;api.seed(id);api.onSegment({detail:{text:'retained'}});assert(stops>0);assert.equal(api.state().queue[0].segment.text,'retained');assert(b.element('hsTranscriptStatus').textContent.includes('storage is full'));
 });
}
console.log(`TRANSCRIPTION_SECTION4=PASS (${checks.length} behavioral cases, ${cloud?'Cloud':'HomeServer'})`);
