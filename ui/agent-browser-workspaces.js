/* A3 Cloud Agent Teams: same Chat canvas, no additional app or sidebar. */
(function () {
'use strict';
if (window.__VP3_AGENT_TEAMS_A3__) return;
window.__VP3_AGENT_TEAMS_A3__ = true;
const cfg={endpoint:'/api/v1/control/agent-browser-workspaces',csrf:'owner'}, form=document.getElementById('chatForm'), shell=form?.parentElement;
if (!shell||!form||!cfg.endpoint||!cfg.csrf) return;
const root=document.createElement('details');
root.className='vp3-agent-teams-a3';
root.setAttribute('data-agent-teams-a3','');
root.innerHTML='<summary><span aria-hidden="true">◉</span> Agent Teams <small>HomeServer missions</small></summary>'+
 '<div class="vp3-teams-body"><p>Specialists research and prepare changes for your review on HomeServer.</p>'+
 '<form class="vp3-teams-create"><label for="vp3MissionObjective">Mission objective</label>'+
 '<div class="vp3-teams-entry"><input id="vp3MissionObjective" name="objective" maxlength="4000" required '+
 'placeholder="Give the team a research objective…" autocomplete="off">'+
 '<button type="submit">Create &amp; run</button></div>'+
 '<label class="vp3-provider-label" for="vp3MissionProvider">Initial worker model</label>'+
 '<select id="vp3MissionProvider" name="provider_key">'+
 '<option value="auto">HomeServer default</option><option value="ollama">Ollama (local)</option>'+
 '<option value="anthropic">Claude / Anthropic</option><option value="openai">OpenAI</option>'+
 '<option value="openrouter">OpenRouter</option></select>'+
 '<small class="vp3-teams-provider-note">Workers use isolated contexts. Prepare a mission to review read capabilities, budgets and outputs; browser pages need separate approval.</small>'+
 '<label class="vp3-teams-browser-optin"><input type="checkbox" name="prepare_only"> Prepare mission first — review specialist tools and browser approvals</label></form>'+
 '<div class="vp3-teams-status" role="status" aria-live="polite">Open to load missions.</div>'+
 '<div class="vp3-teams-list" aria-label="Recent missions"></div>'+
 '<section class="vp3-teams-detail" aria-label="Selected mission" hidden></section></div>';
shell.insertBefore(root,form);root.querySelector('form').hidden=true;
const status=root.querySelector('.vp3-teams-status'), list=root.querySelector('.vp3-teams-list'),
 detail=root.querySelector('.vp3-teams-detail'), create=root.querySelector('form'), input=create.querySelector('input'),
 submit=create.querySelector('button');
let authorityGeneration=0;
let busy=false,selected='',items=[],lastRefresh=0,inflight=false;
const toolsByMission=new Map(), toolDrafts=new Map(), changesByMission=new Map();
const staffingByMission=new Map();
const browserByWorker=new Map();
const liveByWorker=new Map();let liveLastPoll=0;
const pendingOperations=new Map();let statusInflight=false,viewGeneration=0;
function el(tag,cls,txt){const x=document.createElement(tag);if(cls)x.className=cls;if(txt!==undefined)x.textContent=String(txt);return x;}
function btn(name,action,id,taskId){const x=el('button','',name);x.type='button';x.dataset.action=action;if(id)x.dataset.id=id;if(taskId)x.dataset.taskId=taskId;x.disabled=busy;return x;}
function fmt(value){if(!value)return '—';const raw=String(value).trim();
 const valueUtc=/(Z|[+-]\d\d:\d\d)$/.test(raw)?raw:raw.replace(' ','T')+'Z';
 const parsed=new Date(valueUtc);return Number.isNaN(parsed.getTime())?raw:parsed.toLocaleString();}
function setBusy(value){busy=Boolean(value);if(busy)viewGeneration++;submit.disabled=busy;root.querySelectorAll('button[data-action]').forEach(b=>b.disabled=busy||b.dataset.browserBlocked==='true');document.querySelectorAll('.vp3-mission-notice button,.vp3-mission-live-card button').forEach(b=>b.disabled=busy);}
function say(message){status.textContent=String(message);}
async function api(action,extra,signal){
 const authorityAtRequest=authorityGeneration;
 const response=await fetch(String(cfg.endpoint),{method:'POST',credentials:'same-origin',cache:'no-store',signal,
 headers:{'Accept':'application/json','Content-Type':'application/json'},
 body:JSON.stringify(Object.assign({csrf_token:String(cfg.csrf),action:action},extra||{}))});
 let payload;try{payload=await response.json();}catch(_){throw new Error('Mission server response was invalid.');}
 if(!response.ok||!payload||!payload.ok){const error=new Error(String(payload&&(payload.detail||payload.error)||'Mission operation failed.').slice(0,240));error.status=response.status;if([401,403,419].includes(error.status))clearPrivateMissionState();throw error;}
 if(authorityAtRequest!==authorityGeneration){const error=new Error('Mission access changed while loading. Refresh authorized status.');error.status=403;throw error;}
 return payload;
}
// Automatic routing preserves a lost-response intent before any dispatch.
const chatInput=document.getElementById('chatInput'),chatSubmit=form.querySelector('button[type="submit"]');
const taskMode=el('select');taskMode.id='vp3ChatTaskMode';taskMode.setAttribute('aria-label','Work mode');
for(const [value,label] of [['automatic','Automatic'],['chat','Chat only'],['specialists','Specialist plan']]){const option=el('option','',label);option.value=value;taskMode.appendChild(option);}
const taskLabel=el('label','vp3-chat-task-mode','Work mode ');taskLabel.appendChild(taskMode);if(chatInput)shell.insertBefore(taskLabel,form);
function taskContext(){
return {conversation_id:window.HomeServerAgentRouting?.getActiveConversationId?.()||null,parent_agent_id:window.HomeServerAgentRouting?.getSelectedAgentId?.()||null};
}
let followupContext='',followupReview=null,followupRetry=null;
const followupHost=el('section','vp3-mission-followup');followupHost.setAttribute('aria-label','Review mission command');followupHost.setAttribute('aria-live','polite');followupHost.hidden=true;shell.insertBefore(followupHost,form);
const followupStorageKey='vp3.chat-followup.a5c9.'+String(cfg.csrf);
function storeFollowup(){try{if(followupReview)sessionStorage.setItem(followupStorageKey,JSON.stringify({context:followupContext,request_id:followupReview.request_id}));else sessionStorage.removeItem(followupStorageKey);}catch(_){} }
function renderFollowup(value,context=JSON.stringify(taskContext())){
 if(context!==JSON.stringify(taskContext()))return;
 if(!value||!['pause','cancel','resume','scope'].includes(value.operation)||!value.request_id)throw new Error('Mission command review was invalid.');
 followupReview=value;followupContext=context;followupHost.replaceChildren();followupHost.hidden=false;storeFollowup();
 const labels={pause:'Pause mission',cancel:'Cancel mission',resume:'Resume mission',scope:'Change remaining scope'};
 followupHost.appendChild(el('h4','',labels[value.operation]));
 if(value.status==='choosing'){
  followupHost.appendChild(el('p','','Choose the mission you mean. No work has changed.'));
  for(const m of value.choices||[]){const button=el('button','',m.objective+' · '+m.status);button.type='button';button.dataset.followupAction='select';button.dataset.missionId=m.id;button.addEventListener('click',()=>void followupOperation('select',m.id));followupHost.appendChild(button);}
  if(!value.choices?.length)followupHost.appendChild(el('p','','No eligible mission is available in this conversation. Open the original conversation and retry.'));
 }else{
  followupHost.appendChild(el('strong','',value.mission_objective||'Selected mission'));
  followupHost.appendChild(el('p','',Number(value.completed_workers||0)+' completed specialist results remain in history.'));
  if(value.operation==='scope'){
   followupHost.appendChild(el('p','','Revised scope: '+value.objective));
   followupHost.appendChild(el('p','','Confirming stops prior work and retires outstanding change proposals. The linked plan needs fresh assignment review before starting; saved changes remain saved.'));
   for(const t of value.plan?.tasks||[]){followupHost.appendChild(el('p','',t.title+' · '+t.objective+' · Reads: '+(t.tools||[]).join(', ')+' · Proposed changes: '+(t.actions||[]).join(', ')));}
  }
  if(value.status==='proposed'&&!value.expired&&!value.stale&&!(value.authority_current===false&&['resume','scope'].includes(value.operation))){
   if(value.operation==='resume'&&Number(value.interrupted_workers)>0){const label=el('label','');const checkbox=el('input');checkbox.type='checkbox';checkbox.dataset.followupReplay='';label.append(checkbox,document.createTextNode(' I reviewed the interrupted work and authorize its reexecution. Completed work will not run again.'));followupHost.appendChild(label);}
   for(const [decision,label] of [['apply','Confirm '+labels[value.operation].toLowerCase()],['dismiss','Dismiss']]){const button=el('button','',label);button.type='button';button.dataset.followupAction=decision;button.addEventListener('click',()=>void followupOperation(decision));followupHost.appendChild(button);}
   followupHost.appendChild(el('small','','Review expires '+fmt(value.expires_at)+'. Any changed mission state requires a fresh review.'));
  }else followupHost.appendChild(el('p','',value.stale?'Mission state changed. Send the command again.':value.authority_current===false&&['resume','scope'].includes(value.operation)?'Permissions changed. Prepare a new reviewed task.':value.expired&&value.status==='proposed'?'Review expired. Send the command again.':value.status==='applied'?'Command applied.':value.status==='dismissed'?'Command dismissed.':'Review unavailable.'));
 }
 const close=el('button','','Close review');close.type='button';close.addEventListener('click',()=>{followupReview=null;followupRetry=null;followupHost.hidden=true;storeFollowup();});followupHost.appendChild(close);
}
async function followupOperation(decision,mid){
 if(busy||!followupReview)return;
 const review=followupReview,context=followupContext;
 if(context!==JSON.stringify(taskContext())){followupHost.hidden=true;say('Return to the original conversation and lead before reviewing this command.');return;}
 let payload={request_id:review.request_id};
 if(decision==='select')payload.mission_id=mid;
 else{
  const replay=Boolean(followupHost.querySelector('[data-followup-replay]')?.checked);
  if(decision==='apply'&&review.operation==='resume'&&Number(review.interrupted_workers)>0&&!replay){say('Review and authorize interrupted work before resuming.');return;}
  payload={...payload,expected_hash:review.expected_hash,decision,confirmed:true,allow_reexecution:replay};
 }
 const intent=JSON.stringify({decision,payload});
 // An uncertain operation can only retry the exact same decision, never choose another target.
 if(followupRetry&&followupRetry!==intent){say('The prior response is uncertain. Retry the same command decision or refresh its status.');return;}
 followupRetry=intent;setBusy(true);followupHost.querySelectorAll('button,input').forEach(b=>b.disabled=true);
 try{
  const response=await api(decision==='select'?'task.followup.select':'task.followup.decide',payload);
  if(context!==JSON.stringify(taskContext())){followupHost.hidden=true;return;}
  if(!response.followup)throw new Error('Command status was not returned. Retry the same decision.');
  followupRetry=null;renderFollowup(response.followup,context);
  const m=response.followup.mission;
  if(m){selected=m.id;const index=items.findIndex(x=>x.id===m.id);if(index>=0)items[index]=m;else items.unshift(m);if(m.chat_task&&m.status==='planned'){const contract=await api('tools.get',{mission_id:m.id});if(context!==JSON.stringify(taskContext()))return;toolsByMission.set(m.id,contract.tools);toolDrafts.set(m.id,m.chat_task.draft);root.open=true;}showList();showMission(m);publishProgress();}
  say(response.followup.status==='applied'?(review.operation==='scope'?'Revised plan prepared. Review assignments before starting specialists.':'Mission command applied.'):response.followup.status==='dismissed'?'Mission command dismissed.':'Review the exact mission command.');
 }catch(e){if([401,403,419].includes(e.status)){followupReview=null;followupHost.hidden=true;storeFollowup();}if(e.status&&e.status<500)followupRetry=null;say((e.message||'Command response interrupted.')+' No automatic retry will run.');}
 finally{setBusy(false);followupHost.querySelectorAll('button,input').forEach(b=>b.disabled=false);}
}
async function restoreFollowup(){
 if(busy)return;
 let saved;try{saved=JSON.parse(sessionStorage.getItem(followupStorageKey)||'null');}catch(_){}
 if(!saved?.request_id||saved.context!==JSON.stringify(taskContext())){if(followupContext&&followupContext!==JSON.stringify(taskContext()))followupHost.hidden=true;return;}
 try{const response=await api('task.followup.get',{request_id:saved.request_id});if(saved.context===JSON.stringify(taskContext())&&!busy)renderFollowup(response.followup,saved.context);}catch(e){if([401,403,404,419].includes(e.status)){followupReview=null;followupHost.hidden=true;storeFollowup();}}
}
window.setInterval(()=>{if(followupContext&&followupContext!==JSON.stringify(taskContext()))followupHost.hidden=true;else if(followupHost.hidden&&!busy)void restoreFollowup();},5000);
void restoreFollowup();
let directChatBypass=false;
async function prepareChatTask(){
 if(busy||!chatInput||window.HomeServerChatTurn?.isBusy?.())return;
 const objective=chatInput.value.trim();if(!objective||objective.length>4000){root.open=true;say('Enter a task request of 1 to 4,000 characters.');return;}
 const mode=taskMode.value,intent={objective,...taskContext()},context=JSON.stringify(taskContext());
 const taskStorageKey='vp3.chat-task.pending.a5c7.'+String(cfg.csrf)+'.'+mode+'.'+context;
 let request;
 try{const stored=JSON.parse(sessionStorage.getItem(taskStorageKey)||'null');if(stored&&JSON.stringify(stored.intent)===JSON.stringify(intent))request=stored;}catch(_){}
 if(!request){const request_id=window.crypto?.randomUUID?.();if(!request_id){root.open=true;say('Secure request IDs are unavailable.');return;}request={intent,request_id};}
 try{sessionStorage.setItem(taskStorageKey,JSON.stringify(request));}catch(_){root.open=true;say('Task recovery storage is unavailable. Enable session storage and retry.');return;}
 const controller=new AbortController(),timer=window.setTimeout(()=>controller.abort(),120000),wasOpen=root.open;
 let continueChat=false,turnStarted=false;
 function turn(status,extra={}){window.dispatchEvent(new CustomEvent('homeserver:chat-turn',{detail:{requestId:'task:'+request.request_id,status,...extra}}));}
 function startTurn(){if(!turnStarted){turnStarted=true;turn('started',{conversationId:request.intent.conversation_id});}}
 function current(){if(context!==JSON.stringify(taskContext()))throw new Error('Conversation or lead changed. Return to the original conversation to retry.');}
 setBusy(true);taskMode.disabled=true;chatInput.disabled=true;if(chatSubmit)chatSubmit.disabled=true;root.open=true;say(mode==='automatic'?'Lead agent is choosing how to help…':'Lead agent is preparing specialist assignments…');
 if(mode==='specialists')startTurn();
 try{
  const response=await api(mode==='automatic'?'task.route':'task.prepare',{...request.intent,request_id:request.request_id},controller.signal);
  current();
  if(mode==='automatic'&&response.route==='chat'){
   if(response.conversation_id&&window.HomeServerChatTurn?.usePreparedConversation&&!window.HomeServerChatTurn.usePreparedConversation(response.conversation_id))throw new Error('Prepared conversation could not be selected. Retry the same request.');
   sessionStorage.removeItem(taskStorageKey);continueChat=true;root.open=wasOpen;
  }else if(mode==='automatic'&&response.route==='followup'){
   if(!response.followup)throw new Error('Mission command was not confirmed. Retry the same request.');
   if(response.conversation_id&&window.HomeServerChatTurn?.usePreparedConversation&&!window.HomeServerChatTurn.usePreparedConversation(response.conversation_id))throw new Error('Original conversation could not be selected.');
   renderFollowup(response.followup,JSON.stringify(taskContext()));sessionStorage.removeItem(taskStorageKey);chatInput.value='';root.open=wasOpen;
   say('Review the command on the chat canvas. No work changes until you confirm.');startTurn();turn('completed',{reply:status.textContent});
  }else{
   if(mode==='automatic'&&response.route!=='specialists')throw new Error('Routing decision was not confirmed. Retry the same request.');
   const m=response.mission;if(!m?.id||!m.chat_task?.draft)throw new Error('Task preparation was not confirmed. Retry the same request.');
   const contract=await api('tools.get',{mission_id:m.id},controller.signal);current();
   if(response.conversation_id&&window.HomeServerChatTurn?.usePreparedConversation&&!window.HomeServerChatTurn.usePreparedConversation(response.conversation_id))throw new Error('Prepared conversation could not be selected. Retry the same request.');
   selected=m.id;toolsByMission.set(m.id,contract.tools);if(!contract.tools.configured)toolDrafts.set(m.id,m.chat_task.draft);
   const index=items.findIndex(x=>x.id===m.id);if(index>=0)items[index]=m;else items.unshift(m);
   showList();showMission(m);sessionStorage.removeItem(taskStorageKey);chatInput.value='';
   say('Plan prepared. Review and approve assignments, then start specialists. Each proposed edit requires separate approval.');
   startTurn();turn('completed',{reply:status.textContent});
  }
 }catch(e){root.open=true;say((e.name==='AbortError'?'Task preparation timed out.':e.message||'Task preparation interrupted.')+' Your request remains available for retry.');startTurn();turn('failed',{message:status.textContent});}
 finally{window.clearTimeout(timer);setBusy(false);taskMode.disabled=false;chatInput.disabled=false;if(chatSubmit)chatSubmit.disabled=false;}
 if(continueChat){directChatBypass=true;try{form.requestSubmit();}finally{directChatBypass=false;}}
}
form.addEventListener('submit',event=>{
 if(directChatBypass||taskMode.value==='chat'||(taskMode.value==='automatic'&&chatInput.value.trim().length>4000))return;
 event.preventDefault();event.stopImmediatePropagation();void prepareChatTask();
},true);

const schedulesPanel=el('section','vp3-specialist-schedules');schedulesPanel.setAttribute('aria-label','Scheduled specialist tasks');root.querySelector('.vp3-teams-body').appendChild(schedulesPanel);
let schedules=[];
let schedulerHealth=null,healthReceivedAt=0;
function unavailableHealth(){
 schedulerHealth={state:'unavailable',checked_at:new Date().toISOString()};healthReceivedAt=Date.now();
 renderScheduleList();const brain=document.querySelector('.vp3-schedules-brain');if(brain)renderScheduleList(brain);
}
function renderScheduleList(parent=schedulesPanel){
 parent.replaceChildren();parent.appendChild(el('h4','','Scheduled specialist tasks'));
 const states={healthy:'Scheduler running',starting:'Scheduler starting',stopped:'Scheduler stopped',stopping:'Scheduler stopping',degraded:'Scheduler needs attention',stalled:'Scheduler heartbeat overdue',unavailable:'Scheduler status unavailable'};
 let state=schedulerHealth?.state;
 if(!Object.hasOwn(states,state)||Date.now()-healthReceivedAt>90000)state='unavailable';
 const healthLine=el('p',state==='healthy'?'vp3-teams-meta':'vp3-teams-error',states[state]);healthLine.setAttribute('role','status');healthLine.dataset.schedulerState=state;parent.appendChild(healthLine);
 if(schedulerHealth){
  parent.appendChild(el('p','vp3-teams-meta','Checked '+fmt(schedulerHealth.checked_at)+' · Last tick '+fmt(schedulerHealth.last_tick_at)+' · Last successful tick '+fmt(schedulerHealth.last_success_at)));
  if(schedulerHealth.consecutive_failures&&schedulerHealth.last_error)parent.appendChild(el('p','vp3-teams-error',schedulerHealth.last_error+' · Last failure '+fmt(schedulerHealth.last_failure_at)));
 }else parent.appendChild(el('p','vp3-teams-meta','Refresh status. An older HomeServer may need an update to report scheduler health.'));
 if(!schedules.length)parent.appendChild(el('p','','No schedules yet. Prepare a task and approve its assignments to schedule it.'));
 for(const schedule of schedules){
  const row=el('article','vp3-schedule-row');row.appendChild(el('strong','',schedule.objective));
  const day=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'][schedule.weekday];
  row.appendChild(el('p','vp3-teams-meta',schedule.status+' · '+(schedule.frequency==='weekly'?day:'Daily')+' '+String(schedule.hour).padStart(2,'0')+':'+String(schedule.minute).padStart(2,'0')+' '+schedule.timezone+' · Next '+fmt(schedule.next_run_at)));
  row.appendChild(el('p','vp3-teams-meta','Last run '+fmt(schedule.last_run_at)+' · Updated '+fmt(schedule.updated_at)));
  if(schedule.last_error)row.appendChild(el('p','vp3-teams-error',schedule.last_error));
  if(schedule.status==='active')row.appendChild(btn('Pause schedule','schedule.pause',schedule.id));
  if(schedule.status==='paused')row.appendChild(btn('Resume schedule','schedule.resume',schedule.id));
  if(schedule.status!=='cancelled')row.appendChild(btn('Cancel future runs','schedule.cancel',schedule.id));
  for(const run of schedule.runs||[]){
   const line=el('div','vp3-teams-meta',fmt(run.due_at)+' · '+run.status+' · '+(run.mission_status||run.reason||'')+' · '+fmt(run.completed_at||run.mission_updated_at||run.created_at));
   if(run.needs_review)line.appendChild(el('span','vp3-teams-error',' · Needs review · '+(run.reason||'Open the run before resuming.')));
   if(run.long_running_workers)line.appendChild(el('span','vp3-teams-error',' · Worker running longer than five minutes. Inspect the run before deciding whether to stop it.'));
   if(run.mission_id)line.appendChild(btn('View run','get',run.mission_id));row.appendChild(line);
  }
  parent.appendChild(row);
 }
 parent.appendChild(el('small','','Schedules reuse the reviewed plan. Every edit needs separate approval. Missed slots coalesce; unfinished runs or changes prevent overlap. Pausing or cancelling a schedule stops future runs; use the run controls to stop current work.'));
}
function renderScheduleForm(m){
 const contract=toolsByMission.get(m.id);if(m.status!=='planned'||!m.chat_task||!contract?.configured||!contract.active||m.private||m.authority_current===false)return;
 if((contract.browser_requirements||[]).length)return;
 const panel=el('section','vp3-schedule-create');panel.setAttribute('aria-label','Schedule reviewed task');panel.appendChild(el('h4','','Schedule this reviewed task'));
 const frequency=el('select');frequency.dataset.scheduleField='frequency';frequency.setAttribute('aria-label','Schedule frequency');
 for(const [value,label] of [['weekly','Weekly'],['daily','Daily']]){const option=el('option','',label);option.value=value;frequency.appendChild(option);}panel.appendChild(frequency);
 const weekday=el('select');weekday.dataset.scheduleField='weekday';weekday.setAttribute('aria-label','Schedule weekday');
 ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'].forEach((label,index)=>{const option=el('option','',label);option.value=String(index);weekday.appendChild(option);});panel.appendChild(weekday);
 const time=el('input');time.type='time';time.value='09:00';time.dataset.scheduleField='time';time.setAttribute('aria-label','Schedule local time');panel.appendChild(time);
 const zone=el('input');zone.value=Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC';zone.maxLength=80;zone.dataset.scheduleField='timezone';zone.setAttribute('aria-label','Schedule timezone');panel.appendChild(zone);
 panel.appendChild(btn('Create schedule','schedule.create',m.id));detail.appendChild(panel);
}
async function loadSchedules(){
 try{const result=await api('schedule.list');schedules=Array.isArray(result.schedules)?result.schedules:[];schedulerHealth=result.scheduler_health||null;healthReceivedAt=Date.now();renderScheduleList();const brain=document.querySelector('.vp3-schedules-brain');if(brain)renderScheduleList(brain);}
 catch(error){unavailableHealth();throw error;}
}
async function scheduleOperation(action,id){
 if(busy)return;let payload;
 if(action==='schedule.create'){
  const panel=detail.querySelector('.vp3-schedule-create'),contract=toolsByMission.get(id);if(!panel||!contract?.active)return;
  const field=k=>panel.querySelector('[data-schedule-field="'+k+'"]').value;const time=field('time').split(':');
  payload={mission_id:id,expected_revision:contract.revision,confirmed:true,timing:{frequency:field('frequency'),weekday:Number(field('weekday')),hour:Number(time[0]),minute:Number(time[1]),timezone:field('timezone').trim()}};
  if(!window.confirm('Run this exact reviewed specialist plan on this schedule? Each run may use the configured model and tools within the reviewed budgets. Every proposed edit still requires separate approval.'))return;
 }else{
  const schedule=schedules.find(s=>s.id===id);if(!schedule)return;
  payload={schedule_id:id,expected_revision:schedule.revision,confirmed:true};
  if(!window.confirm(action==='schedule.resume'?'Resume future runs of the reviewed plan under current permissions?':'Stop future scheduled runs? Current work and prepared changes remain available in the run controls.'))return;
 }
 const storage='vp3.schedule.pending.a5c6.'+String(cfg.csrf)+'.'+action+'.'+id+'.'+JSON.stringify(payload.timing||{});
 try{
  const pending=JSON.parse(sessionStorage.getItem(storage)||'null');if(pending)payload=pending;
  else{payload.request_id=window.crypto.randomUUID();sessionStorage.setItem(storage,JSON.stringify(payload));}
 }catch(_){say('Schedule recovery storage is unavailable. Retry after enabling session storage.');return;}
 setBusy(true);say('Updating specialist schedule…');
 try{await api(action,payload);sessionStorage.removeItem(storage);await loadSchedules();say('Schedule updated. Each proposed edit still requires your approval.');const brain=document.querySelector('[data-agent-teams-brain-a3]');if(brain?.parentNode)renderBrain(brain.parentNode);}
 catch(e){say((e.message||'Schedule response interrupted.')+' Refresh or retry the same operation.');}
 finally{setBusy(false);}
}


// A5C8: one durable feed updates Chat, Brain and notification surfaces.
let missionNotices={items:[],unread:0},progressInflight=false,progressConnected=false,progressReceived=0;
const noticeHost=el('section','vp3-mission-notifications');noticeHost.setAttribute('aria-label','Mission notifications');
root.querySelector('.vp3-teams-body').insertBefore(noticeHost,list);
const liveSummary=el('section','vp3-mission-live-summary');liveSummary.setAttribute('aria-label','Live mission progress');liveSummary.setAttribute('aria-live','polite');
shell.insertBefore(liveSummary,root);
function progressState(m){
 if(!progressConnected||Date.now()-progressReceived>30000)return 'disconnected';
 return m.progress?.state||m.status||'unknown';
}
const progressLabels={running:'Working',awaiting_review:'Awaiting assignment review',awaiting_approval:'Awaiting edit approval',awaiting_delivery:'Awaiting saved-change delivery',attention:'Results need attention',waiting_review:'Review required',stalled:'Stalled · review required',interrupted:'Interrupted · review required',blocked:'Permissions need review',completed:'Research completed',partial:'Partially completed',failed:'Failed',cancelled:'Cancelled',planned:'Awaiting review',disconnected:'Disconnected · last known status'};
function missionProgress(m){return progressLabels[progressState(m)]||progressState(m);}
function workerProgress(t){
 const a=t.activity;
 return (a?.stage||t.status||'queued')+' · Last activity '+fmt(a?.last_activity_at||t.completed_at||t.started_at);
}
function renderLiveSummary(){
 liveSummary.replaceChildren();
 const active=items.filter(m=>['running','waiting_review','planned'].includes(m.status)||['awaiting_approval','awaiting_delivery','attention'].includes(m.progress?.state));
 liveSummary.hidden=!active.length;
 for(const m of active.slice(0,4)){
  const card=el('article','vp3-mission-live-card');card.dataset.missionId=m.id;card.dataset.progressState=progressState(m);
  card.appendChild(el('strong','',missionProgress(m)+' · '+(m.objective||'Mission')));
  card.appendChild(el('small','',String(m.progress?.completed_workers??(m.tasks||[]).filter(t=>t.status==='completed').length)+' / '+(m.tasks||[]).length+' specialists completed · '+fmt(m.progress?.checked_at||m.updated_at)));
  for(const t of m.tasks||[])card.appendChild(el('p','vp3-teams-meta',(t.title||t.role||'Specialist')+' · '+workerProgress(t)));
  const open=btn('Open mission','open-team-chat',m.id);open.addEventListener('click',e=>{e.stopPropagation();operation('open-team-chat',m.id);});card.appendChild(open);liveSummary.appendChild(card);
 }
}
function renderNotices(host){
 if(!host)return;
 host.replaceChildren();host.appendChild(el('h4','','Mission notifications'));
 const state=el('p','vp3-teams-meta',(progressConnected&&Date.now()-progressReceived<=30000?'Live updates':'Updates disconnected · showing last received notifications')+' · '+Number(missionNotices.unread||0)+(missionNotices.count_is_lower_bound?'+':'')+' unread');state.setAttribute('role','status');host.appendChild(state);
 for(const n of (missionNotices.items||[]).slice(0,20)){
  const row=el('article','vp3-mission-notice');row.dataset.noticeId=String(n.id);row.dataset.read=String(Boolean(n.read_at||n.resolved_at));
  row.appendChild(el('strong','',n.title||'Mission update'));row.appendChild(el('small','',fmt(n.created_at)+(n.resolved_at?' · resolved':'')));
  const open=btn('Open mission','open-team-chat',n.mission_id);open.addEventListener('click',e=>{e.stopPropagation();operation('open-team-chat',n.mission_id);});row.appendChild(open);
  if(!n.read_at&&!n.resolved_at){const read=btn('Mark read','notifications.read',n.mission_id);read.addEventListener('click',e=>{e.stopPropagation();void markMissionNotice(n.id);});row.appendChild(read);}
  host.appendChild(row);
 }
 if(!missionNotices.items?.length)host.appendChild(el('p','vp3-teams-empty','No mission notifications yet.'));
}
function publishProgress(){
 renderNotices(noticeHost);renderLiveSummary();
 window.dispatchEvent(new CustomEvent('vp3:mission-notifications',{detail:{unread:Number(missionNotices.unread||0),connected:progressConnected}}));
 const nativeHost=document.getElementById('agentBrainMissionNotifications');if(nativeHost)renderNotices(nativeHost);
 const brain=document.querySelector('[data-agent-teams-brain-a3]');if(brain?.parentNode)renderBrain(brain.parentNode);
}
window.VP3_AGENT_MISSION_NOTIFICATIONS=renderNotices;
function clearPrivateMissionState(){
 authorityGeneration++;viewGeneration++;
 items=[];missionNotices={items:[],unread:0};selected='';progressConnected=false;
 detail.replaceChildren();detail.hidden=true;const diagnostic=root.querySelector('.vp3-integration-diagnostic');if(diagnostic){diagnostic.replaceChildren();diagnostic.hidden=true;}
 for(const cache of [toolsByMission,toolDrafts,changesByMission,staffingByMission,liveByWorker,browserByWorker,pendingOperations])cache.clear();
 followupReview=null;followupRetry=null;followupHost.replaceChildren();followupHost.hidden=true;storeFollowup();
 schedules=[];schedulerHealth=null;healthReceivedAt=0;renderScheduleList();
 // Pending route/schedule intents contain private objectives and must not survive revocation.
 try{for(const key of Object.keys(sessionStorage)){if(key.startsWith('vp3.chat-task.pending.a5c7.'+String(cfg.csrf))||key.startsWith('vp3.schedule.pending.a5c6.'+String(cfg.csrf)))sessionStorage.removeItem(key);}}catch(_){}
 showList();publishProgress();
}
async function markMissionNotice(id){
 if(busy)return;
 setBusy(true);
 try{const result=await api('notifications.read',{notice_id:id});missionNotices=result.notifications||missionNotices;publishProgress();}
 catch(e){say(e.message||'Notification remains unread. Retry after reconnecting.');}
 finally{setBusy(false);}
}
async function pollProgress(){
 if(progressInflight||busy||document.hidden)return;
 progressInflight=true;const generation=viewGeneration,context=JSON.stringify(taskContext());
 const controller=new AbortController(),timer=window.setTimeout(()=>controller.abort(),26000);
 try{
  const response=await api('progress.feed',{},controller.signal);
  if(busy||generation!==viewGeneration||context!==JSON.stringify(taskContext()))return;
  if(!Array.isArray(response.items)||!response.notifications||!Array.isArray(response.notifications.items))throw new Error('Update HomeServer to enable live mission progress.');
  items=response.items;missionNotices=response.notifications;progressReceived=Date.now();progressConnected=true;lastRefresh=Date.now();
  for(const m of items){if(m.private||m.authority_current===false){toolsByMission.delete(m.id);toolDrafts.delete(m.id);changesByMission.delete(m.id);for(const key of liveByWorker.keys()){if(key.startsWith(m.id+'|')){liveByWorker.delete(key);browserByWorker.delete(key);}}}}
  showList();const current=items.find(m=>m.id===selected);if(current&&(current.private||current.authority_current===false||!detail.contains(document.activeElement)))showMission(current);publishProgress();
 }catch(e){
  if(!busy&&generation===viewGeneration){progressConnected=false;if([401,403,419].includes(e.status))clearPrivateMissionState();publishProgress();say('Mission updates disconnected. Last received status is shown; no work is replayed.');}
 }finally{window.clearTimeout(timer);progressInflight=false;}
}
window.setInterval(pollProgress,5000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)void pollProgress();});
window.addEventListener('online',()=>void pollProgress());
void pollProgress();

