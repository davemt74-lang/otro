// Real Chromium IndexedDB acceptance. Run with pinned Playwright in CI.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import http from 'node:http';
import {chromium} from 'playwright';
const root=process.cwd();
const server=http.createServer(async(req,res)=>{
 try {
  const name=new URL(req.url,'http://localhost').pathname;
  if(name==='/'){res.setHeader('Content-Type','text/html');res.end('<!doctype html><title>Local participant storage acceptance</title>');return;}
  const file=path.resolve(root,'.'+decodeURIComponent(name));
  if(!file.startsWith(root+path.sep))throw Error('outside root');
  res.setHeader('Content-Type','text/javascript');res.end(await fs.readFile(file));
 } catch(_){res.writeHead(404);res.end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
let browser;
try {
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage();
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(`http://127.0.0.1:${server.address().port}/`);
 const cases=await page.evaluate(async()=>{
  const s=await import('/ui/tracky/src/participant-store.js');
  const checks=[];const ok=(v,name)=>{if(!v)throw Error(name);checks.push(name);};
  await s.saveParticipant({id:'a',name:'Same',notes:'initial'});
  await Promise.all([s.patchParticipant('a',{nickname:'updated'}),s.patchParticipant('a',{notes:'new notes'})]);
  let a=await s.getParticipant('a');ok(a.nickname==='updated'&&a.notes==='new notes','concurrent patches preserve independent changes');
  await s.deleteParticipant('a');let rejected=false;try{await s.patchParticipant('a',{name:'revive'});}catch(_){rejected=true;}
  ok(rejected&&!await s.getParticipant('a'),'late patch cannot resurrect deleted profile');
  for(let i=0;i<12;i++){
   await s.saveParticipant({id:'race',name:'Race'});
   await Promise.allSettled([s.patchParticipant('race',{notes:'late'}),s.deleteParticipant('race')]);
   if(await s.getParticipant('race'))throw Error('concurrent deletion was resurrected');
  }
  checks.push('concurrent deletion and patch leave no resurrected profile');
  await s.saveParticipant({id:'b',name:'Same'});await s.saveParticipant({id:'c',name:'Same'});
  await s.saveDialogueTurn({id:'turn',participantId:'b',nearbyParticipantIds:['b','c'],nearbyParticipantNames:['wrong','wrong'],text:'hello'});
  let turns=await s.listDialogueTurns();ok(turns[0].nearbyParticipantNames.join(',')==='Same,Same','nearby names resolve from current profiles');
  await s.saveDialogueTurn({id:'other',participantId:'c',nearbyParticipantIds:['b','c'],text:'other'});
  await s.deleteParticipant('b');turns=await s.listDialogueTurns();
  ok(turns.length===1&&turns[0].id==='other'&&turns[0].nearbyParticipantIds.join(',')==='c'&&turns[0].nearbyParticipantNames.join(',')==='Same','deletion preserves a different participant with the same name');
  ok(await s.saveDialogueTurn({id:'late-turn',participantId:'b',text:'late'})===null&&!(await s.listDialogueTurns()).some(t=>t.id==='late-turn'),'delayed dialogue cannot reintroduce a deleted participant');
  await Promise.all([s.saveDialogueTurn({id:'race-turn',participantId:'c',text:'race'}),s.deleteParticipant('c')]);
  ok(!(await s.listDialogueTurns()).some(t=>t.participantId==='c'),'dialogue write and deletion share one transaction boundary');
  await s.saveParticipant({id:'voice',name:'Voice',notes:'unchanged'});
  const cancelled=await s.patchParticipant('voice',{notes:'must not commit'},()=>false).then(()=>false,e=>e.name==='AbortError');
  ok(cancelled&&(await s.getParticipant('voice')).notes==='unchanged','cancelled guarded patch does not commit');
  let guardCalls=0;
  const aborted=await s.patchParticipant('voice',{notes:'must roll back'},()=>++guardCalls===1).then(()=>false,e=>e.name==='AbortError');
  ok(aborted&&guardCalls===2&&(await s.getParticipant('voice')).notes==='unchanged','cancellation after put rolls back the real IndexedDB transaction');
  await Promise.all([s.patchParticipant('voice',p=>({notes:p.notes+' A'})),s.patchParticipant('voice',p=>({notes:p.notes+' B'}))]);
  ok((await s.getParticipant('voice')).notes==='unchanged A B','functional patches use the current transactional record');

  document.body.innerHTML='<form id="chatForm"><div class="chat-voice-options"></div></form>';
  window.HomeServerSpeakerFusion={profileSummary:async()=>[{id:'voice',name:'Voice',voiceSamples:0}],
   beginVoiceEnrollment(){},clearVoiceProfile(){},isCameraActive:()=>false};
  window.fetch=async()=>({ok:true,json:async()=>({sessions:[],speaker_diarization:{available:false}})});
  await import('/ui/transcription-workspace.js');
  document.getElementById('hsTranscriptOpen').click();
  for(let attempt=0;attempt<20&&!document.querySelector('#hsVoiceParticipant option[value="voice"]');attempt++)await new Promise(r=>setTimeout(r,0));
  const picker=document.getElementById('hsVoiceParticipant');
  picker.value='voice';picker.dispatchEvent(new Event('change'));
  ok(!document.getElementById('hsVoiceEnroll').disabled&&!document.getElementById('hsVoiceClear').disabled,'participant selection immediately enables enrollment and clearing');
  picker.value='';picker.dispatchEvent(new Event('change'));
  ok(document.getElementById('hsVoiceEnroll').disabled&&document.getElementById('hsVoiceClear').disabled,'clearing selection immediately disables enrollment and clearing');
  return checks;
 });
 assert.deepEqual(errors,[]);
 for(const name of cases)console.log('PASS '+name);
 console.log(`PARTICIPANTS_STORE_SECTION5=PASS (${cases.length} real Chromium IndexedDB cases)`);
} finally {if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
