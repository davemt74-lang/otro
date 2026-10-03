/* Cloud-compatible local transcription canvas, built on existing Dictate capture. */
(()=>{
'use strict';
const base='/api/v1/control/transcription-sessions';
let active=null,selected=null,queue=[],saving=null,retryTimer=null,finishRequested=false,listening=false,finishing=false,drawer=null;
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
   queue.shift();
   if(selected?.id===item.sessionId){selected={...j.session,segments:[...(selected.segments||[]),{...item.segment,text:item.segment.text}]};renderSession(selected);}
   status((listening?'Listening · ':'Saving · ')+j.session.segment_count+' transcript segments saved locally.');
  }
 })().catch(e=>{
  status('Save pending: '+e.message+' Keep this page open while it retries.',true);
  window.HomeServerDictation?.stopTranscription();scheduleSaveRetry();throw e;
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
 '<p class="hs-transcription-note">Listening may use installed local Whisper or your browser fallback according to Voice Settings. Audio is not retained by this workspace. Use the separately consented saved-recording controls in Health to retain audio or video.</p>';
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
 $('hsTranscriptDelete').addEventListener('click',()=>{remove().catch(e=>status(e.message,true));});
 window.addEventListener('homeserver:transcription-segment',onSegment);
 window.addEventListener('homeserver:transcription-capture-stopped',()=>{if(listening&&!finishing)finish().catch(e=>status(e.message,true));});
 window.addEventListener('beforeunload',()=>window.HomeServerDictation?.stopTranscription());
}
function controls(){
 const session=selected;
 $('hsTranscriptStop').disabled=!active;
 $('hsTranscriptResume').disabled=!active||listening||finishRequested;
 $('hsTranscriptStart').disabled=!!active;
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
 try{
  const j=await request(),host=$('hsTranscriptList');host.replaceChildren();
  const sessions=j.sessions||[];
  active=sessions.find(s=>s.status==='active')||null;
  for(const s of sessions){
   const b=document.createElement('button');b.type='button';b.className='hs-transcription-session';
   b.textContent=s.title+' · '+s.segment_count+' segments'+(s.cloud_shared?' · Cloud-shared':'');
   b.addEventListener('click',()=>open(s.id).catch(e=>status(e.message,true)));host.appendChild(b);
  }
  if(!sessions.length)host.textContent='No transcriptions saved.';
  if(selected?.id){
   const exists=sessions.some(s=>s.id===selected.id);
   if(exists)await open(selected.id);
   else renderSession(null);
  }else if(active)await open(active.id);
  else controls();
 }catch(e){status(e.message,true);}
}
async function open(id){const data=await request('/'+encodeURIComponent(id));renderSession(data.session);}
async function start(create){
 if(listening||finishing||finishRequested)return;
 if(!window.HomeServerDictation)throw Error('Speech capture is not ready.');
 if(create){
  if(active)throw Error('Finish the active session first.');
  const title=window.prompt('Transcription title','HomeServer transcription');
  if(title===null)return;
  const j=await request('','POST',{title:title||'HomeServer transcription'});
  active=j.session;
 }
 if(!active)throw Error('Create a transcription first.');
 await open(active.id);status('Checking microphone and transcription provider…');
 try{
  listening=await window.HomeServerDictation.startTranscription();
  if(!listening){await finish();status('Speech capture is unavailable. Check Voice Settings.',true);return;}
  status('Listening · your speech is added to this document, never sent as an Agent Chat command.');
 }catch(e){await finish();throw e;}
 controls();
}
function onSegment(event){
 if(!active||!listening||!event.detail?.text||queue.length>=200)return;
 const sessionId=active.id;
 const segment={text:String(event.detail.text).slice(0,8000),
   client_key:Array.from(crypto.getRandomValues(new Uint8Array(16)),x=>x.toString(16).padStart(2,'0')).join(''),
   started_ms:Math.min(86400000,Math.max(0,Math.round(performance.now())))};
 queue.push({sessionId,segment});
 void saveQueue().catch(()=>{});
 if(queue.length>=200){status('Listening stopped at the unsaved text limit. Keep this page open while saves retry.',true);window.HomeServerDictation?.stopTranscription();}

}
async function finish(){
 if(finishing||!active)return;
 finishRequested=true;finishing=true;listening=false;
 window.HomeServerDictation?.stopTranscription();
 $('hsTranscriptStop').disabled=true;
 try{
  await saveQueue();
  const id=active.id;
  const j=await request('/'+id+'/stop','POST');
  active=null;finishRequested=false;renderSession(j.session);status('Transcription saved locally. Cloud sharing is optional.');
  await refresh();
 }catch(e){scheduleSaveRetry();throw e;}finally{finishing=false;controls();}
}
async function share(){
 if(!selected||selected.status!=='completed')return;
 const allow=!selected.cloud_shared;
 if(allow&&!window.confirm('Allow your paired VP3 Cloud account to import the text of this completed transcription? Raw audio stays on HomeServer.'))return;
 const j=await request('/'+selected.id+'/cloud-share','PUT',{cloud_share:allow});
 renderSession({...j.session,segments:selected.segments});
 status(allow?'This transcript is available for explicit import in Cloud Transcriptions.':'Cloud access revoked for future imports.');
 await refresh();
}
function exportText(){
 if(!selected?.segments?.length)return;
 const blob=new Blob([selected.segments.map(s=>s.text).join('\n\n')],{type:'text/plain'});
 const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='homeserver-transcription.txt';a.click();
 setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function remove(){
 if(!selected||selected.status!=='completed')return;
 if(!window.confirm('Permanently delete this local transcript? Previously imported Cloud copies are managed separately.'))return;
 await request('/'+selected.id,'DELETE');selected=null;renderSession(null);status('Local transcription deleted.');await refresh();
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',ensure,{once:true});else ensure();
})();

