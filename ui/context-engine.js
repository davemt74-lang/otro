(() => {
  'use strict';

  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));

  async function contextApi(path, options = {}) {
    const headers = {...(options.headers || {})};
    if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, {...options, headers});
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }

  function ensureStyles() {
    if (document.querySelector('link[href="/assets/context-engine.css"]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/context-engine.css';
    document.head.appendChild(link);
  }

  function activeConversationId() {
    return document.querySelector('[data-brain-conversation].active')?.dataset.brainConversation || null;
  }

  function ensureControls() {
    if (byId('chatContextControls')) return;
    const intro = document.querySelector('#view-chat .section-intro');
    if (!intro) return;
    const panel = document.createElement('div');
    panel.id = 'chatContextControls';
    panel.className = 'context-engine-panel';
    panel.innerHTML = `
      <div class="context-engine-head">
        <div><p class="eyebrow">PRIVATE CONTEXT</p><strong>Agent Brain context</strong></div>
        <span id="contextEngineStatus" class="muted">Start a chat to set per-chat privacy.</span>
      </div>
      <div class="context-engine-controls">
        <label><input id="contextUseMemory" type="checkbox" checked> Memory</label>
        <label><input id="contextUseKnowledge" type="checkbox" checked> Knowledge</label>
        <label><input id="contextUseContacts" type="checkbox" checked> Contacts</label>
        <label class="context-cloud"><input id="contextCloudAllowed" type="checkbox" checked> Cloud providers allowed</label>
        <label class="context-budget">Context budget
          <select id="contextBudget">
            <option value="6000">Small · ~1.5K tokens</option>
            <option value="12000" selected>Standard · ~3K tokens</option>
            <option value="18000">Large · ~4.5K tokens</option>
            <option value="24000">Maximum · ~6K tokens</option>
          </select>
        </label>
      </div>
      <div class="context-engine-foot">
        <span>Uncheck <strong>Cloud providers allowed</strong> to require local Ollama for this conversation.</span>
        <div id="contextSources" class="context-sources"></div>
      </div>`;
    intro.insertAdjacentElement('afterend', panel);
    setControlsEnabled(false);
  }

  function setControlsEnabled(enabled) {
    for (const id of ['contextUseMemory','contextUseKnowledge','contextUseContacts','contextCloudAllowed','contextBudget']) {
      const node = byId(id);
      if (node) node.disabled = !enabled;
    }
  }

  function setStatus(text, error = false) {
    const node = byId('contextEngineStatus');
    if (!node) return;
    node.textContent = text;
    node.classList.toggle('context-error', Boolean(error));
  }

  function applySettings(settings = {}) {
    if (byId('contextUseMemory')) byId('contextUseMemory').checked = settings.include_memory !== false;
    if (byId('contextUseKnowledge')) byId('contextUseKnowledge').checked = settings.include_knowledge !== false;
    if (byId('contextUseContacts')) byId('contextUseContacts').checked = settings.include_contacts !== false;
    if (byId('contextCloudAllowed')) byId('contextCloudAllowed').checked = settings.cloud_allowed !== false;
    if (byId('contextBudget')) byId('contextBudget').value = String(settings.max_context_chars || 12000);
  }

  function renderSources(history = []) {
    const node = byId('contextSources');
    if (!node) return;
    const latest = history?.[0];
    const sources = latest?.sources || [];
    if (!sources.length) {
      node.innerHTML = '<span class="muted">No retrieved sources yet.</span>';
      return;
    }
    node.innerHTML = sources.slice(0, 12).map(source => {
      const label = source.kind === 'knowledge' ? 'Knowledge' : source.kind === 'memory' ? 'Memory' : 'Contact';
      return `<span class="context-source" title="${esc(source.updated_at || '')}"><b>${esc(label)}</b> ${esc(source.title || '')}</span>`;
    }).join('');
  }

  async function loadActive() {
    ensureControls();
    const id = activeConversationId();
    if (!id) {
      setControlsEnabled(false);
      applySettings({include_memory:true, include_knowledge:true, include_contacts:true, cloud_allowed:true, max_context_chars:12000});
      renderSources([]);
      setStatus('Start a chat to set per-chat privacy.');
      return;
    }
    try {
      const data = await contextApi(`/api/v1/control/conversations/${encodeURIComponent(id)}`);
      applySettings(data.context_settings || {});
      renderSources(data.context_history || []);
      setControlsEnabled(true);
      const settings = data.context_settings || {};
      setStatus(settings.cloud_allowed ? 'Cloud allowed · context policy saved' : 'Private · local model required');
    } catch (err) {
      setControlsEnabled(false);
      setStatus(err.message, true);
    }
  }

  async function saveActive() {
    const id = activeConversationId();
    if (!id) return;
    setStatus('Saving context policy…');
    try {
      const data = await contextApi(`/api/v1/control/conversations/${encodeURIComponent(id)}/context`, {
        method: 'PUT',
        body: JSON.stringify({
          include_memory: Boolean(byId('contextUseMemory')?.checked),
          include_knowledge: Boolean(byId('contextUseKnowledge')?.checked),
          include_contacts: Boolean(byId('contextUseContacts')?.checked),
          cloud_allowed: Boolean(byId('contextCloudAllowed')?.checked),
          max_context_chars: Number(byId('contextBudget')?.value || 12000),
        }),
      });
      const settings = data.context_settings || {};
      setStatus(settings.cloud_allowed ? 'Cloud allowed · context policy saved' : 'Private · local model required');
    } catch (err) {
      setStatus(err.message, true);
    }
  }

  let refreshTimer = null;
  function scheduleRefresh(delay = 80) {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => loadActive().catch(() => {}), delay);
  }

  document.addEventListener('change', event => {
    if (event.target.closest('#chatContextControls')) saveActive().catch(() => {});
  });

  document.addEventListener('click', event => {
    if (event.target.closest('[data-brain-conversation]')) scheduleRefresh(120);
    if (event.target.closest('#newChat, #sidebarNewChat')) scheduleRefresh(120);
  });

  const observer = new MutationObserver(() => scheduleRefresh(120));
  const watch = () => {
    ensureControls();
    const conversations = byId('conversationList');
    const messages = byId('chatMessages');
    if (conversations) observer.observe(conversations, {subtree:true, childList:true, attributes:true, attributeFilter:['class']});
    if (messages) observer.observe(messages, {childList:true, subtree:true});
    scheduleRefresh(0);
  };

  ensureStyles();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', watch, {once:true});
  else watch();
})();
