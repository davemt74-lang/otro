(() => {
  'use strict';

  let activeConversationId = null;
  const byId = id => document.getElementById(id);
  const escapeHtml = (value = '') => String(value).replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
  const timeLabel = value => value ? new Date(value).toLocaleString() : '';

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
      node.innerHTML = '<div class="chat-empty">Start a private conversation with your HomeServer agent. Relevant local memory and knowledge are added to context only when permitted.</div>';
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

  async function loadConversations(selectFirst = false) {
    const data = await brainApi('/api/v1/control/conversations?limit=50');
    const list = byId('conversationList');
    if (!list) return;
    list.innerHTML = data.items.length ? data.items.map(item => `<button type="button" class="conversation-item${item.id === activeConversationId ? ' active' : ''}" data-brain-conversation="${escapeHtml(item.id)}"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.last_message || 'No messages yet')}</span><span>${escapeHtml(timeLabel(item.updated_at))}</span></button>`).join('') : '<div class="empty-state">No conversations yet.</div>';
    if (activeConversationId && data.items.some(item => item.id === activeConversationId)) return;
    if (selectFirst && data.items.length) await loadConversation(data.items[0].id);
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

  async function loadAgentTools() {
    const data = await brainApi('/api/v1/control/agent-tools');
    const policy = data.policy || {};
    if (byId('agentToolsEnabled')) byId('agentToolsEnabled').checked = Boolean(policy.enabled);
    if (byId('agentToolsMaxCalls')) byId('agentToolsMaxCalls').value = String(policy.max_calls || 3);
    const state = byId('agentToolsState');
    if (state) {
      const available = (data.available_tools || []).length;
      state.textContent = policy.enabled ? `Enabled · read-only · ${available} tools` : 'Disabled';
    }
  }

  async function loadChatView() {
    await Promise.all([loadConversations(true), loadProvider(), loadAgentTools()]);
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
      try { await Promise.all([loadProvider(), loadAgentTools()]); } catch (err) { brainFlash(err.message, true); }
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
      if (!confirm('Delete this local conversation?')) return;
      try {
        await brainApi(`/api/v1/control/conversations/${encodeURIComponent(activeConversationId)}`, {method: 'DELETE'});
        activeConversationId = null;
        setChatTitle();
        renderMessages({messages: []});
        await loadConversations(false);
        brainFlash('Conversation deleted.');
      } catch (err) { brainFlash(err.message, true); }
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
    try {
      const data = await brainApi('/api/v1/control/chat', {
        method: 'POST',
        body: JSON.stringify({message, conversation_id: activeConversationId}),
      });
      activeConversationId = data.conversation_id;
      await Promise.all([loadConversation(activeConversationId), loadConversations(false)]);
      const context = byId('chatContext');
      if (context) {
        const toolText = data.tools?.call_count ? ` · ${data.tools.call_count} tool call${data.tools.call_count === 1 ? '' : 's'}` : '';
        context.textContent = `${data.context.memory_count} memories · ${data.context.knowledge_count} knowledge matches${toolText} · ${data.model}`;
      }
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
        body: JSON.stringify({
          base_url: byId('providerUrl').value,
          model: byId('providerModel').value,
          enabled: byId('providerEnabled').checked,
        }),
      });
      const status = byId('providerState');
      if (status) status.textContent = data.provider.enabled ? `Enabled · ${data.provider.model}` : 'Disabled';
      brainFlash('Local model provider saved.');
    } catch (err) { brainFlash(err.message, true); }
  });

  byId('agentToolsForm')?.addEventListener('submit', async event => {
    event.preventDefault();
    try {
      const data = await brainApi('/api/v1/control/agent-tools', {
        method: 'PUT',
        body: JSON.stringify({
          enabled: byId('agentToolsEnabled').checked,
          max_calls: Number(byId('agentToolsMaxCalls').value || 3),
        }),
      });
      const state = byId('agentToolsState');
      if (state) state.textContent = data.policy.enabled ? `Enabled · read-only · max ${data.policy.max_calls}` : 'Disabled';
      brainFlash(data.policy.enabled ? 'Read-only Agent Tools enabled.' : 'Agent Tools disabled.');
    } catch (err) { brainFlash(err.message, true); }
  });

  window.addEventListener('hashchange', () => {
    if (location.hash === '#chat') document.querySelector('[data-view="chat"]')?.click();
  });

  if (location.hash === '#chat') setTimeout(() => document.querySelector('[data-view="chat"]')?.click(), 0);
})();
