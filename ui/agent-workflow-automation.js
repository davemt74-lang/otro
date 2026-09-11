(() => {
  'use strict';

  const VERSION = 'v0.58';
  const API = '/api/v1/control/agent-workflows/automations';
  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const state = {checkpoint: null, items: [], busy: false, error: null, formOpen: false, bootAttempts: 0};

  function routing() { return window.HomeServerAgentRouting || null; }
  function activeConversationId() { return routing()?.getActiveConversationId?.() || null; }

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

  function ensureStyles() {
    if (document.querySelector('link[data-agent-workflow-automation-v058]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/agent-workflow-automation.css';
    link.dataset.agentWorkflowAutomationV058 = '1';
    document.head.appendChild(link);
  }

  function ensureCue() {
    const supervision = byId('agentWorkflowSupervisionV057');
    const recovery = byId('agentWorkflowRehydrationV056');
    const panel = document.querySelector('#view-chat .chat-panel');
    const messages = byId('chatMessages');
    if ((!supervision && !recovery && !panel) || !messages) return false;
    if (byId('agentWorkflowAutomationV058')) return true;
    const cue = document.createElement('section');
    cue.id = 'agentWorkflowAutomationV058';
    cue.className = 'workflow-automation hidden';
    cue.dataset.workflowAutomation = VERSION;
    cue.setAttribute('aria-live', 'polite');
    if (supervision) supervision.insertAdjacentElement('afterend', cue);
    else if (recovery) recovery.insertAdjacentElement('afterend', cue);
    else panel.insertBefore(cue, messages);
    return true;
  }

  function eligible(checkpoint) {
    const item = checkpoint?.checkpoint || {};
    const counts = item.counts || {};
    return checkpoint?.safe_to_continue === true
      && item.plan_status === 'approved'
      && Number(counts.failed || 0) === 0
      && ['queued', 'partial', 'completed'].includes(String(item.workflow_status || ''));
  }

  function fmt(value) {
    if (!value) return '—';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
  }

  function triggerLabel(item) {
    const trigger = item.trigger || {};
    if (item.trigger_type === 'once') return `Once · ${fmt(item.next_run_at || trigger.run_at)}`;
    if (item.trigger_type === 'interval') {
      const seconds = Number(trigger.every_seconds || 0);
      const amount = seconds % 3600 === 0 ? `${seconds / 3600} hr` : `${Math.max(1, Math.round(seconds / 60))} min`;
      return `Every ${amount}${item.next_run_at ? ` · next ${fmt(item.next_run_at)}` : ''}`;
    }
    return `On ${String(trigger.action || 'activity')}${trigger.resource_key ? ` · ${trigger.resource_key}` : ''}`;
  }

  function automationRows() {
    if (!state.items.length) return '<div class="workflow-automation-empty">No scheduled or event-triggered continuations for this workflow.</div>';
    return `<div class="workflow-automation-list">${state.items.map(item => `
      <div class="workflow-automation-row" data-automation-id="${Number(item.automation_id)}">
        <div><strong>${esc(triggerLabel(item))}</strong><small>${item.enabled ? 'Active' : 'Paused'} · ${esc(item.last_status || 'idle')}${item.last_fired_at ? ` · last fired ${esc(fmt(item.last_fired_at))}` : ''}</small></div>
        <div class="workflow-automation-row-actions">
          <button type="button" class="text-button" data-automation-toggle="${Number(item.automation_id)}" data-enable="${item.enabled ? '0' : '1'}" ${state.busy ? 'disabled' : ''}>${item.enabled ? 'Pause' : 'Enable'}</button>
          <button type="button" class="text-button danger" data-automation-delete="${Number(item.automation_id)}" ${state.busy ? 'disabled' : ''}>Delete</button>
        </div>
      </div>`).join('')}</div>`;
  }

  function formMarkup() {
    if (!state.formOpen) return '';
    return `
      <form id="workflowAutomationForm" class="workflow-automation-form">
        <div class="workflow-automation-grid">
          <label>Trigger
            <select id="workflowAutomationType">
              <option value="once">Run once later</option>
              <option value="interval">Repeat on an interval</option>
              <option value="activity">Run on an exact activity event</option>
            </select>
          </label>
          <label>Safe step budget
            <select id="workflowAutomationSteps"><option value="2">Up to 2 steps</option><option value="1">1 step</option></select>
          </label>
        </div>
        <div data-automation-fields="once" class="workflow-automation-fields">
          <label>Run at <input id="workflowAutomationRunAt" type="datetime-local" required></label>
        </div>
        <div data-automation-fields="interval" class="workflow-automation-fields hidden">
          <div class="workflow-automation-grid">
            <label>Every <input id="workflowAutomationMinutes" type="number" min="1" max="43200" value="60"></label>
            <label>Start at <span>optional</span><input id="workflowAutomationIntervalStart" type="datetime-local"></label>
          </div>
        </div>
        <div data-automation-fields="activity" class="workflow-automation-fields hidden">
          <label>Exact activity action <input id="workflowAutomationAction" maxlength="160" placeholder="task.reminded"></label>
          <div class="workflow-automation-grid">
            <label>Resource type <span>optional</span><input id="workflowAutomationResourceType" maxlength="120" placeholder="task"></label>
            <label>Resource key <span>optional</span><input id="workflowAutomationResourceKey" maxlength="240" placeholder="42"></label>
          </div>
          <small>Event triggers are limited to task, knowledge, contact, event, app, or cognition activity. Workflow-generated events cannot trigger another workflow automation.</small>
        </div>
        <div class="workflow-automation-form-actions">
          <button type="button" class="button secondary" data-automation-cancel="1">Cancel</button>
          <button type="submit" class="button primary" ${state.busy ? 'disabled' : ''}>${state.busy ? 'Saving…' : 'Create automation'}</button>
        </div>
      </form>`;
  }

  function render() {
    if (!ensureCue()) return;
    const cue = byId('agentWorkflowAutomationV058');
    const checkpoint = state.checkpoint;
    const conversation = activeConversationId();
    if (!cue || !checkpoint || String(checkpoint.conversation_id || '') !== String(conversation || '')) {
      if (cue) { cue.classList.add('hidden'); cue.innerHTML = ''; }
      return;
    }
    const canCreate = eligible(checkpoint);
    cue.innerHTML = `
      <div class="workflow-automation-head">
        <div class="workflow-automation-copy">
          <p class="eyebrow">SCHEDULED / TRIGGERED WORKFLOWS · ${VERSION}</p>
          <strong>${canCreate ? 'Automate approved continuation' : 'Automation creation is blocked at this workflow state'}</strong>
          <p>${canCreate ? 'Schedule this approved workflow or continue it after an exact HomeServer activity event. Every trigger re-checks permissions and canonical workflow state before entering the v0.57 safety boundary.' : 'Resolve the current approval, retry, access, or workflow-state boundary before creating a new automation.'}</p>
          <small>Explicit creation only · max 2 safe steps · no automatic approval · no automatic retry · no automatic parent message</small>
        </div>
        ${canCreate && !state.formOpen ? `<button type="button" class="button secondary" data-automation-open="1" ${state.busy ? 'disabled' : ''}>Schedule / Trigger</button>` : ''}
      </div>
      ${state.error ? `<div class="workflow-automation-error">${esc(state.error)}</div>` : ''}
      ${automationRows()}
      ${formMarkup()}`;
    cue.classList.toggle('has-conflict', !canCreate);
    cue.classList.remove('hidden');
    syncFieldVisibility();
  }

  function syncFieldVisibility() {
    const type = byId('workflowAutomationType')?.value || 'once';
    document.querySelectorAll('[data-automation-fields]').forEach(node => {
      node.classList.toggle('hidden', node.dataset.automationFields !== type);
    });
  }

  async function loadItems() {
    const checkpoint = state.checkpoint;
    if (!checkpoint?.conversation_id) return;
    const conversationId = String(checkpoint.conversation_id);
    try {
      const payload = await requestJson(`${API}?conversation_id=${encodeURIComponent(conversationId)}`);
      if (String(activeConversationId() || '') !== conversationId) return;
      state.items = Array.isArray(payload.items) ? payload.items : [];
      state.error = null;
    } catch (error) {
      state.error = error?.message || 'Workflow automations could not be loaded.';
    }
    render();
  }

  function localIso(value) {
    if (!value) return null;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) throw new Error('Choose a valid date and time.');
    return date.toISOString();
  }

  async function createAutomation() {
    if (state.busy || !eligible(state.checkpoint)) return;
    const type = byId('workflowAutomationType')?.value || 'once';
    const body = {
      conversation_id: state.checkpoint.conversation_id,
      plan_id: Number(state.checkpoint.plan_id),
      trigger_type: type,
      max_steps: Number(byId('workflowAutomationSteps')?.value || 2),
    };
    if (type === 'once') {
      body.run_at = localIso(byId('workflowAutomationRunAt')?.value);
    } else if (type === 'interval') {
      body.every_seconds = Number(byId('workflowAutomationMinutes')?.value || 0) * 60;
      const start = byId('workflowAutomationIntervalStart')?.value;
      if (start) body.run_at = localIso(start);
    } else {
      body.activity_action = String(byId('workflowAutomationAction')?.value || '').trim();
      const resourceType = String(byId('workflowAutomationResourceType')?.value || '').trim();
      const resourceKey = String(byId('workflowAutomationResourceKey')?.value || '').trim();
      if (resourceType) body.resource_type = resourceType;
      if (resourceKey) body.resource_key = resourceKey;
    }
    state.busy = true;
    state.error = null;
    render();
    try {
      await requestJson(API, {method: 'POST', body: JSON.stringify(body)});
      state.formOpen = false;
      await loadItems();
    } catch (error) {
      state.error = error?.message || 'Workflow automation could not be created.';
    } finally {
      state.busy = false;
      render();
    }
  }

  async function toggleAutomation(id, enabled) {
    if (state.busy) return;
    state.busy = true;
    state.error = null;
    render();
    try {
      await requestJson(`${API}/${Number(id)}`, {method: 'PATCH', body: JSON.stringify({enabled})});
      await loadItems();
    } catch (error) {
      state.error = error?.message || 'Workflow automation could not be updated.';
    } finally {
      state.busy = false;
      render();
    }
  }

  async function deleteAutomation(id) {
    if (state.busy) return;
    state.busy = true;
    state.error = null;
    render();
    try {
      await requestJson(`${API}/${Number(id)}`, {method: 'DELETE'});
      await loadItems();
    } catch (error) {
      state.error = error?.message || 'Workflow automation could not be deleted.';
    } finally {
      state.busy = false;
      render();
    }
  }

  document.addEventListener('change', event => {
    if (event.target.closest('#workflowAutomationType')) syncFieldVisibility();
  });

  document.addEventListener('submit', event => {
    if (!event.target.closest('#workflowAutomationForm')) return;
    event.preventDefault();
    // Creation only occurs from this explicit owner submit. Boot, recovery,
    // refresh and scheduler-status events perform GET/render operations only.
    createAutomation().catch(() => null);
  });

  document.addEventListener('click', event => {
    if (event.target.closest('[data-automation-open]')) {
      state.formOpen = true;
      state.error = null;
      render();
      return;
    }
    if (event.target.closest('[data-automation-cancel]')) {
      state.formOpen = false;
      state.error = null;
      render();
      return;
    }
    const toggle = event.target.closest('[data-automation-toggle]');
    if (toggle) {
      toggleAutomation(Number(toggle.dataset.automationToggle), toggle.dataset.enable === '1').catch(() => null);
      return;
    }
    const remove = event.target.closest('[data-automation-delete]');
    if (remove) deleteAutomation(Number(remove.dataset.automationDelete)).catch(() => null);
  });

  window.addEventListener('homeserver:workflow-rehydrated', () => {
    state.checkpoint = window.HomeServerAgentWorkflowRehydration?.getPayload?.() || null;
    state.formOpen = false;
    state.items = [];
    state.error = null;
    render();
    loadItems().catch(() => null);
  });
  window.addEventListener('homeserver:workflow-supervised', () => loadItems().catch(() => null));
  window.addEventListener('homeserver:chat-agent-changed', () => render());

  function boot() {
    ensureStyles();
    if (!window.HomeServerAgentWorkflowRehydration || !ensureCue()) {
      state.bootAttempts += 1;
      if (state.bootAttempts < 180) setTimeout(boot, 80);
      return;
    }
    state.checkpoint = window.HomeServerAgentWorkflowRehydration.getPayload?.() || null;
    render();
    loadItems().catch(() => null);
  }

  window.HomeServerAgentWorkflowAutomation = Object.freeze({version: VERSION, refresh: loadItems});
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})();