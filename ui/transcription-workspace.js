/* Cloud-compatible local transcription canvas, built on existing Dictate capture. */
(()=>{
'use strict';
const base='/api/v1/control/transcription-sessions';
let active=null,selected=null,queue=[],saving=null,retryTimer=null,finishRequested=false,listening=false,finishing=false,drawer=null,selectionEpoch=0,refreshEpoch=0,starting=false,finishSessionId=null,captureSessionId=null,timelineBase=0,captureClock=0,lastTimeline=0,recovered=false;
const recoveryPrefix="homeserver:transcription-outbox:v1:";
const outboxKey=recoveryPrefix+crypto.randomUUID();
const validId=value=>typeof value==="string"&&/^[0-9a-f]{32}$/.test(value);
const $=id=>document.getElementById(id);
const labels={};
async function request(path='',method='GET',body){
 const controller=new AbortController();let timer;
 try{return await Promise.race([(async()=>{
  const r=await fetch(base+path,{method,signal:controller.signal,credentials:'same-origin',cache:'no-store',
    headers:{Accept:'application/json',...(method==='GET'?{}:{'X-Requested-With':'XMLHttpRequest','Content-Type':'application/json'})},
    ...(body?{body:JSON.stringify(body)}:{})});
  const j=await r.json();if(!r.ok)throw new Error(j.detail||'Transcription request failed.');return j;
 })(),new Promise((_,reject)=>{timer=setTimeout(()=>{controller.abort();reject(Error('Transcription save timed out.'));},30000);})]);}
 finally{clearTimeout(timer);}
}
function persistQueue(){
 try{
  if(queue.length||finishSessionId)localStorage.setItem(outboxKey,JSON.stringify({version:1,queue,finishSessionId}));
  else localStorage.removeItem(outboxKey);
  return true;
 }catch(e){
  status('Browser recovery storage is full. Keep this page open or export unsaved text.',true);
  if(listening)window.HomeServerDictation?.stopTranscription();
  return false;
 }
}
function recoverQueue(){
 if(recovered&&(queue.length||finishSessionId))return;recovered=true;
 // Called only after the owner-authenticated list succeeds. Never start a microphone.
 for(let i=0;i<localStorage.length;i++){
  const key=localStorage.key(i);if(!key?.startsWith(recoveryPrefix)||key===outboxKey)continue;
  const raw=localStorage.getItem(key);let backup;
  try{backup=JSON.parse(raw);}catch(e){continue;}
  if(backup?.version!==1||!Array.isArray(backup.queue)||backup.queue.length>200)continue;
  if(backup.queue.some(x=>!validId(x.sessionId)||!validId(x.segment?.client_key)||typeof x.segment.text!=='string'||!x.segment.text.trim()||x.segment.text.length>8000||!Number.isInteger(x.segment.started_ms)||x.segment.started_ms<0||x.segment.started_ms>86400000))continue;
  if(backup.finishSessionId&&!validId(backup.finishSessionId))continue;
  if(queue.length+backup.queue.length>200||finishSessionId&&backup.finishSessionId&&finishSessionId!==backup.finishSessionId)continue;
  const previous=queue;const previousFinish=finishSessionId;
  const keys=new Set(queue.map(x=>x.sessionId+':'+x.segment.client_key));
  queue=[...queue,...backup.queue.filter(x=>!keys.has(x.sessionId+':'+x.segment.client_key))];
  finishSessionId=finishSessionId||backup.finishSessionId||null;finishRequested=!!finishSessionId;
  if(!persistQueue()){queue=previous;finishSessionId=previousFinish;continue;}
  if(localStorage.getItem(key)===raw){localStorage.removeItem(key);i--;}
 }
 if(queue.length||finishSessionId){status('Recovered unsaved text. Retrying its original document; listening remains stopped.');scheduleSaveRetry();}
}
function scheduleSaveRetry(){
 if(retryTimer)return;
 retryTimer=setTimeout(()=>{retryTimer=null;void saveQueue().then(()=>{if(finishRequested)return finish();}).catch(()=>{});},4000);
}
function saveQueue(){
 if(saving)return saving;
 saving=(async()=>{
  while(queue.length){
   const item=queue[0];
   const j=await request('/'+item.sessionId+'/segments','POST',item.segment);
   if(queue[0]===item)queue.shift();
   persistQueue();
   if(selected?.id===item.sessionId){const segments=[...(selected.segments||[])];if(!segments.some(s=>s.client_key===item.segment.client_key))segments.push(item.segment);selected={...j.session,segments};renderSession(selected);}
   status((listening?'Listening · ':'Saving · ')+j.session.segment_count+' transcript segments saved locally.');
  }
 })().catch(e=>{
  status('Save pending: '+e.message+' Text is retained for recovery. You can export unsaved text.',true);
  window.HomeServerDictation?.stopTranscription();scheduleSaveRetry();controls();throw e;
 }).finally(()=>{saving=null;});
 return saving;
}

function status(s,error=false){const n=$('hsTranscriptStatus');if(n){n.textContent=s;n.dataset.error=error?'yes':'no';}}
function ensure(){
 if(drawer||!document.getElementById('chatForm'))return;
 drawer=document.createElement('aside');drawer.id='hsTranscriptionCanvas';drawer.className='hs-transcription-canvas';
 drawer.setAttribute('aria-label','HomeServer Transcription');
 drawer.hidden=true;
 drawer.innerHTML='<div class="hs-transcription-head"><div><small>AGENT CHAT · LISTENING</small><h3>Transcriptions</h3></div><button type="button" id="hsTranscriptClose" aria-label="Close transcription workspace">×</button></div>'+
 '<p>Conversation/Talk sends spoken turns to Agent Chat. Transcription builds a private document without invoking the Agent. Cloud can import only sessions you explicitly share.</p>'+
 '<div class="hs-transcription-actions"><button type="button" id="hsTranscriptStart">New transcription</button><button type="button" id="hsTranscriptStop" disabled>Stop listening</button><button type="button" id="hsTranscriptResume" disabled>Resume listening</button></div>'+
 '<p id="hsTranscriptStatus" role="status" aria-live="polite">Transcriptions are private until shared.</p>'+
 '<div class="hs-transcription-columns"><nav aria-label="Saved transcripts"><h4>My transcriptions</h4><div id="hsTranscriptList"></div></nav>'+
 '<div class="hs-transcription-document"><h4 id="hsTranscriptTitle">Choose a transcription</h4><div id="hsTranscriptText" aria-label="Transcript document"></div><div class="hs-transcription-actions"><button type="button" id="hsTranscriptShare" disabled>Share text with paired Cloud</button><button type="button" id="hsTranscriptExport" disabled>Export text</button><button type="button" id="hsTranscriptDelete" disabled>Delete</button></div></div></div>'+
 '<div class="hs-transcription-actions"><button type="button" id="hsTranscriptRecoveryExport">Export unsaved text</button><button type="button" id="hsTranscriptRecoveryClear">Clear browser recovery</button></div><p class="hs-transcription-note">Unsaved text uses this browser for recovery. Listening may use installed local Whisper or your browser fallback according to Voice Settings. Audio is not retained by this workspace. Use the separately consented saved-recording controls in Health to retain audio or video.</p>';
 document.body.append(drawer);
 const button=document.createElement('button');button.id='hsTranscriptOpen';button.type='button';
 button.className='chat-dictate-button';button.textContent='Transcriptions';
 button.setAttribute('aria-label','Open persistent transcription workspace');
 const group=document.querySelector('#chatForm .chat-voice-options');
 (group||document.getElementById('chatForm')).append(button);
 button.addEventListener('click',()=>{drawer.hidden=false;refresh();});
 $('hsTranscriptClose').addEventListener('click',()=>{drawer.hidden=true;});
 $('hsTranscriptStart').addEventListener('click',()=>{start(true).catch(e=>status(e.message,true));});
 $('hsTranscriptResume').addEventListener('click',()=>{start(false).catch(e=>status(e.message,true));});
 $('hsTranscriptStop').addEventListener('click',()=>{finish().catch(e=>status(e.message,true));});
 $('hsTranscriptShare').addEventListener('click',()=>{share().catch(e=>status(e.message,true));});
 $('hsTranscriptExport').addEventListener('click',()=>exportText());
 $('hsTranscriptRecoveryExport').addEventListener('click',()=>{
  const all=[...queue];
  for(let i=0;i<localStorage.length;i++){const key=localStorage.key(i);if(!key?.startsWith(recoveryPrefix))continue;try{const backup=JSON.parse(localStorage.getItem(key)||'{}');if(Array.isArray(backup.queue))all.push(...backup.queue);}catch(e){}}
  const seen=new Set();const text=all.filter(item=>{if(!item?.segment?.text)return false;const key=item.sessionId+':'+item.segment.client_key+':'+item.segment.text;if(seen.has(key))return false;seen.add(key);return true;}).map(item=>item.sessionId+'\n'+item.segment.text).join('\n\n');
  downloadText(text,'homeserver-unsaved-transcription.txt');
 });
 $('hsTranscriptRecoveryClear').addEventListener('click',()=>{
  if(listening||saving||finishing||!window.confirm('Discard unsaved transcript text from this browser? Export it first if you need a copy.'))return;
  queue=[];finishSessionId=null;finishRequested=false;persistQueue();
  for(let i=localStorage.length-1;i>=0;i--){const key=localStorage.key(i);if(key?.startsWith(recoveryPrefix))localStorage.removeItem(key);}
  status('Browser recovery cleared. Saved transcripts remain available.');controls();
 });
 $('hsTranscriptDelete').addEventListener('click',()=>{remove().catch(e=>status(e.message,true));});
 window.addEventListener('homeserver:transcription-segment',onSegment);
 window.addEventListener('homeserver:transcription-capture-stopped',()=>{if(listening&&!finishing)finish().catch(e=>status(e.message,true));});
 window.addEventListener('beforeunload',()=>{persistQueue();window.HomeServerDictation?.stopTranscription();});
}
function controls(){
 const session=selected;
 $('hsTranscriptStop').disabled=!active;
 $('hsTranscriptResume').disabled=!active||listening||finishRequested;
 $('hsTranscriptStart').disabled=!!active||starting||!!queue.length||finishRequested;
 $('hsTranscriptRecoveryExport').disabled=!queue.length;
 $('hsTranscriptRecoveryClear').disabled=listening||!!saving||finishing||(!queue.length&&!finishRequested);
 const complete=session?.status==='completed';
 $('hsTranscriptShare').disabled=!complete;
 $('hsTranscriptExport').disabled=!session?.segments?.length;
 $('hsTranscriptDelete').disabled=!complete;
 if(complete)$('hsTranscriptShare').textContent=session.cloud_shared?'Revoke Cloud access':'Share text with paired Cloud';
}
function renderSession(session){
 selected=session;
 $('hsTranscriptTitle').textContent=session?.title||'Choose a transcription';
 const out=$('hsTranscriptText');out.replaceChildren();
 for(const segment of session?.segments||[]){
  const line=document.createElement('p');
  line.textContent=segment.text;
  out.appendChild(line);
 }
 if(!session)out.textContent='Choose or create a transcription.';
 controls();
}
async function refresh(){
 const epoch=++refreshEpoch,view=selectionEpoch;
 try{
  const j=await request();if(epoch!==refreshEpoch)return;recoverQueue();const host=$('hsTranscriptList');host.replaceChildren();
  const sessions=j.sessions||[];
  if(!listening&&!starting&&!finishing)active=sessions.find(s=>s.status==='active')||null;
  for(const s of sessions){
   const b=document.createElement('button');b.type='button';b.className='hs-transcription-session';
   b.textContent=s.title+' · '+s.segment_count+' segments'+(s.cloud_shared?' · Cloud-shared':'');
   b.addEventListener('click',()=>open(s.id).catch(e=>status(e.message,true)));host.appendChild(b);
  }
  if(!sessions.length)host.textContent='No transcriptions saved.';
  if(view!==selectionEpoch){controls();return;}
  if(selected?.id){
   const exists=sessions.some(s=>s.id===selected.id);
   if(exists)await open(selected.id);
   else renderSession(null);
  }else if(active)await open(active.id);
  else controls();
 }catch(e){status(e.message,true);}
}
async function open(id){const epoch=++selectionEpoch;const data=await request('/'+encodeURIComponent(id));if(epoch===selectionEpoch)renderSession(data.session);}
async function start(create){
 if(listening||finishing||finishRequested||starting||queue.length)return;
 starting=true;
 try{
 if(!window.HomeServerDictation)throw Error('Speech capture is not ready.');
 if(create){
  if(active)throw Error('Finish the active session first.');
  const title=window.prompt('Transcription title','HomeServer transcription');
  if(title===null)return;
  const j=await request('','POST',{title:title||'HomeServer transcription'});
  active=j.session;
 }
 if(!active)throw Error('Create a transcription first.');
 const id=active.id;await open(id);
 captureSessionId=id;captureClock=performance.now();
 timelineBase=Math.max(Number(active.timeline_ms||0),Math.max(0,Date.now()-Date.parse(active.started_at||new Date().toISOString())));
 lastTimeline=Math.min(86400000,timelineBase);status('Checking microphone and transcription provider…');
 try{
  listening=await window.HomeServerDictation.startTranscription();
  if(!listening){await finish();status('Speech capture is unavailable. Check Voice Settings.',true);return;}
  status('Listening · your speech is added to this document, never sent as an Agent Chat command.');
 }catch(e){await finish();throw e;}
 controls();
 }finally{starting=false;controls();}
}
function onSegment(event){
 if(!active||!listening||!event.detail?.text||queue.length>=200)return;
 const sessionId=captureSessionId||active.id;
 const segment={text:String(event.detail.text).slice(0,8000),
   client_key:Array.from(crypto.getRandomValues(new Uint8Array(16)),x=>x.toString(16).padStart(2,'0')).join(''),
   started_ms:Math.min(86400000,Math.max(lastTimeline,Math.round(timelineBase+Math.max(0,Number(event.detail.capturedAt??performance.now())-captureClock))))};
 lastTimeline=segment.started_ms;
 queue.push({sessionId,segment});persistQueue();controls();
 void saveQueue().catch(()=>{});
 if(queue.length>=200){status('Listening stopped at the unsaved text limit. Keep this page open while saves retry.',true);window.HomeServerDictation?.stopTranscription();}

}
async function finish(){
 if(finishing||(!finishSessionId&&!active))return;
 const id=finishSessionId||captureSessionId||active.id;
 finishSessionId=id;finishRequested=true;finishing=true;listening=false;persistQueue();
 window.HomeServerDictation?.stopTranscription();
 $('hsTranscriptStop').disabled=true;
 try{
  await saveQueue();
  const j=await request('/'+id+'/stop','POST');
  if(active?.id===id)active=null;if(captureSessionId===id)captureSessionId=null;finishSessionId=null;finishRequested=false;persistQueue();if(!selected||selected.id===id)renderSession(j.session);status('Transcription saved locally. Cloud sharing is optional.');
  await refresh();
 }catch(e){scheduleSaveRetry();throw e;}finally{finishing=false;controls();}
}
async function share(){
 if(!selected||selected.status!=='completed')return;
 const target=selected,epoch=selectionEpoch;const allow=!target.cloud_shared;
 if(allow&&!window.confirm('Allow your paired VP3 Cloud account to import the text of this completed transcription? Raw audio stays on HomeServer.'))return;
 const j=await request('/'+target.id+'/cloud-share','PUT',{cloud_share:allow});
 if(epoch===selectionEpoch&&selected?.id===target.id)renderSession({...j.session,segments:target.segments});
 status(allow?'This transcript is available for explicit import in Cloud Transcriptions.':'Cloud access revoked for future imports.');
 await refresh();
}
function exportText(){
 if(!selected?.segments?.length)return;
 downloadText(selected.segments.map(s=>s.text).join('\n\n'),'homeserver-transcription.txt');
}
function downloadText(text,filename){
 const blob=new Blob([text],{type:'text/plain'});
 const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=filename;a.click();
 setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function remove(){
 if(!selected||selected.status!=='completed')return;
 if(!window.confirm('Permanently delete this local transcript? Previously imported Cloud copies are managed separately.'))return;
 const id=selected.id,epoch=selectionEpoch;await request('/'+id,'DELETE');if(epoch===selectionEpoch&&selected?.id===id)renderSession(null);status('Local transcription deleted.');await refresh();
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',ensure,{once:true});else ensure();
})();

