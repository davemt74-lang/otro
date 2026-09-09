(() => {
  'use strict';

  const view = document.getElementById('view-apps');
  if (!view) return;

  const esc = (value = '') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let data = null;
  let loading = false;
  let scheduled = false;

  function ensureStyle() {
    if (document.querySelector('link[data-connected-apps-collaboration-v030]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/connected-apps-collaboration-v030.css';
    link.dataset.connectedAppsCollaborationV030 = '1';
    document.head.appendChild(link);
  }

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

  function hasPermission(app, permission) {
    return Boolean((app.permissions || []).find(item => item.permission === permission && item.allowed));
  }

  function grantFor(consumerId, sourceId) {
    return (data?.collaboration?.grants || []).find(item =>
      String(item.consumer_app_id) === String(consumerId) && String(item.source_app_id) === String(sourceId)
    );
  }

  function sourceRow(consumer, source) {
    const grant = grantFor(consumer.id, source.id) || {};
    const memoryReady = hasPermission(consumer, 'memory.read') && hasPermission(source, 'memory.read');
    const knowledgeReady = hasPermission(consumer, 'knowledge.search') && hasPermission(source, 'knowledge.search');
    const sourceActive = source.status === 'active';
    const eligibility = [];
    if (!sourceActive) eligibility.push(`source ${source.status}`);
    if (!memoryReady) eligibility.push('Memory permission incomplete');
    if (!knowledgeReady) eligibility.push('Knowledge permission incomplete');
    const note = eligibility.length ? eligibility.join(' · ') : 'Eligible now';

    return `<div class="collaboration-source-row" data-v030-source-row="${source.id}">
      <div class="collaboration-source-name"><strong>${esc(source.name)}</strong><span>${esc(source.app_key)} · ${esc(note)}</span></div>
      <label class="collaboration-toggle"><input type="checkbox" data-v030-enabled ${grant.enabled ? 'checked' : ''}> Enabled</label>
      <label class="collaboration-toggle ${memoryReady ? '' : 'unavailable'}"><input type="checkbox" data-v030-memory ${grant.memory_allowed ? 'checked' : ''} ${memoryReady ? '' : 'disabled'}> Memory</label>
      <label class="collaboration-toggle ${knowledgeReady ? '' : 'unavailable'}"><input type="checkbox" data-v030-knowledge ${grant.knowledge_allowed ? 'checked' : ''} ${knowledgeReady ? '' : 'disabled'}> Knowledge</label>
      <button class="button secondary" type="button" data-v030-save="${consumer.id}" data-v030-source="${source.id}">Save</button>
    </div>`;
  }

  function renderConsumer(consumer) {
    const card = document.querySelector(`[data-v029-app-card="${consumer.id}"]`);
    if (!card) return;
    let panel = card.querySelector('[data-v030-collaboration]');
    if (!panel) {
      panel = document.createElement('details');
      panel.className = 'app-control-details collaboration-details-v030';
      panel.dataset.v030Collaboration = '1';
      card.appendChild(panel);
    }
    const sources = (data?.apps || []).filter(item => String(item.id) !== String(consumer.id));
    panel.innerHTML = `
      <summary>Cross-wrapper collaboration <span>Agent context only</span></summary>
      <div class="collaboration-boundary-v030"><strong>Read-only sharing:</strong> an enabled source may contribute only the selected Memory or Knowledge to this app's delegated HomeServer Agent. Direct APIs, credentials, Contacts, Tools, Plugins, cloud access and writes remain isolated.</div>
      ${sources.length ? `<div class="collaboration-source-list">${sources.map(source => sourceRow(consumer, source)).join('')}</div>` : '<div class="empty-state compact">Pair another wrapper before creating a collaboration grant.</div>'}
      <p class="muted collaboration-foot-v030">Source resource boundaries remain authoritative. A paused/revoked source or a missing permission disables its contribution immediately.</p>`;
  }

  function render() {
    if (!data) return;
    (data.apps || []).forEach(renderConsumer);
  }

  async function load() {
    if (loading) return;
    loading = true;
    try {
      data = await request('/api/v1/control/connected-apps');
      render();
    } catch (err) {
      notify(err.message, true);
    } finally {
      loading = false;
    }
  }

  function scheduleLoad() {
    if (scheduled) return;
    scheduled = true;
    setTimeout(() => {
      scheduled = false;
      const cards = [...document.querySelectorAll('[data-v029-app-card]')];
      if (cards.some(card => !card.querySelector('[data-v030-collaboration]'))) load();
    }, 0);
  }

  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-v030-save]');
    if (!button) return;
    const consumerId = button.dataset.v030Save;
    const sourceId = button.dataset.v030Source;
    const row = button.closest('[data-v030-source-row]');
    if (!row) return;
    button.disabled = true;
    try {
      await request(`/api/v1/control/connected-apps/${consumerId}/collaboration/${sourceId}`, {
        method: 'PUT',
        body: JSON.stringify({
          enabled: Boolean(row.querySelector('[data-v030-enabled]')?.checked),
          memory_allowed: Boolean(row.querySelector('[data-v030-memory]')?.checked),
          knowledge_allowed: Boolean(row.querySelector('[data-v030-knowledge]')?.checked),
        }),
      });
      await load();
      notify('Cross-wrapper collaboration updated.');
    } catch (err) {
      notify(err.message, true);
    } finally {
      button.disabled = false;
    }
  });

  ensureStyle();
  const appsList = document.getElementById('appsList');
  if (appsList) new MutationObserver(scheduleLoad).observe(appsList, {childList:true, subtree:true});
  load();
})();
