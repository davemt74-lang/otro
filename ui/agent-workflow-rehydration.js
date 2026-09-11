(() => {
  'use strict';

  const VERSION = 'v0.56';
  const RESUME_API = '/api/v1/control/agent-workflows/resume';
  const REHYDRATE_API = '/api/v1/control/agent-workflows/rehydrate';
  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const state = {busy: false, payload: null, conversationId: null, error: null, bootAttempts: 0};

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
    if (document.querySelector('link[data-agent-workflow-rehydration-v056]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/agent-workflow-rehydration.css';
    link.dataset.agentWorkflowRehydrationV056 = '1';
    document.head.appendChild(link);
  }

  function ensureCue() {
    const panel = document.querySelector('#view-chat .chat-panel');
    const messages = byId('chatMessages');
    if (!panel || !messages) return false;
    if (byId('agentWorkflowRehydrationV056')) return true;
    const cue = document.createElement('section');
    cue.id = 'agentWorkflowRehydrationV056';
    cue.className = 'workflow-rehydration hidden';
    cue.dataset.workflowRehydration = VERSION;
    cue.setAttribute('aria-live', 'polite');
    const continuation = byId('agentWorkflowContinuationV054');
    if (continuation) continuation.insertAdjacentElement('afterend', cue);
    else panel.insertBefore(cue, messages);
    return true;
  }

  function statusLabel(value) {
    const status = String(value || 'workflow');
    return status.charAt(0).toUpperCase() + status.slice(1);
  }

  function render() {
    if (!ensureCue()) return;
    const cue = byId('agentWorkflowRehydrationV056');
    if (!cue) return;
    if (state.error && state.conversationId && activeConversationId() === state.conversationId) {
      cue.innerHTML = `<div class="workflow-rehydration-copy"><p class="eyebrow">WORKFLOW RECOVERY · ${VERSION}</p><strong>Recovery needs review</strong><p>${esc(state.error)}</p></div>`;
      cue.classList.remove('hidden');
      cue.classList.add('has-conflict');
      return;
    }
    const payload = state.payload;
    if (!payload || activeConversationId() !== payload.conversation_id) {
      cue.classList.add('hidden');
      cue.classList.remove('has-conflict');
      cue.innerHTML = '';
      return;
    }
    const checkpoint = payload.checkpoint || {};
    const counts = checkpoint.counts || {};
    const drift = payload.drift || {};
    const conflict = payload.safe_to_continue === false;
    const progress = checkpoint.team_run_id
      ? `${Number(counts.completed || 0)}/${Number(counts.members || 0)} specialists completed${Number(counts.failed || 0) ? ` · ${Number(counts.failed)} failed` : ''}`
      : 'Team Plan restored before execution';
    const driftText = conflict
      ? `Conflict detected: ${(drift.blocking_reasons || []).map(statusLabel).join(', ') || 'workflow access changed'}. Review before continuing.`
      : drift.state_changed_since_last_rehydration
        ? 'Canonical workflow state changed since the previous recovery checkpoint; this checkpoint reflects the current state.'
        : payload.reused
          ? 'The existing durable checkpoint was restored; no duplicate recovery state was created.'
          : 'A durable checkpoint was created from the current canonical workflow state.';
    cue.innerHTML = `
      <div class="workflow-rehydration-copy">
        <p class="eyebrow">RECOVERED WORKFLOW · ${VERSION}</p>
        <div class="workflow-rehydration-title"><strong>${esc(statusLabel(checkpoint.workflow_status || payload.status))}</strong><span>Plan #${Number(payload.plan_id)}${payload.team_run_id ? ` · Team Run #${Number(payload.team_run_id)}` : ''}</span></div>
        <p>${esc(checkpoint.next_action?.label || 'Workflow state is restored and ready for explicit review.')}</p>
        <small>${esc(progress)} · ${esc(driftText)}</small>
      </div>
      <span class="workflow-rehydration-state ${conflict ? 'conflict' : 'ready'}">${conflict ? 'Review conflict' : 'Checkpoint ready'}</span>`;
    cue.classList.toggle('has-conflict', conflict);
    cue.classList.remove('hidden');
  }

  async function waitForConversation(conversationId) {
    for (let attempt = 0; attempt < 20; attempt += 1) {
      if (String(activeConversationId() || '') === String(conversationId || '')) return true;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    return false;
  }

  async function resumeItem(conversationId) {
    const index = await requestJson(`${RESUME_API}?limit=50`, {method: 'GET'});
    const items = Array.isArray(index.items) ? index.items : [];
    return items.find(item => String(item.conversation_id || '') === String(conversationId || '')) || null;
  }

  async function recover(conversationId, button) {
    if (state.busy || !conversationId) return;
    state.busy = true;
    state.error = null;
    state.conversationId = conversationId;
    if (button) button.disabled = true;
    try {
      const item = await resumeItem(conversationId);
      if (!item?.plan_id) throw new Error('The workflow changed before recovery. Refresh and choose the current resumable workflow.');
      const opened = await waitForConversation(conversationId);
      if (!opened) throw new Error('Open the workflow conversation before restoring its checkpoint.');
      const payload = await requestJson(REHYDRATE_API, {
        method: 'POST',
        body: JSON.stringify({conversation_id: conversationId, plan_id: Number(item.plan_id)}),
      });
      if (String(activeConversationId() || '') !== String(conversationId)) return;
      state.payload = payload;
      state.error = null;
      render();
      window.dispatchEvent(new CustomEvent('homeserver:workflow-rehydrated', {detail: {conversationId, planId: Number(item.plan_id), rehydrationId: Number(payload.rehydration_id)}}));
      window.HomeServerAgentWorkflowContinuation?.refresh?.();
    } catch (error) {
      state.payload = null;
      state.error = error?.message || 'Workflow recovery failed.';
      render();
    } finally {
      state.busy = false;
      if (button) button.disabled = false;
    }
  }

  document.addEventListener('click', event => {
    const button = event.target.closest('[data-workflow-resume-conversation]');
    if (!button) return;
    const conversationId = String(button.dataset.workflowResumeConversation || '').trim();
    // v0.55 handles the synchronous navigation. v0.56 only runs because the
    // user explicitly pressed Resume; boot/refresh paths never POST recovery.
    setTimeout(() => recover(conversationId, button).catch(() => null), 0);
  });

  window.addEventListener('homeserver:chat-agent-changed', () => render());

  function boot() {
    ensureStyles();
    if (!window.HomeServerAgentWorkflowResume || !ensureCue()) {
      state.bootAttempts += 1;
      if (state.bootAttempts < 180) setTimeout(boot, 80);
      return;
    }
    render();
  }

  window.HomeServerAgentWorkflowRehydration = Object.freeze({version: VERSION, recover});
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})();
