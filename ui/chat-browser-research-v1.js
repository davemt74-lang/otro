/* Chat coordinates the existing Chrome research ledger; all findings remain server-authorized. */
(function(){
'use strict';
const form=document.getElementById('chatForm'),input=document.getElementById('chatInput'),mode=document.getElementById('vp3ChatTaskMode');
const cfg=window.VP3_CHAT_RESEARCH;if(!form||!input||!mode||!cfg?.endpoint)return;
const node=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=String(text);return e;};
const option=node('option','Chrome research');option.value='chrome';mode.append(option);
const panel=node('section');panel.className='vp3-chat-research';panel.setAttribute('aria-label','Chrome research');panel.hidden=true;form.before(panel);
const status=node('p'),runtime=node('select'),project=node('select'),content=node('div'),savedContent=node('div');
const recall=node('button','Recall saved research');recall.type='button';let savedReports=[],savedQuery=null;
runtime.setAttribute('aria-label','Approved Chrome research runtime');project.setAttribute('aria-label','Research project');
const refresh=node('button','Refresh research');refresh.type='button';
panel.append(node('strong','Research the web'),node('p','Ask a question to review source discovery, or include one to five public HTTPS source URLs. Chrome reads only the approved plan in your existing runtime.'),runtime,refresh,recall,status,content,savedContent);
let busy=false,generation=0,selected=null;const runtimeScopes=new Map();
const context=()=>cfg.native?{local_conversation:window.HomeServerAgentRouting?.getActiveConversationId?.()||'',parent_agent_id:window.HomeServerAgentRouting?.getSelectedAgentId?.()||null}:
 {conversation_id:Number(window.STONEFELLOW_CHAT_CONTINUITY?.conversationId?.()??window.STONEFELLOW_CHAT?.initialConversationId??0),agent_id:Number(window.STONEFELLOW_AGENT_IDENTITY_V236?.agentId??0)};
const key=()=>JSON.stringify(context());
const pendingKey='vp3.chat-research.pending.'+String(cfg.csrf||'owner');
function lock(value){busy=value;mode.disabled=value;input.disabled=value;runtime.disabled=value;project.disabled=value;panel.querySelectorAll('button,select').forEach(x=>x.disabled=value);}
function clear(){generation++;selected=null;content.replaceChildren();savedContent.replaceChildren();savedReports=[];savedQuery=null;status.textContent='';panel.hidden=mode.value!=='chrome';}
async function api(action,args={}){
 const origin=key(),g=generation;
 const response=await fetch(cfg.endpoint,{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,csrf_token:cfg.csrf||'owner',...context(),...args})});
 let result;try{result=await response.json();}catch(_){throw new Error('Research server response was invalid.');}
 if(origin!==key()||g!==generation)throw new Error('Conversation or lead changed. Open the original research conversation.');
 if(!response.ok||!result?.ok){if([401,403,419].includes(response.status))clear();throw new Error(String(result?.error||result?.detail||'Research request failed.').slice(0,300));}
 if(result.contract!=='vp3.chat-browser-research.v1')throw new Error('Update Cloud and HomeServer to the matching research release.');
 if(cfg.native&&result.cloud_base)cfg.cloud_base=String(result.cloud_base);
 return result;
}
const button=(label,action)=>{const b=node('button',label);b.type='button';b.addEventListener('click',()=>void perform(action));return b;};
function render(r){
 selected=r;content.replaceChildren();panel.hidden=false;
 const runtimeLabel=[...runtime.options].find(o=>o.value===String(r.runtime_id))?.textContent||r.runtime_id;
 content.append(node('p','Runtime: '+runtimeLabel),node('h4',r.question),node('p',r.status.replace(/_/g,' ')),node('p',`${r.budgets.sources} websites · ${r.budgets.pages} pages · up to ${r.budgets.claims} claims`));
 const discovery=(r.sources||[]).find(s=>s.origin==='web_search');if(discovery?.discovery_domains)content.append(node('p','Discovery scope: '+discovery.discovery_domains.join(', ')));
 if((r.sources||[]).some(s=>s.origin==='web_search'))content.append(node('p','Search candidates are suggestions, not evidence. Review these exact URLs before Chrome reads them.'));
 for(const s of r.sources||[]){if(s.title)content.append(node('strong',s.title));if(s.reason)content.append(node('p',s.reason));const a=node('a',s.url);try{const u=new URL(s.url);if(u.protocol==='https:'&&!u.username&&!u.password){a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';content.append(a);}}catch(_){} }
 if(r.status==='prepared')content.append(button('Approve plan and start Chrome research','start'),node('small','Review expires '+r.expires_at+'. Website grants and Chrome permissions remain required.'));
 if(r.status==='waiting_chrome')content.append(node('p','Waiting for the connected Chrome extension. If site access is missing, open Chrome’s Agent workspace to approve the listed websites.'));
 if(r.status==='attention')content.append(node('p',r.last_error),node('p','Open Chrome’s Agent workspace to review and resume this interrupted run.'));
 const m=r.mission;
 if(m){
  content.append(node('p',`${m.progress.pages} pages read · ${m.progress.corroborated} corroborated · ${m.progress.conflicted} conflicting claims`));
  const memo=node('pre',m.answer?.text||m.memo_preview||'No supported findings yet.');content.append(memo);
  if(m.answer){if(m.answer.partial)content.append(node('p','Partial answer: source reading is still incomplete.'));renderCitations(m.answer,content);}
  for(const c of m.claims||[]){const box=node('details');box.append(node('summary',`${c.state}: ${c.statement||c.value}`));
   for(const e of c.evidence||[])box.append(node('p',`${e.domain} · ${e.excerpt||''}`));content.append(box);}
  for(const p of m.pages||[])content.append(node('small',`${p.domain} · source receipt ${p.fingerprint} · ${p.source_version_id?'pinned source version '+p.source_version_id:'fingerprint-only evidence'}`));
  for(const gap of m.gaps||[])content.append(node('p','Evidence gap: '+gap));
  if(r.status==='complete')for(const gap of (m.gaps||[]).slice(0,3)){const b=node('button','Research this gap');b.type='button';b.addEventListener('click',()=>{input.value=('Investigate: '+String(gap)).slice(0,1500);status.textContent='Send this follow-up to review a new search plan. Existing research stays unchanged.';input.focus();});content.append(b);}
  if(r.status==='complete'&&!m.report){content.append(node('p','Review the brief and choose a project to save a draft.'),project,button('Save reviewed report draft','save'));}
  if(m.report){const a=node('a','Open report draft');const u=String(m.report.url||'');if(u.startsWith('/')&&!u.startsWith('//')){a.href=cfg.cloud_base?cfg.cloud_base+u:u;content.append(a);}}
 }
 if(!['cancelled','complete'].includes(r.status))content.append(button('Stop research','cancel'));
}
function renderCitations(answer,target){
 for(const c of answer.citations||[]){const box=node('details');box.append(node('summary',`[${c.label}] ${c.domain} · ${c.as_of||'date unknown'}`),node('p','Source receipt '+c.fingerprint),node('p',c.source_version_id?'Pinned source version '+c.source_version_id:'Fingerprint-only evidence'));
  try{const u=new URL(c.url);if(u.protocol==='https:'&&!u.username&&!u.password){const a=node('a','Open captured source URL');a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';box.append(a);}}catch(_){}target.append(box);
 }
}
function renderSaved(reports){
 reports=[...reports].sort((a,b)=>String(b.saved_at).localeCompare(String(a.saved_at)));savedReports=reports;savedContent.replaceChildren();savedContent.append(node('h4','Saved research findings'));
 if(!reports.length){savedContent.append(node('p','No matching saved research is available under current permissions.'));return;}
 for(const r of reports){const box=node('details');box.append(node('summary',r.question+' · saved '+r.saved_at),node('p','Captured findings from an explicitly saved report draft. No live source was refreshed.'),node('pre',r.answer.text));renderCitations(r.answer,box);
  const u=String(r.report?.url||'');if(u.startsWith('/')&&!u.startsWith('//')){const a=node('a','Open saved report');a.href=cfg.cloud_base?cfg.cloud_base+u:u;box.append(a);}savedContent.append(box);}
 if(reports.length<2)return;
 const before=node('select'),after=node('select');before.setAttribute('aria-label','Earlier saved research');after.setAttribute('aria-label','Later saved research');
 for(const r of reports)for(const s of [before,after]){const o=node('option',r.question+' · '+r.saved_at);o.value=r.id;s.append(o);}before.value=reports[1].id;after.value=reports[0].id;
 const compare=node('button','Compare saved research');compare.type='button';const changes=node('div');
 compare.addEventListener('click',()=>void(async()=>{if(busy)return;lock(true);try{
  if(before.value===after.value)throw new Error('Choose two different saved research runs.');
  const result=await api('compare',{before_id:before.value,after_id:after.value});changes.replaceChildren(node('p',result.comparison.notice));
  for(const c of result.comparison.changes||[]){const d=node('details');d.append(node('summary',c.key+' · '+c.state.replace(/_/g,' ')));
   for(const side of ['before','after'])for(const v of c[side]||[])d.append(node('p',side+': '+v.value+' · '+v.kind+' ['+(v.citations||[]).join(', ')+']'));changes.append(d);}
  for(const side of ['before','after']){changes.append(node('strong',side+': '+result.comparison[side].question));renderCitations(result.comparison[side],changes);}
 }catch(e){savedContent.replaceChildren();status.textContent=e.message;}finally{lock(false);}})());savedContent.append(before,after,compare,changes);
}
async function recallSaved(){
 if(busy)return;lock(true);try{savedQuery=input.value.trim().slice(0,1000);renderSaved((await api('recall',{question:savedQuery})).reports||[]);status.textContent='Saved findings retrieved. No search, page read or save was started.';}catch(e){savedContent.replaceChildren();status.textContent=e.message;}finally{lock(false);}
}
async function load(){
 if(busy)return;lock(true);
 try{
  const result=await api('list');const old=runtime.value;runtime.replaceChildren();
  const blank=node('option',result.runtimes.length?'Choose an approved Chrome runtime':'No approved Chrome research runtime');blank.value='';runtime.append(blank);
  runtimeScopes.clear();for(const r of result.runtimes||[]){runtimeScopes.set(String(r.id),[...new Set(r.domains||[])].sort().slice(0,5));const o=node('option',r.title+' · '+r.domains.join(', '));o.value=r.id;runtime.append(o);}runtime.value=old;
  project.replaceChildren();const none=node('option','Choose a Research project');none.value='';project.append(none);
  for(const p of result.projects||[]){const o=node('option',p.title);o.value=p.id;project.append(o);}
  content.replaceChildren();
  for(const r of result.requests||[]){const b=node('button',r.question+' · '+r.status.replace(/_/g,' '));b.type='button';b.addEventListener('click',()=>render(r));content.append(b);}
  if(selected){const r=result.requests.find(x=>x.id===selected.id);if(r)render(r);else selected=null;}
  if(savedQuery!==null)renderSaved((await api('recall',{question:savedQuery})).reports||[]);
  status.textContent=result.runtimes.length?'Choose a runtime and send your research request.':'Approve Browser Research and the website scope in Chrome’s existing delegation workspace first.';
 }catch(e){status.textContent=e.message;}finally{lock(false);}
}
async function perform(action){
 if(busy||!selected)return;lock(true);
 try{if(action==='start'&&!window.confirm('Approve this exact source plan and start the bounded Chrome reads?'))return;
  if(action==='cancel'&&!window.confirm('Stop this Chrome research run?'))return;
  if(action==='save'&&(!project.value||!window.confirm('Save these reviewed findings as a draft in the selected project?')))return;
  render((await api(action,{id:selected.id,expected_hash:selected.review_hash,confirmed:true,project_id:project.value})).request);
 }catch(e){status.textContent=e.message;}finally{lock(false);}
}
function reviewDiscovery(pending){
 const domains=runtimeScopes.get(String(pending.runtime_id))||[];
 if(!domains.length)throw new Error('Approve research websites in this runtime first.');
 selected=null;content.replaceChildren();
 const query=pending.question+' ('+domains.map(d=>'site:'+d).join(' OR ')+')';
 content.append(node('h4','Review source discovery'),node('p','This exact question is sent to DuckDuckGo to find candidates within up to five already approved websites. Search results are not evidence; Chrome opens no source page until you approve the returned source plan.'),node('pre',query));
 const approve=node('button','Approve search and propose sources');approve.type='button';
 approve.addEventListener('click',()=>void (async()=>{if(busy)return;lock(true);try{
  if(pending.context!==key())throw new Error('Conversation or lead changed. Review a fresh search.');
  render((await api('prepare',{question:pending.question,runtime_id:pending.runtime_id,request_id:pending.request_id,discovery_confirmed:true,discovery_domains:domains})).request);
  sessionStorage.removeItem(pendingKey);status.textContent='Review the candidate sources. Chrome has opened no source page.';
 }catch(e){status.textContent=e.message;}finally{lock(false);}})());
 const cancel=node('button','Cancel search review');cancel.type='button';cancel.addEventListener('click',()=>{content.replaceChildren();status.textContent='Search cancelled. No question was sent.';});
 content.append(approve,cancel);status.textContent='Review the external query before source discovery.';
}
form.addEventListener('submit',event=>{
 if(mode.value!=='chrome')return;event.preventDefault();event.stopImmediatePropagation();
 if(busy)return;
 void (async()=>{lock(true);try{
  if(!runtime.value)throw new Error('Choose an approved Chrome research runtime first.');
  let pending;try{pending=JSON.parse(sessionStorage.getItem(pendingKey)||'null');}catch(_){}
  const draft={question:input.value.trim(),runtime_id:runtime.value,context:key(),scope:/https:\/\//i.test(input.value)?undefined:JSON.stringify(runtimeScopes.get(String(runtime.value))||[])};
  if(!pending||pending.question!==draft.question||pending.runtime_id!==draft.runtime_id||pending.context!==draft.context||pending.scope!==draft.scope)pending={...draft,request_id:crypto.randomUUID()};
  try{sessionStorage.setItem(pendingKey,JSON.stringify(pending));}catch(_){}
  if(!/https:\/\//i.test(pending.question)){reviewDiscovery(pending);return;}
  render((await api('prepare',{question:pending.question,runtime_id:pending.runtime_id,request_id:pending.request_id})).request);
  sessionStorage.removeItem(pendingKey);status.textContent='Review the source plan. No page has opened.';
 }catch(e){status.textContent=e.message;}finally{lock(false);}})();
},true);
recall.addEventListener('click',()=>void recallSaved());
mode.addEventListener('change',()=>{panel.hidden=mode.value!=='chrome';if(mode.value==='chrome')void load();});refresh.addEventListener('click',()=>void load());
let previous=key();setInterval(()=>{if(key()!==previous){previous=key();clear();if(mode.value==='chrome')void load();}else if(mode.value==='chrome'&&selected&&['running','waiting_chrome'].includes(selected.status))void load();},5000);
})();
