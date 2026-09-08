// HomeServer v0.26 capability scope controls remain as the backward-compatible fallback.
(() => {
  const parseList = (value) => String(value || '')
    .split(',')
    .map(item => item.trim())
    .filter((item, index, list) => item && list.indexOf(item) === index)
    .slice(0, 32);

  const ensureConnectedAppsDenyV029 = () => {
    if (document.querySelector('script[data-homeserver-connected-apps-deny-v029]')) return;
    const script = document.createElement('script');
    script.src = '/assets/connected-apps-deny-v029.js';
    script.dataset.homeserverConnectedAppsDenyV029 = '1';
    script.async = false;
    document.head.appendChild(script);
  };

  const ensureConnectedAppsCollaborationV030 = () => {
    if (document.querySelector('script[data-homeserver-connected-apps-collaboration-v030]')) return;
    const script = document.createElement('script');
    script.src = '/assets/connected-apps-collaboration-v030.js';
    script.dataset.homeserverConnectedAppsCollaborationV030 = '1';
    script.async = false;
    document.head.appendChild(script);
  };

  const ensureConnectedAppsExtensions = () => {
    ensureConnectedAppsDenyV029();
    ensureConnectedAppsCollaborationV030();
  };

  const ensureConnectedAppsV029 = () => {
    if (document.querySelector('script[data-homeserver-connected-apps-v029]')) {
      ensureConnectedAppsExtensions();
      return;
    }
    const script = document.createElement('script');
    script.src = '/assets/connected-apps-v029.js';
    script.dataset.homeserverConnectedAppsV029 = '1';
    script.async = false;
    script.addEventListener('load', ensureConnectedAppsExtensions, {once:true});
    document.head.appendChild(script);
  };

  const render = () => {
    if (document.getElementById('view-apps')?.dataset.connectedAppsV029 === '1') return;
    if (typeof state === 'undefined' || !Array.isArray(state.apps)) return;
    for (const app of state.apps) {
      const permissionInput = document.querySelector(`[data-app-permission="${app.id}"]`);
      const card = permissionInput?.closest('.item-card');
      if (!card || card.querySelector(`[data-app-scope-panel="${app.id}"]`)) continue;
      const scope = app.scope || {};
      const panel = document.createElement('div');
      panel.className = 'app-scope-panel';
      panel.dataset.appScopePanel = String(app.id);
      panel.innerHTML = `
        <div class="panel-head"><div><p class="eyebrow">CAPABILITY SCOPE</p><h4>Resource boundaries</h4></div></div>
        <p class="muted">Empty lists mean all resources already permitted above. Scopes only narrow permissions; they never grant a capability.</p>
        <label class="check-inline"><input type="checkbox" data-scope-cloud="${app.id}" ${scope.cloud_allowed !== false ? 'checked' : ''}> Allow configured cloud-backed inference</label>
        <div class="form-grid">
          <label>Memory key prefixes<input data-scope-memory="${app.id}" value="${esc((scope.memory_key_prefixes || []).join(', '))}" placeholder="vp3:, work:"></label>
          <label>Knowledge kinds<input data-scope-knowledge="${app.id}" value="${esc((scope.knowledge_kinds || []).join(', '))}" placeholder="note, document"></label>
          <label>Tool keys<input data-scope-tools="${app.id}" value="${esc((scope.tool_names || []).join(', '))}" placeholder="memory.list, knowledge.search"></label>
          <label>Plugin keys<input data-scope-plugins="${app.id}" value="${esc((scope.plugin_keys || []).join(', '))}" placeholder="calendar, crm"></label>
        </div>
        <div class="form-actions"><span class="muted">Enforced locally by HomeServer.</span><button class="button secondary" type="button" data-save-app-scope="${app.id}">Save scope</button></div>`;
      const body = card.firstElementChild;
      if (body) body.append(panel);
      else card.append(panel);
    }
  };

  const appsList = document.getElementById('appsList');
  if (appsList) new MutationObserver(render).observe(appsList, {childList: true, subtree: true});
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-save-app-scope]');
    if (!button) return;
    const id = button.dataset.saveAppScope;
    button.disabled = true;
    try {
      const result = await api(`/api/v1/control/apps/${id}/scope`, {
        method: 'PUT',
        body: JSON.stringify({
          cloud_allowed: Boolean(document.querySelector(`[data-scope-cloud="${id}"]`)?.checked),
          memory_key_prefixes: parseList(document.querySelector(`[data-scope-memory="${id}"]`)?.value),
          knowledge_kinds: parseList(document.querySelector(`[data-scope-knowledge="${id}"]`)?.value),
          tool_names: parseList(document.querySelector(`[data-scope-tools="${id}"]`)?.value),
          plugin_keys: parseList(document.querySelector(`[data-scope-plugins="${id}"]`)?.value),
        }),
      });
      const app = state.apps.find(item => String(item.id) === String(id));
      if (app) app.scope = result.scope;
      flash('Application capability scope updated.');
    } catch (err) {
      flash(err.message, true);
    } finally {
      button.disabled = false;
    }
  });
  render();
  ensureConnectedAppsV029();
})();
