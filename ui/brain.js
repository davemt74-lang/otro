(() => {
  'use strict';

  let activeConversationId = null;
  let chatTurnSequence = 0;
  let pendingChatTurn = null;
  let conversationRevision = 0;
  let restoreLoads = 0;
  let contextReady = false;
  let restoredDraftScope = null;
  const DRAFT_KEY = 'homeserver:chat:drafts-v1', ACTIVE_KEY = 'homeserver:chat:active-v1';
  const validId = id => typeof id === 'string' && /^[0-9a-f]{32}$/.test(id);
  const scopeKey = () => activeConversationId || 'new:' + Number(window.HomeServerAgentRouting?.getSelectedAgentId?.() || 0);
  function readStore(kind, key, fallback) { try { return JSON.parse(window[kind].getItem(key) || 'null') || fallback; } catch (_) { return fallback; } }
  function drafts() {
    const value = readStore('sessionStorage', DRAFT_KEY, {}), result = {};
    for (const [key, item] of Object.entries(value)) if ((validId(key) || /^new:\d+$/.test(key)) && typeof item?.text === 'string' && item.text.length <= 32000 && Number.isFinite(item.at) && item.at <= Date.now() && Date.now() - item.at < 86400000) result[key] = item;
    return result;
  }
  function saveDraft(text, scope = scopeKey(), state = 'draft') {
    const value = drafts();
    if (String(text || '').trim()) value[scope] = {text: String(text).slice(0,32000), at: Date.now(), state}; else delete value[scope];
    const bounded = Object.fromEntries(Object.entries(value).sort((a,b) => b[1].at-a[1].at).slice(0,8));
    try { sessionStorage.setItem(DRAFT_KEY, JSON.stringify(bounded)); } catch (_) {}
  }
  function rememberConversation() { try { localStorage.setItem(ACTIVE_KEY, JSON.stringify({id:activeConversationId,agentId:Number(window.HomeServerAgentRouting?.getSelectedAgentId?.()||0),at:Date.now()})); } catch (_) {} }
  function recoveryStatus(message = '', detail = '') {
    let host = byId('chatSessionStatus');
    if (!host && byId('chatForm')) {
      host = document.createElement('div'); host.id = 'chatSessionStatus'; host.setAttribute('role','status');
      const text = document.createElement('span'); text.id = 'chatSessionStatusText';
      const check = document.createElement('button'); check.type = 'button'; check.textContent = 'Check history'; check.id = 'chatCheckHistory';
      check.addEventListener('click', () => {if(activeConversationId)loadConversation(activeConversationId).catch(() => recoveryStatus('History is unavailable. Your draft is kept.'));});
      const close = document.createElement('button'); close.type = 'button'; close.textContent = 'Dismiss'; close.addEventListener('click',()=>recoveryStatus());
      host.append(text,check,close); byId('chatForm').appendChild(host);
    }
    if (host) { host.hidden = !message; byId('chatSessionStatusText').textContent = message; byId('chatCheckHistory').hidden = !activeConversationId; }
    if (message) window.dispatchEvent(new CustomEvent('homeserver:chat-diagnostic',{detail:{state:'attention',message:detail || message,time:new Date().toISOString()}}));
  }
  function restoreDraft() {
    restoredDraftScope=scopeKey();
    const input = byId('chatInput'), saved = drafts()[restoredDraftScope];
    if (input) {input.value = saved?.text || ''; input.style.height='auto'; input.style.height=`${Math.min(input.scrollHeight,180)}px`;}
    recoveryStatus(saved && saved.state !== 'draft' ? 'Your message is kept. Check history before sending again.' : '');
  }
  function clearPrivateRecovery() {
    const turn=pendingChatTurn;pendingChatTurn=null;if(turn){clearTimeout(turn.timer);turn.controller.abort();}
    try {sessionStorage.removeItem(DRAFT_KEY);localStorage.removeItem(ACTIVE_KEY);} catch (_) {}
    window.HomeServerConversationVoice?.stop?.();
    conversationRevision++;activeConversationId=null;contextReady=false;restoredDraftScope=null;
    window.HomeServerAgentRouting?.adoptConversation?.(null,null);
    byId('chatMessages')?.replaceChildren();byId('conversationList')?.replaceChildren();
    if (byId('chatInput')) byId('chatInput').value = '';
    if(byId('chatInput'))byId('chatInput').disabled=false;
    const submit=byId('chatForm')?.querySelector('button[type="submit"]');if(submit)submit.disabled=false;
    recoveryStatus('Sign in again to continue.');
  }
  window.addEventListener('homeserver:owner-session-expired',clearPrivateRecovery);
  const emitChatTurn = (turn, status, extra = {}) => window.dispatchEvent(new CustomEvent('homeserver:chat-turn', {detail:{requestId:turn.id,conversationId:turn.conversationId,status,...extra}}));
  function cancelChatTurn(requestId = null, restore = true){
    const turn=pendingChatTurn;
    if(!turn || (requestId != null && requestId !== turn.id))return false;
    pendingChatTurn=null;
    turn.controller.abort();clearTimeout(turn.timer);
    saveDraft(turn.message,turn.scope,'interrupted');
    if(restore && turn.revision===conversationRevision){if(byId('chatInput')&&!byId('chatInput').value)byId('chatInput').value=turn.message;recoveryStatus('Stopped. Your message is kept. Check history before sending again.');}
    emitChatTurn(turn,'cancelled');
    const input=byId('chatInput'),submit=byId('chatForm')?.querySelector('button[type="submit"]');
    if(input)input.disabled=false;if(submit)submit.disabled=false;
    return true;
  }
  function leaveConversation(){
    window.dispatchEvent(new Event('homeserver:chat-context-changing'));
    if(!pendingChatTurn)saveDraft(byId('chatInput')?.value);
    conversationRevision+=1;cancelChatTurn(null,false);
    window.HomeServerConversationVoice?.stop?.();
  }
  window.HomeServerChatTurn=Object.freeze({isReady:()=>contextReady&&restoreLoads===0,isBusy:()=>Boolean(pendingChatTurn),conversationId:()=>activeConversationId,cancel:cancelChatTurn,usePreparedConversation:id=>{
    if(pendingChatTurn||typeof id!=='string'||!/^[0-9a-f]{32}$/.test(id))return false;
    const previous=scopeKey();activeConversationId=id;
    const old=drafts()[previous];if(old&&previous!==id){saveDraft(old.text,id,old.state);saveDraft('',previous);}
    rememberConversation();setChatTitle();void loadConversations(false).catch(()=>{});return true;
  }});
  window.addEventListener('pagehide',()=>cancelChatTurn());
  const byId = id => document.getElementById(id);
  const escapeHtml = (value = '') => String(value).replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
  const timeLabel = value => value ? new Date(value).toLocaleString() : '';

  if (!document.querySelector('script[data-homeserver-shell]')) {
    const script = document.createElement('script');
    script.src = '/assets/shell.js';
    script.dataset.homeserverShell = '1';
    script.async = false;
    document.head.appendChild(script);
  }

  async function brainApi(path, options = {}) {
    const headers = {...(options.headers || {})};
    if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, {...options, headers});
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) {const error=new Error(data.detail || `Request failed (${response.status})`);error.status=response.status;if(response.status===401)clearPrivateRecovery();throw error;}
    return data;
  }

  function brainFlash(message, error = false) {
    const node = byId('flash');
    if (!node) return;
    node.textContent = message;
    node.className = `flash show${error ? ' error' : ''}`;
    clearTimeout(brainFlash.timer);
    brainFlash.timer = setTimeout(() => { node.className = 'flash'; }, 3500);
  }

  function setChatTitle(title = 'New conversation') {
    const node = byId('chatConversationTitle');
    if (node) node.textContent = title;
  }

  function durationLabel(ms) {
    const total = Math.max(0, Math.round(Number(ms || 0) / 1000));
    const minutes = Math.floor(total / 60);
    const seconds = total % 60;
    return minutes ? `${minutes}m ${seconds}s` : `${seconds}s`;
  }

  function meetingRows(items, key, label) {
    const rows = Array.isArray(items) ? items : [];
    if (!rows.length) return '';
    return `<section class="meeting-card-section"><h5>${escapeHtml(label)}</h5><ul>${rows.map(item => {
      const primary = escapeHtml(item?.[key] || '');
      const extras = [item?.owner, item?.due_date, item?.contact, item?.signal].filter(Boolean).map(escapeHtml).join(' · ');
      return `<li><span>${primary}</span>${extras ? `<small>${extras}</small>` : ''}</li>`;
    }).join('')}</ul></section>`;
  }

  function renderMeetingCard(card) {
    const status = card?.status || 'completed';
    const meta = [
      durationLabel(card?.duration_ms),
      `${Number(card?.segment_count || 0)} transcript segment${Number(card?.segment_count || 0) === 1 ? '' : 's'}`,
      status,
    ].filter(Boolean).join(' · ');
    return `<article class="meeting-card">
      <header><div><p class="eyebrow">VP3 PHYSICAL MEETING</p><h4>${escapeHtml(card?.title || 'Meeting')}</h4></div><span class="meeting-card-status">${escapeHtml(status)}</span></header>
      <p class="meeting-card-summary">${escapeHtml(card?.summary || 'Meeting intelligence was not available.')}</p>
      <div class="meeting-card-meta">${escapeHtml(meta)}</div>
      ${meetingRows(card?.decisions, 'decision', 'Decisions')}
      ${meetingRows(card?.actions, 'action', 'Actions & commitments')}
      ${meetingRows(card?.task_candidates, 'title', 'Task candidates')}
      ${meetingRows(card?.questions, 'question', 'Open questions')}
      ${meetingRows(card?.risks, 'risk', 'Risks')}
      ${card?.follow_up_draft ? `<section class="meeting-card-section"><h5>Follow-up draft</h5><p>${escapeHtml(card.follow_up_draft)}</p></section>` : ''}
    </article>`;
  }

  function researchReportLinks(message) {
    return (Array.isArray(message.research_reports) ? message.research_reports.slice(0,5) : []).map((report,index) => {
      try {
        const source = new URL(String(report.url || ''));
        if (!['https:','http:'].includes(source.protocol) || source.username || source.password) return '';
        return `<a class="hs-chat-research-source" style="display:inline-block;max-width:100%;overflow-wrap:anywhere" href="${escapeHtml(source.href)}" target="_blank" rel="noopener noreferrer">[R${index+1}] ${escapeHtml(report.title || 'Saved research report')}</a>`;
      } catch (_) { return ''; }
    }).join(' · ');
  }

  function annotationCitationLinks(message) {
    return (Array.isArray(message.annotation_citations)?message.annotation_citations.slice(0,4):[]).map(c=>{
      try{const u=new URL(String(c.source_url||''));if(!['https:','http:'].includes(u.protocol)||u.username||u.password||!/^A[1-4]$/.test(c.label))return '';
      return `<details class="hs-chat-annotation-source"><summary><a href="${escapeHtml(u.href)}" target="_blank" rel="noopener noreferrer">[${escapeHtml(c.label)}] ${escapeHtml(c.title)}</a> · ${escapeHtml(c.captured_at)} UTC</summary><blockquote>${escapeHtml(c.excerpt||'')}</blockquote></details>`;
      }catch(_){return '';}
    }).join('');
  }

  function projectContextLinks(message){
    const packet=message.project_knowledge_r6;if(!packet?.active)return '';
    return '<section class="hs-chat-project-sources"><strong>'+escapeHtml(packet.state.title)+'</strong>'+packet.sources.map(s=>{
      try{const u=new URL(s.url);if(!['http:','https:'].includes(u.protocol)||u.username||u.password)return '';
      return '<a style="display:block;overflow-wrap:anywhere" href="'+escapeHtml(u.href)+'" target="_blank" rel="noopener noreferrer">['+escapeHtml(s.label)+'] '+escapeHtml(s.title)+' · '+escapeHtml(s.captured_at)+' UTC</a>';
      }catch(_){return '';}
    }).join('')+'</section>';
  }

  function chatMessageMarkup(message = {}) {
    const role = String(message.role || 'assistant') === 'user' ? 'user' : 'assistant';
    const assistantName = String(byId('sidebarAgentName')?.textContent || 'HomeServer Agent').trim() || 'HomeServer Agent';
    const label = role === 'user' ? 'You' : assistantName;
    const avatar = role === 'user' ? 'Y' : '✦';
    const model = message.model ? `<small class="hs-chat-model">${escapeHtml(message.model)}</small>` : '';
    return `<div class="chat-message ${role}" data-chat-message-id="${Number(message.id||0)}">
      <div class="hs-chat-avatar" aria-hidden="true">${avatar}</div>
      <div class="hs-chat-message-body">
        <div class="hs-chat-role">${escapeHtml(label)}</div>
        <div class="hs-chat-copy">${escapeHtml(message.content || '')}</div>
        ${model}
        ${role === 'assistant' ? researchReportLinks(message)+annotationCitationLinks(message)+projectContextLinks(message) : ''}
      </div>
    </div>`;
  }

  function renderMessages(data) {
    const node = byId('chatMessages');
    if (!node) return;
    const messages = data?.messages || [];
    window.VP3ProjectKnowledgeR6?.record([...messages].reverse().find(m=>m.role==='assistant')?.project_knowledge_r6);
    if (!messages.length) {
      node.innerHTML = '<div class="chat-empty">Start a private conversation with your HomeServer Agent Brain. Local memory, knowledge and tools are used only within your permissions.</div>';
      return;
    }
    node.innerHTML = messages.map(message => {
      if (message?.card?.card_type === 'meeting') return renderMeetingCard(message.card);
      return chatMessageMarkup(message);
    }).join('');
    for(const message of messages){if(message.role==='assistant')window.VP3KnowledgeActionsR9?.show(message.knowledge_actions_r9,node.querySelector('[data-chat-message-id="'+Number(message.id||0)+'"] .hs-chat-message-body'));}

    if (byId('view-chat')?.classList.contains('active')) {
      requestAnimationFrame(() => node.lastElementChild?.scrollIntoView({block: 'end', behavior: 'instant'}));
    }
  }

  async function loadConversation(id, revision = null, turnId = null) {
    const switching=revision===null;if(switching)restoreLoads++;
    try {
    if(revision === null){leaveConversation();revision=conversationRevision;}
    const data = await brainApi(`/api/v1/control/conversations/${encodeURIComponent(id)}`);
    if(revision !== conversationRevision || (turnId !== null && turnId !== chatTurnSequence))return;
    activeConversationId = id; contextReady=true;
    window.HomeServerAgentRouting?.adoptConversation?.(id,data.routing?.agent);
    rememberConversation();
    setChatTitle(data.conversation?.title || 'Conversation');
    renderMessages(data);
    if(turnId === null)restoreDraft();
    document.querySelectorAll('[data-brain-conversation]').forEach(item => item.classList.toggle('active', item.dataset.brainConversation === id));
    } finally {if(switching)restoreLoads--;}
  }

  function conversationMarkup(item) {
    const active = item.id === activeConversationId ? ' active' : '';
    return `<div class="conversation-row" data-conversation-row="${escapeHtml(item.id)}">
      <button type="button" class="conversation-item${active}" data-brain-conversation="${escapeHtml(item.id)}" title="${escapeHtml(item.title)}">
        <strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.last_message || 'No messages yet')}</span><span>${escapeHtml(timeLabel(item.updated_at))}</span>
      </button>
      <button class="conversation-more" type="button" data-conversation-more="${escapeHtml(item.id)}" aria-label="Conversation options">⋯</button>
      <div class="conversation-menu hidden" data-conversation-menu="${escapeHtml(item.id)}">
        <button type="button" data-conversation-rename="${escapeHtml(item.id)}" data-conversation-title="${escapeHtml(item.title)}">Rename</button>
        <button type="button" class="danger" data-conversation-delete="${escapeHtml(item.id)}">Delete</button>
      </div>
    </div>`;
  }

  async function loadConversations(selectFirst = false) {
    const data = await brainApi('/api/v1/control/conversations?limit=50');
    const list = byId('conversationList');
    if (!list) return;
    list.innerHTML = data.items.length ? data.items.map(conversationMarkup).join('') : '<div class="empty-state">No conversations yet.</div>';
    if (activeConversationId && data.items.some(item => item.id === activeConversationId)) return;
    if (selectFirst) {
      const requested=new URLSearchParams(location.search).get('conversation_id');
      if(validId(requested)){try{await loadConversation(requested);return;}catch(error){if(error.status!==404)throw error;}finally{const url=new URL(location.href);url.searchParams.delete('conversation_id');history.replaceState(null,'',url.pathname+url.search+url.hash);}}
      const saved=readStore('localStorage',ACTIVE_KEY,null);
      if(saved && Number.isFinite(saved.at) && saved.at<=Date.now() && Date.now()-saved.at<2592000000){
        if(saved.id === null){
          const agent=window.HomeServerAgentRouting?.getAgents?.().find(item=>Number(item.id)===Number(saved.agentId));
          window.HomeServerAgentRouting?.adoptConversation?.(null,agent);
          contextReady=true;restoreDraft();return;
        }
        if(validId(saved.id)){try{await loadConversation(saved.id);}catch(error){if(error.status!==404)throw error;activeConversationId=null;contextReady=true;rememberConversation();restoreDraft();}return;}
      }
      if(data.items.length)await loadConversation(data.items[0].id);
      else {contextReady=true;restoreDraft();}
    }
  }

  async function deleteConversation(id) {
    if (!confirm('Delete this local conversation?')) return;
    if(activeConversationId === id)leaveConversation();
    await brainApi(`/api/v1/control/conversations/${encodeURIComponent(id)}`, {method: 'DELETE'});
    if (activeConversationId === id) {
      activeConversationId = null;
      rememberConversation();
      window.HomeServerAgentRouting?.adoptConversation?.(null,null);
      setChatTitle();
      renderMessages({messages: []});
      restoreDraft();
    }
    saveDraft('',id);
    await loadConversations(false);
    brainFlash('Conversation deleted.');
  }

  async function renameConversation(id, currentTitle) {
    const title = prompt('Rename conversation', currentTitle || '');
    if (title == null) return;
    const normalized = title.trim();
    if (!normalized) return;
    await brainApi(`/api/v1/control/conversations/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify({title: normalized}),
    });
    if (activeConversationId === id) setChatTitle(normalized);
    await loadConversations(false);
    brainFlash('Conversation renamed.');
  }

  function ensureAgentBrainControls() {
    const view = byId('view-agent');
    if (!view || byId('agentCredentialsForm')) return;
    const introTitle = view.querySelector('.section-intro h2');
    if (introTitle) introTitle.textContent = 'AGENT BRAIN';
    const introCopy = view.querySelector('.section-intro p');
    if (introCopy) introCopy.textContent = 'Configure the identity, intelligence providers, private credentials and bounded tools used by HomeServer and paired applications such as VP3.';

    const providerForm = byId('providerForm');
    const form = document.createElement('form');
    form.id = 'agentCredentialsForm';
    form.className = 'panel agent-credentials-panel';
    form.innerHTML = `
      <div class="panel-head"><div><p class="eyebrow">AGENT INTELLIGENCE</p><h3>Provider connections</h3></div><span id="inferenceState" class="provider-state">Loading…</span></div>
      <p class="muted">HomeServer prefers its own available intelligence. VP3 can use this Agent Brain without debiting VP3 cloud tokens. If no HomeServer provider is ready, VP3 may use the user's cloud subscription and purchased token balance.</p>
      <label>Preferred inference provider
        <select id="preferredInferenceProvider">
          <option value="auto">Auto — local first</option>
          <option value="ollama">Ollama local</option>
          <option value="anthropic">Claude / Anthropic</option>
          <option value="openai">OpenAI</option>
          <option value="openrouter">OpenRouter</option>
        </select>
      </label>
      <div class="credential-grid">
        <label class="credential-field">Claude / Anthropic API key <span id="credentialAnthropicStatus" class="credential-status"></span><input id="anthropicApiKey" type="password" autocomplete="off" placeholder="Leave blank to keep saved key"><button class="text-button danger" type="button" data-clear-provider-credential="anthropic">Clear saved key</button></label>
        <label class="credential-field">OpenAI API key <span id="credentialOpenaiStatus" class="credential-status"></span><input id="openaiApiKey" type="password" autocomplete="off" placeholder="Leave blank to keep saved key"><button class="text-button danger" type="button" data-clear-provider-credential="openai">Clear saved key</button></label>
        <label class="credential-field">OpenRouter API key <span id="credentialOpenrouterStatus" class="credential-status"></span><input id="openrouterApiKey" type="password" autocomplete="off" placeholder="Leave blank to keep saved key"><button class="text-button danger" type="button" data-clear-provider-credential="openrouter">Clear saved key</button></label>
        <label class="credential-field">ElevenLabs API key <span id="credentialElevenlabsStatus" class="credential-status"></span><input id="elevenlabsApiKey" type="password" autocomplete="off" placeholder="Leave blank to keep saved key"><button class="text-button danger" type="button" data-clear-provider-credential="elevenlabs">Clear saved key</button></label>
      </div>
      <div class="provider-grid" style="margin-top:16px">
        <label>Claude model<input id="anthropicModel" list="anthropicModelOptions" maxlength="200" placeholder="claude-haiku-5-5"><datalist id="anthropicModelOptions"><option value="claude-haiku-5-5">Claude Haiku 5.5 — Main model</option><option value="claude-haiku-4-5">Claude Haiku 4.5</option><option value="claude-sonnet-5">Claude Sonnet 5</option><option value="claude-opus-5">Claude Opus 5</option></datalist></label>
        <label>OpenAI model<input id="openaiModel" maxlength="200" placeholder="Enter your OpenAI model id"></label>
        <label>OpenRouter model<input id="openrouterModel" maxlength="200" placeholder="provider/model"></label>
      </div>
      <div class="form-grid">
        <label class="check-inline"><input id="anthropicEnabled" type="checkbox"> Enable Claude / Anthropic</label>
        <label class="check-inline"><input id="openaiEnabled" type="checkbox"> Enable OpenAI</label>
        <label class="check-inline"><input id="openrouterEnabled" type="checkbox"> Enable OpenRouter</label>
      </div>
      <div class="credential-actions"><button class="button primary" type="submit">Save Agent Brain Connections</button></div>`;
    providerForm?.insertAdjacentElement('afterend', form);
  }

  function setCredentialStatus(provider, info) {
    const id = `credential${provider[0].toUpperCase()}${provider.slice(1)}Status`;
    const node = byId(id);
    if (!node) return;
    const configured = Boolean(info?.configured);
    node.classList.toggle('configured', configured);
    node.textContent = configured ? `Saved ••••${info.suffix || ''}` : 'Not set';
  }

  async function loadProvider() {
    const data = await brainApi('/api/v1/control/provider');
    const provider = data.provider || {};
    if (byId('providerUrl')) byId('providerUrl').value = provider.base_url || 'http://127.0.0.1:11434';
    if (byId('providerModel')) byId('providerModel').value = provider.model || '';
    if (byId('providerEnabled')) byId('providerEnabled').checked = Boolean(provider.enabled);
    const status = byId('providerState');
    if (status) status.textContent = provider.enabled ? `Enabled · ${provider.model || 'model not set'}` : 'Disabled';
  }

  async function loadInference() {
    ensureAgentBrainControls();
    const [inference, credentials] = await Promise.all([
      brainApi('/api/v1/control/inference'),
      brainApi('/api/v1/control/provider-credentials'),
    ]);
    if (byId('preferredInferenceProvider')) byId('preferredInferenceProvider').value = inference.preferred_provider || 'auto';
    const providers = Object.fromEntries((inference.providers || []).map(item => [item.provider_key, item]));
    for (const key of ['anthropic', 'openai', 'openrouter']) {
      if (byId(`${key}Model`)) byId(`${key}Model`).value = providers[key]?.model || '';
      if (byId(`${key}Enabled`)) byId(`${key}Enabled`).checked = Boolean(providers[key]?.enabled);
    }
    for (const key of ['anthropic', 'openai', 'openrouter', 'elevenlabs']) setCredentialStatus(key, credentials.providers?.[key]);
    const state = byId('inferenceState');
    if (state) {
      state.textContent = inference.available
        ? `Ready · ${inference.compute_source === 'homeserver_local' ? 'HomeServer local' : 'user provider'} · ${inference.selected_provider}${inference.model ? ` / ${inference.model}` : ''}`
        : 'VP3 cloud fallback required';
    }
  }

  async function loadAgentTools() {
    const data = await brainApi('/api/v1/control/agent-tools');
    const policy = data.policy || {};
    if (byId('agentToolsEnabled')) byId('agentToolsEnabled').checked = Boolean(policy.enabled);
    if (byId('agentToolsMaxCalls')) byId('agentToolsMaxCalls').value = String(policy.max_calls || 3);
    if (byId('agentWriteProposals')) byId('agentWriteProposals').checked = Boolean(policy.allow_write_proposals);
    const state = byId('agentToolsState');
    if (state) {
      const available = (data.available_tools || []).length;
      const proposalText = policy.allow_write_proposals ? ' · proposals on' : '';
      state.textContent = policy.enabled ? `Enabled · ${available} tools${proposalText}` : 'Disabled';
    }
  }

  async function loadChatView() {
    restoreLoads++;
    try {
    await window.HomeServerAgentRouting?.refresh?.();
    const calls = [loadConversations(true), loadProvider(), loadAgentTools()];
    calls.push(loadInference().catch(() => null));
    await Promise.all(calls);
    if (!activeConversationId) {
      setChatTitle();
      renderMessages({messages: []});
    }
    } catch(error) { if(error.status!==401)recoveryStatus('Conversation history is unavailable. Open a conversation or start a new chat.'); throw error; }
    finally {restoreLoads--;}
  }

  document.addEventListener('click', async event => {
    const chatNav = event.target.closest('[data-view="chat"], [data-go="chat"]');
    if (chatNav) {
      const title = byId('pageTitle');
      if (title) title.textContent = 'Agent Chat';
      try { await loadChatView(); } catch (err) { brainFlash(err.message, true); }
      return;
    }

    const agentNav = event.target.closest('[data-view="agent"], [data-go="agent"]');
    if (agentNav) {
      if (byId('pageTitle')) byId('pageTitle').textContent = 'AGENT BRAIN';
      try { await Promise.all([loadProvider(), loadAgentTools(), loadInference()]); } catch (err) { brainFlash(err.message, true); }
    }

    const more = event.target.closest('[data-conversation-more]');
    if (more) {
      event.stopPropagation();
      const id = more.dataset.conversationMore;
      document.querySelectorAll('.conversation-menu').forEach(menu => menu.classList.toggle('hidden', menu.dataset.conversationMenu !== id || !menu.classList.contains('hidden')));
      return;
    }

    const rename = event.target.closest('[data-conversation-rename]');
    if (rename) {
      try { await renameConversation(rename.dataset.conversationRename, rename.dataset.conversationTitle); } catch (err) { brainFlash(err.message, true); }
      return;
    }

    const remove = event.target.closest('[data-conversation-delete]');
    if (remove) {
      try { await deleteConversation(remove.dataset.conversationDelete); } catch (err) { brainFlash(err.message, true); }
      return;
    }

    const conversation = event.target.closest('[data-brain-conversation]');
    if (conversation) {
      try { await loadConversation(conversation.dataset.brainConversation); } catch (err) { brainFlash(err.message, true); }
      return;
    }

    if (event.target.id === 'newChat') {
      leaveConversation();contextReady=true;
      activeConversationId = null;
      window.HomeServerAgentRouting?.adoptConversation?.(null,null);
      rememberConversation();
      setChatTitle();
      renderMessages({messages: []});
      restoreDraft();
      document.querySelectorAll('[data-brain-conversation]').forEach(item => item.classList.remove('active'));
      byId('chatInput')?.focus();
    }

    if (event.target.id === 'deleteChat' && activeConversationId) {
      try { await deleteConversation(activeConversationId); } catch (err) { brainFlash(err.message, true); }
    }

    const clearCredential = event.target.closest('[data-clear-provider-credential]');
    if (clearCredential) {
      const provider = clearCredential.dataset.clearProviderCredential;
      if (!confirm(`Clear the saved ${provider} API key from HomeServer?`)) return;
      try {
        await brainApi('/api/v1/control/provider-credentials', {method:'PUT', body:JSON.stringify({clear:[provider]})});
        await loadInference();
        brainFlash('Saved provider key cleared.');
      } catch (err) { brainFlash(err.message, true); }
      return;
    }

    if (event.target.id === 'detectOllama') {
      const button = event.target;
      button.disabled = true;
      try {
        const data = await brainApi('/api/v1/control/provider/test', {
          method: 'POST',
          body: JSON.stringify({base_url: byId('providerUrl').value, model: byId('providerModel').value, enabled: byId('providerEnabled').checked}),
        });
        const list = byId('ollamaModels');
        if (list) list.innerHTML = data.models.map(model => `<option value="${escapeHtml(model)}"></option>`).join('');
        if (!byId('providerModel').value && data.models.length) byId('providerModel').value = data.models[0];
        brainFlash(data.models.length ? `Ollama connected · ${data.models.length} model${data.models.length === 1 ? '' : 's'} found.` : 'Ollama connected, but no models are installed.');
      } catch (err) { brainFlash(err.message, true); }
      finally { button.disabled = false; }
    }
  });

  const chatInput = byId('chatInput');
  window.addEventListener('homeserver:chat-agent-changed',event=>{
    if(event.detail?.reason!=='selection'||activeConversationId||pendingChatTurn||!contextReady)return;
    if(restoredDraftScope)saveDraft(chatInput?.value,restoredDraftScope);
    rememberConversation();restoreDraft();
  });
  document.addEventListener('submit',event=>{
    if(event.target!==byId('chatForm') || (contextReady && !restoreLoads && navigator.onLine!==false))return;
    event.preventDefault();event.stopImmediatePropagation();
    if(contextReady&&!restoreLoads)saveDraft(chatInput?.value);window.HomeServerConversationVoice?.stop?.();
    recoveryStatus(!contextReady?'Open a conversation or start a new chat before sending.':restoreLoads?'Restoring your conversation. Your message is kept.':'You are offline. Your message is kept.');
  },true);
  chatInput?.addEventListener('input', () => {
    if(!pendingChatTurn){saveDraft(chatInput.value);if(!activeConversationId)rememberConversation();}
    chatInput.style.height = 'auto';
    chatInput.style.height = `${Math.min(chatInput.scrollHeight, 180)}px`;
  });
  chatInput?.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      byId('chatForm')?.requestSubmit();
    }
  });

  async function submitChatTurn(event) {
    event.preventDefault();
    const input = byId('chatInput');
    const message = input.value.trim();
    if (!message || pendingChatTurn) return;
    const submit = event.target.querySelector('button[type="submit"]');
    if(navigator.onLine===false){saveDraft(message);window.HomeServerConversationVoice?.stop?.();recoveryStatus('You are offline. Your message is kept.');return;}
    recoveryStatus();
    const turn={id:++chatTurnSequence,conversationId:activeConversationId,message,scope:scopeKey(),controller:new AbortController(),revision:conversationRevision};
    saveDraft(message,turn.scope,'pending');
    pendingChatTurn=turn;
    turn.timer=setTimeout(()=>turn.controller.abort(),120000);
    submit.disabled = true;
    input.disabled = true;
    emitChatTurn(turn,'started');
    const existing = byId('chatMessages');
    if (existing && existing.querySelector('.chat-empty')) existing.innerHTML = '';
    existing?.insertAdjacentHTML('beforeend', chatMessageMarkup({role:'user',content:message}));
    input.value = '';
    input.style.height = 'auto';
    try {
      // Settle promptly on cancellation even if a transport ignores its signal.
      const data = await new Promise((resolve,reject)=>{
        const abort=()=>reject(new DOMException('Chat turn cancelled or timed out.','AbortError'));
        turn.controller.signal.addEventListener('abort',abort,{once:true});
        brainApi('/api/v1/control/chat', {method:'POST', signal:turn.controller.signal,body:JSON.stringify({message, speaker_context:window.VP3VoiceGuard?.takeContext(message)||(!window.VP3VoiceGuard?.wasVoiceTurn?.(message)?window.VP3ParticipantContinuity?.selectedContext():null),conversation_id:turn.conversationId, project_knowledge_r6:window.VP3ProjectKnowledgeR6?.value(),knowledge_revision:window.VP3KnowledgeRevisionsR11?.value(message)})})
          .then(resolve,reject).finally(()=>turn.controller.signal.removeEventListener('abort',abort));
      });
      if(pendingChatTurn !== turn || turn.revision !== conversationRevision || turn.controller.signal.aborted)return;
      if(!data.conversation_id || typeof data.reply !== 'string')throw new Error('Agent returned an invalid chat response. Try again.');
      clearTimeout(turn.timer);
      activeConversationId = data.conversation_id;
      rememberConversation();saveDraft('',turn.scope);
      window.VP3ProjectKnowledgeR6?.consume(data.project_knowledge_r6);window.VP3KnowledgeRevisionsR11?.consume();
      const reply=String(data.reply || '').trim();
      if(reply)existing?.insertAdjacentHTML('beforeend',chatMessageMarkup({role:'assistant',content:reply,annotation_citations:data.annotation_citations||[],project_knowledge_r6:data.project_knowledge_r6}));
      window.VP3KnowledgeActionsR9?.show(data.knowledge_actions_r9,existing?.lastElementChild?.querySelector('.hs-chat-message-body'));
      emitChatTurn(turn,'completed',{conversationId:data.conversation_id,runId:data.run_id,reply});
      const context = byId('chatContext');
      if (context) {
        const toolText = data.tools?.call_count ? ` · ${data.tools.call_count} tool call${data.tools.call_count === 1 ? '' : 's'}` : '';
        const pending = data.tools?.action_request_ids?.length || 0;
        const approvalText = pending ? ` · ${pending} approval${pending === 1 ? '' : 's'} pending` : '';
        const computeText = data.knowledge_actions_r9?.execution?.source==='vp3_cloud'?'VP3 Cloud':data.compute_source === 'homeserver_local' ? 'HomeServer local' : data.knowledge_actions_r9?'Knowledge review':'user provider';
        context.textContent = `${computeText} · ${Number(data.cloud_tokens_debited||0)} VP3 cloud tokens · ${data.context?.memory_count || 0} memories · ${data.context?.knowledge_count || 0} knowledge matches${toolText}${approvalText} · ${data.model || ''}`;
      }
      if (data.tools?.action_request_ids?.length) brainFlash('Agent created a pending action. Review it in Approvals.');
      // Sidebar/history availability must not turn a successful reply into a
      // failed turn or hold the composer disabled.
      void Promise.allSettled([loadConversation(activeConversationId,turn.revision,turn.id),loadConversations(false)]).then(results=>{
        if(turn.id===chatTurnSequence && turn.revision===conversationRevision && results.some(result=>result.status==='rejected'))brainFlash('Reply received. Conversation history refresh failed; retry from the conversation list.',true);
      });
    } catch (err) {
      if(pendingChatTurn !== turn)return;
      if(err.status===401){clearPrivateRecovery();emitChatTurn(turn,'failed',{message:'Sign in again to continue.'});return;}
      saveDraft(message,turn.scope,'failed');
      if(!input.value)input.value=message;
      recoveryStatus('Your message is kept. Check history before sending again.',`Chat ${err.status || err.name || 'transport error'}`);
      emitChatTurn(turn,'failed',{message:err.name==='AbortError'?'Chat request timed out. Try again.':err.message});
      brainFlash(err.name==='AbortError'?'Chat request timed out. Try again.':err.message,true);
    } finally {
      clearTimeout(turn.timer);
      if(pendingChatTurn===turn){
        pendingChatTurn=null;submit.disabled=false;input.disabled=false;input.focus();
      }
    }
  }
  byId('chatForm')?.addEventListener('submit',submitChatTurn);

  byId('providerForm')?.addEventListener('submit', async event => {
    event.preventDefault();
    try {
      const data = await brainApi('/api/v1/control/provider', {
        method: 'PUT',
        body: JSON.stringify({base_url:byId('providerUrl').value, model:byId('providerModel').value, enabled:byId('providerEnabled').checked}),
      });
      const status = byId('providerState');
      if (status) status.textContent = data.provider.enabled ? `Enabled · ${data.provider.model}` : 'Disabled';
      await loadInference().catch(() => null);
      brainFlash('Local model provider saved.');
    } catch (err) { brainFlash(err.message, true); }
  });

  document.addEventListener('submit', async event => {
    if (event.target.id !== 'agentCredentialsForm') return;
    event.preventDefault();
    const button = event.target.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const credentials = {};
      const credentialInputs = {
        anthropic: byId('anthropicApiKey'),
        openai: byId('openaiApiKey'),
        openrouter: byId('openrouterApiKey'),
        elevenlabs: byId('elevenlabsApiKey'),
      };
      for (const [key, input] of Object.entries(credentialInputs)) {
        const value = input?.value.trim();
        if (value) credentials[key] = value;
      }
      if (Object.keys(credentials).length) {
        await brainApi('/api/v1/control/provider-credentials', {method:'PUT', body:JSON.stringify(credentials)});
        Object.values(credentialInputs).forEach(input => { if (input) input.value = ''; });
      }
      for (const key of ['anthropic', 'openai', 'openrouter']) {
        await brainApi('/api/v1/control/inference/provider', {
          method:'PUT',
          body:JSON.stringify({
            provider_key:key,
            model:byId(`${key}Model`)?.value || '',
            enabled:Boolean(byId(`${key}Enabled`)?.checked),
          }),
        });
      }
      await brainApi('/api/v1/control/inference/preference', {
        method:'PUT',
        body:JSON.stringify({preferred_provider:byId('preferredInferenceProvider')?.value || 'auto'}),
      });
      await loadInference();
      brainFlash('Agent Brain connections saved.');
    } catch (err) { brainFlash(err.message, true); }
    finally { button.disabled = false; }
  });

  byId('agentToolsForm')?.addEventListener('submit', async event => {
    event.preventDefault();
    try {
      const data = await brainApi('/api/v1/control/agent-tools', {
        method: 'PUT',
        body: JSON.stringify({
          enabled: byId('agentToolsEnabled').checked,
          max_calls: Number(byId('agentToolsMaxCalls').value || 3),
          allow_write_proposals: byId('agentWriteProposals').checked,
        }),
      });
      const state = byId('agentToolsState');
      const proposalText = data.policy.allow_write_proposals ? ' · proposals on' : '';
      if (state) state.textContent = data.policy.enabled ? `Enabled · max ${data.policy.max_calls}${proposalText}` : 'Disabled';
      brainFlash(data.policy.enabled ? 'Agent Tool policy saved.' : 'Agent Tools disabled.');
    } catch (err) { brainFlash(err.message, true); }
  });

  window.addEventListener('hashchange', () => {
    if (location.hash === '#chat') document.querySelector('.nav [data-view="chat"]')?.click();
  });

  ensureAgentBrainControls();
  if (location.hash === '#chat') setTimeout(() => document.querySelector('.nav [data-view="chat"]')?.click(), 0);
})();
