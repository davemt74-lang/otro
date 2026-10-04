// Production fusion module with deterministic media/model/DB boundaries.
// Run: node --experimental-vm-modules tests/speaker_fusion_lifecycle_section9.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {participantRecord} from '../ui/tracky/src/participant-core.js';

const source=fs.readFileSync(new URL('../ui/tracky/speaker-fusion.js',import.meta.url),'utf8');
const vector=new Array(32).fill(0.1);
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
const tick=()=>new Promise(r=>setImmediate(r));
function wav(){
 const bytes=new ArrayBuffer(44+32000),v=new DataView(bytes);
 for(const [at,value] of [[0,0x46464952],[4,32036],[8,0x45564157],[12,0x20746d66],[16,16],[24,16000],[28,32000],[36,0x61746164],[40,32000]])v.setUint32(at,value,true);
 for(const [at,value] of [[20,1],[22,1],[32,2],[34,16]])v.setUint16(at,value,true);
 return {arrayBuffer:async()=>bytes};
}
async function runtime(options={}){
 let rows=[{id:'owner',name:'Owner',recognitionEnabled:true,embeddings:[vector,vector,vector],voiceEmbeddings:[vector,vector],voiceProfileReady:false,voiceRecognitionEnabled:false}];
 let stopped=0,writes=0,privacyChecks=0,faces=options.faces||[];
 const events=[],handlers=new Map(),docHandlers=new Map();
 const listen=(map,name,fn)=>{const list=map.get(name)||[];list.push(fn);map.set(name,list);};
 const stop=()=>{stopped++;};
 const stream={getTracks:()=>[{stop,addEventListener(){}}]};
 const video={play:()=>options.play?options.play():Promise.resolve()};
 const context=vm.createContext({Date,Map,Array,Math,Number,String,Boolean,Float32Array,DataView,Promise,Infinity,
  performance:{now:()=>1000},setTimeout,clearTimeout,
  CustomEvent:class{constructor(type,config={}){this.type=type;this.detail=config.detail;}},
  window:{isSecureContext:true,dispatchEvent:event=>{events.push(event);},addEventListener:(name,fn)=>listen(handlers,name,fn)},
  document:{hidden:false,addEventListener:(name,fn)=>listen(docHandlers,name,fn),createElement:()=>video},
  navigator:{mediaDevices:{getUserMedia:async()=>{if(options.permission)await options.permission.promise;return stream;}}},
  fetch:async()=>({ok:true,json:async()=>({privacy_engaged:options.privacy?options.privacy(++privacyChecks):false})})});
 const values={
  './src/participant-core.js':{bestParticipantMatch:()=>({matched:true,participant:rows[0],similarity:.95})},
  './src/participant-store.js':{
   listParticipants:async()=>{if(options.listGate)await options.listGate.promise;return rows;},
   getParticipant:async()=>rows[0],
   patchParticipant:async(id,patch,guard=()=>true)=>{
    if(typeof patch==='function'&&options.writeGate)await options.writeGate.promise;
    if(!guard()){const error=new Error('cancelled');error.name='AbortError';throw error;}
    const current=rows[0];const changes=typeof patch==='function'?patch(current):patch;
    rows=[participantRecord({...current,...changes})];writes++;return rows[0];
   }},
  './src/model-config.js':{HUMAN_ESM_URL:'human-fixture',HUMAN_MODEL_BASE:'fixture'},
  './src/voice-profile-core.js':{VOICE_FEATURE_VERSION:'tracky-acoustic-v1',VOICE_PROFILE_MIN_SAMPLES:3,VOICE_PROFILE_MAX_SAMPLES:12,
   voiceEmbeddingFromPcm:()=>vector,bestVoiceParticipantMatch:(_,participants)=>({matched:true,participant:participants[0],similarity:.95})},
  'human-fixture':{Human:class{async load(){}async detect(){return {face:faces};}}}};
 const modules=new Map();
 async function link(name){
  if(modules.has(name))return modules.get(name);
  const exports=values[name];assert.ok(exports,name);
  const module=new vm.SyntheticModule(Object.keys(exports),function(){for(const [key,value] of Object.entries(exports))this.setExport(key,value);},{context});
  modules.set(name,module);await module.link(()=>{});await module.evaluate();return module;
 }
 const module=new vm.SourceTextModule(source,{context,importModuleDynamically:link});
 await module.link(link);await module.evaluate();
 return {api:module.namespace,events,context,rows:()=>rows,writes:()=>writes,stopped:()=>stopped,
  fire(name,documentEvent=false){for(const fn of (documentEvent?docHandlers:handlers).get(name)||[])fn();}};
}
const turn={text:'Solo speech',speaker_label:'Speaker 1',started_ms:0,ended_ms:1000,overlap:false};
const cases=[];
async function check(name,run){await run();cases.push(name);console.log('PASS '+name);}

