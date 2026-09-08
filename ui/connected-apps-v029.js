(() => {
  'use strict';

  const view = document.getElementById('view-apps');
  if (!view) return;

  const esc = (value = '') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = value => value ? new Date(value).toLocaleString() : 'Never';
  const parseList = value => String(value || '').split(',').map(item => item.trim()).filter((item, index, list) => item && list.indexOf(item) === index).slice(0, 32);
  const groups = [
    ['Agent', ['agent.chat']],
    ['Private data', ['memory.read','memory.write','knowledge.search','contacts.read']],
    ['Operations', ['tools.execute','tasks.read','tasks.write','notifications.read','events.read','events.write','plugins.read','awareness.read','usage.read','usage.write']],
  ];
  let data = {apps: [], pending: [], counts: {}, available_permissions: []};
  let loading = false;

  async function request(path, options = {}) {
    const headers = {...(options.headers || {})};
    if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, {...options, headers});
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
    return payload;
  }

  function notify(message, error = false) {
    if (typeof window.flash === 'function') return window.flash(message, error);
    console[error ? 'error' : 'log'](message);
  }

  function ensureStyle() {
    if (document.querySelector('link[data-connected-apps-v029]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/connected-apps-v029.css';
    link.dataset.connectedAppsV029 = '1';
    document.head.appendChild(link);
  }

  function ensureLayout() {
    if (view.dataset.connectedAppsV029 === '1') return;
    view.dataset.connectedAppsV029 = '1';
    view.innerHTML = `
      <div class="section-intro split connected-apps-intro">
        <div><h2>Connected Apps</h2><p>Control exactly how VP3, Microgifter, and future wrappers can use this private HomeServer.</p></div>
        <button class="button secondary" id="refreshConnectedAppsV029" type="button">Refresh</button>
      </div>
      <div class="connected-app-stats" id="connectedAppStatsV029"></div>
      <div class="panel pairing-panel connected-pairing-panel">
        <div><p class="eyebrow">PAIR APPLICATION</p><h3>Approve a one-time code</h3><p>The requesting app must use its stable application identity. Approval grants only the requested coarse permissions; resource scopes can narrow access further.</p></div>
        <form id="pairingFormV029"><input id="pairingCodeV029" placeholder="AB12-CD34" maxlength="32" required><button class="button primary" type="submit">Approve</button></form>
        <div id="pairingResultV029" class="pairing-token hidden"></div>
      </div>
      <div id="pendingPairing" class="connected-pending-list"></div>
      <div class="connected-apps-heading"><div><p class="eyebrow">AUTHORIZED WRAPPERS</p><h3>Application boundaries</h3></div><span class="muted">Permissions grant capabilities. Scopes can only narrow them.</span></div>
      <div id="appsList" class="connected-apps-v029-list"><div class="panel empty-state">Loading connected applications…</div></div>`;
  }

  function scopeLabel(summary = {}) {
    const parts = [];
    parts.push(summary.cloud_allowed === false ? 'Cloud blocked' : 'Cloud allowed');
    parts.push(summary.memory_restricted ? `Memory ${summary.memory_prefix_count} boundary` : 'Memory all permitted');
    parts.push(summary.knowledge_restricted ? `Knowledge ${summary.knowledge_kind_count} type${summary.knowledge_kind_count === 1 ? '' : 's'}` : 'Knowledge all permitted');
    parts.push(summary.tools_restricted ? `Tools ${summary.tool_count}` : 'Tools all permitted');
    parts.push(summary.plugins_restricted ? `Plugins ${summary.plugin_count}` : 'Plugins all permitted');
    return parts;
  }

  function renderStats() {
    const counts = data.counts || {};
    const target = document.getElementById('connectedAppStatsV029');
    if (!target) return;
    target.innerHTML = [
      ['Active', counts.active || 0],
      ['Paused', counts.paused || 0],
      ['Revoked', counts.revoked || 0],
      ['Pending', counts.pending || 0],
    ].map(([label, value]) => `<article><span>${esc(label)}</span><strong>${Number(value)}</strong></article>`).join('');
  }

  function renderPending() {
    const target = document.getElementById('pendingPairing');
    if (!target) return;
    if (!data.pending?.length) {
      target.innerHTML = '';
      return;
    }
    target.innerHTML = `<div class="connected-apps-heading"><div><p class="eyebrow">PENDING REQUESTS</p><h3>Review before approving</h3></div></div>` + data.pending.map(item => {
      const warning = item.requests_additional_capabilities
        ? `<div class="access-warning"><strong>Requests additional capabilities</strong><span>${esc(item.new_permissions.join(', '))}</span></div>`
        : item.existing_app ? '<div class="access-note">Re-pair request does not add capabilities beyond the current grant.</div>' : '<div class="access-note">New application identity.</div>';
      return `<article class="panel pending-app-card">
        <div><h3>${esc(item.app_name)}</h3><p class="muted">${esc(item.app_key)} · expires ${esc(fmt(item.expires_at))}</p>${warning}</div>
        <div><p class="eyebrow">REQUESTED PERMISSIONS</p><div class="chip-row">${(item.requested_permissions || []).map(p => `<span>${esc(p)}</span>`).join('') || '<span>None</span>'}</div></div>
      </article>`;
    }).join('');
  }

  function permissionMarkup(app) {
    const current = new Map((app.permissions || []).map(item => [item.permission, Boolean(item.allowed)]));
    return groups.map(([label, names]) => {
      const available = names.filter(name => data.available_permissions.includes(name));
      if (!available.length) return '';
      return `<fieldset class="permission-group"><legend>${esc(label)}</legend>${available.map(name => `<label><input type="checkbox" data-v029-permission="${app.id}" data-permission="${esc(name)}" data-original="${current.get(name) ? '1' : '0'}" ${current.get(name) ? 'checked' : ''}>${esc(name)}</label>`).join('')}</fieldset>`;
    }).join('');
  }

  function renderActivityPreview(items = []) {
    if (!items.length) return '<div class="empty-state compact">No application activity recorded yet.</div>';
    return items.map(item => `<div class="app-activity-row"><span>${esc(fmt(item.created_at))}</span><strong>${esc(item.action)}</strong><small>${esc(item.resource_type || '')}</small></div>`).join('');
  }

  function appMarkup(app) {
    const scope = app.scope || {};
    const boundaryChips = scopeLabel(app.scope_summary).map(label => `<span>${esc(label)}</span>`).join('');
    return `<article class="panel connected-app-card-v029" data-v029-app-card="${app.id}">
      <div class="connected-app-card-head">
        <div><div class="connected-app-title"><h3>${esc(app.name)}</h3><span class="app-status-pill ${esc(app.status)}">${esc(app.status)}</span></div><p class="muted">${esc(app.app_key)} · paired ${esc(fmt(app.paired_at))} · last seen ${esc(fmt(app.last_seen_at))}</p></div>
        <div class="app-card-actions"><select data-v029-status="${app.id}" aria-label="Application status"><option value="active" ${app.status==='active'?'selected':''}>Active</option><option value="paused" ${app.status==='paused'?'selected':''}>Paused</option><option value="revoked" ${app.status==='revoked'?'selected':''}>Revoked</option></select><button class="button secondary danger" type="button" data-v029-repair="${app.id}">Require re-pair</button></div>
      </div>
      <div class="scope-summary-row">${boundaryChips}</div>
      <details class="app-control-details" open>
        <summary>Capabilities & permissions <span>${Number(app.allowed_permission_count || 0)} enabled</span></summary>
        <div class="permission-groups">${permissionMarkup(app)}</div>
        <div class="form-actions"><span class="muted">New HomeServer capabilities are not granted automatically.</span><button class="button secondary" type="button" data-v029-save-permissions="${app.id}">Save permissions</button></div>
      </details>
      <details class="app-control-details">
        <summary>Private resource boundaries <span>HomeServer enforced</span></summary>
        <p class="muted">Empty boundaries mean all resources already allowed by the coarse permission. A scope never grants a missing permission.</p>
        <label class="check-inline"><input type="checkbox" data-v029-cloud="${app.id}" ${scope.cloud_allowed !== false ? 'checked' : ''}> Allow configured cloud-backed inference</label>
        <div class="form-grid">
          <label>Memory key prefixes<input data-v029-memory="${app.id}" value="${esc((scope.memory_key_prefixes || []).join(', '))}" placeholder="vp3:, work:"></label>
          <label>Knowledge kinds<input data-v029-knowledge="${app.id}" value="${esc((scope.knowledge_kinds || []).join(', '))}" placeholder="note, document"></label>
          <label>Tool keys<input data-v029-tools="${app.id}" value="${esc((scope.tool_names || []).join(', '))}" placeholder="memory.list, knowledge.search"></label>
          <label>Plugin keys<input data-v029-plugins="${app.id}" value="${esc((scope.plugin_keys || []).join(', '))}" placeholder="calendar, crm"></label>
        </div>
        <div class="form-actions"><span class="muted">Private boundary values remain local to this owner control surface.</span><button class="button secondary" type="button" data-v029-save-scope="${app.id}">Save boundaries</button></div>
      </details>
      <details class="app-control-details" data-v029-activity-details="${app.id}">
        <summary>Connection activity <span>Safe audit view</span></summary>
        <div class="app-activity-list" data-v029-activity="${app.id}">${renderActivityPreview(app.recent_activity)}</div>
        <div class="form-actions"><span class="muted">Prompt text, Memory contents, Knowledge contents, tokens, and private scope values are not shown here.</span><button class="text-button" type="button" data-v029-load-activity="${app.id}">Load more</button></div>
      </details>
    </article>`;
  }

  function renderApps() {
    const target = document.getElementById('appsList');
    if (!target) return;
    target.innerHTML = data.apps?.length ? data.apps.map(appMarkup).join('') : '<div class="panel empty-state">No applications connected yet.</div>';
  }

  function render() {
    renderStats();
    renderPending();
    renderApps();
  }

  async function refreshConnectedAppsV029() {
    if (loading) return;
    loading = true;
    try {
      data = await request('/api/v1/control/connected-apps');
      if (typeof window.state === 'object') window.state.apps = data.apps;
      render();
    } finally {
      loading = false;
    }
  }

  async function savePermissions(appId, button) {
    const app = data.apps.find(item => String(item.id) === String(appId));
    if (!app) return;
    const inputs = [...document.querySelectorAll(`[data-v029-permission="${appId}"]`)];
    const changed = inputs.filter(input => input.checked !== (input.dataset.original === '1'));
    if (!changed.length) return notify('No permission changes to save.');
    button.disabled = true;
    try {
      for (const input of changed) {
        await request(`/api/v1/control/apps/${appId}/permission`, {method:'PUT', body:JSON.stringify({permission:input.dataset.permission, allowed:Boolean(input.checked)})});
      }
      await refreshConnectedAppsV029();
      notify('Application permissions updated.');
    } catch (err) {
      notify(err.message, true);
      await refreshConnectedAppsV029().catch(() => {});
    } finally { button.disabled = false; }
  }

  async function saveScope(appId, button) {
    button.disabled = true;
    try {
      await request(`/api/v1/control/apps/${appId}/scope`, {method:'PUT', body:JSON.stringify({
        cloud_allowed: Boolean(document.querySelector(`[data-v029-cloud="${appId}"]`)?.checked),
        memory_key_prefixes: parseList(document.querySelector(`[data-v029-memory="${appId}"]`)?.value),
        knowledge_kinds: parseList(document.querySelector(`[data-v029-knowledge="${appId}"]`)?.value),
        tool_names: parseList(document.querySelector(`[data-v029-tools="${appId}"]`)?.value),
        plugin_keys: parseList(document.querySelector(`[data-v029-plugins="${appId}"]`)?.value),
      })});
      await refreshConnectedAppsV029();
      notify('Private resource boundaries updated.');
    } catch (err) { notify(err.message, true); }
    finally { button.disabled = false; }
  }

  async function loadActivity(appId, button) {
    button.disabled = true;
    try {
      const result = await request(`/api/v1/control/connected-apps/${appId}/activity?limit=50`);
      const target = document.querySelector(`[data-v029-activity="${appId}"]`);
      if (target) target.innerHTML = renderActivityPreview(result.items || []);
    } catch (err) { notify(err.message, true); }
    finally { button.disabled = false; }
  }

  document.addEventListener('submit', async event => {
    if (event.target.id !== 'pairingFormV029') return;
    event.preventDefault();
    const code = document.getElementById('pairingCodeV029')?.value || '';
    try {
      const result = await request('/api/v1/pairing/approve', {method:'POST', body:JSON.stringify({code})});
      const target = document.getElementById('pairingResultV029');
      target?.classList.remove('hidden');
      if (target) target.innerHTML = result.delivery === 'claim_token'
        ? `<strong>Pairing approved.</strong><span>Return to ${esc(result.app_key)}. It can complete the connection automatically.</span>`
        : `<strong>Legacy pairing approved.</strong><span>Copy the one-time token now: ${esc(result.token || '')}</span>`;
      const input = document.getElementById('pairingCodeV029'); if (input) input.value = '';
      await refreshConnectedAppsV029();
      notify(`${result.app_key} paired successfully.`);
    } catch (err) { notify(err.message, true); }
  });

  document.addEventListener('click', async event => {
    if (event.target.closest('#refreshConnectedAppsV029')) return refreshConnectedAppsV029().catch(err => notify(err.message, true));
    const permissions = event.target.closest('[data-v029-save-permissions]');
    if (permissions) return savePermissions(permissions.dataset.v029SavePermissions, permissions);
    const scope = event.target.closest('[data-v029-save-scope]');
    if (scope) return saveScope(scope.dataset.v029SaveScope, scope);
    const activity = event.target.closest('[data-v029-load-activity]');
    if (activity) return loadActivity(activity.dataset.v029LoadActivity, activity);
    const repair = event.target.closest('[data-v029-repair]');
    if (repair) {
      const app = data.apps.find(item => String(item.id) === String(repair.dataset.v029Repair));
      if (!confirm(`Revoke ${app?.name || 'this application'} now and require it to pair again? Existing HomeServer resource boundaries will be preserved.`)) return;
      repair.disabled = true;
      try {
        const result = await request(`/api/v1/control/connected-apps/${repair.dataset.v029Repair}/require-repair`, {method:'POST'});
        await refreshConnectedAppsV029();
        notify(result.message || 'Application must pair again.');
      } catch (err) { notify(err.message, true); }
      finally { repair.disabled = false; }
    }
  });

  document.addEventListener('change', async event => {
    const status = event.target.closest('[data-v029-status]');
    if (!status) return;
    try {
      await request(`/api/v1/control/apps/${status.dataset.v029Status}`, {method:'PATCH', body:JSON.stringify({status:status.value})});
      await refreshConnectedAppsV029();
      notify('Application status updated.');
    } catch (err) { notify(err.message, true); await refreshConnectedAppsV029().catch(() => {}); }
  });

  ensureStyle();
  ensureLayout();
  window.loadApps = refreshConnectedAppsV029;
  window.loadConnectedAppsV029 = refreshConnectedAppsV029;
  refreshConnectedAppsV029().catch(err => notify(err.message, true));
})();
