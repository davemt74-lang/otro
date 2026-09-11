(() => {
  'use strict';

  const VERSION = 'v0.48';
  const HANDOFF_VERSION = 'v0.49';
  const TIMELINE_VERSION = 'v0.50';
  const API = '/api/v1/control/agent-workflows';
  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const state = {
    policy: null,
    workers: [],
    tasks: [],
    handoffs: [],
    timeline: [],
    chatMessages: [],
    open: false,
    busy: false,
    handoffBusy: null,
    synthesisBusy: false,
    synthesisSelection: new Set(),
  };

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
          <small>One-hop only in v0.48. ${HANDOFF_VERSION} handoffs are explicit, bounded and consumed once. ${TIMELINE_VERSION} can prepare an exact 2–4-result synthesis set.</small>
        </div>
        <div class="agent-workflow-history-head">
          <div><strong>Delegation activity</strong><span id="agentWorkflowSynthesisCount" class="agent-synthesis-count"></span></div>
          <div class="agent-workflow-history-actions"><button class="button secondary" id="agentWorkflowSynthesize" type="button">Synthesize selected</button><button class="text-button" id="agentWorkflowRefresh" type="button">Refresh</button></div>
        </div>
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

  function synthesisEligible(item) {
    const handoff = handoffForTask(item.id);
    return Boolean(item.result && item.status === 'completed' && item.conversation_id && handoff?.status !== 'consumed');
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

  function synthesisMarkup(item) {
    if (!synthesisEligible(item)) return '';
    const checked = state.synthesisSelection.has(Number(item.id)) ? 'checked' : '';
    return `<label class="agent-synthesis-select"><input type="checkbox" data-synthesis-task="${Number(item.id)}" ${checked} ${state.synthesisBusy ? 'disabled' : ''}><span>Include in parent synthesis</span></label>`;
  }

  function render() {
    if (!ensureUi()) return;
    const parent = parentAgent();
    const parentNode = byId('agentWorkflowParent');
    if (parentNode) parentNode.textContent = parent ? `Parent: ${parent.name}` : 'Choose an Agent';

    const eligibleIds = new Set(state.tasks.filter(synthesisEligible).map(item => Number(item.id)));
    for (const taskId of [...state.synthesisSelection]) {
      if (!eligibleIds.has(Number(taskId))) state.synthesisSelection.delete(Number(taskId));
    }

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

    const selectedCount = state.synthesisSelection.size;
    const synth = byId('agentWorkflowSynthesize');
    const synthCount = byId('agentWorkflowSynthesisCount');
    if (synth) {
      synth.disabled = state.synthesisBusy || selectedCount < 2 || selectedCount > 4 || !activeConversationId();
      synth.textContent = state.synthesisBusy ? 'Preparing synthesis…' : 'Synthesize selected';
    }
    if (synthCount) synthCount.textContent = selectedCount ? ` · ${selectedCount} selected` : '';

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
        ${synthesisMarkup(item)}
        ${handoffMarkup(item)}
        <small>Parent: ${esc(item.parent_agent_name || 'Agent')}${item.model ? ` · ${esc(item.model)}` : ''}</small>
      </article>`).join('');
  }

  function timelineLabel(event) {
    const labels = {
      delegation_queued: 'Delegated',
      delegation_started: 'Specialist working',
      delegation_completed: 'Specialist completed',
      delegation_failed: 'Specialist failed',
      delegation_cancelled: 'Delegation cancelled',
      handoff_queued: 'Result queued for parent',
      handoff_revoked: 'Result handoff cancelled',
      handoff_consumed: 'Result used by parent',
      synthesis_prepared: 'Synthesis prepared',
      parent_synthesis: 'Parent synthesis completed',
    };
    return labels[event.event_type] || 'Workflow event';
  }

  function timelineEventNode(event) {
    const article = document.createElement('article');
    article.className = `agent-workflow-inline-event event-${String(event.event_type || '').replace(/[^a-z0-9_-]/gi, '')}`;
    article.dataset.workflowTimeline = TIMELINE_VERSION;
    const names = event.event_type === 'parent_synthesis'
      ? (event.metadata?.worker_names || []).join(', ')
      : (event.worker_agent_name || '');
    const detail = event.event_type === 'synthesis_prepared'
      ? `${Number(event.metadata?.count || 0)} specialist results prepared for the parent Agent.`
      : event.event_type === 'parent_synthesis'
        ? `${Number(event.metadata?.count || 0)} specialist results were consumed in parent run ${Number(event.run_id || 0)}.`
        : event.task || '';
    const result = event.result ? `<div class="agent-workflow-inline-result">${esc(event.result)}</div>` : '';
    const redacted = event.metadata?.result_redacted ? '<small>Result hidden because current access no longer authorizes it.</small>' : '';
    article.innerHTML = `<div class="agent-workflow-inline-head"><strong>${esc(timelineLabel(event))}</strong><span>${esc(names)}</span></div>${detail ? `<p>${esc(detail)}</p>` : ''}${result}${redacted}`;
    return article;
  }

  function renderTimeline() {
    const node = byId('chatMessages');
    if (!node) return;
    node.querySelectorAll('.agent-workflow-inline-event').forEach(item => item.remove());
    if (!state.timeline.length || !activeConversationId()) return;
    const empty = node.querySelector('.chat-empty');
    if (empty) empty.remove();

    const messageNodes = [...node.querySelectorAll(':scope > .chat-message')];
    const messageTimes = state.chatMessages.map(item => Date.parse(item.created_at || '') || 0);
    const canInterleave = messageNodes.length === state.chatMessages.length;
    for (const event of state.timeline) {
      const card = timelineEventNode(event);
      if (!canInterleave) {
        node.appendChild(card);
        continue;
      }
      const eventTime = Date.parse(event.sort_at || '') || 0;
      const beforeIndex = messageTimes.findIndex(value => value > eventTime);
      if (beforeIndex >= 0 && messageNodes[beforeIndex]) node.insertBefore(card, messageNodes[beforeIndex]);
      else node.appendChild(card);
    }
    node.scrollTop = node.scrollHeight;
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

  async function loadTimeline() {
    const conversationId = activeConversationId();
    if (!conversationId) {
      state.timeline = [];
      state.chatMessages = [];
      return;
    }
    const [timeline, conversation] = await Promise.all([
      requestJson(`${API}/timeline?conversation_id=${encodeURIComponent(conversationId)}&limit=200`),
      requestJson(`/api/v1/control/conversations/${encodeURIComponent(conversationId)}`),
    ]);
    if (activeConversationId() !== conversationId) return;
    state.timeline = Array.isArray(timeline.items) ? timeline.items : [];
    state.chatMessages = Array.isArray(conversation.messages) ? conversation.messages : [];
  }

  async function refresh() {
    if (!ensureUi()) return;
    try {
      await Promise.all([loadPolicy(), loadWorkers(), loadTasks(), loadHandoffs(), loadTimeline()]);
      render();
      renderTimeline();
    } catch (error) {
      flash(error.message, true);
    }
  }

  async function refreshWorkflowState() {
    await Promise.all([loadTasks(), loadHandoffs(), loadTimeline()]);
    render();
    renderTimeline();
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
      await refreshWorkflowState();
      flash(`${completed.worker_agent_name} completed the delegated task.`);
    } catch (error) {
      flash(error.message, true);
      await refreshWorkflowState().catch(() => null);
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
      await loadTimeline();
      renderTimeline();
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
      await loadTimeline();
      renderTimeline();
      flash('Specialist result handoff cancelled.');
    } catch (error) {
      flash(error.message, true);
      await loadHandoffs().catch(() => null);
    } finally {
      state.handoffBusy = null;
      render();
    }
  }

  async function prepareSynthesis() {
    if (state.synthesisBusy) return;
    const conversationId = activeConversationId();
    const taskIds = [...state.synthesisSelection].map(Number).filter(value => value > 0);
    if (!conversationId || taskIds.length < 2 || taskIds.length > 4) return;
    state.synthesisBusy = true;
    render();
    try {
      const prepared = await requestJson(`${API}/synthesis`, {
        method: 'POST',
        body: JSON.stringify({conversation_id: conversationId, task_ids: taskIds}),
      });
      state.synthesisSelection.clear();
      await Promise.all([loadHandoffs(), loadTimeline()]);
      render();
      renderTimeline();
      const input = byId('chatInput');
      if (input) {
        input.focus();
        input.placeholder = 'Ask the parent Agent to synthesize the prepared specialist results…';
      }
      flash(`${Number(prepared.count || taskIds.length)} specialist results are ready for the parent Agent's next message.`);
    } catch (error) {
      flash(error.message, true);
    } finally {
      state.synthesisBusy = false;
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
    if (event.target.closest('#agentWorkflowSynthesize')) {
      prepareSynthesis();
      return;
    }
    if (event.target.closest('#agentWorkflowRefresh')) refresh();
    if (event.target.closest('#agentWorkflowSavePolicy')) savePolicy();
    if (event.target.closest('[data-view="chat"], [data-go="chat"]')) setTimeout(() => loadTimeline().then(renderTimeline).catch(() => null), 0);
  });

  document.addEventListener('change', event => {
    const checkbox = event.target.closest('[data-synthesis-task]');
    if (!checkbox) return;
    const taskId = Number(checkbox.dataset.synthesisTask || 0);
    if (!taskId) return;
    if (checkbox.checked) {
      if (state.synthesisSelection.size >= 4) {
        checkbox.checked = false;
        flash('A parent synthesis can include at most four specialist results.', true);
        return;
      }
      state.synthesisSelection.add(taskId);
    } else {
      state.synthesisSelection.delete(taskId);
    }
    render();
  });

  document.addEventListener('submit', event => {
    if (event.target.id === 'agentWorkflowForm') runDelegation(event);
  });

  window.addEventListener('homeserver:chat-agent-changed', () => {
    state.synthesisSelection.clear();
    loadTimeline().then(renderTimeline).catch(() => null);
    if (state.open) refresh();
  });

  const chatObserver = new MutationObserver(mutations => {
    const changedOnlyTimeline = mutations.every(mutation => {
      const nodes = [...mutation.addedNodes, ...mutation.removedNodes].filter(node => node.nodeType === 1);
      return nodes.length && nodes.every(node => node.classList?.contains('agent-workflow-inline-event'));
    });
    if (changedOnlyTimeline) return;
    clearTimeout(chatObserver._refreshTimer);
    chatObserver._refreshTimer = setTimeout(async () => {
      await loadTimeline().catch(() => null);
      renderTimeline();
      if (state.open) {
        await Promise.all([loadTasks(), loadHandoffs()]).catch(() => null);
        render();
      }
    }, 120);
  });

  function boot() {
    ensureStyles();
    ensureUi();
    const messages = byId('chatMessages');
    if (messages) chatObserver.observe(messages, {childList: true, subtree: true});
    if (activeConversationId()) loadTimeline().then(renderTimeline).catch(() => null);
  }

  window.HomeServerAgentWorkflows = Object.freeze({
    version: VERSION,
    handoffVersion: HANDOFF_VERSION,
    timelineVersion: TIMELINE_VERSION,
    refresh,
    renderTimeline,
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})();
