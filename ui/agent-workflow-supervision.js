(() => {
  'use strict';

  const VERSION = 'v0.57';
  const API = '/api/v1/control/agent-workflows/supervise';
  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const state = {checkpoint: null, result: null, busy: false, error: null, bootAttempts: 0};

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
    if (document.querySelector('link[data-agent-workflow-supervision-v057]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/agent-workflow-supervision.css';
    link.dataset.agentWorkflowSupervisionV057 = '1';
    document.head.appendChild(link);
  }

  function ensureAutomationExtension() {
    if (document.querySelector('script[data-agent-workflow-automation-v058]')) return;
    const script = document.createElement('script');
    script.src = '/assets/agent-workflow-automation.js';
    script.dataset.agentWorkflowAutomationV058 = '1';
    script.async = false;
    document.head.appendChild(script);
  }

  function ensureCue() {
    const recovery = byId('agentWorkflowRehydrationV056');
    const panel = document.querySelector('#view-chat .chat-panel');
    const messages = byId('chatMessages');
    if ((!recovery && !panel) || !messages) return false;
    if (byId('agentWorkflowSupervisionV057')) return true;
    const cue = document.createElement('section');
    cue.id = 'agentWorkflowSupervisionV057';
    cue.className = 'workflow-supervision hidden';
    cue.dataset.workflowSupervision = VERSION;
    cue.setAttribute('aria-live', 'polite');
    if (recovery) recovery.insertAdjacentElement('afterend', cue);
    else panel.insertBefore(cue, messages);
    return true;
  }

  function eligible(checkpoint) {
    const item = checkpoint?.checkpoint || {};
    if (checkpoint?.safe_to_continue !== true || item.plan_status !== 'approved') return false;
    const counts = item.counts || {};
    if (Number(counts.failed || 0) > 0) return false;
    return ['queued', 'partial', 'completed'].includes(String(item.workflow_status || ''));
  }

  function boundaryLabel(key) {
    return ({
      plan_approval_required: 'Plan approval required',
      retry_required: 'Explicit retry required',
      parent_synthesis_required: 'Ready for parent synthesis',
      specialists_running: 'Specialists already running',
      step_budget_exhausted: 'Step budget reached',
      checkpoint_changed: 'Checkpoint changed',
      access_or_state_conflict: 'Access/state changed',
      action_failed: 'Action needs review',
      workflow_complete: 'Workflow complete',
      workflow_cancelled: 'Workflow cancelled',
    })[String(key || '')] || 'Supervision stopped';
  }

  function actionLabel(action) {
    return ({run: 'Ran specialists', prepare: 'Prepared synthesis'})[String(action || '')] || String(action || 'Step');
  }

  function render() {
    if (!ensureCue()) return;
    const cue = byId('agentWorkflowSupervisionV057');
    const checkpoint = state.checkpoint;
    const conversation = activeConversationId();
    if (!cue || !checkpoint || String(checkpoint.conversation_id || '') !== String(conversation || '')) {
      if (cue) { cue.classList.add('hidden'); cue.innerHTML = ''; }
      return;
    }

    if (state.error) {
      cue.innerHTML = `<div class="workflow-supervision-copy"><p class="eyebrow">SUPERVISED CONTINUATION · ${VERSION}</p><strong>Continuation needs review</strong><p>${esc(state.error)}</p></div>`;
      cue.classList.remove('hidden');
      cue.classList.add('has-conflict');
      return;
    }

    if (state.result) {
      const result = state.result;
      const steps = Array.isArray(result.steps) ? result.steps : [];
      const actions = steps.length ? steps.map(step => actionLabel(step.action)).join(' → ') : 'No workflow action executed';
      cue.innerHTML = `
        <div class="workflow-supervision-copy">
          <p class="eyebrow">SUPERVISED CONTINUATION · ${VERSION}</p>
          <strong>${esc(boundaryLabel(result.stop_boundary))}</strong>
          <p>${esc(result.stop_label || 'Supervised continuation stopped at a review boundary.')}</p>
          <small>${esc(actions)} · ${Number(result.actions_executed || 0)}/${Number(result.max_steps || 2)} supervised steps</small>
        </div>
        <span class="workflow-supervision-state">${result.reused ? 'Existing session' : 'Session complete'}</span>`;
      cue.classList.toggle('has-conflict', result.status === 'conflict' || result.status === 'error');
      cue.classList.remove('hidden');
      return;
    }

    const item = checkpoint.checkpoint || {};
    const counts = item.counts || {};
    const hasFailed = Number(counts.failed || 0) > 0;
    const canContinue = eligible(checkpoint);
    const boundary = hasFailed
      ? 'A failed specialist must be reviewed and explicitly retried before supervised continuation.'
      : item.plan_status !== 'approved'
        ? 'The Team Plan still requires an explicit approval or rejection decision.'
        : checkpoint.safe_to_continue === false
          ? 'The recovery checkpoint has an access/state conflict and cannot continue.'
          : 'One explicit action can run up to two pre-approved safe steps: run queued specialists, then prepare synthesis if all succeed.';
    cue.innerHTML = `
      <div class="workflow-supervision-copy">
        <p class="eyebrow">SUPERVISED CONTINUATION · ${VERSION}</p>
        <strong>${canContinue ? 'Ready to continue safely' : 'Explicit review boundary'}</strong>
        <p>${esc(boundary)}</p>
        <small>No automatic approval · no automatic retry · no automatic parent message</small>
      </div>
      ${canContinue ? `<button class="button secondary workflow-supervision-action" type="button" data-workflow-supervise="1" ${state.busy ? 'disabled' : ''}>${state.busy ? 'Continuing…' : 'Continue safely'}</button>` : ''}`;
    cue.classList.toggle('has-conflict', !canContinue);
    cue.classList.remove('hidden');
  }

  async function continueSafely(button) {
    const checkpoint = state.checkpoint;
    if (state.busy || !eligible(checkpoint)) return;
    if (String(activeConversationId() || '') !== String(checkpoint.conversation_id || '')) return;
    state.busy = true;
    state.error = null;
    if (button) button.disabled = true;
    render();
    try {
      const result = await requestJson(API, {
        method: 'POST',
        body: JSON.stringify({
          conversation_id: checkpoint.conversation_id,
          plan_id: Number(checkpoint.plan_id),
          rehydration_id: Number(checkpoint.rehydration_id),
          state_fingerprint: String(checkpoint.state_fingerprint || ''),
          max_steps: 2,
        }),
      });
      if (String(activeConversationId() || '') !== String(checkpoint.conversation_id || '')) return;
      state.result = result;
      state.error = null;
      render();
      window.dispatchEvent(new CustomEvent('homeserver:workflow-supervised', {
        detail: {conversationId: checkpoint.conversation_id, planId: Number(checkpoint.plan_id), supervisionId: Number(result.supervision_id)},
      }));
      window.HomeServerAgentWorkflowContinuation?.refresh?.();
    } catch (error) {
      state.error = error?.message || 'Supervised continuation failed.';
      state.result = null;
      render();
    } finally {
      state.busy = false;
      if (button) button.disabled = false;
      render();
    }
  }

  document.addEventListener('click', event => {
    const button = event.target.closest('[data-workflow-supervise]');
    if (!button) return;
    // POST is reachable only from this explicit user action. Boot, refresh,
    // recovery events and conversation changes never start supervised work.
    continueSafely(button).catch(() => null);
  });

  window.addEventListener('homeserver:workflow-rehydrated', () => {
    state.checkpoint = window.HomeServerAgentWorkflowRehydration?.getPayload?.() || null;
    state.result = null;
    state.error = null;
    render();
  });
  window.addEventListener('homeserver:chat-agent-changed', () => render());

  function boot() {
    ensureStyles();
    ensureAutomationExtension();
    if (!window.HomeServerAgentWorkflowRehydration || !ensureCue()) {
      state.bootAttempts += 1;
      if (state.bootAttempts < 180) setTimeout(boot, 80);
      return;
    }
    state.checkpoint = window.HomeServerAgentWorkflowRehydration.getPayload?.() || null;
    render();
  }

  window.HomeServerAgentWorkflowSupervision = Object.freeze({version: VERSION, continueSafely});
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})();