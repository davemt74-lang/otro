(() => {
  'use strict';

  const VERSION = 'v0.54';
  const API = '/api/v1/control/agent-workflows/continuation';
  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const state = {payload: null, conversationId: null, bootAttempts: 0, loading: false};

  function routing() { return window.HomeServerAgentRouting || null; }
  function activeConversationId() { return routing()?.getActiveConversationId?.() || null; }

  async function readContinuation(conversationId) {
    const response = await fetch(`${API}?conversation_id=${encodeURIComponent(conversationId)}`, {
      cache: 'no-store',
      credentials: 'same-origin',
      method: 'GET',
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
    return payload;
  }

  function ensureStyles() {
    if (document.querySelector('link[data-agent-workflow-continuation-v054]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/agent-workflow-continuation.css';
    link.dataset.agentWorkflowContinuationV054 = '1';
    document.head.appendChild(link);
  }

  function ensureResumeExtension() {
    if (document.querySelector('script[data-agent-workflow-resume-v055]')) return;
    const script = document.createElement('script');
    script.src = '/assets/agent-workflow-resume.js';
    script.dataset.agentWorkflowResumeV055 = '1';
    script.async = false;
    document.head.appendChild(script);
  }

  function ensureCue() {
    const panel = document.querySelector('#view-chat .chat-panel');
    const head = panel?.querySelector('.chat-head');
    const messages = byId('chatMessages');
    if (!panel || !head || !messages) return false;
    if (byId('agentWorkflowContinuationV054')) return true;
    const cue = document.createElement('section');
    cue.id = 'agentWorkflowContinuationV054';
    cue.className = 'agent-workflow-continuation hidden';
    cue.dataset.workflowContinuation = VERSION;
    cue.setAttribute('aria-live', 'polite');
    panel.insertBefore(cue, messages);
    return true;
  }

  function actionLabel(key) {
    return ({
      review_plan: 'Review plan',
      run_specialists: 'Open Team Run',
      run_remaining: 'Open Team Run',
      retry_failed: 'Review retry',
      resolve_partial: 'Review Team Run',
      prepare_synthesis: 'Prepare synthesis',
      parent_chat: 'Continue in parent chat',
    })[String(key || '')] || '';
  }

  function statusLabel(value) {
    const status = String(value || 'workflow');
    return status.charAt(0).toUpperCase() + status.slice(1);
  }

  function render() {
    if (!ensureCue()) return;
    const cue = byId('agentWorkflowContinuationV054');
    const current = state.payload?.current || null;
    if (!cue || !current || !state.conversationId) {
      cue?.classList.add('hidden');
      if (cue) cue.innerHTML = '';
      return;
    }
    const next = current.next_action || {};
    const counts = current.counts || {};
    const label = actionLabel(next.key);
    const activeCount = Number(state.payload?.active_count || 0);
    const progress = current.plan_status === 'approved'
      ? `${Number(counts.completed || 0)}/${Number(counts.members || 0)} specialists completed${Number(counts.failed || 0) ? ` · ${Number(counts.failed)} failed` : ''}`
      : 'Team Plan awaiting an explicit decision';
    cue.innerHTML = `
      <div class="workflow-continuation-copy">
        <div class="workflow-continuation-title"><span class="workflow-continuation-dot" aria-hidden="true"></span><strong>${esc(statusLabel(current.status))}</strong><span>Plan #${Number(current.plan_id)}${current.team_run_id ? ` · Team Run #${Number(current.team_run_id)}` : ''}</span></div>
        <p>${esc(next.label || 'Workflow state is available for review.')}</p>
        <small>${esc(progress)}${activeCount > 1 ? ` · ${activeCount} active workflows in this conversation` : ''}</small>
      </div>
      ${label ? `<button class="button secondary workflow-continuation-action" type="button" data-workflow-continuation-action="${esc(next.key)}" data-workflow-continuation-plan="${Number(current.plan_id)}">${esc(label)}</button>` : ''}`;
    cue.classList.remove('hidden');
  }

  async function refresh() {
    if (state.loading) return;
    if (!ensureCue()) return;
    const conversation = activeConversationId();
    if (!conversation) {
      state.payload = null;
      state.conversationId = null;
      render();
      return;
    }
    state.loading = true;
    try {
      const payload = await readContinuation(conversation);
      if (activeConversationId() !== conversation) return;
      state.payload = payload;
      state.conversationId = conversation;
    } catch (_) {
      if (activeConversationId() === conversation) {
        state.payload = null;
        state.conversationId = conversation;
      }
    } finally {
      state.loading = false;
      render();
    }
  }

  function focusExistingAction(key, planId) {
    if (key === 'parent_chat') {
      const input = byId('chatInput');
      if (!input) return;
      input.focus();
      input.placeholder = 'Ask the parent Agent to synthesize the prepared specialist results…';
      return;
    }
    const card = document.querySelector(`[data-plan-id="${Number(planId)}"]`);
    if (!card) return;
    const selector = ({
      review_plan: `[data-plan-approve="${Number(planId)}"]`,
      run_specialists: `[data-orch-run="${Number(planId)}"]`,
      run_remaining: `[data-orch-run="${Number(planId)}"]`,
      retry_failed: '[data-orch-retry]',
      resolve_partial: '[data-orchestration-plan]',
      prepare_synthesis: `[data-orch-prepare="${Number(planId)}"]`,
    })[key];
    card.scrollIntoView({behavior: 'smooth', block: 'center'});
    const target = selector ? card.querySelector(selector) : null;
    if (target && typeof target.focus === 'function') target.focus({preventScroll: true});
  }

  document.addEventListener('click', event => {
    const action = event.target.closest('[data-workflow-continuation-action][data-workflow-continuation-plan]');
    if (!action) return;
    focusExistingAction(String(action.dataset.workflowContinuationAction || ''), Number(action.dataset.workflowContinuationPlan));
  });

  window.addEventListener('homeserver:chat-agent-changed', () => refresh().catch(() => null));

  const observer = new MutationObserver(() => {
    clearTimeout(observer._continuationRefresh);
    observer._continuationRefresh = setTimeout(() => refresh().catch(() => null), 180);
  });

  function boot() {
    ensureStyles();
    ensureResumeExtension();
    if (!window.HomeServerAgentTeamOrchestration || !ensureCue()) {
      state.bootAttempts += 1;
      if (state.bootAttempts < 160) setTimeout(boot, 80);
      return;
    }
    const messages = byId('chatMessages');
    const plans = byId('agentPlanList');
    if (messages) observer.observe(messages, {childList: true, subtree: true});
    if (plans) observer.observe(plans, {childList: true, subtree: true});
    refresh().catch(() => null);
  }

  window.HomeServerAgentWorkflowContinuation = Object.freeze({version: VERSION, refresh});
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})();
