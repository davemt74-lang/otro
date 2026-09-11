(() => {
  'use strict';

  const VERSION = 'v0.53';
  const API = '/api/v1/control/agent-workflows';
  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const state = {items: [], busy: null, bootAttempts: 0};

  function routing() { return window.HomeServerAgentRouting || null; }
  function activeConversationId() { return routing()?.getActiveConversationId?.() || null; }

  function flash(message, error = false) {
    const node = byId('flash');
    if (!node) return;
    node.textContent = message;
    node.className = `flash show${error ? ' error' : ''}`;
  }

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

  function ensureContinuationExtension() {
    if (document.querySelector('script[data-agent-workflow-continuation-v054]')) return;
    const script = document.createElement('script');
    script.src = '/assets/agent-workflow-continuation.js';
    script.dataset.agentWorkflowContinuationV054 = '1';
    script.async = false;
    document.head.appendChild(script);
  }

  function statusLabel(value) {
    const status = String(value || 'queued');
    return status.charAt(0).toUpperCase() + status.slice(1);
  }

  function memberName(item, taskId) {
    const member = (item.team_run?.members || []).find(row => Number(row.id) === Number(taskId));
    return member?.worker_agent_name || `Task ${Number(taskId)}`;
  }

  function memberSummary(item) {
    const members = item.team_run?.members || [];
    if (!members.length) return '';
    return `<div class="agent-orchestration-members">${members.map(member => {
      const status = String(member.status || 'queued');
      const result = member.result
        ? `<small>${esc(member.result)}</small>`
        : member.result_redacted
          ? '<small>Result hidden because current access no longer authorizes it.</small>'
          : member.error
            ? `<small class="agent-orchestration-error">${esc(member.error)}</small>`
            : '';
      return `<div class="agent-orchestration-member"><div><strong>${esc(member.worker_agent_name || 'Specialist')}</strong><span class="agent-orchestration-status status-${esc(status)}">${esc(statusLabel(status))}</span></div><p>${esc(member.task || '')}</p>${result}</div>`;
    }).join('')}</div>`;
  }

  function actionMarkup(item) {
    const actions = new Set(item.allowed_actions || []);
    const disabled = state.busy ? 'disabled' : '';
    const buttons = [];
    if (actions.has('run')) {
      const label = item.status === 'partial' ? 'Run remaining queued specialists' : 'Run specialists';
      buttons.push(`<button class="button primary" type="button" data-orch-run="${Number(item.plan_id)}" ${disabled}>${label}</button>`);
    }
    if (actions.has('retry')) {
      for (const taskId of item.team_run?.retryable_task_ids || []) {
        buttons.push(`<button class="button secondary" type="button" data-orch-retry="${Number(item.plan_id)}:${Number(taskId)}" ${disabled}>Retry ${esc(memberName(item, taskId))}</button>`);
      }
    }
    if (actions.has('prepare')) {
      buttons.push(`<button class="button primary" type="button" data-orch-prepare="${Number(item.plan_id)}" ${disabled}>Prepare parent synthesis</button>`);
    }
    if (actions.has('parent_chat')) {
      buttons.push(`<button class="button primary" type="button" data-orch-parent-chat="${Number(item.plan_id)}">Continue with parent Agent</button>`);
    }
    return buttons.join('');
  }

  function lifecycleMarkup(item) {
    const next = item.next_action || {};
    const run = item.team_run || {};
    const counts = run.counts || {};
    const progress = item.plan_status === 'approved'
      ? `<div class="agent-orchestration-counts"><span>${Number(counts.completed || 0)} completed</span><span>${Number(counts.working || 0)} working</span><span>${Number(counts.queued || 0)} queued</span><span>${Number(counts.failed || 0)} failed</span></div>`
      : '';
    const complete = item.status === 'synthesized'
      ? '<div class="agent-orchestration-complete">Parent synthesis completed.</div>'
      : '';
    return `<div class="agent-orchestration" data-orchestration-plan="${Number(item.plan_id)}" data-orchestration-version="${VERSION}">
      <div class="agent-orchestration-head"><div><span class="eyebrow">ORCHESTRATION · ${VERSION}</span><strong>${esc(statusLabel(item.status))}</strong></div><span>Plan #${Number(item.plan_id)}${item.team_run_id ? ` · Team Run #${Number(item.team_run_id)}` : ''}</span></div>
      ${progress}
      ${memberSummary(item)}
      <div class="agent-orchestration-next"><strong>Next</strong><span>${esc(next.label || '')}</span></div>
      <div class="agent-orchestration-actions">${actionMarkup(item)}</div>
      ${complete}
    </div>`;
  }

  function render() {
    for (const item of state.items) {
      const card = document.querySelector(`[data-plan-id="${Number(item.plan_id)}"]`);
      if (!card) continue;
      const old = card.querySelector('[data-orchestration-plan]');
      if (old) old.remove();
      if (item.plan_status !== 'approved') continue;
      const legacy = card.querySelector('.agent-plan-approved');
      if (legacy) legacy.textContent = `Approved · Team Run #${Number(item.team_run_id || 0)} · ${statusLabel(item.status)}. Execution remains explicit.`;
      card.insertAdjacentHTML('beforeend', lifecycleMarkup(item));
    }
  }

  async function load() {
    const conversation = activeConversationId();
    if (!conversation) {
      state.items = [];
      return;
    }
    const payload = await requestJson(`${API}/team-orchestrations?conversation_id=${encodeURIComponent(conversation)}&limit=20`);
    if (activeConversationId() !== conversation) return;
    state.items = Array.isArray(payload.items) ? payload.items : [];
  }

  async function refresh({refreshPlanning = false} = {}) {
    if (refreshPlanning) await window.HomeServerAgentTeamPlanning?.refresh?.();
    await load();
    render();
  }

  async function perform(planId, suffix, successMessage) {
    if (state.busy) return;
    state.busy = `${planId}:${suffix}`;
    render();
    try {
      const item = await requestJson(`${API}/team-plans/${encodeURIComponent(planId)}/${suffix}`, {method: 'POST'});
      state.items = [item, ...state.items.filter(row => Number(row.plan_id) !== Number(item.plan_id))];
      await window.HomeServerAgentTeamRuns?.refresh?.();
      await window.HomeServerAgentTeamPlanning?.refresh?.();
      await load();
      render();
      flash(successMessage(item));
    } catch (error) {
      flash(error.message, true);
      await load().catch(() => null);
      render();
    } finally {
      state.busy = null;
      render();
    }
  }

  function runPlan(planId) {
    return perform(planId, 'run', item => {
      if (item.status === 'completed') return 'All specialists completed. Prepare the exact result set when ready.';
      if (item.status === 'partial') return 'Team Run is partial. Retry failed specialists or run any remaining queued work.';
      return `Team Run is ${item.status}.`;
    });
  }

  function retryMember(planId, taskId) {
    return perform(planId, `members/${encodeURIComponent(taskId)}/retry`, item => {
      if (item.status === 'completed') return 'Specialist retry completed. All team results are ready for explicit synthesis preparation.';
      return `Specialist retry finished. Team Run is ${item.status}.`;
    });
  }

  function preparePlan(planId) {
    return perform(planId, 'prepare', item => {
      const input = byId('chatInput');
      if (input) input.placeholder = 'Ask the parent Agent to synthesize this prepared Team Run…';
      return item.status === 'prepared'
        ? 'Specialist results are prepared. Send the parent Agent a message when you want synthesis.'
        : `Team Run is ${item.status}.`;
    });
  }

  function focusParentChat(planId) {
    const item = state.items.find(row => Number(row.plan_id) === Number(planId));
    const input = byId('chatInput');
    if (!input) return;
    input.focus();
    input.placeholder = 'Ask the parent Agent to synthesize the prepared specialist results…';
    flash(item?.status === 'prepared'
      ? 'Prepared results are waiting for the parent Agent. Sending your next message performs the synthesis.'
      : 'Continue with the parent Agent.');
  }

  document.addEventListener('click', event => {
    const run = event.target.closest('[data-orch-run]');
    if (run) { runPlan(Number(run.dataset.orchRun)); return; }
    const retry = event.target.closest('[data-orch-retry]');
    if (retry) {
      const [planId, taskId] = String(retry.dataset.orchRetry || '').split(':').map(Number);
      if (planId && taskId) retryMember(planId, taskId);
      return;
    }
    const prepare = event.target.closest('[data-orch-prepare]');
    if (prepare) { preparePlan(Number(prepare.dataset.orchPrepare)); return; }
    const parentChat = event.target.closest('[data-orch-parent-chat]');
    if (parentChat) focusParentChat(Number(parentChat.dataset.orchParentChat));
  });

  window.addEventListener('homeserver:chat-agent-changed', () => refresh().catch(() => null));

  const messageObserver = new MutationObserver(() => {
    clearTimeout(messageObserver._orchestrationRefresh);
    messageObserver._orchestrationRefresh = setTimeout(() => {
      if (activeConversationId()) refresh({refreshPlanning: true}).catch(() => null);
    }, 220);
  });

  function boot() {
    if (!window.HomeServerAgentTeamPlanning || !document.querySelector('#agentTeamPlanningV052')) {
      state.bootAttempts += 1;
      if (state.bootAttempts < 140) setTimeout(boot, 80);
      return;
    }
    const messages = byId('chatMessages');
    if (messages) messageObserver.observe(messages, {childList: true, subtree: true});
    refresh().catch(() => null);
  }

  window.HomeServerAgentTeamOrchestration = Object.freeze({version: VERSION, refresh});
  ensureContinuationExtension();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})();