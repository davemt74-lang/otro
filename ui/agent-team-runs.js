(() => {
  'use strict';

  const VERSION = 'v0.51';
  const API = '/api/v1/control/agent-workflows';
  const MIN_MEMBERS = 2;
  const MAX_MEMBERS = 4;
  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const state = {
    workers: [],
    runs: [],
    rows: [{workerId: 0, task: ''}, {workerId: 0, task: ''}],
    busy: false,
    actionBusy: null,
    bootAttempts: 0,
  };

  function routing() {
    return window.HomeServerAgentRouting || null;
  }

  function parentAgent() {
    return routing()?.getSelectedAgent?.() || null;
  }

  function activeConversationId() {
    return routing()?.getActiveConversationId?.() || null;
  }

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
    if (document.querySelector('link[data-agent-team-runs-v051]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/agent-team-runs.css';
    link.dataset.agentTeamRunsV051 = '1';
    document.head.appendChild(link);
  }

  function ensureUi() {
    const panel = byId('agentWorkflowPanel');
    if (!panel) return false;
    if (byId('agentTeamRunsV051')) return true;
    const section = document.createElement('section');
    section.id = 'agentTeamRunsV051';
    section.className = 'agent-team-runs';
    section.dataset.agentTeamRuns = VERSION;
    section.innerHTML = `
      <div class="agent-team-head">
        <div><p class="eyebrow">TEAM RUN · ${VERSION}</p><h4>Fan out to a specialist team</h4><small>2–4 distinct specialists · one hop only · parent synthesis stays explicit</small></div>
        <button class="text-button" id="agentTeamRefresh" type="button">Refresh</button>
      </div>
      <form id="agentTeamForm" class="agent-team-form">
        <label>Team objective<textarea id="agentTeamObjective" rows="2" maxlength="16000" placeholder="What should this specialist team investigate or produce together?" required></textarea></label>
        <div id="agentTeamRows" class="agent-team-rows"></div>
        <div class="agent-team-compose-actions">
          <button class="button secondary" id="agentTeamAdd" type="button">Add specialist</button>
          <button class="button primary" id="agentTeamStart" type="submit">Start Team Run</button>
        </div>
        <small id="agentTeamConversationState" class="muted"></small>
      </form>
      <div class="agent-team-history-head"><strong>Team Runs</strong><span id="agentTeamSummary" class="muted"></span></div>
      <div id="agentTeamRunList" class="agent-team-run-list"><div class="muted">No Team Runs in this conversation.</div></div>`;
    const historyHead = panel.querySelector('.agent-workflow-history-head');
    if (historyHead) panel.insertBefore(section, historyHead);
    else panel.appendChild(section);
    return true;
  }

  function workerOptions(selectedId, rowIndex) {
    const used = new Set(state.rows.map((row, index) => index === rowIndex ? 0 : Number(row.workerId || 0)).filter(Boolean));
    const options = ['<option value="">Choose specialist</option>'];
    for (const worker of state.workers) {
      const id = Number(worker.id);
      const disabled = used.has(id) ? 'disabled' : '';
      const selected = id === Number(selectedId) ? 'selected' : '';
      options.push(`<option value="${id}" ${selected} ${disabled}>${esc(worker.name)}</option>`);
    }
    return options.join('');
  }

  function renderRows() {
    const node = byId('agentTeamRows');
    if (!node) return;
    node.innerHTML = state.rows.map((row, index) => `
      <div class="agent-team-row" data-team-row="${index}">
        <div class="agent-team-row-number">${index + 1}</div>
        <label>Specialist<select data-team-worker="${index}" ${state.busy ? 'disabled' : ''}>${workerOptions(row.workerId, index)}</select></label>
        <label>Task brief<textarea rows="2" maxlength="16000" data-team-task="${index}" placeholder="Give this specialist one bounded task…" ${state.busy ? 'disabled' : ''}>${esc(row.task)}</textarea></label>
        ${state.rows.length > MIN_MEMBERS ? `<button class="text-button danger agent-team-remove" type="button" data-team-remove="${index}" ${state.busy ? 'disabled' : ''}>Remove</button>` : ''}
      </div>`).join('');
    const add = byId('agentTeamAdd');
    if (add) add.disabled = state.busy || state.rows.length >= MAX_MEMBERS || !state.workers.length;
  }

  function statusLabel(value) {
    const status = String(value || 'queued');
    return status.charAt(0).toUpperCase() + status.slice(1);
  }

  function memberMarkup(member, run) {
    const status = String(member.status || 'queued');
    const result = member.result
      ? `<div class="agent-team-result"><span>Result</span>${esc(member.result)}</div>`
      : member.result_redacted
        ? '<div class="agent-team-redacted">Result hidden because current access no longer authorizes it.</div>'
        : '';
    const error = member.error ? `<div class="agent-team-error">${esc(member.error)}</div>` : '';
    const retry = status === 'failed' && !['cancelled', 'prepared', 'synthesized'].includes(run.status)
      ? `<button class="text-button" type="button" data-team-retry="${Number(run.id)}:${Number(member.id)}" ${state.actionBusy ? 'disabled' : ''}>Retry failed specialist</button>`
      : '';
    return `<div class="agent-team-member">
      <div class="agent-team-member-head"><strong>${esc(member.worker_agent_name || 'Specialist')}</strong><span class="agent-team-status status-${esc(status)}">${esc(statusLabel(status))}</span></div>
      <p>${esc(member.task || '')}</p>${result}${error}${retry}
    </div>`;
  }

  function runActions(run) {
    const disabled = state.actionBusy ? 'disabled' : '';
    const actions = [];
    if (run.can_run) actions.push(`<button class="button secondary" type="button" data-team-run="${Number(run.id)}" ${disabled}>Run remaining</button>`);
    if (run.can_prepare) actions.push(`<button class="button primary" type="button" data-team-prepare="${Number(run.id)}" ${disabled}>Prepare parent synthesis</button>`);
    if (!['cancelled', 'prepared', 'synthesized'].includes(run.status) && !Number(run.counts?.working || 0)) {
      actions.push(`<button class="text-button danger" type="button" data-team-cancel="${Number(run.id)}" ${disabled}>Cancel Team Run</button>`);
    }
    if (run.status === 'prepared') actions.push('<span class="agent-team-next">Ready for the parent Agent’s next message.</span>');
    if (run.status === 'synthesized') actions.push('<span class="agent-team-next">Parent synthesis completed.</span>');
    return actions.join('');
  }

  function renderRuns() {
    const node = byId('agentTeamRunList');
    const summary = byId('agentTeamSummary');
    if (summary) summary.textContent = state.runs.length ? `${state.runs.length} in this conversation` : '';
    if (!node) return;
    if (!state.runs.length) {
      node.innerHTML = '<div class="muted">No Team Runs in this conversation.</div>';
      return;
    }
    node.innerHTML = state.runs.map(run => `
      <article class="agent-team-run" data-team-run-id="${Number(run.id)}">
        <div class="agent-team-run-head"><div><strong>${esc(run.objective)}</strong><small>Parent: ${esc(run.parent_agent_name || 'Agent')} · ${Number(run.counts?.members || 0)} specialists</small></div><span class="agent-team-run-state team-${esc(run.status)}">${esc(statusLabel(run.status))}</span></div>
        <div class="agent-team-member-grid">${(run.members || []).map(member => memberMarkup(member, run)).join('')}</div>
        <div class="agent-team-run-actions">${runActions(run)}</div>
      </article>`).join('');
  }

  function render() {
    if (!ensureUi()) return;
    renderRows();
    renderRuns();
    const conversation = activeConversationId();
    const parent = parentAgent();
    const stateNode = byId('agentTeamConversationState');
    if (stateNode) {
      stateNode.textContent = conversation && parent
        ? `Conversation-bound parent: ${parent.name}. Team results stay in this thread until explicitly prepared for synthesis.`
        : 'Start or select a conversation before creating a Team Run.';
    }
    const start = byId('agentTeamStart');
    if (start) {
      start.disabled = state.busy || !conversation || !parent?.id || state.workers.length < MIN_MEMBERS;
      start.textContent = state.busy ? 'Team working…' : 'Start Team Run';
    }
  }

  async function loadWorkers() {
    const parent = parentAgent();
    if (!parent?.id) {
      state.workers = [];
      return;
    }
    const payload = await requestJson(`${API}/workers?parent_agent_id=${encodeURIComponent(parent.id)}`);
    state.workers = Array.isArray(payload.items) ? payload.items : [];
    for (const row of state.rows) {
      if (row.workerId && !state.workers.some(item => Number(item.id) === Number(row.workerId))) row.workerId = 0;
    }
  }

  async function loadRuns() {
    const conversation = activeConversationId();
    if (!conversation) {
      state.runs = [];
      return;
    }
    const payload = await requestJson(`${API}/team-runs?conversation_id=${encodeURIComponent(conversation)}&limit=20`);
    if (activeConversationId() !== conversation) return;
    state.runs = Array.isArray(payload.items) ? payload.items : [];
  }

  async function refresh() {
    if (!ensureUi()) return;
    await Promise.all([loadWorkers(), loadRuns()]);
    render();
  }

  function syncRowsFromDom() {
    state.rows = state.rows.map((row, index) => ({
      workerId: Number(document.querySelector(`[data-team-worker="${index}"]`)?.value || row.workerId || 0),
      task: String(document.querySelector(`[data-team-task="${index}"]`)?.value ?? row.task ?? '').trim(),
    }));
  }

  async function startTeam(event) {
    event.preventDefault();
    if (state.busy) return;
    syncRowsFromDom();
    const parent = parentAgent();
    const conversation = activeConversationId();
    const objective = String(byId('agentTeamObjective')?.value || '').trim();
    if (!parent?.id || !conversation || !objective) return;
    const workerIds = state.rows.map(row => Number(row.workerId || 0));
    if (workerIds.some(value => !value) || new Set(workerIds).size !== workerIds.length || state.rows.some(row => !row.task)) {
      flash('Choose a different specialist and a task brief for every Team Run row.', true);
      return;
    }
    state.busy = true;
    render();
    try {
      const maxContext = Number(byId('agentWorkflowContext')?.value || 12000);
      const created = await requestJson(`${API}/team-runs`, {
        method: 'POST',
        body: JSON.stringify({
          parent_agent_id: Number(parent.id),
          conversation_id: conversation,
          objective,
          members: state.rows.map(row => ({
            worker_agent_id: Number(row.workerId),
            task: row.task,
            include_memory: true,
            include_knowledge: true,
            include_contacts: true,
            cloud_allowed: true,
            max_context_chars: maxContext,
          })),
        }),
      });
      state.runs = [created, ...state.runs.filter(item => Number(item.id) !== Number(created.id))];
      renderRuns();
      const completed = await requestJson(`${API}/team-runs/${encodeURIComponent(created.id)}/run`, {method: 'POST'});
      state.runs = [completed, ...state.runs.filter(item => Number(item.id) !== Number(completed.id))];
      if (completed.status === 'completed') flash('All Team Run specialists completed. Prepare the exact result set when you want the parent Agent to synthesize it.');
      else if (completed.status === 'partial') flash('Team Run finished with partial results. Retry failed specialists or run any remaining authorized member.', true);
      else flash(`Team Run is ${completed.status}.`);
      const objectiveNode = byId('agentTeamObjective');
      if (objectiveNode) objectiveNode.value = '';
      state.rows = [{workerId: 0, task: ''}, {workerId: 0, task: ''}];
      byId('agentWorkflowRefresh')?.click();
      await refresh();
    } catch (error) {
      flash(error.message, true);
      await refresh().catch(() => null);
    } finally {
      state.busy = false;
      render();
    }
  }

  async function runRemaining(teamRunId) {
    if (state.actionBusy) return;
    state.actionBusy = `run:${teamRunId}`;
    renderRuns();
    try {
      const run = await requestJson(`${API}/team-runs/${encodeURIComponent(teamRunId)}/run`, {method: 'POST'});
      state.runs = [run, ...state.runs.filter(item => Number(item.id) !== Number(run.id))];
      flash(run.status === 'completed' ? 'All Team Run specialists completed.' : `Team Run is ${run.status}.`, run.status === 'partial');
      byId('agentWorkflowRefresh')?.click();
    } catch (error) {
      flash(error.message, true);
    } finally {
      state.actionBusy = null;
      await loadRuns().catch(() => null);
      renderRuns();
    }
  }

  async function retryMember(teamRunId, taskId) {
    if (state.actionBusy) return;
    state.actionBusy = `retry:${taskId}`;
    renderRuns();
    try {
      const run = await requestJson(`${API}/team-runs/${encodeURIComponent(teamRunId)}/members/${encodeURIComponent(taskId)}/retry`, {method: 'POST'});
      state.runs = [run, ...state.runs.filter(item => Number(item.id) !== Number(run.id))];
      flash(run.retry_outcome?.ok ? 'Specialist retry completed.' : 'Specialist retry failed again.', !run.retry_outcome?.ok);
      byId('agentWorkflowRefresh')?.click();
    } catch (error) {
      flash(error.message, true);
    } finally {
      state.actionBusy = null;
      await loadRuns().catch(() => null);
      renderRuns();
    }
  }

  async function prepareTeam(teamRunId) {
    if (state.actionBusy) return;
    state.actionBusy = `prepare:${teamRunId}`;
    renderRuns();
    try {
      const run = await requestJson(`${API}/team-runs/${encodeURIComponent(teamRunId)}/prepare`, {method: 'POST'});
      state.runs = [run, ...state.runs.filter(item => Number(item.id) !== Number(run.id))];
      const input = byId('chatInput');
      if (input) {
        input.focus();
        input.placeholder = 'Ask the parent Agent to synthesize this prepared Team Run…';
      }
      flash(`${Number(run.synthesis?.count || run.counts?.members || 0)} Team Run results are prepared for the parent Agent's next message.`);
      byId('agentWorkflowRefresh')?.click();
    } catch (error) {
      flash(error.message, true);
    } finally {
      state.actionBusy = null;
      await loadRuns().catch(() => null);
      renderRuns();
    }
  }

  async function cancelTeam(teamRunId) {
    if (state.actionBusy) return;
    state.actionBusy = `cancel:${teamRunId}`;
    renderRuns();
    try {
      const run = await requestJson(`${API}/team-runs/${encodeURIComponent(teamRunId)}/cancel`, {method: 'POST'});
      state.runs = [run, ...state.runs.filter(item => Number(item.id) !== Number(run.id))];
      flash('Team Run cancelled.');
      byId('agentWorkflowRefresh')?.click();
    } catch (error) {
      flash(error.message, true);
    } finally {
      state.actionBusy = null;
      await loadRuns().catch(() => null);
      renderRuns();
    }
  }

  document.addEventListener('click', event => {
    if (event.target.closest('#agentTeamAdd')) {
      syncRowsFromDom();
      if (state.rows.length < MAX_MEMBERS) state.rows.push({workerId: 0, task: ''});
      renderRows();
      return;
    }
    const remove = event.target.closest('[data-team-remove]');
    if (remove) {
      syncRowsFromDom();
      if (state.rows.length > MIN_MEMBERS) state.rows.splice(Number(remove.dataset.teamRemove), 1);
      renderRows();
      return;
    }
    const retry = event.target.closest('[data-team-retry]');
    if (retry) {
      const [teamRunId, taskId] = String(retry.dataset.teamRetry || '').split(':').map(Number);
      if (teamRunId && taskId) retryMember(teamRunId, taskId);
      return;
    }
    const run = event.target.closest('[data-team-run]');
    if (run) {
      runRemaining(Number(run.dataset.teamRun));
      return;
    }
    const prepare = event.target.closest('[data-team-prepare]');
    if (prepare) {
      prepareTeam(Number(prepare.dataset.teamPrepare));
      return;
    }
    const cancel = event.target.closest('[data-team-cancel]');
    if (cancel) {
      cancelTeam(Number(cancel.dataset.teamCancel));
      return;
    }
    if (event.target.closest('#agentTeamRefresh')) refresh().catch(error => flash(error.message, true));
  });

  document.addEventListener('change', event => {
    const select = event.target.closest('[data-team-worker]');
    if (!select) return;
    syncRowsFromDom();
    renderRows();
  });

  document.addEventListener('input', event => {
    if (!event.target.closest('[data-team-task]')) return;
    const index = Number(event.target.dataset.teamTask || 0);
    if (state.rows[index]) state.rows[index].task = String(event.target.value || '');
  });

  document.addEventListener('submit', event => {
    if (event.target.id === 'agentTeamForm') startTeam(event);
  });

  window.addEventListener('homeserver:chat-agent-changed', () => {
    state.rows = [{workerId: 0, task: ''}, {workerId: 0, task: ''}];
    refresh().catch(() => null);
  });

  const messageObserver = new MutationObserver(() => {
    clearTimeout(messageObserver._teamRefresh);
    messageObserver._teamRefresh = setTimeout(() => {
      if (activeConversationId()) loadRuns().then(renderRuns).catch(() => null);
    }, 180);
  });

  function boot() {
    ensureStyles();
    if (!window.HomeServerAgentWorkflows || !ensureUi()) {
      state.bootAttempts += 1;
      if (state.bootAttempts < 100) setTimeout(boot, 80);
      return;
    }
    const messages = byId('chatMessages');
    if (messages) messageObserver.observe(messages, {childList: true, subtree: true});
    refresh().catch(() => null);
  }

  window.HomeServerAgentTeamRuns = Object.freeze({version: VERSION, refresh});
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})();
