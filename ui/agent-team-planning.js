(() => {
  'use strict';

  const VERSION = 'v0.52';
  const API = '/api/v1/control/agent-workflows';
  const MIN_MEMBERS = 2;
  const MAX_MEMBERS = 4;
  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const state = {plans: [], workers: [], busy: false, actionBusy: null, bootAttempts: 0};

  function routing() { return window.HomeServerAgentRouting || null; }
  function parentAgent() { return routing()?.getSelectedAgent?.() || null; }
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

  function ensureStyles() {
    if (document.querySelector('link[data-agent-team-planning-v052]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/agent-team-planning.css';
    link.dataset.agentTeamPlanningV052 = '1';
    document.head.appendChild(link);
  }

  function ensureUi() {
    const teamSection = byId('agentTeamRunsV051');
    if (!teamSection) return false;
    if (byId('agentTeamPlanningV052')) return true;
    const section = document.createElement('section');
    section.id = 'agentTeamPlanningV052';
    section.className = 'agent-team-planning';
    section.dataset.agentTeamPlanning = VERSION;
    section.innerHTML = `
      <div class="agent-plan-head">
        <div><p class="eyebrow">TEAM PLANNING · ${VERSION}</p><h4>Ask the parent Agent to propose the team</h4><small>Planning creates no tasks. Review and explicitly approve before a queued Team Run exists.</small></div>
        <button class="text-button" id="agentPlanRefresh" type="button">Refresh</button>
      </div>
      <form id="agentPlanForm" class="agent-plan-form">
        <label>Planning objective<textarea id="agentPlanObjective" rows="2" maxlength="16000" placeholder="Describe the result you want. The parent Agent will divide it into 2–4 bounded specialist tasks." required></textarea></label>
        <div class="agent-plan-policy">
          <label class="check-inline"><input id="agentPlanMemory" type="checkbox" checked> Memory if permitted</label>
          <label class="check-inline"><input id="agentPlanKnowledge" type="checkbox" checked> Knowledge if permitted</label>
          <label class="check-inline"><input id="agentPlanContacts" type="checkbox"> Contacts if permitted</label>
          <label class="check-inline"><input id="agentPlanCloud" type="checkbox" checked> Cloud if this conversation permits it</label>
        </div>
        <div class="agent-plan-submit-row"><small id="agentPlanState" class="muted"></small><button class="button secondary" id="agentPlanPropose" type="submit">Propose team plan</button></div>
      </form>
      <div class="agent-plan-list-head"><strong>Review plans</strong><span id="agentPlanSummary" class="muted"></span></div>
      <div id="agentPlanList" class="agent-plan-list"><div class="muted">No Team Plans in this conversation.</div></div>`;
    const form = byId('agentTeamForm');
    if (form) teamSection.insertBefore(section, form);
    else teamSection.prepend(section);
    return true;
  }

  function statusLabel(value) {
    const status = String(value || 'proposed');
    return status.charAt(0).toUpperCase() + status.slice(1);
  }

  function workerOptions(selectedId, usedIds) {
    return [
      '<option value="">Choose specialist</option>',
      ...state.workers.map(worker => {
        const id = Number(worker.id);
        const selected = id === Number(selectedId) ? 'selected' : '';
        const disabled = usedIds.has(id) && !selected ? 'disabled' : '';
        return `<option value="${id}" ${selected} ${disabled}>${esc(worker.name)}</option>`;
      }),
    ].join('');
  }

  function proposedMembersMarkup(plan) {
    const members = Array.isArray(plan.members) ? plan.members : [];
    return members.map((member, index) => {
      const used = new Set(members.filter((_, i) => i !== index).map(item => Number(item.worker_agent_id || 0)).filter(Boolean));
      return `<div class="agent-plan-member" data-plan-member="${Number(plan.id)}:${index}">
        <div class="agent-plan-member-number">${index + 1}</div>
        <label>Specialist<select data-plan-worker="${Number(plan.id)}:${index}" ${state.actionBusy ? 'disabled' : ''}>${workerOptions(member.worker_agent_id, used)}</select></label>
        <label>Task brief<textarea rows="2" maxlength="16000" data-plan-task="${Number(plan.id)}:${index}" ${state.actionBusy ? 'disabled' : ''}>${esc(member.task || '')}</textarea></label>
      </div>`;
    }).join('');
  }

  function planMarkup(plan) {
    const proposed = plan.status === 'proposed';
    const memberCount = Array.isArray(plan.members) ? plan.members.length : 0;
    const provider = plan.provider_key ? `${esc(plan.provider_key)} · ${esc(plan.model || '')}` : 'edited plan';
    const controls = proposed
      ? `<div class="agent-plan-actions">
          <button class="button secondary" type="button" data-plan-save="${Number(plan.id)}" ${state.actionBusy ? 'disabled' : ''}>Save edits</button>
          <button class="button primary" type="button" data-plan-approve="${Number(plan.id)}" ${state.actionBusy ? 'disabled' : ''}>Approve & create queued Team Run</button>
          <button class="text-button danger" type="button" data-plan-reject="${Number(plan.id)}" ${state.actionBusy ? 'disabled' : ''}>Reject plan</button>
        </div>`
      : plan.status === 'approved'
        ? `<div class="agent-plan-approved">Approved · Team Run #${Number(plan.team_run_id || 0)} is queued. Execution still requires a separate Run action below.</div>`
        : '<div class="agent-plan-rejected">Rejected · no Team Run was created.</div>';
    return `<article class="agent-plan-card plan-${esc(plan.status)}" data-plan-id="${Number(plan.id)}">
      <div class="agent-plan-card-head"><div><strong>Plan #${Number(plan.id)}</strong><small>${memberCount} specialists · ${provider}${plan.cloud_used ? ' · cloud' : ' · local'}</small></div><span>${esc(statusLabel(plan.status))}</span></div>
      ${proposed
        ? `<label>Objective<textarea rows="2" maxlength="16000" data-plan-objective="${Number(plan.id)}" ${state.actionBusy ? 'disabled' : ''}>${esc(plan.objective || '')}</textarea></label><div class="agent-plan-members">${proposedMembersMarkup(plan)}</div>`
        : `<p>${esc(plan.objective || '')}</p><div class="agent-plan-readonly-members">${(plan.members || []).map(item => `<div><strong>${esc(item.worker_agent_name || `Agent ${item.worker_agent_id}`)}</strong><span>${esc(item.task || '')}</span></div>`).join('')}</div>`}
      ${controls}
    </article>`;
  }

  function render() {
    if (!ensureUi()) return;
    const conversation = activeConversationId();
    const parent = parentAgent();
    const stateNode = byId('agentPlanState');
    if (stateNode) stateNode.textContent = conversation && parent
      ? `Parent: ${parent.name}. Proposal only—nothing runs until you approve, then separately execute the Team Run.`
      : 'Start or select a parent conversation before proposing a Team Plan.';
    const propose = byId('agentPlanPropose');
    if (propose) {
      propose.disabled = state.busy || !conversation || !parent?.id || state.workers.length < MIN_MEMBERS;
      propose.textContent = state.busy ? 'Planning…' : 'Propose team plan';
    }
    const list = byId('agentPlanList');
    const summary = byId('agentPlanSummary');
    if (summary) summary.textContent = state.plans.length ? `${state.plans.length} in this conversation` : '';
    if (!list) return;
    list.innerHTML = state.plans.length
      ? state.plans.map(planMarkup).join('')
      : '<div class="muted">No Team Plans in this conversation.</div>';
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

  async function loadPlans() {
    const conversation = activeConversationId();
    if (!conversation) {
      state.plans = [];
      return;
    }
    const payload = await requestJson(`${API}/team-plans?conversation_id=${encodeURIComponent(conversation)}&limit=20`);
    if (activeConversationId() !== conversation) return;
    state.plans = Array.isArray(payload.items) ? payload.items : [];
  }

  async function refresh() {
    if (!ensureUi()) return;
    await Promise.all([loadWorkers(), loadPlans()]);
    render();
  }

  async function proposePlan(event) {
    event.preventDefault();
    if (state.busy) return;
    const parent = parentAgent();
    const conversation = activeConversationId();
    const objective = String(byId('agentPlanObjective')?.value || '').trim();
    if (!parent?.id || !conversation || !objective) return;
    state.busy = true;
    render();
    try {
      const maxContext = Number(byId('agentWorkflowContext')?.value || 12000);
      const plan = await requestJson(`${API}/team-plans`, {
        method: 'POST',
        body: JSON.stringify({
          parent_agent_id: Number(parent.id),
          conversation_id: conversation,
          objective,
          context: {
            include_memory: Boolean(byId('agentPlanMemory')?.checked),
            include_knowledge: Boolean(byId('agentPlanKnowledge')?.checked),
            include_contacts: Boolean(byId('agentPlanContacts')?.checked),
            cloud_allowed: Boolean(byId('agentPlanCloud')?.checked),
            max_context_chars: maxContext,
          },
        }),
      });
      state.plans = [plan, ...state.plans.filter(item => Number(item.id) !== Number(plan.id))];
      const input = byId('agentPlanObjective');
      if (input) input.value = '';
      flash(`Team Plan #${Number(plan.id)} proposed. Review or edit every specialist task before approval.`);
    } catch (error) {
      flash(error.message, true);
    } finally {
      state.busy = false;
      render();
    }
  }

  function planEdits(planId) {
    const plan = state.plans.find(item => Number(item.id) === Number(planId));
    if (!plan) return null;
    const objective = String(document.querySelector(`[data-plan-objective="${Number(planId)}"]`)?.value || '').trim();
    const members = (plan.members || []).map((member, index) => ({
      worker_agent_id: Number(document.querySelector(`[data-plan-worker="${Number(planId)}:${index}"]`)?.value || member.worker_agent_id || 0),
      task: String(document.querySelector(`[data-plan-task="${Number(planId)}:${index}"]`)?.value ?? member.task ?? '').trim(),
    }));
    return {objective, members};
  }

  function validateEdits(edits) {
    if (!edits?.objective || edits.members.length < MIN_MEMBERS || edits.members.length > MAX_MEMBERS) return false;
    const ids = edits.members.map(item => Number(item.worker_agent_id || 0));
    return ids.every(Boolean) && new Set(ids).size === ids.length && edits.members.every(item => item.task);
  }

  async function savePlan(planId) {
    if (state.actionBusy) return null;
    const edits = planEdits(planId);
    if (!validateEdits(edits)) {
      flash('Every proposed row needs a different specialist and a task brief.', true);
      return null;
    }
    state.actionBusy = `save:${planId}`;
    render();
    try {
      const plan = await requestJson(`${API}/team-plans/${encodeURIComponent(planId)}`, {
        method: 'PUT',
        body: JSON.stringify(edits),
      });
      state.plans = [plan, ...state.plans.filter(item => Number(item.id) !== Number(plan.id))];
      flash(`Team Plan #${Number(plan.id)} edits saved.`);
      return plan;
    } catch (error) {
      flash(error.message, true);
      return null;
    } finally {
      state.actionBusy = null;
      await loadPlans().catch(() => null);
      render();
    }
  }

  async function approvePlan(planId) {
    if (state.actionBusy) return;
    const edits = planEdits(planId);
    if (!validateEdits(edits)) {
      flash('Review every specialist and task before approval.', true);
      return;
    }
    state.actionBusy = `approve:${planId}`;
    render();
    try {
      // Save visible edits first. Approval itself atomically rechecks current grants and creates only queued v0.51 tasks.
      await requestJson(`${API}/team-plans/${encodeURIComponent(planId)}`, {
        method: 'PUT',
        body: JSON.stringify(edits),
      });
      const result = await requestJson(`${API}/team-plans/${encodeURIComponent(planId)}/approve`, {method: 'POST'});
      const plan = result.plan;
      state.plans = [plan, ...state.plans.filter(item => Number(item.id) !== Number(plan.id))];
      await window.HomeServerAgentTeamRuns?.refresh?.();
      flash(`Team Plan #${Number(plan.id)} approved. Team Run #${Number(result.team_run?.id || 0)} is queued and has not executed.`);
    } catch (error) {
      flash(error.message, true);
    } finally {
      state.actionBusy = null;
      await loadPlans().catch(() => null);
      render();
    }
  }

  async function rejectPlan(planId) {
    if (state.actionBusy) return;
    state.actionBusy = `reject:${planId}`;
    render();
    try {
      const plan = await requestJson(`${API}/team-plans/${encodeURIComponent(planId)}/reject`, {method: 'POST'});
      state.plans = [plan, ...state.plans.filter(item => Number(item.id) !== Number(plan.id))];
      flash(`Team Plan #${Number(plan.id)} rejected. No Team Run was created.`);
    } catch (error) {
      flash(error.message, true);
    } finally {
      state.actionBusy = null;
      await loadPlans().catch(() => null);
      render();
    }
  }

  document.addEventListener('submit', event => {
    if (event.target.id === 'agentPlanForm') proposePlan(event);
  });

  document.addEventListener('click', event => {
    const save = event.target.closest('[data-plan-save]');
    if (save) { savePlan(Number(save.dataset.planSave)); return; }
    const approve = event.target.closest('[data-plan-approve]');
    if (approve) { approvePlan(Number(approve.dataset.planApprove)); return; }
    const reject = event.target.closest('[data-plan-reject]');
    if (reject) { rejectPlan(Number(reject.dataset.planReject)); return; }
    if (event.target.closest('#agentPlanRefresh')) refresh().catch(error => flash(error.message, true));
  });

  document.addEventListener('change', event => {
    if (!event.target.closest('[data-plan-worker]')) return;
    const [planId] = String(event.target.dataset.planWorker || '').split(':').map(Number);
    const plan = state.plans.find(item => Number(item.id) === planId);
    if (!plan) return;
    const edits = planEdits(planId);
    if (!edits) return;
    plan.members = edits.members.map((item, index) => ({...plan.members[index], ...item}));
    render();
  });

  window.addEventListener('homeserver:chat-agent-changed', () => refresh().catch(() => null));

  function boot() {
    ensureStyles();
    if (!window.HomeServerAgentTeamRuns || !ensureUi()) {
      state.bootAttempts += 1;
      if (state.bootAttempts < 120) setTimeout(boot, 80);
      return;
    }
    refresh().catch(() => null);
  }

  window.HomeServerAgentTeamPlanning = Object.freeze({version: VERSION, refresh});
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})();