await check('preview failure stops all camera tracks and clears active state',async()=>{
 const r=await runtime({play:()=>Promise.reject(Error('preview failed'))});
 await assert.rejects(r.api.startCameraCorroboration(),/preview failed/);
 assert.equal(r.api.isCameraActive(),false);assert.ok(r.stopped()>0);
});
await check('Stop during camera permission acquisition discards the late stream',async()=>{
 const permission=deferred(),r=await runtime({permission});
 const pending=r.api.startCameraCorroboration();await tick();
 assert.equal(r.api.isCameraActive(),true);r.api.stopCameraCorroboration();permission.resolve();
 assert.equal(await pending,false);assert.equal(r.api.isCameraActive(),false);assert.ok(r.stopped()>0);
 assert.ok(!r.events.some(e=>e.detail?.state==='camera_ready'));
});
await check('Stop during preview startup cannot restart camera inference',async()=>{
 const play=deferred(),r=await runtime({play:()=>play.promise});
 const pending=r.api.startCameraCorroboration();await tick();r.api.stopCameraCorroboration();play.resolve();
 assert.equal(await pending,false);assert.equal(r.api.isCameraActive(),false);
 assert.ok(!r.events.some(e=>e.detail?.state==='camera_ready'));
});
await check('privacy is rechecked after permission acquisition',async()=>{
 const r=await runtime({privacy:count=>count>1});
 await assert.rejects(r.api.startCameraCorroboration(),/privacy is engaged/);
 assert.equal(r.api.isCameraActive(),false);assert.ok(r.stopped()>0);
});
await check('cancelling an awaited enrollment write prevents profile re-enabling',async()=>{
 const writeGate=deferred(),r=await runtime({writeGate});await r.api.beginVoiceEnrollment('owner');
 const pending=r.api.analyzeChunk(wav(),[turn],0);await tick();r.api.cancelVoiceEnrollment();writeGate.resolve();await pending;
 assert.equal(r.writes(),0);assert.equal(r.rows()[0].voiceEmbeddings.length,2);assert.equal(r.api.enrollmentState(),null);
});
await check('clearing a profile cannot be undone by an older enrollment write',async()=>{
 const writeGate=deferred(),r=await runtime({writeGate});await r.api.beginVoiceEnrollment('owner');
 const pending=r.api.analyzeChunk(wav(),[turn],0);await tick();await r.api.clearVoiceProfile('owner');writeGate.resolve();await pending;
 assert.equal(r.writes(),1);assert.equal(r.rows()[0].voiceEmbeddings.length,0);assert.equal(r.rows()[0].voiceRecognitionEnabled,false);
});
await check('page hide cancels enrollment before a delayed write commits',async()=>{
 const writeGate=deferred(),r=await runtime({writeGate});await r.api.beginVoiceEnrollment('owner');
 const pending=r.api.analyzeChunk(wav(),[turn],0);await tick();r.fire('pagehide');writeGate.resolve();await pending;
 assert.equal(r.writes(),0);assert.equal(r.api.enrollmentState(),null);
});
await check('overlap on base attribution blocks enrollment and voice evidence',async()=>{
 const r=await runtime();await r.api.beginVoiceEnrollment('owner');
 const output=await r.api.analyzeChunk(wav(),[{...turn,attribution:{source:'provider_diarization',overlap:true}}],0);
 assert.equal(r.writes(),0);assert.ok(!output[0].speaker_evidence.some(e=>e.source==='verified_voice'));
});
await check('ambiguous camera view preserves valid voice evidence without a visual row',async()=>{
 const r=await runtime({faces:[{embedding:vector},{embedding:vector}]});await r.api.startCameraCorroboration();await tick();
 const output=await r.api.analyzeChunk(wav(),[turn],0);r.api.stopCameraCorroboration();
 assert.ok(output[0].speaker_evidence.some(e=>e.source==='verified_voice'));
 assert.ok(!output[0].speaker_evidence.some(e=>e.source==='visual_corroboration'));
});
await check('one unambiguous camera match emits a valid local corroboration row',async()=>{
 const r=await runtime({faces:[{embedding:vector}]});await r.api.startCameraCorroboration();await tick();
 const output=await r.api.analyzeChunk(wav(),[turn],0);r.api.stopCameraCorroboration();
 const visual=output[0].speaker_evidence.find(e=>e.source==='visual_corroboration');
 assert.equal(visual?.participant_identity,'tracky:owner');assert.equal(visual?.confidence,.95);
});
console.log(`SPEAKER_FUSION_LIFECYCLE_SECTION9C=PASS (${cases.length} behavioral cases)`);
