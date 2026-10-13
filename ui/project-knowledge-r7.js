/* Deliberate source review; existing Research owns browser reads and evidence. */
(() => {
  'use strict';
  const chromeMode=!!document.getElementById('agentView');
  const cfg=window.VP3_PROJECT_KNOWLEDGE||{mode:chromeMode?'chrome':'cloud'};
  const draft=document.getElementById(chromeMode?'agentMessageInput':'chatInput');
  const form=document.getElementById('chatForm');
  if(!draft||(!chromeMode&&!cfg.endpoint))return;
  const node=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
  const button=(text,fn)=>{const n=node('button',text);n.type='button';n.onclick=fn;return n;};
  const panel=node('details');panel.className='project-knowledge-r7';panel.append(node('summary','Review Knowledge freshness'));
  const status=node('p');status.setAttribute('role','status');
  const interval=node('input');interval.type='number';interval.min='1';interval.max='365';interval.value='30';interval.setAttribute('aria-label','Review interval in days');
  const runtime=node('select');runtime.setAttribute('aria-label','Approved Chrome research reader');
  const project=node('select');project.setAttribute('aria-label','Reviewed Research destination');
  const folder=node('select');folder.setAttribute('aria-label','Reviewed Knowledge folder');
  const items=node('section'),runs=node('section');
  panel.append(node('p','Review age is a reminder, not proof of correctness. A source reread and a Knowledge update each require your review.'),interval,button('Check source ages',load),runtime,project,folder,status,items,runs);
  if(chromeMode)document.querySelector('#agentView .agent-workspace-card').prepend(panel);else form.before(panel);
  const style=node('style');style.textContent='.project-knowledge-r7{padding:12px;margin:8px 0;border:1px solid var(--border-color,#ddd);border-radius:12px;max-width:100%;box-sizing:border-box;overflow-wrap:anywhere}.project-knowledge-r7 summary{font-weight:600;cursor:pointer}.project-knowledge-r7[open]{max-height:50vh;overflow:auto;overscroll-behavior:contain}.project-knowledge-r7 select,.project-knowledge-r7 input{box-sizing:border-box;width:100%;min-width:0;padding:8px;margin:6px 0}.project-knowledge-r7 button{padding:9px;margin:4px 5px 4px 0}.project-knowledge-r7 article{border-top:1px solid var(--border-color,#ddd);padding:10px 0}.project-knowledge-r7 pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;max-height:220px;overflow:auto}.project-knowledge-r7 a,.r7-brain-receipts a{display:block;overflow-wrap:anywhere}.project-knowledge-r7 label{display:block;margin:10px 0}';document.head.append(style);
  const context=()=>chromeMode?{agent_id:Number(agentWorkspaceAgentId||0),conversation_id:Number(agentConversationId||0)}:
    cfg.mode==='native'?{local_conversation:window.HomeServerAgentRouting?.getActiveConversationId?.()||'',parent_agent_id:Number(window.HomeServerAgentRouting?.getSelectedAgentId?.()||0)}:
    {conversation_id:Number(window.STONEFELLOW_CHAT_CONTINUITY?.conversationId?.()??window.STONEFELLOW_CHAT?.initialConversationId??0),agent_id:Number(window.STONEFELLOW_AGENT_IDENTITY_V236?.agentId??0)};
  const key=()=>JSON.stringify(context());let binding='',generation=0,busy=false,state=null,cloudOrigin='';
  const retries=new Map(),receipts=new Map();
  function lock(value){busy=value;panel.querySelectorAll('button,input,select').forEach(n=>n.disabled=value||n.dataset.unavailable==='true');}
  function clear(message='Conversation changed. Check its source ages again.'){
    generation++;binding='';state=null;retries.clear();receipts.clear();items.replaceChildren();runs.replaceChildren();runtime.replaceChildren();project.replaceChildren();folder.replaceChildren();status.textContent=message;lock(false);brain();
  }
  function link(url,title){const a=node('a',title);try{const u=new URL(url,cloudOrigin||document.baseURI);if(!['http:','https:'].includes(u.protocol)||u.username||u.password)throw Error();a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';}catch(_){}return a;}
  async function api(action,extra={}){
    const before=key(),gen=generation;let result;
    if(chromeMode)result=await msg('project_knowledge_r7',{payload:{action,...context(),...extra}});
    else{
      const endpoint=cfg.mode==='native'?cfg.endpoint.replace(/project-knowledge$/,'project-refresh'):cfg.endpoint.replace('project-knowledge-r5.php','project-knowledge-r7.php');
      const response=await fetch(endpoint,{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},body:JSON.stringify({action,csrf_token:cfg.csrf,...context(),...extra})});
      result=await response.json();if(!response.ok||!result?.ok)throw Error(String(result?.error||result?.detail||'Reviewed refresh is unavailable.'));
    }
    if(before!==key()||gen!==generation){clear();throw Error('Conversation changed. Check its source ages again.');}
    if(result?.contract!=='vp3.project-knowledge.r7')throw Error('Update the matching Reviewed Knowledge Refresh release.');cloudOrigin=result.cloud_origin||cloudOrigin;return result;
  }
  async function action(fn){if(busy)return;lock(true);try{await fn();}catch(e){
    // Remove source text and actionable hashes after any authority or transport failure.
    items.replaceChildren();runs.replaceChildren();receipts.clear();state=null;brain();status.textContent=e.message+' Check source ages to reload current authority.';
  }finally{lock(false);}}
  function confirmation(card,text,fn){const label=node('label'),check=node('input');check.type='checkbox';check.style.width='auto';label.append(check,document.createTextNode(' '+text));card.append(label);const b=button(text,()=>{if(check.checked)return fn();status.textContent='Confirm the displayed review first.';});card.append(b);}
  function brain(target=document.querySelector('#agentBrainDrawer .agent-brain-drawer-body')||(document.querySelector('#chatNotificationDrawer [data-notification-tab=brain].active')?document.querySelector('#chatNotificationDrawer [data-notification-drawer-body]'):null)){
    if(!target)return;let host=target.querySelector('.r7-brain-receipts');if(!host){host=node('section');host.className='r7-brain-receipts';target.prepend(host);}host.replaceChildren();
    for(const r of receipts.values()){host.append(node('h3','Reviewed Knowledge refresh'),node('p',r.source.title),node('p',receiptText(r)));if(r.report?.url)host.append(link(r.report.url,'Reviewed Research report'));if(r.knowledge_url)host.append(link(r.knowledge_url,'Refreshed Knowledge'));}
  }
  function receiptText(r){return r.status==='superseded'?'This reviewed revision has been superseded.':r.sync?.state==='received'?`HomeServer received this exact Knowledge revision · ${r.sync.confirmed_at} UTC`:'Knowledge updated in Cloud; awaiting HomeServer confirmation.';}
  function renderRun(r){
    let card=[...runs.children].find(n=>n.dataset.id===r.id);if(!card){card=node('article');card.dataset.id=r.id;runs.prepend(card);}card.replaceChildren();
    card.append(node('strong',r.source.title),node('p',`Refresh status: ${r.status}`),link(r.source.url,'Original source'),node('p',`Original capture: ${r.source.captured_at} UTC · source revision ${r.source.revision}`));
    if(['applied','superseded'].includes(r.status)){receipts.set(r.id,r);card.append(node('p',receiptText(r)));if(r.report?.url)card.append(link(r.report.url,'Reviewed Research report'));if(r.knowledge_url)card.append(link(r.knowledge_url,'Refreshed Knowledge'));brain();}
    else if(r.status==='prepared'){
      card.append(node('p',`Reread only these approved URLs. Budget: ${r.research.budgets.pages} pages, ${r.research.budgets.claims} claims, ${r.research.budgets.duration_minutes} minutes. No earlier quote or private note is sent as a search query.`));
      for(const s of r.research.sources)card.append(link(s.url,s.url));
      card.append(node('p',`Research project: ${r.project_id} · Knowledge folder: ${r.folder_id||'Unfiled'}`));
      confirmation(card,'Start this exact source reread',()=>action(async()=>renderRun((await api('start',{id:r.id,expected_hash:r.research.review_hash,confirmed:true})).refresh)));
    }else if(r.status==='complete'){
      card.append(node('h4','What changed in the observations'),node('p','Different observations can reflect different coverage or source text; they do not prove a real-world change.'),node('pre',JSON.stringify(r.comparison,null,2)),node('h4','Current refresh note'),node('pre',r.before_knowledge_content||'No separate refresh note yet.'),node('h4','Exact proposed Knowledge content'),node('pre',r.knowledge_content),node('p',`Save a cited Research draft in project ${r.project_id} and this separate Knowledge note in folder ${r.folder_id||'Unfiled'}. Original highlights and report history remain available.`));
      confirmation(card,'Apply this reviewed Knowledge update',()=>action(async()=>renderRun((await api('apply',{id:r.id,expected_hash:r.review_hash,confirmed:true})).refresh)));
    }else if(['waiting_chrome','running','attention'].includes(r.status))card.append(node('p','Use the approved Chrome reader for this run. If its site grant or pairing changed, review current permissions in the existing Agent workspace.'));
    card.append(button('Check refresh progress',()=>action(async()=>renderRun((await api('get',{id:r.id})).refresh))));
    if(!['applied','superseded','cancelled'].includes(r.status))confirmation(card,'Stop this refresh',()=>action(async()=>renderRun((await api('cancel',{id:r.id,confirmed:true})).refresh)));
  }
  function renderItem(s){
    const card=node('article');card.append(node('strong',s.title),link(s.url,'View original source'),node('p',`${s.freshness.state.replaceAll('_',' ')} · ${s.freshness.age_days===null?'date unknown':s.freshness.age_days+' days old'} · ${s.captured_at} UTC`),node('p',s.freshness.notice));
    const b=button('Prepare reviewed refresh',()=>action(async()=>{
      if(!state||binding!==key())throw Error('Check current source ages first.');
      const payload={reference:{kind:s.kind,id:s.id,revision:s.revision},state_revision:state.revision,runtime_id:runtime.value,project_id:project.value,folder_id:Number(folder.value)};
      const k=JSON.stringify(payload);if(!retries.has(k))retries.set(k,crypto.randomUUID());
      renderRun((await api('prepare',{...payload,request_id:retries.get(k)})).refresh);status.textContent='Review the exact source plan before starting.';
    }));if(!s.refreshable||!runtime.value||!project.value){b.dataset.unavailable='true';b.disabled=true;card.append(node('p','A safe exact source URL, existing approved Chrome reader, and Research destination are required.'));}card.append(b);items.append(card);
  }
  async function load(){if(busy)return;generation++;binding=key();lock(true);status.textContent='Checking current source ages…';items.replaceChildren();runs.replaceChildren();receipts.clear();brain();
    try{const days=Number(interval.value);if(!Number.isInteger(days)||days<1||days>365)throw Error('Choose a review interval from 1 to 365 days.');const d=await api('list',{review_days:days});state=d.state;
      runtime.replaceChildren(...(d.runtimes||[]).map(r=>new Option(r.title,r.id)));project.replaceChildren(...(d.projects||[]).map(p=>new Option(p.title,p.id)));folder.replaceChildren(new Option('Unfiled Knowledge','0'),...(d.folders||[]).map(f=>new Option(f.folder_name,String(f.id))));
      if(state.scope.mode==='project')project.value=state.scope.project_id;if(state.scope.mode==='folder')folder.value=String(state.scope.folder_id);
      if(!state.available||state.scope.mode==='off')status.textContent='Knowledge is unavailable or disabled in this conversation. Choose a context in Conversation Knowledge first.';
      else{for(const s of d.items||[])renderItem(s);for(const r of d.runs||[])renderRun(r);status.textContent=`Context: ${state.title}. ${d.items.length} sources checked; browser rereads start only after your plan review.`;}
    }catch(e){clear(e.message);}finally{lock(false);}
  }
  panel.addEventListener('toggle',()=>{if(panel.open&&!state)load();});panel.addEventListener('pointerdown',()=>{if(binding&&binding!==key())clear();},true);
  for(const event of ['vp3:agent-changed','stonefellow:chat-conversation-changed','homeserver:agent-routing-changed','vp3:project-knowledge-r6-clear','vp3:project-knowledge-r6-context-changed'])window.addEventListener(event,()=>clear());
  window.VP3ProjectKnowledgeR7={refresh:load,renderBrain:brain,clear};
})();
