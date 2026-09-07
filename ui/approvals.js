(() => {
  'use strict';

  let filter = 'pending';
  const byId = id => document.getElementById(id);
  const escapeHtml = (value = '') => String(value).replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
  const fmt = value => value ? new Date(value).toLocaleString() : '—';

  async function request(path, options = {}) {
    const headers = {...(options.headers || {})};
    if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, {...options, headers});
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }

  function notify(message, error = false) {
    const node = byId('flash');
    if (!node) return;
    node.textContent = message;
    node.className = `flash show${error ? ' error' : ''}`;
    clearTimeout(notify.timer);
    notify.timer = setTimeout(() => { node.className = 'flash'; }, 3500);
  }

  function proposalContent(item) {
    const args = item.arguments || {};
    if (item.action_key === 'tasks.create') {
      const detail = [];
      if (args.priority) detail.push(`priority ${escapeHtml(args.priority)}`);
      if (args.due_at) detail.push(`due ${escapeHtml(fmt(args.due_at))}`);
      if (args.remind_at) detail.push(`remind ${escapeHtml(fmt(args.remind_at))}`);
      if (args.recurrence && args.recurrence !== 'none') detail.push(`${Number(args.recurrence_interval || 1)}× ${escapeHtml(args.recurrence)}`);
      if (args.contact_id) detail.push(`contact #${Number(args.contact_id)}`);
      return {
        title: escapeHtml(args.title || 'Proposed task'),
        body: escapeHtml(args.description || ''),
        detail: detail.map(value => `<span>${value}</span>`).join(''),
        confirm: 'Approve this task/reminder and create it now?',
      };
    }
    return {
      title: escapeHtml(args.memory_key || 'Proposed memory'),
      body: escapeHtml(args.content || ''),
      detail: `<span>importance ${Number(args.importance ?? 0.5).toFixed(1)}</span>`,
      confirm: 'Approve this action and write the proposed memory now?',
    };
  }

  function render(items) {
    const node = byId('approvalsList');
    if (!node) return;
    if (!items.length) {
      node.innerHTML = `<div class="panel empty-state">${filter === 'pending' ? 'No pending approvals.' : 'No approval history yet.'}</div>`;
      return;
    }
    node.innerHTML = items.map(item => {
      const content = proposalContent(item);
      const actions = item.status === 'pending'
        ? `<div class="approval-actions"><button class="button secondary danger" data-action-deny="${escapeHtml(item.id)}">Deny</button><button class="button primary" data-action-approve="${escapeHtml(item.id)}" data-confirm="${escapeHtml(content.confirm)}">Approve</button></div>`
        : `<div class="approval-actions"><span class="approval-status ${escapeHtml(item.status)}">${escapeHtml(item.status)}</span></div>`;
      return `<article class="approval-card ${escapeHtml(item.status)}"><div><h3>${content.title}</h3><p>${content.body}</p><div class="approval-meta"><span class="approval-status ${escapeHtml(item.status)}">${escapeHtml(item.status)}</span><span>${escapeHtml(item.action_key || 'action')}</span><span>${escapeHtml(item.source_app_key)}</span>${content.detail}<span>created ${escapeHtml(fmt(item.created_at))}</span><span>expires ${escapeHtml(fmt(item.expires_at))}</span>${item.execution_tool_run_id ? `<span>tool run #${Number(item.execution_tool_run_id)}</span>` : ''}</div>${item.error ? `<div class="muted">${escapeHtml(item.error)}</div>` : ''}</div>${actions}</article>`;
    }).join('');
  }

  async function updatePendingCount() {
    const data = await request('/api/v1/control/action-requests?status=pending&limit=100');
    const count = (data.items || []).length;
    const node = byId('approvalNavCount');
    if (!node) return;
    node.textContent = String(count);
    node.classList.toggle('hidden', count === 0);
  }

  async function loadApprovals() {
    const path = filter === 'pending'
      ? '/api/v1/control/action-requests?status=pending&limit=200'
      : '/api/v1/control/action-requests?limit=200';
    const data = await request(path);
    render(data.items || []);
    await updatePendingCount();
  }

  document.addEventListener('click', async event => {
    const nav = event.target.closest('[data-view="approvals"], [data-go="approvals"]');
    if (nav) {
      try { await loadApprovals(); } catch (err) { notify(err.message, true); }
      return;
    }

    const filterButton = event.target.closest('[data-approval-filter]');
    if (filterButton) {
      filter = filterButton.dataset.approvalFilter === 'all' ? 'all' : 'pending';
      document.querySelectorAll('[data-approval-filter]').forEach(button => button.classList.toggle('active', button === filterButton));
      try { await loadApprovals(); } catch (err) { notify(err.message, true); }
      return;
    }

    const approve = event.target.closest('[data-action-approve]');
    if (approve) {
      if (!confirm(approve.dataset.confirm || 'Approve this action and execute it now?')) return;
      approve.disabled = true;
      try {
        await request(`/api/v1/control/action-requests/${encodeURIComponent(approve.dataset.actionApprove)}/approve`, {method:'POST'});
        notify('Action approved and executed.');
        await loadApprovals();
      } catch (err) { notify(err.message, true); }
      finally { approve.disabled = false; }
      return;
    }

    const deny = event.target.closest('[data-action-deny]');
    if (deny) {
      deny.disabled = true;
      try {
        await request(`/api/v1/control/action-requests/${encodeURIComponent(deny.dataset.actionDeny)}/deny`, {method:'POST'});
        notify('Action denied.');
        await loadApprovals();
      } catch (err) { notify(err.message, true); }
      finally { deny.disabled = false; }
    }
  });

  byId('refreshButton')?.addEventListener('click', () => {
    if (byId('view-approvals')?.classList.contains('active')) loadApprovals().catch(err => notify(err.message, true));
    else updatePendingCount().catch(() => {});
  });

  window.addEventListener('hashchange', () => {
    if (location.hash === '#approvals') loadApprovals().catch(err => notify(err.message, true));
  });

  updatePendingCount().catch(() => {});
})();