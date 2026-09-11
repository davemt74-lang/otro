(() => {
  'use strict';

  const VERSION = 'v0.48';
  const HANDOFF_VERSION = 'v0.49';
  const API = '/api/v1/control/agent-workflows';
  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const state = {policy: null, workers: [], tasks: [], handoffs: [], open: false, busy: false, handoffBusy: null};

  async function requestJson(path, options = {}) {
    const response = await fetch(path, {
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

  function routing() {
    return window.HomeServerAgentRouting || null;
  }

  function parentAgent() {
    return routing()?.getSelectedAgent?.() || null;
  }

  function activeConversationId() {
    return routing()?.getActiveConversationId?.() || null;
  }

  function ensureStyles() {
    if (document.querySelector('link[data-agent-workflows-v048]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/agent-workflows.css';
    link.dataset.agentWorkflowsV048 = '1';
    document.head.appendChild(link);
  }

  function flash(message, error = false) {
    const node = byId('flash');
    if (!node) return;
    node.textContent = message;
    node.className = `flash show${error ? ' error' : ''}`;
  }

  function ensureUi() {
    const head = document.querySelector('#view-chat .chat-head');
    const panel = document.querySelector('#view-chat .chat-panel');
    const messages = byId('chatMessages');
    if (!head || !panel || !messages) return false;

    if (!byId('agentWorkflowToggle')) {
      const button = document.createElement('button');
      button.id = 'agentWorkflowToggle';
      button.type = 'button';
      button.className = 'text-button agent-workflow-toggle';
      button.textContent = 'Delegate';
      button.setAttribute('aria-expanded', 'false');
      const deleteButton = byId('deleteChat');
      if (deleteButton) head.insertBefore(button, deleteButton);
      else head.appendChild(button);
    }

    if (!byId('agentWorkflowPanel')) {
      const workspace = document.createElement('section');
      workspace.id = 'agentWorkflowPanel';
      workspace.className = 'agent-workflow-panel hidden';
      workspace.innerHTML = `
        <div class="agent-workflow-head">
          <div><p class="eyebrow">MULTI-AGENT WORKFLOW · ${VERSION}</p><h4>Delegate to a specialist</h4></div>
          <span id="agentWorkflowParent" class="agent-workflow-parent"></span>
        </div>
        <form id="agentWorkflowForm" class="agent-workflow-form">
          <label>Worker Agent<select id="agentWorkflowWorker" required></select></label>
          <label class="agent-workflow-task-label">Task brief<textarea id="agentWorkflowTask" rows="3" maxlength="16000" placeholder="Give the specialist one bounded task…" required></textarea></label>
          <button class="button primary" id="agentWorkflowRun" type="submit">Run specialist</button>
        </form>
        <div class="agent-workflow-policy">
          <label class="check-inline"><input id="agentWorkflowModelEnabled" type="checkbox"> Let Agents delegate tasks automatically</label>
          <label>Worker context<select id="agentWorkflowContext"><option value="8000">8k</option><option value="12000">12k</option><option value="16000">16k</option><option value="20000">20k</option><option value="24000">24k</option></select></label>
          <button class="button secondary" id="agentWorkflowSavePolicy" type="button">Save policy</button>
          <small>Delegation remains one-hop in v0.48. ${HANDOFF_VERSION} handoffs are explicit, bounded and consumed once.</small>
        </div>
        <div class="agent-workflow-history-head"><strong>Delegation activity</strong><button class="text-button" id="agentWorkflowRefresh" type="button">Refresh</button></div>
        <div id="agentWorkflowTasks" class="agent-workflow-tasks"><div class="muted">No delegation tasks yet.</div></div>`;
      panel.insertBefore(workspace, messages);
    }
    return true;
  }

  function statusLabel(status) {
    const value = String(status || 'queued');
    return value.charAt(0).toUpperCase() + value.slice(1);
  }

  function handoffForTask(taskId) {
    return state.handoffs.find(item => Number(item.task_id) === Number(taskId)) || null;
  }

  function handoffMarkup(item) {
    if (!item.result || item.status !== 'completed') return '';
    const handoff = handoffForTask(item.id);
    if (handoff?.status === 'pending') {
      return `<div class="agent-handoff-row"><span class="agent-handoff-state handoff-pending">Queued for parent · next turn</span><button class="text-button" type="button" data-revoke-handoff="${Number(handoff.id)}" ${state.handoffBusy ? 'disabled' : ''}>Undo</button></div>`;
    }
    if (handoff?.status === 'consumed') {
      return `<div class="agent-handoff-row"><span class="agent-handoff-state handoff-consumed">Used by parent Agent</span></div>`;
    }
    if (handoff?.status === 'revoked') {
      return `<div class="agent-handoff-row"><span class="agent-handoff-state">Handoff cancelled</span><button class="text-button" type="button" data-queue-handoff="${Number(item.id)}" ${state.handoffBusy ? 'disabled' : ''}>Use in parent chat</button></div>`;
    }
    if (!item.conversation_id) {
      return `<div class="agent-handoff-row"><span class="agent-handoff-state">Start or attach a parent conversation to hand off this result.</span></div>`;
    }
    return `<div class="agent-handoff-row"><span class="agent-handoff-state">${HANDOFF_VERSION} · one-shot result handoff</span><button class="button secondary agent-handoff-button" type="button" data-queue-handoff="${Number(item.id)}" ${state.handoffBusy ? 'disabled' : ''}>Use in parent chat</button></div>`;
  }

  function render() {
    if (!ensureUi()) return;
    const parent = parentAgent();
    const parentNode = byId('agentWorkflowParent');
    if (parentNode) parentNode.textContent = parent ? `Parent: ${parent.name}` : 'Choose an Agent';

    const worker = byId('agentWorkflowWorker');
    if (worker) {
      const previous = worker.value;
      worker.innerHTML = state.workers.length
        ? state.workers.map(item => `<option value="${Number(item.id)}">${esc(item.name)}${item.is_primary ? ' · Primary' : ''}</option>`).join('')
        : '<option value="">No authorized specialist Agents</option>';
      if (state.workers.some(item => String(item.id) === previous)) worker.value = previous;
      worker.disabled = !state.workers.length || state.busy;
    }

    if (state.policy) {
      const enabled = byId('agentWorkflowModelEnabled');
      const context = byId('agentWorkflowContext');
      if (enabled) enabled.checked = Boolean(state.policy.enabled);
      if (context) context.value = String(state.policy.max_context_chars || 12000);
    }

    const run = byId('agentWorkflowRun');
    if (run) {
      run.disabled = state.busy || !state.workers.length || !parent;
      run.textContent = state.busy ? 'Specialist working…' : 'Run specialist';
    }

    const list = byId('agentWorkflowTasks');
    if (!list) return;
    if (!state.tasks.length) {
      list.innerHTML = '<div class="muted">No delegation tasks yet.</div>';
      return;
    }
    list.innerHTML = state.tasks.map(item => `
      <article class="agent-workflow-task" data-agent-workflow-task="${Number(item.id)}">
        <div class="agent-workflow-task-top"><strong>${esc(item.worker_agent_name || 'Worker Agent')}</strong><span class="agent-workflow-status status-${esc(item.status)}">${esc(statusLabel(item.status))}</span></div>
        <p>${esc(item.task)}</p>
        ${item.result ? `<div class="agent-workflow-result"><span>Result</span>${esc(item.result)}</div>` : ''}
        ${item.error ? `<div class="agent-workflow-error">${esc(item.error)}</div>` : ''}
        ${handoffMarkup(item)}
        <small>Parent: ${esc(item.parent_agent_name || 'Agent')}${item.model ? ` · ${esc(item.model)}` : ''}</small>
      </article>`).join('');
  }

  async function loadPolicy() {
    state.policy = await requestJson(`${API}/policy`);
  }

  async function loadWorkers() {
    const parent = parentAgent();
    if (!parent?.id) {
      state.workers = [];
      return;
    }
    const payload = await requestJson(`${API}/workers?parent_agent_id=${encodeURIComponent(parent.id)}`);
    state.workers = Array.isArray(payload.items) ? payload.items : [];
  }

  async function loadTasks() {
    const conversationId = activeConversationId();
    const query = conversationId ? `?limit=20&conversation_id=${encodeURIComponent(conversationId)}` : '?limit=20';
    const payload = await requestJson(`${API}/delegations${query}`);
    state.tasks = Array.isArray(payload.items) ? payload.items : [];
  }

  async function loadHandoffs() {
    const conversationId = activeConversationId();
    const query = conversationId ? `?limit=50&conversation_id=${encodeURIComponent(conversationId)}` : '?limit=50';
    const payload = await requestJson(`${API}/handoffs${query}`);
    state.handoffs = Array.isArray(payload.items) ? payload.items : [];
  }

  async function refresh() {
    if (!ensureUi()) return;
    try {
      await Promise.all([loadPolicy(), loadWorkers(), loadTasks(), loadHandoffs()]);
      render();
    } catch (error) {
      flash(error.message, true);
    }
  }

  async function runDelegation(event) {
    event.preventDefault();
    if (state.busy) return;
    const parent = parentAgent();
    const workerId = Number(byId('agentWorkflowWorker')?.value || 0);
    const task = String(byId('agentWorkflowTask')?.value || '').trim();
    if (!parent?.id || !workerId || !task) return;
    state.busy = true;
    render();
    try {
      const queued = await requestJson(`${API}/delegations`, {
        method: 'POST',
        body: JSON.stringify({
          parent_agent_id: Number(parent.id),
          worker_agent_id: workerId,
          task,
          conversation_id: activeConversationId(),
          include_memory: true,
          include_knowledge: true,
          include_contacts: true,
          cloud_allowed: true,
          max_context_chars: Number(state.policy?.max_context_chars || 12000),
        }),
      });
      state.tasks = [queued, ...state.tasks.filter(item => Number(item.id) !== Number(queued.id))];
      render();
      const completed = await requestJson(`${API}/delegations/${encodeURIComponent(queued.id)}/run`, {method: 'POST'});
      state.tasks = [completed, ...state.tasks.filter(item => Number(item.id) !== Number(completed.id))];
      const input = byId('agentWorkflowTask');
      if (input) input.value = '';
      flash(`${completed.worker_agent_name} completed the delegated task.`);
    } catch (error) {
      flash(error.message, true);
      await loadTasks().catch(() => null);
    } finally {
      state.busy = false;
      render();
    }
  }

  async function queueHandoff(taskId) {
    if (state.handoffBusy) return;
    state.handoffBusy = Number(taskId);
    render();
    try {
      const handoff = await requestJson(`${API}/delegations/${encodeURIComponent(taskId)}/handoff`, {method: 'POST'});
      state.handoffs = [handoff, ...state.handoffs.filter(item => Number(item.id) !== Number(handoff.id))];
      flash(`${handoff.worker_agent_name}'s result will be provided to the parent Agent on the next message.`);
    } catch (error) {
      flash(error.message, true);
      await loadHandoffs().catch(() => null);
    } finally {
      state.handoffBusy = null;
      render();
    }
  }

  async function revokeHandoff(handoffId) {
    if (state.handoffBusy) return;
    state.handoffBusy = Number(handoffId);
    render();
    try {
      const handoff = await requestJson(`${API}/handoffs/${encodeURIComponent(handoffId)}/revoke`, {method: 'POST'});
      state.handoffs = [handoff, ...state.handoffs.filter(item => Number(item.id) !== Number(handoff.id))];
      flash('Specialist result handoff cancelled.');
    } catch (error) {
      flash(error.message, true);
      await loadHandoffs().catch(() => null);
    } finally {
      state.handoffBusy = null;
      render();
    }
  }

  async function savePolicy() {
    const enabled = Boolean(byId('agentWorkflowModelEnabled')?.checked);
    const maxContext = Number(byId('agentWorkflowContext')?.value || 12000);
    try {
      state.policy = await requestJson(`${API}/policy`, {
        method: 'PUT',
        body: JSON.stringify({enabled, max_context_chars: maxContext}),
      });
      render();
      flash(enabled ? 'Agent delegation enabled.' : 'Agent delegation disabled.');
    } catch (error) {
      flash(error.message, true);
      await loadPolicy().catch(() => null);
      render();
    }
  }

  document.addEventListener('click', event => {
    if (event.target.closest('#agentWorkflowToggle')) {
      state.open = !state.open;
      const panel = byId('agentWorkflowPanel');
      panel?.classList.toggle('hidden', !state.open);
      byId('agentWorkflowToggle')?.setAttribute('aria-expanded', String(state.open));
      if (state.open) refresh();
      return;
    }
    const queue = event.target.closest('[data-queue-handoff]');
    if (queue) {
      queueHandoff(Number(queue.dataset.queueHandoff));
      return;
    }
    const revoke = event.target.closest('[data-revoke-handoff]');
    if (revoke) {
      revokeHandoff(Number(revoke.dataset.revokeHandoff));
      return;
    }
    if (event.target.closest('#agentWorkflowRefresh')) refresh();
    if (event.target.closest('#agentWorkflowSavePolicy')) savePolicy();
    if (event.target.closest('[data-view="chat"], [data-go="chat"]') && state.open) setTimeout(refresh, 0);
  });

  document.addEventListener('submit', event => {
    if (event.target.id === 'agentWorkflowForm') runDelegation(event);
  });

  window.addEventListener('homeserver:chat-agent-changed', () => {
    if (state.open) refresh();
  });

  const chatObserver = new MutationObserver(() => {
    if (!state.open) return;
    clearTimeout(chatObserver._refreshTimer);
    chatObserver._refreshTimer = setTimeout(() => Promise.all([loadTasks(), loadHandoffs()]).then(render).catch(() => null), 120);
  });

  function boot() {
    ensureStyles();
    ensureUi();
    const messages = byId('chatMessages');
    if (messages) chatObserver.observe(messages, {childList: true, subtree: true});
  }

  window.HomeServerAgentWorkflows = Object.freeze({version: VERSION, handoffVersion: HANDOFF_VERSION, refresh});
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})();
