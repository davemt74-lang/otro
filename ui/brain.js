(() => {
  'use strict';

  let activeConversationId = null;
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
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
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

  function renderMessages(data) {
    const node = byId('chatMessages');
    if (!node) return;
    const messages = data?.messages || [];
    if (!messages.length) {
      node.innerHTML = '<div class="chat-empty">Start a private conversation with your HomeServer Agent Brain. Local memory, knowledge and tools are used only within your permissions.</div>';
      return;
    }
    node.innerHTML = messages.map(message => `<div class="chat-message ${escapeHtml(message.role)}">${escapeHtml(message.content)}${message.model ? `<small>${escapeHtml(message.model)}</small>` : ''}</div>`).join('');
    node.scrollTop = node.scrollHeight;
  }

  async function loadConversation(id) {
    const data = await brainApi(`/api/v1/control/conversations/${encodeURIComponent(id)}`);
    activeConversationId = id;
    setChatTitle(data.conversation?.title || 'Conversation');
    renderMessages(data);
    document.querySelectorAll('[data-brain-conversation]').forEach(item => item.classList.toggle('active', item.dataset.brainConversation === id));
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
    if (selectFirst && data.items.length) await loadConversation(data.items[0].id);
  }

  async function deleteConversation(id) {
    if (!confirm('Delete this local conversation?')) return;
    await brainApi(`/api/v1/control/conversations/${encodeURIComponent(id)}`, {method: 'DELETE'});
    if (activeConversationId === id) {
      activeConversationId = null;
      setChatTitle();
      renderMessages({messages: []});
    }
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
        <label>Claude model<input id="anthropicModel" maxlength="200" placeholder="Enter your Anthropic model id"></label>
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
    const calls = [loadConversations(true), loadProvider(), loadAgentTools()];
    calls.push(loadInference().catch(() => null));
    await Promise.all(calls);
    if (!activeConversationId) {
      setChatTitle();
      renderMessages({messages: []});
    }
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
      activeConversationId = null;
      setChatTitle();
      renderMessages({messages: []});
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
  chatInput?.addEventListener('input', () => {
    chatInput.style.height = 'auto';
    chatInput.style.height = `${Math.min(chatInput.scrollHeight, 180)}px`;
  });
  chatInput?.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      byId('chatForm')?.requestSubmit();
    }
  });

  byId('chatForm')?.addEventListener('submit', async event => {
    event.preventDefault();
    const input = byId('chatInput');
    const message = input.value.trim();
    if (!message) return;
    const submit = event.target.querySelector('button[type="submit"]');
    submit.disabled = true;
    input.disabled = true;
    const existing = byId('chatMessages');
    if (existing && existing.querySelector('.chat-empty')) existing.innerHTML = '';
    existing?.insertAdjacentHTML('beforeend', `<div class="chat-message user">${escapeHtml(message)}</div>`);
    input.value = '';
    input.style.height = 'auto';
    try {
      const data = await brainApi('/api/v1/control/chat', {method:'POST', body:JSON.stringify({message, conversation_id:activeConversationId})});
      activeConversationId = data.conversation_id;
      await Promise.all([loadConversation(activeConversationId), loadConversations(false)]);
      const context = byId('chatContext');
      if (context) {
        const toolText = data.tools?.call_count ? ` · ${data.tools.call_count} tool call${data.tools.call_count === 1 ? '' : 's'}` : '';
        const pending = data.tools?.action_request_ids?.length || 0;
        const approvalText = pending ? ` · ${pending} approval${pending === 1 ? '' : 's'} pending` : '';
        const computeText = data.compute_source === 'homeserver_local' ? 'HomeServer local' : 'user provider';
        context.textContent = `${computeText} · 0 VP3 cloud tokens · ${data.context.memory_count} memories · ${data.context.knowledge_count} knowledge matches${toolText}${approvalText} · ${data.model}`;
      }
      if (data.tools?.action_request_ids?.length) brainFlash('Agent created a pending action. Review it in Approvals.');
    } catch (err) {
      existing?.insertAdjacentHTML('beforeend', `<div class="chat-message assistant">${escapeHtml(err.message)}</div>`);
      brainFlash(err.message, true);
    } finally {
      submit.disabled = false;
      input.disabled = false;
      input.focus();
    }
  });

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
