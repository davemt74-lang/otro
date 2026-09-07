(() => {
  'use strict';

  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
  const fmt = value => value ? new Date(value).toLocaleString() : 'Never';

  async function api(path, options = {}) {
    const headers = {...(options.headers || {})};
    if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, {...options, headers});
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }

  function ensureStyles() {
    if (document.querySelector('link[href="/assets/cognition.css"]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/cognition.css';
    document.head.appendChild(link);
  }

  function ensureView() {
    if (byId('view-cognition')) return;
    const main = document.querySelector('.main');
    if (!main) return;
    const section = document.createElement('section');
    section.className = 'view';
    section.id = 'view-cognition';
    section.innerHTML = `
      <div class="section-intro split cognition-intro">
        <div><p class="eyebrow">SHARED COGNITIVE RUNTIME</p><h2>Cognition & Plugins</h2><p>One local event bus, awareness layer and memory-consolidation path shared across every permitted HomeServer application.</p></div>
        <button class="button secondary" id="processCognition" type="button">Process now</button>
      </div>
      <div class="stats-grid cognition-stats">
        <article class="stat"><span>Events</span><strong id="cognitionEventCount">0</strong><small>canonical timeline</small></article>
        <article class="stat"><span>Awareness</span><strong id="cognitionAwarenessCount">0</strong><small>open signals</small></article>
        <article class="stat"><span>Memory candidates</span><strong id="cognitionMemoryCount">0</strong><small>awaiting review</small></article>
        <article class="stat"><span>Plugins</span><strong id="cognitionPluginCount">0</strong><small>active manifests</small></article>
      </div>
      <div class="cognition-security"><strong>Boundary:</strong> apps publish observations into the event ledger. They do not silently become permanent memory. Cross-app summaries require <code>awareness.read</code>; plugin Agent tools are read-only in v0.17 and require a loaded handler plus every declared permission.</div>
      <div class="two-col cognition-columns">
        <section>
          <div class="panel-head"><div><p class="eyebrow">CURRENT SIGNALS</p><h3>Multi-app awareness</h3></div></div>
          <div id="cognitionAwareness" class="cognition-list"><div class="panel empty-state">No awareness items yet.</div></div>
        </section>
        <section>
          <div class="panel-head"><div><p class="eyebrow">CONSOLIDATION</p><h3>Memory candidates</h3></div></div>
          <div id="cognitionMemoryCandidates" class="cognition-list"><div class="panel empty-state">No pending memory candidates.</div></div>
        </section>
      </div>
      <section class="cognition-section">
        <div class="panel-head"><div><p class="eyebrow">MODULAR CAPABILITIES</p><h3>Plugin registry</h3></div><button class="button secondary" id="showPluginManifest" type="button">Register manifest</button></div>
        <form id="pluginManifestForm" class="panel cognition-plugin-form hidden">
          <label>Plugin manifest JSON<textarea id="pluginManifestJson" rows="12" spellcheck="false" placeholder='{"plugin_key":"example.plugin","name":"Example Plugin","version":"1.0.0","produces_events":["example.updated"],"subscriptions":[],"tools":[]}'></textarea></label>
          <label class="check-inline"><input id="pluginTrusted" type="checkbox"> Mark packaged plugin as trusted</label>
          <div class="form-actions"><button class="button secondary" id="cancelPluginManifest" type="button">Cancel</button><button class="button primary" type="submit">Register plugin</button></div>
          <p class="muted">Registering a manifest never executes arbitrary downloaded code. Event/tool handlers must already be packaged and explicitly bound inside HomeServer.</p>
        </form>
        <div id="cognitionPlugins" class="cognition-plugin-grid"><div class="panel empty-state">No plugins registered yet.</div></div>
      </section>
      <section class="cognition-section">
        <div class="panel-head"><div><p class="eyebrow">CANONICAL TIMELINE</p><h3>Recent cognitive events</h3></div></div>
        <div class="panel table-wrap"><table><thead><tr><th>Time</th><th>Source</th><th>Event</th><th>Entity</th><th>Importance</th><th>Summary</th></tr></thead><tbody id="cognitionEvents"><tr><td colspan="6">No cognitive events yet.</td></tr></tbody></table></div>
      </section>`;
    main.appendChild(section);
  }

  function ensureMenu() {
    const menu = byId('sidebarUserMenu');
    if (!menu || menu.querySelector('[data-view="cognition"]')) return Boolean(menu);
    const tools = menu.querySelector('[data-view="tools"]');
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.view = 'cognition';
    button.textContent = 'Cognition & Plugins';
    if (tools) tools.insertAdjacentElement('afterend', button);
    else menu.appendChild(button);
    return true;
  }

  function sourceList(item) {
    const sources = Array.isArray(item.source_apps) ? item.source_apps : [];
    return sources.length ? sources.join(', ') : 'HomeServer';
  }

  function renderAwareness(items) {
    const node = byId('cognitionAwareness');
    if (!node) return;
    node.innerHTML = items?.length ? items.map(item => `
      <article class="panel cognition-card">
        <div class="cognition-card-head"><div><span class="tag">${esc(item.event_type)}</span><h4>${esc(item.title)}</h4></div><strong>${Math.round(Number(item.importance || 0) * 100)}%</strong></div>
        <p>${esc(item.summary)}</p>
        <div class="item-meta"><span>${Number(item.occurrence_count || 1)} occurrence${Number(item.occurrence_count || 1) === 1 ? '' : 's'}</span><span>${esc(sourceList(item))}</span><span>${esc(fmt(item.last_seen_at))}</span></div>
        <div class="cognition-actions"><button class="text-button" data-awareness-status="resolved" data-awareness-id="${item.id}" type="button">Resolve</button><button class="text-button danger" data-awareness-status="dismissed" data-awareness-id="${item.id}" type="button">Dismiss</button></div>
      </article>`).join('') : '<div class="panel empty-state">No open awareness items yet.</div>';
  }

  function renderCandidates(items) {
    const node = byId('cognitionMemoryCandidates');
    if (!node) return;
    node.innerHTML = items?.length ? items.map(item => `
      <article class="panel cognition-card">
        <div class="cognition-card-head"><div><span class="tag">${esc(item.memory_type)}</span><h4>${esc(item.memory_key || 'Memory candidate')}</h4></div><strong>${Math.round(Number(item.confidence || 0) * 100)}%</strong></div>
        <p>${esc(item.content)}</p>
        <div class="item-meta"><span>importance ${Number(item.importance || 0).toFixed(2)}</span><span>${esc(item.source_app_key || 'HomeServer')}</span><span>${esc(fmt(item.created_at))}</span></div>
        <div class="cognition-actions"><button class="button secondary" data-memory-decision="rejected" data-memory-id="${item.id}" type="button">Reject</button><button class="button primary" data-memory-decision="accepted" data-memory-id="${item.id}" type="button">Add to Memory</button></div>
      </article>`).join('') : '<div class="panel empty-state">No pending memory candidates.</div>';
  }

  function renderPlugins(items) {
    const node = byId('cognitionPlugins');
    if (!node) return;
    node.innerHTML = items?.length ? items.map(item => {
      const manifest = item.manifest || {};
      const subscriptions = manifest.subscriptions?.length || 0;
      const tools = manifest.tools?.length || 0;
      const produced = manifest.produces_events?.length || 0;
      return `<article class="panel cognition-plugin-card"><div class="cognition-card-head"><div><span class="tag">${esc(item.status)}</span><h4>${esc(item.name)}</h4></div><strong>v${esc(item.version)}</strong></div><p>${esc(item.description || 'No description.')}</p><div class="item-meta"><span>${esc(item.plugin_key)}</span><span>${produced} event type${produced === 1 ? '' : 's'}</span><span>${subscriptions} subscription${subscriptions === 1 ? '' : 's'}</span><span>${tools} read tool${tools === 1 ? '' : 's'}</span>${item.trusted ? '<span>trusted packaged handler</span>' : ''}</div><label class="plugin-status-label">Status<select data-plugin-status="${esc(item.plugin_key)}"><option value="active" ${item.status==='active'?'selected':''}>Active</option><option value="paused" ${item.status==='paused'?'selected':''}>Paused</option><option value="revoked" ${item.status==='revoked'?'selected':''}>Revoked</option></select></label></article>`;
    }).join('') : '<div class="panel empty-state">No plugins registered yet.</div>';
  }

  function renderEvents(items) {
    const node = byId('cognitionEvents');
    if (!node) return;
    node.innerHTML = items?.length ? items.map(item => `<tr><td>${esc(fmt(item.occurred_at))}</td><td>${esc(item.source_app_key)}</td><td>${esc(item.event_type)}</td><td>${esc([item.entity_type,item.entity_key].filter(Boolean).join(' · ') || '—')}</td><td>${Math.round(Number(item.importance || 0) * 100)}%</td><td>${esc(item.summary)}</td></tr>`).join('') : '<tr><td colspan="6">No cognitive events yet.</td></tr>';
  }

  async function loadCognition() {
    ensureStyles();
    ensureView();
    ensureMenu();
    if (byId('pageTitle')) byId('pageTitle').textContent = 'Cognition & Plugins';
    const [data, events] = await Promise.all([
      api('/api/v1/control/cognition'),
      api('/api/v1/control/cognition/events?limit=100'),
    ]);
    const runtime = data.runtime || {};
    if (byId('cognitionEventCount')) byId('cognitionEventCount').textContent = Number(runtime.events || 0).toLocaleString();
    if (byId('cognitionAwarenessCount')) byId('cognitionAwarenessCount').textContent = Number(runtime.open_awareness || 0).toLocaleString();
    if (byId('cognitionMemoryCount')) byId('cognitionMemoryCount').textContent = Number(runtime.memory_candidates || 0).toLocaleString();
    if (byId('cognitionPluginCount')) byId('cognitionPluginCount').textContent = Number(runtime.plugins || 0).toLocaleString();
    renderAwareness(data.awareness || []);
    renderCandidates(data.memory_candidates || []);
    renderPlugins(data.plugins || []);
    renderEvents(events.items || []);
  }

  window.loadHomeServerCognition = loadCognition;

  document.addEventListener('click', async event => {
    const view = event.target.closest('[data-view="cognition"]');
    if (view) {
      setTimeout(() => loadCognition().catch(console.error), 0);
      return;
    }
    if (event.target.id === 'processCognition') {
      const button = event.target;
      button.disabled = true;
      try { await api('/api/v1/control/cognition/process', {method:'POST'}); await loadCognition(); }
      catch (err) { alert(err.message); }
      finally { button.disabled = false; }
      return;
    }
    if (event.target.id === 'showPluginManifest') {
      byId('pluginManifestForm')?.classList.remove('hidden');
      byId('pluginManifestJson')?.focus();
      return;
    }
    if (event.target.id === 'cancelPluginManifest') {
      byId('pluginManifestForm')?.classList.add('hidden');
      return;
    }
    const awareness = event.target.closest('[data-awareness-status]');
    if (awareness) {
      try {
        await api(`/api/v1/control/cognition/awareness/${awareness.dataset.awarenessId}`, {method:'PATCH', body:JSON.stringify({status:awareness.dataset.awarenessStatus})});
        await loadCognition();
      } catch (err) { alert(err.message); }
      return;
    }
    const memory = event.target.closest('[data-memory-decision]');
    if (memory) {
      try {
        await api(`/api/v1/control/cognition/memory-candidates/${memory.dataset.memoryId}`, {method:'POST', body:JSON.stringify({decision:memory.dataset.memoryDecision})});
        await loadCognition();
      } catch (err) { alert(err.message); }
      return;
    }
  });

  document.addEventListener('change', async event => {
    const select = event.target.closest('[data-plugin-status]');
    if (!select) return;
    try { await api(`/api/v1/control/plugins/${encodeURIComponent(select.dataset.pluginStatus)}`, {method:'PATCH', body:JSON.stringify({status:select.value})}); await loadCognition(); }
    catch (err) { alert(err.message); }
  });

  document.addEventListener('submit', async event => {
    if (event.target.id !== 'pluginManifestForm') return;
    event.preventDefault();
    try {
      const manifest = JSON.parse(byId('pluginManifestJson')?.value || '{}');
      await api('/api/v1/control/plugins', {method:'POST', body:JSON.stringify({manifest, trusted:Boolean(byId('pluginTrusted')?.checked)})});
      event.target.reset();
      event.target.classList.add('hidden');
      await loadCognition();
    } catch (err) { alert(err.message); }
  });

  window.addEventListener('hashchange', () => {
    if (location.hash === '#cognition' && typeof window.openView === 'function') {
      ensureView();
      window.openView('cognition');
      loadCognition().catch(console.error);
    }
  });

  function boot() {
    ensureStyles();
    ensureView();
    if (!ensureMenu()) {
      const observer = new MutationObserver(() => {
        if (ensureMenu()) observer.disconnect();
      });
      observer.observe(document.body, {childList:true, subtree:true});
    }
    if (location.hash === '#cognition' && typeof window.openView === 'function') {
      window.openView('cognition');
      loadCognition().catch(console.error);
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once:true});
  else boot();
})();