function showList(){
 list.replaceChildren();
 if(!items.length){list.appendChild(el('p','vp3-teams-empty','No missions yet.'));return;}
 for(const item of items){
  const row=el('div','vp3-teams-item'),main=el('div','vp3-teams-item-main');
  main.appendChild(el('strong','',item.objective||'Mission'));
  main.appendChild(el('small','',missionProgress(item)+' · '+fmt(item.updated_at||item.created_at)+' · '+(item.tasks||[]).length+' workers'));
  row.appendChild(main);row.appendChild(btn(selected===item.id?'Viewing':'View','get',item.id));list.appendChild(row);
 }
}
function draftKey(node){return ['ownerValueTask','ownerControlTask','ownerSearchTask','domApprovalTask','browserTask'].map(k=>node.dataset[k]?k+'|'+node.dataset[k]:'').join('');}
function showMission(m){
 const draftValues=new Map(Array.from(detail.querySelectorAll('input[data-owner-value-task],select[data-owner-control-task],select[data-owner-search-task],input[data-dom-approval-task],select[data-dom-approval-task],input[data-browser-task]')).map(node=>[draftKey(node),node.value]));
 const openBrowsers=new Set(Array.from(detail.querySelectorAll('.vp3-worker-browser[open]'))
   .map(pane=>pane.dataset.browserWorker));
 selected=String(m.id||'');detail.hidden=false;detail.replaceChildren();
 detail.appendChild(el('h3','',m.objective||'Mission'));
 detail.appendChild(el('p','vp3-teams-meta',missionProgress(m)+' · '+fmt(m.updated_at||m.created_at)+' · Specialists prepare changes; saving requires your review'));
 const buttons=el('div','vp3-teams-actions');
 if(m.status==='planned'){buttons.appendChild(btn('Review specialist assignments','tools.get',selected));if(!m.chat_task||m.tools_configured){const start=btn('Start',m.tools_configured||toolsByMission.get(selected)?.configured?'tools.start':'start',selected);const needs=toolsByMission.get(selected)?.browser_requirements||[];start.disabled=needs.some(r=>!r.ready||!r.expires_at||Date.parse(r.expires_at.replace(' ','T')+'Z')<=Date.now());start.dataset.browserBlocked=String(start.disabled);buttons.appendChild(start);}}
 if(m.status==='running')buttons.appendChild(btn('Pause','pause',selected));
 if(m.status==='waiting_review')buttons.appendChild(btn('Resume (rerun interrupted)','resume',selected));
 if(['planned','running','waiting_review'].includes(m.status))buttons.appendChild(btn('Cancel','cancel',selected));
 buttons.appendChild(btn('Refresh','get',selected));detail.appendChild(buttons);
 if(m.continuity?.successor_id){detail.appendChild(el('p','vp3-teams-meta','Superseded by a reviewed scope change. Completed findings and saved changes remain in history.'));detail.appendChild(btn('Open revised mission','get',m.continuity.successor_id));}
 if(m.continuity?.predecessor_id){detail.appendChild(el('p','vp3-teams-meta','Continues a prior mission. Completed findings are reused under current permissions.'));detail.appendChild(btn('Open prior mission','get',m.continuity.predecessor_id));}
 if(m.authority_current===false)detail.appendChild(el('p','vp3-teams-error','Permissions changed. Results are hidden; prepare a new reviewed mission.'));
 if(m.chat_task&&m.status==='planned'&&!m.tools_configured&&!toolDrafts.has(m.id))toolDrafts.set(m.id,m.chat_task.draft);
 if(m.chat_task?.automatic_reason&&!m.private&&m.authority_current!==false)detail.appendChild(el('p','vp3-teams-meta','Lead chose specialists: '+m.chat_task.automatic_reason+' · '+fmt(m.chat_task.decided_at)));
 if(m.chat_task&&!m.private&&m.authority_current!==false)detail.appendChild(el('p','vp3-teams-meta','Plan prepared '+fmt(m.chat_task.prepared_at)+' · '+m.chat_task.provider_key+' · '+m.chat_task.model+' · assignment review required before tools run'));
 renderAssignments(m);
 renderScheduleForm(m);
 renderChanges(m);
 renderCompletion(m,detail);
 const workers=el('ol','vp3-teams-workers');
 (m.tasks||[]).forEach(t=>{
  const li=el('li','');li.appendChild(el('strong','',t.title||t.role||'Worker'));
  li.appendChild(el('small','',(t.role||'specialist')+' · '+workerProgress(t)+(t.model?' · '+t.model:'')));
  if(t.error)li.appendChild(el('p','vp3-teams-error',t.error));
  if(['failed','partial','waiting_review'].includes(m.status)&&['failed','interrupted'].includes(t.status)&&t.error!=='Dependency failed')li.appendChild(btn('Retry worker','retry',selected,t.id));
  li.appendChild(el('small','','Read calls used: '+(t.read_calls_used||0)+' · budgets include retries'));
  if(t.result){const exp=el('details','vp3-teams-result');exp.appendChild(el('summary','','Worker result'));
   let text=t.result;try{const out=JSON.parse(text);if(out.kind&&out.body)text=out.title+'\n\n'+out.body+'\n\nEvidence IDs: '+(out.citations||[]).join(', ');}catch(_){}
   exp.appendChild(el('pre','',text));li.appendChild(exp);}
  for(const source of t.browser_sources||[]){try{const url=new URL(source.url);if(url.protocol!=='https:'||url.username||url.password)continue;const evidence=el('p','vp3-browser-source');const link=el('a','',source.title||url.hostname);link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';evidence.appendChild(link);evidence.appendChild(el('small','',' · '+source.citation_id+' · '+fmt(source.captured_at)));li.appendChild(evidence);}catch(_){}}
  const browser=el('details','vp3-worker-browser');
  browser.dataset.browserWorker=t.id;
  browser.open=openBrowsers.has(t.id);
  browser.appendChild(el('summary','','Worker browser · supervised read-only'));
  const area=el('div','vp3-worker-browser-body');
  const key=selected+'|'+t.id;
  const state=browserByWorker.get(key);
  if(state){
   area.appendChild(el('p','vp3-teams-meta','Status: '+state.status+' · '+(state.visit_count||0)+' / '+(state.max_visits||5)+' captures'));
   if(state.approved_origin)area.appendChild(el('p','vp3-teams-meta','Approved origin: '+state.approved_origin));
   if(state.page_title)area.appendChild(el('p','',state.page_title));
   if(state.image_base64&&/^[A-Za-z0-9+/=]{100,200000}$/.test(state.image_base64)){
    const preview=el('img','vp3-worker-browser-preview');
    preview.alt='Read-only screenshot for '+(t.title||'worker');
    preview.src='data:image/jpeg;base64,'+state.image_base64;
    area.appendChild(preview);
   }
   if(state.text_snapshot){
    const text=el('details','vp3-teams-result');
    text.appendChild(el('summary','','Page text evidence'));
    text.appendChild(el('pre','',state.text_snapshot));
    area.appendChild(text);
   }
   if(state.last_error)area.appendChild(el('p','vp3-teams-error',state.last_error));
  }else area.appendChild(el('p','vp3-teams-meta','No browser snapshot loaded.'));
  const entry=el('input','vp3-worker-browser-url');
  entry.type='url';entry.placeholder='https://public-site.example/page';
  entry.setAttribute('aria-label','Approved HTTPS page for '+(t.title||'worker'));
  entry.dataset.browserTask=t.id;
  entry.maxLength=1400;
  const assignment=((toolsByMission.get(m.id)?.configured?toolsByMission.get(m.id)?.assignments:toolDrafts.get(m.id)||m.chat_task?.draft)?.assignments||[]).find(a=>a.task_id===t.id);
  if(assignment?.browser_url){entry.value=assignment.browser_url;area.appendChild(el('p','vp3-teams-meta','Proposed research website: '+assignment.browser_url+' · separate origin approval required.'));}
  if(state&&state.current_url)entry.value=state.current_url;
  area.appendChild(entry);
  const actions=el('div','vp3-teams-actions');
  if(t.status==='queued'&&m.status==='planned'&&(!state||state.status==='closed')){
   actions.appendChild(btn('Approve URL','browser.grant',selected,t.id));
  }
  if(state&&state.status==='approved'){
   if(['planned','running'].includes(m.status))actions.appendChild(btn('Capture page','browser.capture',selected,t.id));
   actions.appendChild(btn('Revoke','browser.revoke',selected,t.id));
  }
  actions.appendChild(btn('View browser','browser.get',selected,t.id));
  const livePanel=el('section','vp3-worker-live');
  livePanel.setAttribute('data-live-browser','');
  const live=liveByWorker.get(key);
  livePanel.appendChild(el('strong','','Live browser workspace'));
  livePanel.appendChild(el('p','vp3-teams-meta',
    live?(live.status+' · '+(live.visit_count||0)+' / '+(live.max_visits||5)+' navigations · '+
    (live.session_active?'Session connected':'Session stopped')):'No live session'));
  if(live?.current_url)livePanel.appendChild(el('p','vp3-teams-meta','Page: '+live.current_url));
  if(live?.image_base64&&/^[A-Za-z0-9+/=]{100,200000}$/.test(live.image_base64)){
   const screen=el('img','vp3-worker-live-preview');
   screen.src='data:image/jpeg;base64,'+live.image_base64;
   screen.alt='Current read-only browser screenshot for '+(t.title||'worker');
   livePanel.appendChild(screen);
  }
  const proposal=live?.proposed_link;
  if(proposal&&proposal.id){
   const pending=el('p','vp3-teams-meta','Agent suggests: '+(proposal.title||proposal.url));
   livePanel.appendChild(pending);
   livePanel.appendChild(el('p','',proposal.reason||''));
  }
  const controlProposal=live?.proposed_action;
  if(controlProposal?.id){
   const interaction=el('section','vp3-dom-approval');
   interaction.appendChild(el('strong','','Agent suggests '+controlProposal.kind+': '+controlProposal.label));
   interaction.appendChild(el('p','vp3-teams-meta',controlProposal.reason||''));
   if(controlProposal.kind==='fill'){
    const input=el('input','vp3-dom-approval-value');
    input.type='text';input.maxLength=300;
    input.placeholder='Enter text to fill (never sent to the agent)';
    input.setAttribute('aria-label','Text to fill in '+controlProposal.label);
    input.dataset.domApprovalTask=t.id;
    interaction.appendChild(input);
   }else if(controlProposal.kind==='select'){
    const selector=el('select','vp3-dom-approval-value');
    selector.dataset.domApprovalTask=t.id;
    selector.setAttribute('aria-label','Choose option for '+controlProposal.label);
    (controlProposal.options||[]).forEach(option=>{
     const entry=el('option','',option.label);
     entry.value=String(option.index);selector.appendChild(entry);
    });
    interaction.appendChild(selector);
   }else{
    interaction.appendChild(el('p','vp3-teams-meta','Requires explicit confirmation. No form submission is allowed.'));
   }
   interaction.appendChild(btn('Approve safe control','browser.action.approve',selected,t.id));
   livePanel.appendChild(interaction);
  }
  const owner=live?.owner_takeover||{mode:'agent',actions_used:0,max_actions:8,forms:[]};
  const takeover=el('section','vp3-owner-takeover');
  takeover.appendChild(el('strong','',owner.mode==='owner'?'Owner controlling browser':'Owner takeover'));
  takeover.appendChild(el('p','vp3-teams-meta',owner.mode==='owner'?
   'Agent paused · '+owner.actions_used+'/'+owner.max_actions+' actions · until '+fmt(owner.expires_at):
   'Take exclusive control for up to five minutes; agent proposals pause.'));
  if(live?.session_active&&m.status==='planned'&&t.status==='queued'){
   if(owner.mode==='owner'){
    const safe=Array.isArray(live.controls)?live.controls:[];
    if(safe.length){
     const pick=el('select','vp3-owner-control-select');
     pick.dataset.ownerControlTask=t.id;
     pick.setAttribute('aria-label','Select safe browser control');
     safe.forEach(item=>{
      const opt=el('option','',item.label+' ('+item.kind+')');
      opt.value=String(item.index);pick.appendChild(opt);
     });
     takeover.appendChild(pick);
     const entered=el('input','vp3-owner-control-value');
     entered.type='text';entered.maxLength=300;
     entered.placeholder='Text or select option number';
     entered.setAttribute('aria-label','Value for selected browser control');
     entered.dataset.ownerValueTask=t.id;
     takeover.appendChild(entered);
     takeover.appendChild(btn('Apply owner control','browser.owner.control',selected,t.id));
    }
    const available=Array.isArray(owner.forms)?owner.forms:[];
    if(available.length){
     const pick=el('select','vp3-owner-search-select');
     pick.dataset.ownerSearchTask=t.id;pick.setAttribute('aria-label','Safe GET search form');
     available.forEach(f=>{const opt=el('option','',f.label+' · GET '+f.action);
      opt.value=String(f.index);pick.appendChild(opt);});
     takeover.appendChild(pick);
     takeover.appendChild(btn('Review GET search','browser.owner.search.review',selected,t.id));
    }
    if(owner.pending_form?.id&&owner.review?.id===owner.pending_form.id){
     takeover.appendChild(el('p','vp3-teams-meta','Search destination: '+owner.review.destination));
     takeover.appendChild(el('p','vp3-teams-meta','Exact query: '+owner.review.query));
     takeover.appendChild(el('p','vp3-teams-meta','Required fields: '+(owner.review.required_fields||[]).map(f=>f.name+' '+(f.valid?'valid':'missing')).join(', ')));
     takeover.appendChild(el('p','vp3-teams-meta','Your search query will appear in the destination URL. No POST actions.'));
     takeover.appendChild(btn('Confirm search submission','browser.owner.search.submit',selected,t.id));
    }
    takeover.appendChild(btn('Return control to agent','browser.owner.release',selected,t.id));
   }else takeover.appendChild(btn('Take control','browser.owner.takeover',selected,t.id));
  }
  livePanel.appendChild(takeover);
  const liveActions=el('div','vp3-teams-actions');
  if(m.status==='planned'&&t.status==='queued'&&state?.status==='approved'&&!live?.session_active)
   liveActions.appendChild(btn('Start live browser','browser.live.start',selected,t.id));
  if(live?.session_active){
   liveActions.appendChild(btn('Refresh live view','browser.live.refresh',selected,t.id));
   if(owner.mode!=='owner'&&m.status==='planned'&&t.status==='queued'&&(live.actions_used||0)<(live.max_actions||6))
    liveActions.appendChild(btn('Ask agent about page controls','browser.action.propose',selected,t.id));
   if(owner.mode!=='owner'&&live.visit_count<live.max_visits&&m.status==='planned'){
    liveActions.appendChild(btn('Ask agent for next link','browser.live.propose',selected,t.id));
    if(live.plan?.status!=='running')liveActions.appendChild(btn('Run browser plan','browser.live.plan',selected,t.id));
    if(proposal?.id)liveActions.appendChild(btn('Approve suggested navigation','browser.live.approve',selected,t.id));
   }
   liveActions.appendChild(btn('Stop live browser','browser.live.stop',selected,t.id));
  }else if(state?.status==='approved'){
   liveActions.appendChild(btn('View live status','browser.live.get',selected,t.id));
  }
  if(live?.plan?.status&&live.plan.status!=='idle'){
   livePanel.appendChild(el('p','vp3-teams-meta','Browser plan: '+live.plan.status.replaceAll('_',' ')));
   if(live.plan.status==='waiting_approval')livePanel.appendChild(el('p','','Agent prepared a search. Take control to review required fields and approve the exact submission.'));
   (live.plan.history||[]).forEach(step=>livePanel.appendChild(el('p','vp3-teams-meta',step.kind+' · '+step.outcome)));
  }
  livePanel.appendChild(liveActions);
  area.appendChild(livePanel);
  area.appendChild(actions);browser.appendChild(area);li.appendChild(browser);
  workers.appendChild(li);
 });detail.appendChild(workers);
 if(!m.tools_configured&&(['completed','partial','failed'].includes(m.status)||staffingByMission.has(selected))){
  const review=el('section','vp3-teams-supervisor');
  review.setAttribute('data-agent-teams-supervisor','');
  review.appendChild(el('h4','','Supervisor · adaptive staffing'));
  const proposals=staffingByMission.get(selected)||[];
  const latest=proposals[0];
  if(latest){
   review.appendChild(el('p','vp3-teams-meta',
     (latest.status==='proposed'?'Awaiting your approval':latest.status.replaceAll('_',' '))+
     ' · '+fmt(latest.created_at)+(latest.private?' · Private HomeServer review':' · Confidence '+latest.confidence+'%')));
   if(latest.reason)review.appendChild(el('p','vp3-teams-supervisor-reason',latest.reason));
   const roster=el('ol','vp3-teams-supervisor-candidates');
   (latest.tasks||[]).forEach(t=>{
    const li=el('li','');li.appendChild(el('strong','',t.title||t.role||'Specialist'));
    li.appendChild(el('small','',(t.role||'specialist')+' · '+(t.objective||'')));
    roster.appendChild(li);
   });
   review.appendChild(roster);
   if(latest.status==='proposed'){
    review.appendChild(btn('Approve new workers','approve',selected,latest.id));
    review.appendChild(btn('Reject proposal','reject',selected,latest.id));
   }
  }else{
   review.appendChild(el('p','vp3-teams-meta','No additional staffing review yet.'));
  }
  if(['completed','partial','failed'].includes(m.status)&&!(latest&&latest.status==='proposed')&&proposals.filter(p=>p.status==='approved').length<2){
   review.appendChild(btn('Evaluate need for specialists','evaluate',selected));
  }
  detail.appendChild(review);
 }
 if(m.result){const result=el('details','vp3-teams-result');
  result.appendChild(el('summary','','Combined output (not independently verified)'));
  result.appendChild(el('pre','',m.result));detail.appendChild(result);}
 const events=(m.events||[]).slice(-10);
 if(events.length){detail.appendChild(el('h4','','Recent activity'));const history=el('ol','vp3-teams-events');
  events.forEach(e=>history.appendChild(el('li','',fmt(e.created_at)+' · '+(e.kind||'event'))));
  detail.appendChild(history);}
 const workspaceActions=new Set(['get','actions.list','actions.review','actions.recover','tools.get','tools.configure','tools.start','start','pause','resume','cancel','retry']);
 detail.querySelectorAll('button[data-action]').forEach(button=>{if(!button.dataset.action.startsWith('browser.')&&!button.dataset.action.startsWith('schedule.')&&!workspaceActions.has(button.dataset.action))button.remove();});
 detail.querySelectorAll('input[data-owner-value-task],select[data-owner-control-task],select[data-owner-search-task],input[data-dom-approval-task],select[data-dom-approval-task],input[data-browser-task]').forEach(node=>{const value=draftValues.get(draftKey(node));if(value!==undefined)node.value=value;});
 showList();
}
function renderBrain(body){
 if(!body)return;
 const old=body.querySelector('[data-agent-teams-brain-a3]');if(old)old.remove();
 const section=el('section','chat-activity-section vp3-agent-teams-brain-a3');
 section.setAttribute('data-agent-teams-brain-a3','');
 const scheduled=el('section','vp3-schedules-brain');renderScheduleList(scheduled);section.appendChild(scheduled);
 const header=el('div','chat-activity-section-head');
 const name=el('div','');name.appendChild(el('strong','','HomeServer Agent Teams'));
 name.appendChild(el('span','','Mission execution · '+(lastRefresh?fmt(new Date(lastRefresh).toISOString()):'not yet synchronized')));
 header.appendChild(name);
 const open=btn('Open in Chat','open-team-chat','');open.dataset.action='open-team-chat';open.addEventListener('click',()=>{root.open=true;root.scrollIntoView({block:'nearest'});});header.appendChild(open);
 section.appendChild(header);
 const recent=items.slice(0,5);
 if(!recent.length)section.appendChild(el('p','chat-activity-empty','No HomeServer mission activity is available.'));
 for(const m of recent){
  const row=el('div','vp3-teams-brain-row');
  row.appendChild(el('strong','',m.objective||'Mission'));
  const owned=[...liveByWorker.entries()].some(([k,v])=>k.startsWith(m.id+'|')&&v?.owner_takeover?.mode==='owner');
  if(m.chat_task?.automatic_reason&&!m.private&&m.authority_current!==false)row.appendChild(el('p','vp3-teams-meta','Lead chose specialists: '+m.chat_task.automatic_reason+' · '+fmt(m.chat_task.decided_at)));
  row.appendChild(el('small','',missionProgress(m)+' · '+fmt(m.updated_at||m.created_at)+' · '+(m.tasks||[]).length+' workers'+
   (owned?' · Owner controlling browser':'')+
   ((staffingByMission.get(m.id)||[]).some(p=>p.status==='proposed')?' · Staffing approval needed':'')));
  renderCompletion(m,row);
  for(const task of m.tasks||[]){
   const view=liveByWorker.get(m.id+'|'+task.id);
   const worker=el('details','vp3-brain-worker');
   worker.appendChild(el('summary','',task.title||task.role||'Worker'));
   worker.appendChild(el('p','vp3-teams-meta',workerProgress(task)));
   if(view){
    worker.appendChild(el('p','','Viewing: '+(view.page_title||'Untitled')+' · '+(view.current_url||'')));
    worker.appendChild(el('p','','Control: '+(view.owner_takeover?.mode||'agent')+' · Approval: '+(view.owner_takeover?.pending_form?.id?'form awaiting confirmation':view.proposed_action?.id||view.proposed_link?.id?'action awaiting approval':view.plan?.status==='running'?'approved read plan':'none pending')));
    const proposal=view.proposed_action||{};
    const next=view.proposed_link||{};
    worker.appendChild(el('p','','Proposed action: '+(proposal.label||next.title||view.plan?.status||'none')));
    worker.appendChild(el('p','vp3-teams-meta','Evidence captured: '+fmt(view.screenshot_at)));
    if(view.image_base64&&/^[A-Za-z0-9+/=]{100,200000}$/.test(view.image_base64)){
     const preview=el('img','vp3-worker-browser-preview');preview.alt='Evidence for '+(task.title||'worker');preview.src='data:image/jpeg;base64,'+view.image_base64;worker.appendChild(preview);
    }
    const evidence=el('details','vp3-teams-result');evidence.appendChild(el('summary','','Page evidence'));evidence.appendChild(el('pre','',view.page_text||'No page evidence.'));worker.appendChild(evidence);
   }else worker.appendChild(el('p','','No active browser evidence.'));
   (m.action_summaries||[]).filter(c=>c.task_id===task.id).forEach(c=>worker.appendChild(el('p','vp3-teams-meta',c.action_key+' · '+(c.outcome_state||c.status)+' · '+fmt(c.checked_at||c.executed_at||c.created_at))));
   (m.events||[]).filter(e=>e.task_id===task.id).slice(-10).forEach(e=>worker.appendChild(el('p','vp3-teams-meta',fmt(e.created_at)+' · '+e.kind)));
   const openWorker=btn('Open worker in Chat','open-team-chat',m.id,task.id);
   openWorker.addEventListener('click',()=>operation('open-team-chat',m.id,task.id));worker.appendChild(openWorker);
   row.appendChild(worker);
  }
  section.appendChild(row);
 }
 body.prepend(section);
 if(!inflight&&!busy&&Date.now()-lastRefresh>20000){
  // The canonical Brain drawer can request a fresh mission projection, but
  // it must not block or replace the drawer's own Brain rendering.
  lastRefresh=Date.now();
  load().then(()=>{if(body.isConnected&&body.closest('#chatBrainDrawer')?.querySelector('[data-notification-tab="brain"].active'))renderBrain(body);})
    .catch(()=>{});
 }
}
window.VP3_AGENT_TEAMS_A3_BRAIN=renderBrain;
window.VP3_AGENT_TEAMS_A5B4_STATUS=pollBrowserStatus;
async function loadSupervision(mid){
 const result=await api('decisions',{mission_id:mid});
 const records=Array.isArray(result.items)?result.items:[];
 staffingByMission.set(mid,records);
 if(mid===selected){
  const mission=await api('get',{mission_id:mid});
  if(mission.mission)showMission(mission.mission);
 }
}
async function load(){
 if(inflight)return;
 inflight=true;
 try{
  const result=await api('list');items=Array.isArray(result.items)?result.items:[];lastRefresh=Date.now();showList();renderLiveSummary();await loadSchedules();
  say('HomeServer connected · '+items.length+' recent mission'+(items.length===1?'':'s')+'.');
  if(selected&&items.some(x=>x.id===selected)){const current=await api('get',{mission_id:selected});if(current.mission){if(current.mission.chat_task&&current.mission.status==='planned'&&!current.mission.private&&current.mission.authority_current!==false){const tools=await api('tools.get',{mission_id:selected});toolsByMission.set(selected,tools.tools);}if(current.mission.tools_enabled&&!current.mission.private&&current.mission.authority_current!==false){const changes=await api('actions.list',{mission_id:selected});changesByMission.set(selected,Array.isArray(changes.actions)?changes.actions:[]);}showMission(current.mission);}
   try{await loadSupervision(selected);}catch(_){/* Mission status remains available if supervision is unsupported. */}}
 }catch(error){unavailableHealth();throw error;}finally{inflight=false;}
}
function renderCompletion(m,parent){
 if(m.private||m.authority_current===false||!m.completion_report)return;
 const report=m.completion_report;
 const panel=el('section','vp3-specialist-completion');panel.setAttribute('aria-label','Lead agent completion report');
 panel.appendChild(el('h4','','Lead agent completion report'));
 panel.appendChild(el('p','',report.state+' · '+report.summary));
 if(report.next_action)panel.appendChild(el('p','vp3-completion-next-action',report.next_action));
 panel.appendChild(el('small','','Saved changes are verified separately from model-generated findings.'));
 parent.appendChild(panel);
}
function renderChanges(m){
 if(m.authority_current===false||m.private)return;
 const changes=changesByMission.get(m.id)||[];
 const panel=el('section','vp3-specialist-changes');panel.setAttribute('aria-label','Prepared specialist changes');
 panel.appendChild(el('h4','','Prepared changes'));
 panel.appendChild(el('p','','Research completion does not mean changes were saved. Review each exact change below.'));
 if(!changes.length)panel.appendChild(el('p','','No prepared changes.'));
 for(const change of changes){
  const card=el('article','vp3-specialist-change');card.dataset.changeId=change.id;
  const worker=(m.tasks||[]).find(x=>x.id===change.task_id);
  card.appendChild(el('strong','',change.action_key+' · '+(worker?.title||'Specialist')));
  card.appendChild(el('small','',(change.outcome_state||change.status)+' · '+change.destination+' · '+fmt(change.checked_at||change.executed_at||change.created_at)));
  const preview=el('details','vp3-teams-result');preview.open=true;preview.appendChild(el('summary','','Exact proposed change'));preview.appendChild(el('pre','',JSON.stringify(change.arguments,null,2)));card.appendChild(preview);
  if(change.status==='pending'){
   if(change.can_approve){const approve=btn('Approve this change','actions.review',m.id,change.id);approve.dataset.decision='approve';card.appendChild(approve);}
   else card.appendChild(el('p','','This review expired or was interrupted. Prepare a new mission.'));
   const reject=btn('Reject','actions.review',m.id,change.id);reject.dataset.decision='deny';card.appendChild(reject);
  }
  if(change.outcome_message)card.appendChild(el('p','',change.outcome_message));
  if(change.verified_at)card.appendChild(el('small','','Last verified '+fmt(change.verified_at)));
  if(change.execution_tool_run_id)card.appendChild(el('small','','Execution receipt '+change.execution_tool_run_id));
  if(change.can_recover)card.appendChild(btn('Retry Cloud delivery','actions.recover',m.id,change.id));
  panel.appendChild(card);
 }
 if(m.tools_enabled)panel.appendChild(btn('Refresh prepared changes','actions.list',m.id));
 detail.appendChild(panel);
}
function renderAssignments(m){
 const contract=toolsByMission.get(m.id);if(!contract)return;
 const panel=el('section','vp3-specialist-assignments');panel.setAttribute('aria-label','Specialist assignments');
 panel.appendChild(el('h4','','Specialist assignments'));
 panel.appendChild(el('p','','Review revision '+contract.revision+' · '+(contract.active?'expires '+fmt(contract.expires_at):'a fresh review is required')+'. Read budgets are cumulative across retries. Outputs are model generated and require your review.'));
 if(m.status!=='planned'){detail.appendChild(panel);return;}
 for(const r of contract.browser_requirements||[]){const row=el('p','vp3-browser-requirement',r.origin+' · '+(r.ready?'Website approved until '+fmt(r.expires_at):'Website approval required before Start'));row.dataset.browserRequirement=r.task_id;panel.appendChild(row);}
 const draft=toolDrafts.get(m.id)||contract.assignments||{};
 const field=(node,key,tid,value)=>{node.dataset.toolField=key;if(tid)node.dataset.toolTask=tid;node.value=String(value);return node;};
 const label=(text,node)=>{const x=el('label','',text+' ');x.appendChild(node);return x;};
 const parallel=field(el('input'),'max_parallel','',draft.max_parallel||2);parallel.type='number';parallel.min='1';parallel.max='4';
 panel.appendChild(label('Concurrent workers (1–4)',parallel));
 for(const task of m.tasks||[]){
  const entry=(draft.assignments||[]).find(a=>a.task_id===task.id)||{tools:[],max_calls:0,output:'analysis',browser_url:''};
  const group=el('fieldset');group.appendChild(el('legend','',task.title||task.role));
  for(const capability of contract.capabilities||[]){const checkbox=field(el('input'),'tool',task.id,capability);checkbox.type='checkbox';checkbox.checked=entry.tools.includes(capability);group.appendChild(label(capability,checkbox));}
  for(const capability of contract.action_capabilities||[]){const checkbox=field(el('input'),'action',task.id,capability);checkbox.type='checkbox';checkbox.checked=(entry.actions||[]).includes(capability);group.appendChild(label('Propose '+capability,checkbox));}
  if(contract.action_capabilities){const actionBudget=field(el('input'),'max_actions',task.id,entry.max_actions||0);actionBudget.type='number';actionBudget.min='0';actionBudget.max=String(contract.max_actions_per_worker||3);group.appendChild(label('Prepared changes (0–3; approval required)',actionBudget));}
  const calls=field(el('input'),'max_calls',task.id,entry.max_calls);calls.type='number';calls.min='0';calls.max=String(contract.max_calls_per_worker||3);group.appendChild(label('Read calls (0–3)',calls));
  const output=el('select');for(const kind of ['analysis','sources','document']){const option=el('option','',kind);option.value=kind;output.appendChild(option);}field(output,'output',task.id,entry.output);group.appendChild(label('Output',output));
  const url=field(el('input'),'browser_url',task.id,entry.browser_url);url.type='url';url.maxLength=1400;url.placeholder='Separately approved HTTPS URL';group.appendChild(label('Browser URL',url));panel.appendChild(group);
 }
 panel.appendChild(btn('Approve assignments','tools.configure',m.id));detail.appendChild(panel);
}
function assignmentDraft(){
 const panel=detail.querySelector('.vp3-specialist-assignments');if(!panel)return null;
 const value={max_parallel:Number(panel.querySelector('[data-tool-field="max_parallel"]').value),assignments:[]};
 for(const group of panel.querySelectorAll('fieldset')){
  const nodes=Array.from(group.querySelectorAll('[data-tool-field]'));const task=nodes[0]?.dataset.toolTask;
  const get=key=>nodes.find(n=>n.dataset.toolField===key)?.value||'';
  value.assignments.push({task_id:task,tools:nodes.filter(n=>n.dataset.toolField==='tool'&&n.checked).map(n=>n.value),max_calls:Number(get('max_calls')),...(nodes.some(n=>n.dataset.toolField==='max_actions')?{actions:nodes.filter(n=>n.dataset.toolField==='action'&&n.checked).map(n=>n.value),max_actions:Number(get('max_actions'))}:{}),output:get('output'),browser_url:get('browser_url')});
 }
 return value;
}
root.addEventListener('input',event=>{if(event.target.dataset.toolField){const draft=assignmentDraft();if(draft)toolDrafts.set(selected,draft);}});
function renderDiagnostic(report){
 const area=root.querySelector('.vp3-integration-diagnostic');area.replaceChildren();area.hidden=false;area.appendChild(el('h4','','Installed integration diagnostic'));area.appendChild(el('p','',report.ready?'Checks are ready.':'Some installed checks need review.'));
 for(const c of [...(report.checks||[]),...(report.homeserver?.checks||[])]){const row=el('p','',c.key+' · '+c.status+' · '+c.detail+(c.next_action?' '+c.next_action:''));area.appendChild(row);}
 area.appendChild(el('small','','Checked '+fmt(report.checked_at)+' · read-only · no model tokens or repairs.'));
}
const diagnosticHost=el('section','vp3-integration-diagnostic');diagnosticHost.hidden=true;root.querySelector('.vp3-teams-body').appendChild(btn('Check integration','diagnostics.get'));root.querySelector('.vp3-teams-body').appendChild(diagnosticHost);
async function operation(action,id,taskId,decision){
 if(action==='diagnostics.get'){if(busy)return;setBusy(true);say('Checking installed integration…');try{const r=await api(action);renderDiagnostic(r.diagnostics);say('Integration diagnostic complete.');}catch(e){diagnosticHost.replaceChildren();diagnosticHost.hidden=true;say(e.message);}finally{setBusy(false);}return;}
 if(action.startsWith('schedule.'))return scheduleOperation(action,id);
 if(action==='open-team-chat'){root.open=true;root.scrollIntoView({block:'nearest'});if(id)operation('get',id,taskId);return;}
 if(busy)return;
 if(action==='actions.recover'){
  const change=(changesByMission.get(id)||[]).find(x=>x.id===taskId);
  if(!change?.can_recover||!window.confirm('Resume this approved queued Cloud change? The original change ID and fields will be retained.'))return;
 }
 if(action==='actions.review'){
  const change=(changesByMission.get(id)||[]).find(x=>x.id===taskId);
  if(!change||!['approve','deny'].includes(decision))return;
  if(!window.confirm((decision==='approve'?'Apply this exact change?':'Reject this prepared change?')+'\n'+change.action_key+' · '+change.destination+'\n\n'+JSON.stringify(change.arguments,null,2)))return;
 }
 if(action==='tools.configure'&&!window.confirm('Approve these exact read capabilities, prepared-change permissions, cumulative budgets and output formats for 15 minutes? Each change requires a separate review before saving.'))return;
 if(action==='tools.start'&&!window.confirm('Start the reviewed specialists? Model and approved read usage will occur within the displayed budgets.'))return;
 if(action==='resume'&&!window.confirm('Resume and rerun interrupted model work?'))return;
 if(action==='retry'&&!window.confirm('Retry this worker? The model request may use additional tokens.'))return;
 if(action==='approve'&&!window.confirm('Approve these new read-only workers? Additional model usage will occur.'))return;
 if(action==='reject'&&!window.confirm('Reject this staffing proposal? No workers will be created.'))return;
 if(action==='browser.grant'&&!window.confirm('Approve this read-only HTTPS origin for the selected worker for 15 minutes?'))return;
 if(action==='browser.revoke'&&!window.confirm('Revoke this worker browser and erase its stored snapshot?'))return;
 if(action==='browser.live.start'&&!window.confirm('Open an isolated 10-minute browser session on this approved HTTPS origin?'))return;
 if(action==='browser.live.plan'&&!window.confirm('Allow up to three read steps on this approved origin? Forms stop for your review; purchases, messages and publishing remain blocked.'))return;
 if(action==='browser.live.approve'&&!window.confirm('Approve the agent-suggested link? Navigation will remain read-only on the approved origin.'))return;
 if(action==='browser.action.approve'&&!window.confirm('Approve this exact non-submitting browser control action? Text is supplied only by you.'))return;
 if(action==='browser.owner.takeover'&&!window.confirm('Take exclusive control for up to five minutes and pause the agent?'))return;
 if(action==='browser.owner.release'&&!window.confirm('Return browser control to the agent?'))return;
 if(action==='browser.owner.control'&&!window.confirm('Apply this selected safe browser control yourself?'))return;
 if(action==='browser.owner.search.submit'&&!window.confirm('Submit this one approved same-origin GET search? Its query appears in the URL.'))return;
 setBusy(true);
 try{const payload={mission_id:id};if(action==='resume')payload.allow_reexecution=true;
  if(action==='actions.recover'){
   const change=(changesByMission.get(id)||[]).find(x=>x.id===taskId);
   if(!change?.can_recover)throw new Error('Refresh the queued change first.');
   payload.action_id=change.id;payload.expected_hash=change.payload_hash;payload.confirmed=true;
   const key=action+'|'+id+'|'+JSON.stringify(payload);payload.request_id=pendingOperations.get(key)||window.crypto.randomUUID();pendingOperations.set(key,payload.request_id);
  }
  if(action==='actions.review'){
   const change=(changesByMission.get(id)||[]).find(x=>x.id===taskId);
   if(!change)throw new Error('Refresh and review the current change first.');
   payload.action_id=change.id;payload.expected_hash=change.payload_hash;payload.decision=decision;payload.confirmed=true;
   const key=action+'|'+id+'|'+JSON.stringify(payload);payload.request_id=pendingOperations.get(key)||window.crypto.randomUUID();pendingOperations.set(key,payload.request_id);
  }
  if(action==='tools.configure'||action==='tools.start'){
   const contract=toolsByMission.get(id);if(!contract)throw new Error('Load and review specialist assignments first.');
   payload.expected_revision=contract.revision;payload.confirmed=true;
   if(action==='tools.configure')payload.assignments=assignmentDraft();
   const key=action+'|'+id+'|'+JSON.stringify(payload);payload.request_id=pendingOperations.get(key)||window.crypto.randomUUID();pendingOperations.set(key,payload.request_id);
  }
  if(action==='retry')payload.task_id=taskId;
  if(action.startsWith('browser.owner.')){
   payload.task_id=taskId;
   const current=liveByWorker.get(id+'|'+taskId)||{};
   const takeover=current.owner_takeover||{};
   if(action!=='browser.owner.takeover'){if(!takeover.lease_id)throw new Error('Owner lease expired. Refresh browser status.');payload.lease_id=takeover.lease_id;}
   if(action==='browser.owner.control'){
    const pick=Array.from(detail.querySelectorAll('[data-owner-control-task]'))
      .find(x=>x.dataset.ownerControlTask===taskId);
    const item=(current.controls||[]).find(x=>x.index===Number(pick?.value));
    if(!item)throw new Error('Select a safe browser control.');
    payload.index=item.index;payload.fingerprint=item.fingerprint;payload.kind=item.kind;
    const value=Array.from(detail.querySelectorAll('[data-owner-value-task]'))
      .find(x=>x.dataset.ownerValueTask===taskId)?.value||'';
    payload.value=item.kind==='fill'?value:item.kind==='select'?Number(value):true;
    payload.confirmed=true;
    const key=id+'|'+taskId+'|'+JSON.stringify(payload);
    payload.request_id=pendingOperations.get(key)||window.crypto.randomUUID();pendingOperations.set(key,payload.request_id);
   }
   if(action==='browser.owner.search.review'){
    const pick=Array.from(detail.querySelectorAll('[data-owner-search-task]'))
      .find(x=>x.dataset.ownerSearchTask===taskId);
    const form=(takeover.forms||[]).find(x=>x.index===Number(pick?.value));
    if(!form)throw new Error('Select an approved GET search.');
    payload.index=form.index;payload.fingerprint=form.fingerprint;
   }
   if(action==='browser.owner.search.submit'){
    if(!takeover.pending_form?.id||takeover.review?.id!==takeover.pending_form.id)throw new Error('Search review has expired.');
    payload.proposal_id=takeover.pending_form.id;payload.confirmed=true;
   }
   say('Updating owner browser controls…');
  }else if(action.startsWith('browser.action.')){
   payload.task_id=taskId;
   if(action==='browser.action.approve'){
    const proposal=liveByWorker.get(id+'|'+taskId)?.proposed_action;
    if(!proposal?.id)throw new Error('Safe-control proposal expired or missing.');
    payload.proposal_id=proposal.id;payload.confirmed=true;
    if(proposal.kind==='fill'||proposal.kind==='select'){
     const field=Array.from(detail.querySelectorAll('[data-dom-approval-task]'))
       .find(node=>node.dataset.domApprovalTask===taskId);
     if(!field)throw new Error('Choose a value before approving this action.');
     payload.value=proposal.kind==='fill'?field.value:Number(field.value);
    }else payload.value=true;
   }
   say('Applying the explicitly approved safe page control…');
  }else if(action.startsWith('browser.live.')){
   payload.task_id=taskId;
   if(action==='browser.live.plan'){const key=id+'|'+taskId+'|plan';payload.request_id=pendingOperations.get(key)||window.crypto.randomUUID();pendingOperations.set(key,payload.request_id);payload.confirmed=true;}
   if(action==='browser.live.approve'){
    const proposed=liveByWorker.get(id+'|'+taskId)?.proposed_link;
    if(!proposed?.id)throw new Error('No current agent navigation proposal.');
    payload.proposal_id=proposed.id;payload.confirmed=true;
   }
   say(action==='browser.live.propose'?'Agent is reviewing the allowed page links…':'Updating live browser…');
  }else if(action.startsWith('browser.')){
   payload.task_id=taskId;
   const field=Array.from(detail.querySelectorAll('input[data-browser-task]'))
     .find(node=>node.dataset.browserTask===taskId);
   if(action==='browser.grant'||action==='browser.capture'){
    const url=field?.value.trim()||'';
    if(action==='browser.grant'&&!url)throw new Error('Enter the HTTPS page to approve.');
    if(url)payload.url=url;
   }
   say(action==='browser.capture'?'Capturing approved browser page…':'Updating worker browser…');
  }
  if(action==='evaluate'){
   const requestId=window.crypto&&window.crypto.randomUUID?window.crypto.randomUUID():'';
   if(!requestId)throw new Error('Secure staffing request IDs are not available.');
   payload.request_id=requestId;
   say('Supervisor is evaluating mission results…');
  }
  if(action==='approve'||action==='reject'){payload.decision_id=taskId;payload.confirmed=true;}
  const response=await api(action,payload);
  if(Array.isArray(response.actions))changesByMission.set(id,response.actions);
  if(action==='get'&&response.mission?.chat_task&&response.mission.status==='planned'&&!response.mission.private&&response.mission.authority_current!==false){const tools=await api('tools.get',{mission_id:id});toolsByMission.set(id,tools.tools);}
  if(action==='get'&&response.mission?.tools_enabled&&!response.mission.private&&response.mission.authority_current!==false){const changes=await api('actions.list',{mission_id:id});changesByMission.set(id,Array.isArray(changes.actions)?changes.actions:[]);}
  if(response.tools){toolsByMission.set(id,response.tools);if(action!=='tools.get')toolDrafts.delete(id);}
  if(payload.request_id)for(const [key,value] of pendingOperations)if(value===payload.request_id)pendingOperations.delete(key);
  if(response.live_browser!==undefined){
   const key=id+'|'+taskId;
   if(response.live_browser)liveByWorker.set(key,response.live_browser);
   else liveByWorker.delete(key);
  }
  if(response.browser!==undefined){
   const key=id+'|'+taskId;
   if(response.browser)browserByWorker.set(key,response.browser);
   else browserByWorker.delete(key);
   if(action==='browser.revoke')liveByWorker.delete(key);
  }
  const currentBrain=document.querySelector('[data-agent-teams-brain-a3]');if(currentBrain?.parentNode)renderBrain(currentBrain.parentNode);
  if(response.mission)showMission(response.mission);
  if(response.supervision){
   staffingByMission.set(id,[response.supervision,...(staffingByMission.get(id)||[]).filter(p=>p.id!==response.supervision.id)]);
   say(action==='evaluate'?'Supervisor recommendation ready for your review.':'Staffing decision recorded.');
  }
  await load();
  if(action==='get'&&response.mission)showMission(response.mission);
  if(action.startsWith('browser.')){
   const current=await api('get',{mission_id:id});
   if(current.mission)showMission(current.mission);
  }
 }catch(e){say(e.message||'Mission action failed.');}finally{setBusy(false);}
}
root.addEventListener('toggle',()=>{
 if(root.open&&!busy&&Date.now()-lastRefresh>5000){setBusy(true);load().catch(e=>say(e.message||'HomeServer is unavailable.')).finally(()=>setBusy(false));}
});
root.addEventListener('click',ev=>{
 const target=ev.target.closest('button[data-action]');if(!target||!root.contains(target))return;
 operation(target.dataset.action,target.dataset.id,target.dataset.taskId,target.dataset.decision);
});
create.addEventListener('submit',async ev=>{
 ev.preventDefault();if(busy)return;const objective=input.value.trim();if(!objective||objective.length>4000)return;
 const request_id=window.crypto&&window.crypto.randomUUID?window.crypto.randomUUID():'';
 if(!request_id){say('Secure request IDs are unavailable in this browser.');return;}
 const raw=Number((window.STONEFELLOW_CHAT||{}).initialConversationId||0);
 const thread_id=Number.isSafeInteger(raw)&&raw>0&&raw<2147483648?raw:0;
 setBusy(true);say('Creating worker mission…');
 try{
  const created=await api('create',{objective,request_id,thread_id});
  const mid=created.mission&&created.mission.id;if(!mid)throw new Error('Mission ID was not returned.');
  selected=mid;input.value='';showMission(created.mission);
  const preferred=String(create.querySelector('[name="provider_key"]')?.value||'auto');
  if(preferred!=='auto'){
   const workers=Array.isArray(created.mission.tasks)?created.mission.tasks:[];
   if(!workers.length)throw new Error('Mission has no configurable workers.');
   say('Configuring '+workers.length+' worker models…');
   for(const task of workers){await api('bind_provider',{mission_id:mid,task_id:task.id,provider_key:preferred});}
  }
  if(create.querySelector('[name="prepare_only"]')?.checked){
   say('Mission prepared. Review specialist assignments and approve browser pages before starting.');
   await load();
  }else{
   say('Starting workers…');
   await api('start',{mission_id:mid});await load();
  }
 }catch(e){say(e.message||'Mission creation failed. Refresh mission history before retrying.');}
 finally{setBusy(false);}
});
async function pollBrowserStatus(){
 if(statusInflight||busy||document.hidden)return;
 const brain=document.querySelector('#chatBrainDrawer [data-agent-teams-brain-a3]');
 if(!root.open&&!brain)return;
 statusInflight=true;
 const generation=viewGeneration;
 try{
  const missions=items.slice(0,5);
  for(const m of missions){
   if(busy)break;
   const full=await api('get',{mission_id:m.id});
   if(full.mission){const idx=items.findIndex(x=>x.id===m.id);if(idx>=0)items[idx]=full.mission;}
   for(const task of (full.mission?.tasks||m.tasks||[]).slice(0,6)){
    let response;
    try{response=await api('browser.live.get',{mission_id:m.id,task_id:task.id});}
    catch(_){if(!busy&&generation===viewGeneration){liveByWorker.delete(m.id+'|'+task.id);browserByWorker.delete(m.id+'|'+task.id);}continue;}
    if(busy||generation!==viewGeneration)return;
    if(response.live_browser?.status==='private'){browserByWorker.delete(m.id+'|'+task.id);}
    if(response.live_browser)liveByWorker.set(m.id+'|'+task.id,response.live_browser);
    else liveByWorker.delete(m.id+'|'+task.id);
   }
  }
  if(!busy){const current=items.find(x=>x.id===selected);if(current)showMission(current);if(brain?.parentNode)renderBrain(brain.parentNode);}
 }catch(_){/* Preserve drafts on transient relay failures. */}finally{statusInflight=false;}
}
window.setInterval(pollBrowserStatus,9000);

})();
