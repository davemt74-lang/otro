/* Chat coordinates the existing Chrome research ledger; all findings remain server-authorized. */
(function(){
'use strict';
const form=document.getElementById('chatForm'),input=document.getElementById('chatInput'),mode=document.getElementById('vp3ChatTaskMode');
const cfg=window.VP3_CHAT_RESEARCH;if(!form||!input||!mode||!cfg?.endpoint)return;
const node=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=String(text);return e;};
const option=node('option','Chrome research');option.value='chrome';mode.append(option);
const panel=node('section');panel.className='vp3-chat-research';panel.setAttribute('aria-label','Chrome research');panel.hidden=true;form.before(panel);
const status=node('p'),runtime=node('select'),project=node('select'),content=node('div');
runtime.setAttribute('aria-label','Approved Chrome research runtime');project.setAttribute('aria-label','Research project');
const refresh=node('button','Refresh research');refresh.type='button';
panel.append(node('strong','Research the web'),node('p','Include one to five public HTTPS source URLs. Review the plan here; Chrome reads only websites in your existing approved research runtime.'),runtime,refresh,status,content);
let busy=false,generation=0,selected=null;
const context=()=>cfg.native?{local_conversation:window.HomeServerAgentRouting?.getActiveConversationId?.()||'',parent_agent_id:window.HomeServerAgentRouting?.getSelectedAgentId?.()||null}:
 {conversation_id:Number(window.STONEFELLOW_CHAT_CONTINUITY?.conversationId?.()??window.STONEFELLOW_CHAT?.initialConversationId??0),agent_id:Number(window.STONEFELLOW_AGENT_IDENTITY_V236?.agentId??0)};
const key=()=>JSON.stringify(context());
const pendingKey='vp3.chat-research.pending.'+String(cfg.csrf||'owner');
function lock(value){busy=value;mode.disabled=value;input.disabled=value;panel.querySelectorAll('button').forEach(x=>x.disabled=value);}
function clear(){generation++;selected=null;content.replaceChildren();status.textContent='';panel.hidden=mode.value!=='chrome';}
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
 content.append(node('h4',r.question),node('p',r.status.replace(/_/g,' ')),node('p',`${r.budgets.sources} websites · ${r.budgets.pages} pages · up to ${r.budgets.claims} claims`));
 for(const s of r.sources||[]){const a=node('a',s.url);try{const u=new URL(s.url);if(u.protocol==='https:'&&!u.username&&!u.password){a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';content.append(a);}}catch(_){} }
 if(r.status==='prepared')content.append(button('Approve plan and start Chrome research','start'),node('small','Review expires '+r.expires_at+'. Website grants and Chrome permissions remain required.'));
 if(r.status==='waiting_chrome')content.append(node('p','Waiting for the connected Chrome extension. If site access is missing, open Chrome’s Agent workspace to approve the listed websites.'));
 if(r.status==='attention')content.append(node('p',r.last_error),node('p','Open Chrome’s Agent workspace to review and resume this interrupted run.'));
 const m=r.mission;
 if(m){
  content.append(node('p',`${m.progress.pages} pages read · ${m.progress.corroborated} corroborated · ${m.progress.conflicted} conflicting claims`));
  const memo=node('pre',m.memo_preview||'No supported findings yet.');content.append(memo);
  for(const c of m.claims||[]){const box=node('details');box.append(node('summary',`${c.state}: ${c.statement||c.value}`));
   for(const e of c.evidence||[])box.append(node('p',`${e.domain} · ${e.excerpt||''}`));content.append(box);}
  for(const p of m.pages||[])content.append(node('small',`${p.domain} · source receipt ${p.fingerprint} · ${p.source_version_id?'pinned source version '+p.source_version_id:'fingerprint-only evidence'}`));
  for(const gap of m.gaps||[])content.append(node('p','Evidence gap: '+gap));
  if(r.status==='complete'&&!m.report){content.append(node('p','Review the brief and choose a project to save a draft.'),project,button('Save reviewed report draft','save'));}
  if(m.report){const a=node('a','Open report draft');const u=String(m.report.url||'');if(u.startsWith('/')&&!u.startsWith('//')){a.href=cfg.cloud_base?cfg.cloud_base+u:u;content.append(a);}}
 }
 if(!['cancelled','complete'].includes(r.status))content.append(button('Stop research','cancel'));
}
async function load(){
 if(busy)return;lock(true);
 try{
  const result=await api('list');const old=runtime.value;runtime.replaceChildren();
  const blank=node('option',result.runtimes.length?'Choose an approved Chrome runtime':'No approved Chrome research runtime');blank.value='';runtime.append(blank);
  for(const r of result.runtimes||[]){const o=node('option',r.title+' · '+r.domains.join(', '));o.value=r.id;runtime.append(o);}runtime.value=old;
  project.replaceChildren();const none=node('option','Choose a Research project');none.value='';project.append(none);
  for(const p of result.projects||[]){const o=node('option',p.title);o.value=p.id;project.append(o);}
  content.replaceChildren();
  for(const r of result.requests||[]){const b=node('button',r.question+' · '+r.status.replace(/_/g,' '));b.type='button';b.addEventListener('click',()=>render(r));content.append(b);}
  if(selected){const r=result.requests.find(x=>x.id===selected.id);if(r)render(r);else selected=null;}
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
form.addEventListener('submit',event=>{
 if(mode.value!=='chrome')return;event.preventDefault();event.stopImmediatePropagation();
 if(busy)return;
 void (async()=>{lock(true);try{
  if(!runtime.value)throw new Error('Choose an approved Chrome research runtime first.');
  let pending;try{pending=JSON.parse(sessionStorage.getItem(pendingKey)||'null');}catch(_){}
  const draft={question:input.value.trim(),runtime_id:runtime.value,context:key()};
  if(!pending||pending.question!==draft.question||pending.runtime_id!==draft.runtime_id||pending.context!==draft.context)pending={...draft,request_id:crypto.randomUUID()};
  try{sessionStorage.setItem(pendingKey,JSON.stringify(pending));}catch(_){}
  render((await api('prepare',{question:pending.question,runtime_id:pending.runtime_id,request_id:pending.request_id})).request);
  sessionStorage.removeItem(pendingKey);status.textContent='Review the source plan. No page has opened.';
 }catch(e){status.textContent=e.message;}finally{lock(false);}})();
},true);
mode.addEventListener('change',()=>{panel.hidden=mode.value!=='chrome';if(mode.value==='chrome')void load();});refresh.addEventListener('click',()=>void load());
let previous=key();setInterval(()=>{if(key()!==previous){previous=key();clear();if(mode.value==='chrome')void load();}else if(mode.value==='chrome'&&selected&&['running','waiting_chrome'].includes(selected.status))void load();},5000);
})();
