(() => {
  'use strict';

  const VERSION = 'v0.47';
  const OWNER_AGENTS_ENDPOINT = '/api/v1/control/agent-routing';
  const OWNER_CHAT_ENDPOINT = '/api/v1/control/chat';
  const LEGACY_TTS_ENDPOINT = '/api/v1/control/voice/synthesize';
  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  const state = {
    agents: [],
    selectedAgentId: null,
    primaryAgentId: null,
    activeConversationId: null,
    syncingConversation: false,
  };

  const nativeFetch = window.fetch.bind(window);

  function urlPath(input) {
    try {
      const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : input?.url;
      if (!raw) return '';
      return new URL(raw, window.location.origin).pathname;
    } catch (_) {
      return '';
    }
  }

  function selectedAgent() {
    return state.agents.find(item => Number(item.id) === Number(state.selectedAgentId)) || null;
  }

  function notifyAgentChanged(reason = 'selection') {
    window.dispatchEvent(new CustomEvent('homeserver:chat-agent-changed', {
      detail: {version: VERSION, reason, agent: selectedAgent()},
    }));
  }

  function routedFetch(input, init = {}) {
    const path = urlPath(input);
    let nextInput = input;
    let nextInit = init;

    if (path === OWNER_CHAT_ENDPOINT && String(init.method || 'GET').toUpperCase() === 'POST' && state.selectedAgentId) {
      try {
        const body = JSON.parse(String(init.body || '{}'));
        if (body && typeof body === 'object' && body.agent_id == null) {
          nextInit = {...init, body: JSON.stringify({...body, agent_id: Number(state.selectedAgentId)})};
        }
      } catch (_) {}
    }

    if (path === LEGACY_TTS_ENDPOINT && state.selectedAgentId) {
      const routed = `/api/v1/control/voice/agents/${Number(state.selectedAgentId)}/synthesize`;
      if (typeof input === 'string') nextInput = routed;
      else if (input instanceof URL) nextInput = new URL(routed, window.location.origin);
      // The HomeServer conversation TTS caller uses a URL string. Do not rebuild
      // arbitrary Request objects here because doing so can consume or alter a
      // streaming request body; unknown callers safely retain their original URL.
    }

    return nativeFetch(nextInput, nextInit);
  }

  if (!window.__homeServerAgentRoutingFetchInstalled) {
    window.__homeServerAgentRoutingFetchInstalled = true;
    window.fetch = routedFetch;
  }

  async function requestJson(path, options = {}) {
    const response = await nativeFetch(path, {
      cache: 'no-store',
      credentials: 'same-origin',
      ...options,
      headers: {
        ...(options.body ? {'Content-Type': 'application/json'} : {}),
        ...(options.headers || {}),
      },
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
    return payload;
  }

  function ensureStyles() {
    if (document.querySelector('link[data-agent-routing-v047]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/agent-routing.css';
    link.dataset.agentRoutingV047 = '1';
    document.head.appendChild(link);
  }

  function ensureSelector() {
    const head = document.querySelector('#view-chat .chat-head');
    if (!head) return false;
    let control = byId('chatAgentRoutingControl');
    if (!control) {
      control = document.createElement('label');
      control.id = 'chatAgentRoutingControl';
      control.className = 'chat-agent-routing';
      control.innerHTML = `<span>Agent</span><select id="chatAgentSelect" aria-label="Agent persona"></select><span id="chatAgentRoutingLock" class="routing-lock"></span>`;
      const actions = head.querySelector('.chat-head-actions');
      if (actions) head.insertBefore(control, actions);
      else head.appendChild(control);
      byId('chatAgentSelect')?.addEventListener('change', event => {
        if (state.activeConversationId) {
          event.target.value = String(state.selectedAgentId || state.primaryAgentId || '');
          return;
        }
        state.selectedAgentId = Number(event.target.value || state.primaryAgentId || 0) || null;
        notifyAgentChanged('selection');
      });
    }
    return true;
  }

  function renderSelector() {
    if (!ensureSelector()) return;
    const select = byId('chatAgentSelect');
    const lock = byId('chatAgentRoutingLock');
    if (!select) return;
    select.innerHTML = state.agents.map(agent => `<option value="${Number(agent.id)}">${esc(agent.name)}${agent.is_primary ? ' · Primary' : ''}</option>`).join('');
    const wanted = Number(state.selectedAgentId || state.primaryAgentId || state.agents[0]?.id || 0);
    if (wanted) select.value = String(wanted);
    select.disabled = Boolean(state.activeConversationId);
    if (lock) lock.textContent = state.activeConversationId ? 'Bound to conversation' : 'Choose for new chat';
  }

  async function loadAgents() {
    const payload = await requestJson(OWNER_AGENTS_ENDPOINT);
    state.agents = Array.isArray(payload.items) ? payload.items : [];
    state.primaryAgentId = Number(state.agents.find(item => item.is_primary)?.id || state.agents[0]?.id || 0) || null;
    if (!state.selectedAgentId || !state.agents.some(item => Number(item.id) === Number(state.selectedAgentId))) {
      state.selectedAgentId = state.primaryAgentId;
    }
    renderSelector();
    return payload;
  }

  function activeConversationFromDom() {
    const node = document.querySelector('[data-brain-conversation].active');
    return node?.dataset?.brainConversation || null;
  }

  async function syncConversationBinding(conversationId = activeConversationFromDom()) {
    if (state.syncingConversation) return;
    if (!conversationId) {
      state.activeConversationId = null;
      state.selectedAgentId = state.primaryAgentId;
      renderSelector();
      notifyAgentChanged('new_conversation');
      return;
    }
    state.syncingConversation = true;
    try {
      const payload = await requestJson(`/api/v1/control/conversations/${encodeURIComponent(conversationId)}/routing`);
      state.activeConversationId = conversationId;
      if (payload.agent?.id) state.selectedAgentId = Number(payload.agent.id);
      renderSelector();
      notifyAgentChanged('conversation_binding');
    } catch (_) {
      state.activeConversationId = conversationId;
      renderSelector();
    } finally {
      state.syncingConversation = false;
    }
  }

  function stopConversationVoiceIfActive() {
    const button = byId('voiceInputButton');
    if (button?.getAttribute('aria-pressed') === 'true') button.click();
  }

  function appCard(appId) {
    return document.querySelector(`[data-v029-app-card="${appId}"]`)
      || document.querySelector(`[data-app-status="${appId}"]`)?.closest('.item-card');
  }

  function accessMarkup(appId, access) {
    const items = access.items || [];
    return `<details class="app-control-details agent-routing-access" data-agent-access-panel="${appId}">
      <summary>Agent access <span>${Number(access.secondary_allowed_count || 0)} specialist${Number(access.secondary_allowed_count || 0) === 1 ? '' : 's'} enabled</span></summary>
      <p class="muted">The primary Agent is available through agent.chat for compatibility. Every secondary Agent must be explicitly authorized for this wrapper.</p>
      <div class="agent-access-grid">${items.map(agent => `<label class="agent-access-option"><input type="checkbox" data-agent-access-app="${appId}" data-agent-access-id="${Number(agent.id)}" ${agent.allowed ? 'checked' : ''} ${agent.is_primary ? 'disabled' : ''}><span><strong>${esc(agent.name)}</strong><small>${agent.is_primary ? 'Primary · implicit access' : 'Secondary persona'}</small></span></label>`).join('')}</div>
      <div class="agent-access-summary">Agent selection never expands this app's memory, knowledge, cloud, tool, plugin, or contact permissions.</div>
    </details>`;
  }

  async function enhanceAppCard(appId) {
    const card = appCard(appId);
    if (!card || card.querySelector(`[data-agent-access-panel="${appId}"]`)) return;
    try {
      const access = await requestJson(`/api/v1/control/connected-apps/${appId}/agents`);
      card.insertAdjacentHTML('beforeend', accessMarkup(appId, access));
    } catch (_) {}
  }

  async function enhanceConnectedApps() {
    let payload;
    try { payload = await requestJson('/api/v1/control/connected-apps'); } catch (_) { return; }
    await Promise.all((payload.apps || []).map(app => enhanceAppCard(app.id)));
  }

  document.addEventListener('change', async event => {
    const toggle = event.target.closest('[data-agent-access-app][data-agent-access-id]');
    if (!toggle) return;
    toggle.disabled = true;
    try {
      await requestJson(`/api/v1/control/connected-apps/${toggle.dataset.agentAccessApp}/agents/${toggle.dataset.agentAccessId}`, {
        method: 'PUT',
        body: JSON.stringify({allowed: Boolean(toggle.checked)}),
      });
      const panel = toggle.closest('[data-agent-access-panel]');
      panel?.remove();
      await enhanceAppCard(toggle.dataset.agentAccessApp);
    } catch (error) {
      toggle.checked = !toggle.checked;
      const flash = byId('flash');
      if (flash) {
        flash.textContent = error.message;
        flash.className = 'flash show error';
      }
    } finally {
      if (document.body.contains(toggle)) toggle.disabled = false;
    }
  });

  document.addEventListener('click', event => {
    if (event.target.closest('#newChat')) {
      stopConversationVoiceIfActive();
      state.activeConversationId = null;
      state.selectedAgentId = state.primaryAgentId;
      setTimeout(() => {
        renderSelector();
        notifyAgentChanged('new_conversation');
      }, 0);
      return;
    }
    const conversation = event.target.closest('[data-brain-conversation]');
    if (conversation) setTimeout(() => syncConversationBinding(conversation.dataset.brainConversation), 0);
    if (event.target.closest('[data-view="chat"], [data-go="chat"]')) {
      setTimeout(() => {
        ensureSelector();
        loadAgents().then(() => syncConversationBinding()).catch(() => null);
      }, 0);
    }
    if (event.target.closest('[data-view="apps"], [data-go="apps"], #refreshConnectedAppsV029')) {
      setTimeout(() => enhanceConnectedApps(), 120);
    }
  });

  const conversationObserver = new MutationObserver(() => {
    const active = activeConversationFromDom();
    if (active !== state.activeConversationId) syncConversationBinding(active).catch(() => null);
  });

  const appsObserver = new MutationObserver(() => {
    if (document.querySelector('#view-apps.active')) enhanceConnectedApps().catch(() => null);
  });

  function boot() {
    ensureStyles();
    ensureSelector();
    loadAgents().then(() => syncConversationBinding()).catch(() => null);
    const list = byId('conversationList');
    if (list) conversationObserver.observe(list, {subtree: true, childList: true, attributes: true, attributeFilter: ['class']});
    const appsList = byId('appsList');
    if (appsList) appsObserver.observe(appsList, {subtree: true, childList: true});
    if (location.hash === '#apps') setTimeout(() => enhanceConnectedApps(), 150);
  }

  window.HomeServerAgentRouting = Object.freeze({
    version: VERSION,
    getSelectedAgentId: () => state.selectedAgentId,
    getSelectedAgent: () => selectedAgent() ? {...selectedAgent()} : null,
    refresh: loadAgents,
    syncConversationBinding,
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})();
