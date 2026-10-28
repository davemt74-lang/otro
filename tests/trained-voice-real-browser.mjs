// Real pinned ONNX/WASM inference in Chromium. No mocked model or network inference.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
const {chromium}=createRequire(import.meta.url)(process.env.VP3_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(fs.existsSync('voice-trained-core.js')?'.':'ui');
const core=await import(pathToFileURL(path.join(root,'voice-trained-core.js')));
const local=process.env.VP3_VOICE_MODEL_FIXTURES;
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'vp3-real-voice-'));
const fixtureRevision='542437c29cc5273cfd29e25c77696f2b349badd7';
const fixtures=[
 ['sv_speaker-1_1.wav','c65258289d72485624a5ea06b0defd2c4012e672ed83198a327c3f5fc6a1f42b'],
 ['sv_speaker-1_2.wav','a4e032b319a4ac5933a29a090ad23431f334528564eb381017da2692e56ec45f'],
 ['sv_speaker-2_1.wav','20d3a722fecc2090ba5bc90fdfe7e4f3863b3695247b5ffc25f047a7e25d077d'],
 ['sv_speaker-2_2.wav','9a2e91feae8d4b39a1c07572b852ccdec38707a0b8b13d3159cf957304a4eaf6'],
 ['jfk.wav','aa81c2552465568567e670f3823117e633900d16bd6202346a72f3c8464c74c8'],
];
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
const assets=new Map();
async function obtain(url,file,expected,maxBytes){
 if(fs.existsSync(file)){const b=fs.readFileSync(file);assert.equal(sha(b),expected);return file;}
 let r;
 for(let attempt=0;attempt<3;attempt++){
  try{r=await fetch(url,{signal:AbortSignal.timeout(120000)});if(r.ok)break;if(r.status!==429&&r.status<500)break;}catch(error){if(attempt===2)throw error;}
  await new Promise(resolve=>setTimeout(resolve,1000*(attempt+1)));
 }
 assert.ok(r?.ok,'Fixture/model download failed: '+r?.status);
 const chunks=[];let count=0;for await(const chunk of r.body){count+=chunk.length;assert.ok(count<=maxBytes,'Downloaded asset exceeds its pinned size');chunks.push(chunk);}
 const bytes=Buffer.concat(chunks);assert.equal(sha(bytes),expected);
 fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,bytes);return file;
}
for(const f of core.ASSET_FILES){
 const runtime=f.name.startsWith('runtime/');
 const url=runtime?new URL(f.name.slice(8),core.SDK).href:`https://huggingface.co/${core.MODEL}/resolve/${core.REVISION}/${f.name}`;
 const file=local?(runtime?path.join(local,'node_modules/@huggingface/transformers/dist',f.name.slice(8)):path.join(local,'models/wavlm',f.name)):path.join(temp,'model',f.name);
 assets.set(f.name,await obtain(url,file,f.sha256,f.bytes));
}
for(const [name,hash] of fixtures){const file=local?path.join(local,'audio',name):path.join(temp,'audio',name);assets.set('audio/'+name,await obtain(`https://huggingface.co/datasets/Xenova/transformers.js-docs/resolve/${fixtureRevision}/${name}`,file,hash,4000000));}
let offline=false;const requests=[];
const server=http.createServer((req,res)=>{
 requests.push({url:req.url,method:req.method});let file;
 if(req.url.startsWith('/api/v1/control/voice/profile-model/')){if(offline){res.writeHead(503);res.end();return;}file=assets.get(req.url.split('/profile-model/')[1]);}
 else if(req.url.startsWith('/audio/'))file=assets.get(req.url.slice(1));
 else if(req.url.startsWith('/assets/')){file=path.join(root,path.basename(req.url.split('?')[0]));if(!fs.existsSync(file)&&file.endsWith('voice-profile-core.js'))file=path.join(root,'tracky/src/voice-profile-core.js');}
 else{res.setHeader('Content-Type','text/html');res.end('<!doctype html><title>Trained speaker acceptance</title><button id="stop">Stop</button>');return;}
 if(!file||!fs.existsSync(file)){res.writeHead(404);res.end();return;}
 res.setHeader('Content-Type',file.endsWith('.js')||file.endsWith('.mjs')?'text/javascript':file.endsWith('.wasm')?'application/wasm':file.endsWith('.json')?'application/json':'application/octet-stream');res.setHeader('Content-Length',fs.statSync(file).size);fs.createReadStream(file).pipe(res);
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));let browser,checks=0;
const test=async(name,fn)=>{await fn();checks++;console.log('PASS '+name);};
try{
 browser=await chromium.launch({headless:true,args:['--no-sandbox']});const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);
 await page.evaluate(async()=>{
  window.microphones=0;navigator.mediaDevices.getUserMedia=async()=>{microphones++;throw Error('This test must not capture a microphone.');};
  window.lib=await import('/assets/voice-trained-client.js');window.client=lib.createTrainedVoiceClient();
  window.readPcm=async name=>{const b=await(await fetch('/audio/'+name)).arrayBuffer(),v=new DataView(b);let at=12,rate,channels,bits,start,size;
   while(at+8<=b.byteLength){const id=v.getUint32(at,true),length=v.getUint32(at+4,true);if(id===0x20746d66){channels=v.getUint16(at+10,true);rate=v.getUint32(at+12,true);bits=v.getUint16(at+22,true);}if(id===0x61746164){start=at+8;size=length;break;}at+=8+length+(length%2);}
   if(bits!==16||!rate||!start)throw Error('Invalid public audio fixture.');const frames=Math.min(size/2/channels,rate*8),pcm=new Float32Array(frames);
   for(let i=0;i<frames;i++)for(let c=0;c<channels;c++)pcm[i]+=v.getInt16(start+2*(i*channels+c),true)/32768/channels;
   return {pcm,rate};};
 });
 await test('optional install verifies the actual model and runtime without microphone capture',async()=>{await page.evaluate(()=>client.install('homeserver'));assert.equal(await page.evaluate(()=>microphones),0);assert.equal(await page.evaluate(()=>client.isReady()),true);});
 await test('real model returns bounded 512-dimensional embeddings for two speakers',async()=>{
  const rows=await page.evaluate(async()=>{window.rows=[];for(const name of ['sv_speaker-1_1.wav','sv_speaker-1_2.wav','sv_speaker-2_1.wav','sv_speaker-2_2.wav','jfk.wav']){const {pcm,rate}=await readPcm(name),r=await client.extract(pcm,rate);rows.push({name,...r});}return rows.map(r=>({name:r.name,dimensions:r.embedding?.length,elapsedMs:r.elapsedMs,quality:r.quality}));});
  for(const r of rows){assert.equal(r.dimensions,512);assert.equal(r.quality.ok,true);assert.ok(r.elapsedMs<8000);}console.log('REAL_MODEL_TIMING '+JSON.stringify(rows));
 });
 await test('held-out recordings match their own speaker and reject an unrelated speaker',async()=>{const result=await page.evaluate(async()=>{const core=await import('/assets/voice-trained-core.js');const p=(id,row)=>({id,voiceTrainedProfile:{feature:core.FEATURE,revision:core.REVISION,consentedAt:new Date().toISOString(),embeddings:[row.embedding,row.embedding,row.embedding]}}),people=[p('one',rows[0]),p('two',rows[2])];return [core.profileMatch(rows[1].embedding,people),core.profileMatch(rows[3].embedding,people),core.profileMatch(rows[4].embedding,people)].map(m=>({id:m.participant?.id,matched:m.matched,similarity:m.similarity}));});assert.equal(result[0].id,'one');assert.equal(result[1].id,'two');assert.equal(result[2].matched,false);console.log('REAL_MODEL_MATCHES '+JSON.stringify(result));});
 await page.evaluate(async()=>{
  const core=await import('/assets/voice-trained-core.js');window.trainedCore=core;
  const person=(id,row)=>({id,name:id,notes:'Keep ordinary notes',voiceEmbeddings:[[1,0,0]],voiceTrainedProfile:{feature:core.FEATURE,revision:core.REVISION,consentedAt:new Date().toISOString(),embeddings:[row.embedding,row.embedding,row.embedding]}});
  window.people=[person('one',rows[0]),person('two',rows[2]),{id:'new',name:'New participant',notes:'Keep ordinary notes',voiceEmbeddings:[[1,0,0]],voiceTrainedProfile:null}];
  window.saves=0;window.events=[];
  window.VP3_ORB_ADAPTER={ownerKey:'real-model',voiceFeatureUrl:'/assets/voice-profile-core.js',listen:()=>{},loadVoiceProfiles:async()=>structuredClone(people),saveTrainedVoiceSample:async({id,embedding,isCurrent})=>{if(!isCurrent())throw Error('Cancelled');const p=people.find(p=>p.id===id);p.voiceTrainedProfile={feature:core.FEATURE,revision:core.REVISION,consentedAt:new Date().toISOString(),embeddings:[...(p.voiceTrainedProfile?.embeddings||[]),embedding].slice(-5)};saves++;return {...p,voiceEmbeddings:p.voiceTrainedProfile.embeddings,voiceProfileReady:p.voiceTrainedProfile.embeddings.length>=3};},recordEyes:async e=>events.push(e)};
  window.VP3TrainedVoice={enabled:()=>true,extract:(...args)=>client.extract(...args),match:core.profileMatch,readyProfile:p=>(core.cleanProfile(p.voiceTrainedProfile)?.embeddings.length||0)>=3,clear:()=>client.cancelInference()};
  document.body.insertAdjacentHTML('beforeend','<div class="vp3-eyes-content"></div>');await new Promise((resolve,reject)=>{const s=document.createElement('script');s.src='/assets/agent-voice-guard.js';s.onload=resolve;s.onerror=reject;document.head.append(s);});
  const context={sampleRate:16000,destination:{},createMediaStreamSource:()=>({connect(){},disconnect(){}}),createBiquadFilter:()=>({frequency:{},connect(){},disconnect(){}}),createGain:()=>({gain:{},connect(){},disconnect(){}}),createScriptProcessor:()=>window.processor={connect(){},disconnect(){}}};VP3VoiceGuard.attach({},context);
  window.feed=async(name,double=false)=>{const {pcm,rate}=await readPcm(name);let data=core.resample16k(pcm,rate);if(double){const joined=new Float32Array(data.length*2);joined.set(data);joined.set(data,data.length);data=joined;}for(let at=0;at<data.length;at+=2048)processor.onaudioprocess({inputBuffer:{getChannelData:()=>data.slice(at,at+2048)}});};
 });
 await test('the existing guard routes a real trained match into one-use conversational context',async()=>{const r=await page.evaluate(async()=>{await feed('sv_speaker-1_2.wav');const result=await VP3VoiceGuard.check('Current known speaker');return {result,first:VP3VoiceGuard.takeContext('Current known speaker'),second:VP3VoiceGuard.takeContext('Current known speaker')};});assert.equal(r.result.participant.participant_id,'one');assert.equal(r.first.authentication_authority,false);assert.equal(r.second,null);});
 await test('a slow unknown trained match cannot fall back to a typed participant selection',async()=>{
  await page.evaluate(async()=>{const box=document.querySelector('[data-voice-known]');box.checked=true;box.dispatchEvent(new Event('change'));});await page.waitForFunction(()=>VP3VoiceGuard.isKnownOnly());
  const r=await page.evaluate(async()=>{await feed('jfk.wav');const task=VP3VoiceGuard.check('An unrelated speaker'),clock=Date.now;Date.now=()=>clock()+6000;try{return {result:await task,voiceTurn:VP3VoiceGuard.wasVoiceTurn('An unrelated speaker'),context:VP3VoiceGuard.takeContext('An unrelated speaker')};}finally{Date.now=clock;}});
  assert.equal(r.result.allowed,false);assert.equal(r.voiceTurn,true);assert.equal(r.context,null);
 });
 await test('explicit trained enrollment saves three model embeddings without replacing legacy features',async()=>{const r=await page.evaluate(async()=>{await VP3VoiceGuard.beginEnrollment('new');for(let i=0;i<3;i++){await feed('sv_speaker-1_1.wav',true);await VP3VoiceGuard.check('Enrollment phrase '+i);}return {saves,person:people.find(p=>p.id==='new')};});assert.equal(r.saves,3);assert.equal(r.person.voiceTrainedProfile.embeddings.length,3);assert.deepEqual(r.person.voiceEmbeddings,[[1,0,0]]);assert.equal(r.person.notes,'Keep ordinary notes');});
 await test('silence, clipping and caller-reported overlap produce no embedding',async()=>{const rejected=await page.evaluate(async()=>{const {pcm,rate}=await readPcm('sv_speaker-1_1.wav');return [await client.extract(new Float32Array(48000),16000),await client.extract(new Float32Array(48000).fill(1),16000),await client.extract(pcm,rate,{overlap:true})].map(r=>!r.embedding&&!r.quality.ok);});assert.ok(rejected.every(Boolean));});
 await test('Stop cancels inference immediately without late completion or another microphone',async()=>{const result=await page.evaluate(async()=>{const {pcm,rate}=await readPcm('sv_speaker-1_1.wav');const at=performance.now(),p=client.extract(pcm,rate).then(()=>false,()=>true);client.cancelInference();return {cancelled:await p,ms:performance.now()-at,microphones};});assert.equal(result.cancelled,true);assert.ok(result.ms<500);assert.equal(result.microphones,0);});
 await test('worker restart uses verified cached assets while the model server is offline',async()=>{offline=true;const before=requests.length;const r=await page.evaluate(async()=>{const {pcm,rate}=await readPcm('sv_speaker-1_2.wav');return client.extract(pcm,rate);});assert.equal(r.embedding.length,512);assert.equal(requests.slice(before).some(r=>r.url.startsWith('/api/')),false);});
 await page.evaluate(async()=>{client.cancel();delete window.VP3TrainedVoice;Object.assign(VP3_ORB_ADAPTER,{trainedVoiceUrl:'/assets/voice-trained-client.js',trainedVoiceSource:'homeserver',installTrainedVoice:async()=>{if(window.deferInstall)await new Promise(r=>window.finishInstall=r);}});await new Promise((resolve,reject)=>{const s=document.createElement('script');s.src='/assets/agent-trained-voice.js';s.onload=resolve;s.onerror=reject;document.head.append(s);});});
 await test('the actual optional component UI enables cached inference without starting capture',async()=>{await page.locator('.vp3-trained-voice summary').click();await page.locator('[data-trained-install]').click();await page.waitForFunction(()=>VP3TrainedVoice.enabled(),{},{timeout:60000});assert.equal(await page.evaluate(()=>microphones),0);assert.equal(await page.evaluate(()=>localStorage.getItem('vp3:trained-voice:real-model')),'enabled');});
 await test('Disable during installation prevents a late operation from enabling matching',async()=>{await page.locator('[data-trained-disable]').click();await page.evaluate(()=>window.deferInstall=true);await page.locator('[data-trained-install]').click();await page.waitForFunction(()=>typeof finishInstall==='function');await page.locator('[data-trained-disable]').click();await page.evaluate(()=>finishInstall());await page.waitForFunction(()=>!document.querySelector('[data-trained-install]').disabled);assert.equal(await page.evaluate(()=>VP3TrainedVoice.enabled()),false);assert.equal(await page.evaluate(()=>localStorage.getItem('vp3:trained-voice:real-model')),null);});
 await test('a corrupted runtime is refused before execution and install repairs only that file',async()=>{
  const failed=await page.evaluate(async()=>{const cache=await caches.open('vp3-trained-voice-'+trainedCore.FEATURE),request=(await cache.keys()).find(r=>r.url.endsWith('/runtime/transformers.min.js')),old=await cache.match(request),bytes=new Uint8Array(await old.arrayBuffer());bytes[0]^=1;await cache.put(request,new Response(bytes,{headers:old.headers}));window.repairClient=lib.createTrainedVoiceClient();return repairClient.prepare().then(()=>false,error=>/checksum/.test(error.message));});
  assert.equal(failed,true);offline=false;const before=requests.length;await page.evaluate(()=>repairClient.install('homeserver'));const repaired=requests.slice(before).filter(r=>r.url.startsWith('/api/')).map(r=>r.url);assert.ok(repaired.length<=1&&repaired.every(url=>url==='/api/v1/control/voice/profile-model/runtime/transformers.min.js'));assert.equal(await page.evaluate(()=>repairClient.isReady()),true);await page.evaluate(()=>repairClient.cancel());
 });
 await test('a stalled worker deadline rejects promptly and terminates the request',async()=>{const result=await page.evaluate(async()=>{const original=setTimeout;window.setTimeout=(fn,ms,...args)=>original(fn,ms===30000?10:ms,...args);const c=lib.createTrainedVoiceClient(),at=performance.now();try{const timedOut=await c.prepare().then(()=>false,error=>/timed out/.test(error.message));return {timedOut,ms:performance.now()-at};}finally{window.setTimeout=original;c.cancel();}});assert.equal(result.timedOut,true);assert.ok(result.ms<500);});
 await test('model processing sends no audio POST or participant payload',async()=>{assert.equal(requests.some(r=>r.method!=='GET'),false);assert.equal(await page.evaluate(()=>microphones),0);assert.deepEqual(errors,[]);});
 console.log(`TRAINED_VOICE_REAL_BROWSER=PASS (${checks} cases)`);
}finally{if(browser)await browser.close();await new Promise(r=>server.close(r));fs.rmSync(temp,{recursive:true,force:true});}
