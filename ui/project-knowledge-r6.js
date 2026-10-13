/* Persistent conversation scope and revision-bound attachments; opening this panel never sends a turn. */
(() => {
  'use strict';
  const chrome=!!document.getElementById('agentView'),cfg=window.VP3_PROJECT_KNOWLEDGE||{mode:chrome?'chrome':'cloud'};
  const input=document.getElementById(chrome?'agentMessageInput':'chatInput');
  const form=document.getElementById('chatForm');
  if(!input||(!chrome&&!cfg.endpoint))return;
  const node=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=String(text);return e;};
  const panel=node('details');panel.className='project-knowledge-r6';panel.append(node('summary','Conversation Knowledge'));
  const scope=node('select');scope.setAttribute('aria-label','Conversation Knowledge scope');
  const choose=node('button','Apply context');choose.type='button';
  const query=node('input');query.type='search';query.maxLength=240;query.placeholder='Find highlights and saved reports';query.setAttribute('aria-label','Find sources');
  const search=node('button','Find sources');search.type='button';
  const status=node('p');status.setAttribute('role','status');
  const selected=node('div');selected.className='r6-selected';const results=node('div');results.className='r6-results';
  const used=node('section');used.className='r6-used';used.setAttribute('aria-label','Sources used by the Agent');used.hidden=true;
  panel.append(scope,choose,query,search,status,selected,results,used);
  if(chrome)document.querySelector('#agentView .agent-workspace-card').prepend(panel);else form.before(panel);
  const style=node('style');style.textContent='.project-knowledge-r6{margin:8px 0;padding:12px;border:1px solid var(--border-color,#ddd);border-radius:12px;box-sizing:border-box;max-width:100%;overflow-wrap:anywhere}.project-knowledge-r6 summary{font-weight:600;cursor:pointer}.project-knowledge-r6[open]{max-height:45vh;overflow:auto;overscroll-behavior:contain}.project-knowledge-r6 select,.project-knowledge-r6 input{box-sizing:border-box;width:100%;min-width:0;padding:8px;margin:6px 0}.project-knowledge-r6 button{padding:8px;margin:4px 6px 4px 0}.project-knowledge-r6 article{border-top:1px solid var(--border-color,#ddd);padding:10px 0}.project-knowledge-r6 blockquote{margin:6px 0;max-height:110px;overflow:auto;white-space:pre-wrap}.project-knowledge-r6 a,.r6-used a{display:block;overflow-wrap:anywhere;max-width:100%}.r6-used{padding:10px 0}.r6-selected{display:flex;gap:5px;flex-wrap:wrap}.r6-selected button{font-size:12px;border-radius:8px}.r6-context-heading{margin:8px 0}';document.head.append(style);
  const context=()=>chrome?{agent_id:Number(agentWorkspaceAgentId||0),conversation_id:Number(agentConversationId||0)}:
    cfg.mode==='native'?{local_conversation:window.HomeServerAgentRouting?.getActiveConversationId?.()||'',parent_agent_id:Number(window.HomeServerAgentRouting?.getSelectedAgentId?.()||0)}:
    {conversation_id:Number(window.STONEFELLOW_CHAT_CONTINUITY?.conversationId?.()??window.STONEFELLOW_CHAT?.initialConversationId??0),agent_id:Number(window.STONEFELLOW_AGENT_IDENTITY_V236?.agentId??0)};
  const key=()=>JSON.stringify(context());let binding='',generation=0,busy=false,state=null,lastPacket=null,cloudOrigin='';const attachments=new Map();
  const safeLink=(source)=>{const a=node('a',`[${source.label||'source'}] ${source.title}`);try{const u=new URL(source.url,cloudOrigin||document.baseURI);if(!['http:','https:'].includes(u.protocol)||u.username||u.password)throw Error();a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';}catch(_){a.removeAttribute('href');}return a;};
  function clear(message='Conversation changed. Refresh its Knowledge context.'){
    generation++;state=null;lastPacket=null;binding='';attachments.clear();selected.replaceChildren();results.replaceChildren();used.replaceChildren();used.hidden=true;scope.replaceChildren();status.textContent=message;lock(false);document.querySelectorAll('.r6-brain-sources').forEach(e=>e.replaceChildren());brainRecord(null);
  }
  function lock(on){busy=on;panel.querySelectorAll('button,input,select').forEach(e=>e.disabled=on);}
  async function api(action,extra={}){
    const origin=key(),gen=generation;let result;
    if(chrome)result=await msg('project_knowledge_r6',{payload:{action,...context(),...extra}});
    else{
      const endpoint=cfg.mode==='native'?cfg.endpoint.replace(/project-knowledge$/,'project-context'):cfg.endpoint.replace('project-knowledge-r5.php','project-knowledge-r6.php');
      const response=await fetch(endpoint,{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},body:JSON.stringify({action,csrf_token:cfg.csrf,...context(),...extra})});
      result=await response.json();if(!response.ok||!result?.ok)throw Error(String(result?.error||result?.detail||'Conversation Knowledge is unavailable.'));
    }
    if(origin!==key()||gen!==generation){clear();throw Error('Conversation changed. Refresh its Knowledge context.');}
    if(result?.contract!=='vp3.project-knowledge.r6')throw Error('Update the matching Conversation Knowledge release.');return result;
  }
  function renderAttachments(){
    selected.replaceChildren();for(const [id,s] of attachments){const remove=node('button',`Remove ${s.title}`);remove.type='button';remove.onclick=()=>{attachments.delete(id);renderAttachments();};selected.append(remove);}
  }
  function renderSource(source){
    const card=node('article');card.append(node('strong',source.title),node('p',`${source.kind==='report'?'Saved research':'Captured highlight'} · ${source.captured_at} UTC`),safeLink(source),node('blockquote',String(source.excerpt||'').slice(0,700)));
    const receipt=source.sync?.state==='received'?`HomeServer received this revision · ${source.sync.confirmed_at}`:source.kind==='annotation'?'Awaiting HomeServer confirmation':'Canonical Cloud report';card.append(node('small',receipt));
    const attach=node('button','Attach to next message');attach.type='button';attach.onclick=()=>{
      if(binding!==key()){clear();return;}
      if(attachments.size>=6&&!attachments.has(`${source.kind}:${source.id}`)){status.textContent='Attach up to six sources.';return;}
      attachments.set(`${source.kind}:${source.id}`,source);renderAttachments();status.textContent=`${attachments.size} source${attachments.size===1?'':'s'} attached. Type your question and send when ready.`;
    };card.append(attach);results.append(card);
  }
  async function load(){
    if(busy)return;generation++;binding=key();results.replaceChildren();lock(true);status.textContent='Loading conversation context…';
    try{
      const data=await api('options',{q:query.value.trim()});state=data.state;cloudOrigin=data.cloud_origin||'';
      scope.replaceChildren(new Option('All authorized Knowledge','all'),new Option('Knowledge off','off'));
      for(const p of data.projects||[])scope.append(new Option(`Project: ${p.title}`,`project:${p.id}`));
      for(const f of data.folders||[])scope.append(new Option(`Folder: ${f.folder_name}`,`folder:${f.id}`));
      const value=state.scope.mode==='project'?`project:${state.scope.project_id}`:state.scope.mode==='folder'?`folder:${state.scope.folder_id}`:state.scope.mode;
      if(![...scope.options].some(o=>o.value===value))scope.append(new Option('Current context unavailable',value));scope.value=value;
      if(!state.available){attachments.clear();renderAttachments();status.textContent='This context is unavailable. Choose another context explicitly.';}
      else{for(const s of [...data.items,...data.reports])renderSource(s);status.textContent=`Context: ${state.title}. ${state.scope.mode==='off'?'Knowledge is disabled.':'Sources are rechecked when you send.'}`;}
    }catch(e){clear(e.message);}finally{lock(false);}
  }
  choose.onclick=async()=>{
    if(!state||busy)return;lock(true);
    const value=scope.value;const requested={mode:value.split(':')[0],project_id:value.startsWith('project:')?value.slice(8):'',folder_id:value.startsWith('folder:')?Number(value.slice(7)):0};
    try{await api('set_context',{scope:requested,expected_revision:state.revision});attachments.clear();renderAttachments();lock(false);await load();}
    catch(e){status.textContent=e.message;lock(false);}
  };
  function brainRecord(packet,target=document.querySelector('#agentBrainDrawer .agent-brain-drawer-body')||(document.querySelector('#chatNotificationDrawer [data-notification-tab=brain].active')?document.querySelector('#chatNotificationDrawer [data-notification-drawer-body]'):null)){
    if(!target)return;let host=target.querySelector('.r6-brain-sources');if(!host){host=node('section');host.className='r6-brain-sources';target.prepend(host);}host.replaceChildren();
    if(!packet?.active||packet.used===false)return;host.append(node('h3','Conversation Knowledge'),node('p',packet.state.title));for(const s of packet.sources){host.append(safeLink(s),node('small',`${s.captured_at} UTC · ${s.sync?.state==='received'?'HomeServer received this revision':'Canonical Cloud evidence'}`));}
  }
  function record(packet){
    lastPacket=packet;cloudOrigin=packet?.cloud_origin||cloudOrigin;used.replaceChildren();used.hidden=!packet?.active||packet.used===false;if(!packet?.active||packet.used===false){brainRecord(null);return;}
    used.append(node('h4','Sources in the last answer'),node('p',packet.state.title));for(const s of packet.sources)used.append(safeLink(s),node('small',`${s.captured_at} UTC`));brainRecord(packet);
    window.dispatchEvent(new CustomEvent('vp3:project-knowledge-r6-used',{detail:packet}));
  }
  window.VP3ProjectKnowledgeR6={
    value(){if(binding&&binding!==key()){clear();throw Error('Conversation changed. Refresh selected sources.');}if(!state)return undefined;return {state_revision:state.revision,attachments:[...attachments.values()].map(s=>({kind:s.kind,id:s.id,revision:s.revision}))};},
    consume(packet){attachments.clear();renderAttachments();record(packet);},record,refresh:load,renderBrain(target){brainRecord(lastPacket,target);}
  };
  search.onclick=load;query.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();load();}});
  panel.addEventListener('toggle',()=>{if(panel.open&&!state)load();});panel.addEventListener('pointerdown',()=>{if(binding&&binding!==key())clear();},true);
  for(const event of ['vp3:agent-changed','stonefellow:chat-conversation-changed','homeserver:agent-routing-changed'])window.addEventListener(event,()=>clear());
  window.addEventListener('vp3:project-knowledge-r6-clear',()=>clear('Knowledge access changed. Refresh current context.'));
})();
