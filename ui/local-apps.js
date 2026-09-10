(() => {
  'use strict';

  const byteLabel = (value) => {
    const bytes = Number(value || 0);
    if (!bytes) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    let n = bytes;
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
    const digits = i === 0 ? 0 : n >= 10 ? 1 : 2;
    return `${n.toFixed(digits)} ${units[i]}`;
  };

  const html = (value = '') => String(value).replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));

  const state = { loading: false, data: null, filter: 'all' };

  function ensureWorkspace() {
    if (document.getElementById('view-local-apps')) return;

    const connectedNav = document.querySelector('.nav-item[data-view="apps"]');
    if (connectedNav && !document.querySelector('.nav-item[data-view="local-apps"]')) {
      const button = document.createElement('button');
      button.className = 'nav-item';
      button.dataset.view = 'local-apps';
      button.textContent = 'Local Apps';
      connectedNav.parentNode.insertBefore(button, connectedNav);
    }

    const connectedView = document.getElementById('view-apps');
    if (!connectedView) return;
    const section = document.createElement('section');
    section.className = 'view';
    section.id = 'view-local-apps';
    section.innerHTML = `
      <div class="section-intro split local-apps-intro">
        <div>
          <p class="eyebrow">LOCAL CAPABILITY STORE</p>
          <h2>Local Apps</h2>
          <p>Install trusted speech, vision and AI capabilities directly into this HomeServer. Packages are staged locally and verified before activation.</p>
        </div>
        <div class="local-apps-summary" aria-live="polite">
          <strong id="localAppsInstalledCount">0</strong><span>installed</span>
        </div>
      </div>
      <div class="local-apps-security">
        <strong>One-click, fail-closed:</strong>
        HomeServer uses an embedded reviewed catalog, HTTPS-only sources, SHA-256 verification, safe archive extraction and rollback on failed updates. Apps install only inside HomeServer's private data directory.
      </div>
      <div class="local-apps-toolbar" role="group" aria-label="Local Apps filter">
        <button class="button secondary active" type="button" data-local-app-filter="all">All</button>
        <button class="button secondary" type="button" data-local-app-filter="installed">Installed</button>
        <button class="button secondary" type="button" data-local-app-filter="available">Available</button>
        <span id="localAppsPlatform" class="muted"></span>
      </div>
      <div id="localAppsCatalog" class="local-apps-grid">
        <div class="panel empty-state">Loading Local Apps…</div>
      </div>`;
    connectedView.parentNode.insertBefore(section, connectedView);
  }

  function requirementBadges(item) {
    const req = item.requirements || {};
    const badges = [];
    if (req.gpu === false) badges.push('No GPU required');
    if (req.python === false) badges.push('No Python required');
    if (req.wasm) badges.push('WASM');
    if (Array.isArray(req.os) && req.os.length) badges.push(req.os.map(v => v === 'darwin' ? 'macOS' : v).join(' / '));
    if (Array.isArray(req.arch) && req.arch.length) badges.push(req.arch.join(' / '));
    return badges.map((badge) => `<span class="local-app-badge">${html(badge)}</span>`).join('');
  }

  function capabilityBadges(item) {
    return (item.capabilities || []).map((capability) => `<span class="local-capability">${html(capability)}</span>`).join('');
  }

  function actionMarkup(item) {
    const installed = item.installed;
    if (!item.supported && !installed) {
      return `<button class="button secondary" type="button" disabled>Unavailable</button>`;
    }
    if (installed?.status === 'installing' || installed?.status === 'updating') {
      return `<button class="button secondary" type="button" disabled>${installed.status === 'updating' ? 'Updating…' : 'Installing…'}</button>`;
    }
    if (installed?.status === 'installed') {
      const update = item.update_available
        ? `<button class="button primary" type="button" data-local-app-action="update" data-app-key="${html(item.key)}">Update</button>`
        : `<span class="local-app-current">Up to date</span>`;
      return `${update}<button class="text-button danger" type="button" data-local-app-action="uninstall" data-app-key="${html(item.key)}">Uninstall</button>`;
    }
    const label = installed?.status === 'failed' ? 'Retry install' : 'Install';
    return `<button class="button primary" type="button" data-local-app-action="install" data-app-key="${html(item.key)}">${label}</button>`;
  }

  function appCard(item) {
    const installed = item.installed;
    const statusClass = installed?.status === 'installed' ? 'installed' : installed?.status === 'failed' ? 'failed' : 'available';
    const statusText = installed?.status === 'installed'
      ? `Installed ${html(installed.installed_version || '')}`
      : installed?.status === 'failed'
        ? 'Install needs attention'
        : item.supported ? 'Available' : 'Not supported on this device';
    const detail = installed?.status === 'installed'
      ? `${byteLabel(installed.disk_bytes)} verified download · local capability registered`
      : `${byteLabel(item.download_bytes)} download · ${html(item.integrity || 'verified')}`;
    const warning = !item.supported && item.support_reason
      ? `<div class="local-app-warning">${html(item.support_reason)}</div>`
      : installed?.last_error
        ? `<div class="local-app-warning">Last attempt: ${html(installed.last_error)}</div>`
        : '';
    const defaultVoice = item.default_voice ? `<span>Default voice: <strong>${html(item.default_voice)}</strong></span>` : '';
    return `
      <article class="panel local-app-card" data-local-app-card data-installed="${installed?.status === 'installed' ? '1' : '0'}" data-supported="${item.supported ? '1' : '0'}">
        <div class="local-app-card-head">
          <div class="local-app-icon" aria-hidden="true">${item.key === 'piper-tts' ? 'TTS' : item.key === 'whisper-stt' ? 'STT' : 'APP'}</div>
          <div class="local-app-heading"><div><span class="local-app-status ${statusClass}"></span><span>${statusText}</span></div><h3>${html(item.name)}</h3><p>${html(item.description)}</p></div>
        </div>
        <div class="local-app-badges">${requirementBadges(item)}</div>
        <div class="local-app-details"><span>${detail}</span><span>Runtime: <strong>${html(item.runtime || 'Local')}</strong></span>${defaultVoice}<span>Source: ${html(item.source_label || 'HomeServer catalog')}</span><span>License: ${html(item.license || 'See upstream')}</span></div>
        ${warning}
        <div class="local-capabilities">${capabilityBadges(item)}</div>
        <div class="local-app-actions">${actionMarkup(item)}</div>
      </article>`;
  }

  function render() {
    const container = document.getElementById('localAppsCatalog');
    if (!container || !state.data) return;
    const packages = (state.data.packages || []).filter((item) => {
      if (state.filter === 'installed') return item.installed?.status === 'installed';
      if (state.filter === 'available') return item.supported && item.installed?.status !== 'installed';
      return true;
    });
    document.getElementById('localAppsInstalledCount').textContent = String(state.data.installed_count || 0);
    const platform = state.data.platform || {};
    document.getElementById('localAppsPlatform').textContent = `${platform.os || 'unknown'} · ${platform.arch || 'unknown'} · catalog ${state.data.catalog_version || ''}`;
    container.innerHTML = packages.length ? packages.map(appCard).join('') : '<div class="panel empty-state">No Local Apps match this filter.</div>';
  }

  async function load() {
    if (state.loading) return;
    state.loading = true;
    const container = document.getElementById('localAppsCatalog');
    if (container && !state.data) container.innerHTML = '<div class="panel empty-state">Loading Local Apps…</div>';
    try {
      state.data = await window.api('/api/v1/control/local-apps');
      render();
    } catch (error) {
      if (container) container.innerHTML = `<div class="panel empty-state local-app-error">${html(error.message || 'Unable to load Local Apps.')}</div>`;
      throw error;
    } finally {
      state.loading = false;
    }
  }

  async function runAction(button) {
    const action = button.dataset.localAppAction;
    const key = button.dataset.appKey;
    const item = state.data?.packages?.find((entry) => entry.key === key);
    if (!item) return;
    if (action === 'uninstall') {
      if (!window.confirm(`Uninstall ${item.name}? This removes only its managed Local App files. HomeServer data, memory and conversations are not removed.`)) return;
    } else if (action === 'install') {
      if (!window.confirm(`Install ${item.name}? HomeServer will download ${byteLabel(item.download_bytes)}, verify every artifact, and activate it only after all checks pass.`)) return;
    }

    const original = button.textContent;
    button.disabled = true;
    button.textContent = action === 'uninstall' ? 'Removing…' : action === 'update' ? 'Updating…' : 'Installing…';
    try {
      const method = action === 'uninstall' ? 'DELETE' : 'POST';
      const suffix = action === 'uninstall' ? '' : `/${action}`;
      const result = await window.api(`/api/v1/control/local-apps/${encodeURIComponent(key)}${suffix}`, { method });
      await load(true);
      window.flash(
        result.changed === false
          ? `${item.name} is already current.`
          : action === 'uninstall' ? `${item.name} uninstalled.` : `${item.name} installed and registered.`
      );
    } catch (error) {
      window.flash(error.message || `${item.name} operation failed safely.`, true);
      await load(true).catch(() => {});
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  }

  // Force reload is intentionally represented by clearing the cached payload;
  // install/update/uninstall requests remain synchronous so the UI cannot claim
  // success before verification and activation are complete.
  const originalLoad = load;
  load = async (force = false) => { // eslint-disable-line no-func-assign
    if (force) state.data = null;
    return originalLoad();
  };

  ensureWorkspace();
  window.loadHomeServerLocalApps = load;

  document.addEventListener('click', (event) => {
    const filter = event.target.closest('[data-local-app-filter]');
    if (filter) {
      state.filter = filter.dataset.localAppFilter;
      document.querySelectorAll('[data-local-app-filter]').forEach((node) => node.classList.toggle('active', node === filter));
      render();
      return;
    }
    const action = event.target.closest('[data-local-app-action]');
    if (action) {
      runAction(action);
      return;
    }
    const nav = event.target.closest('[data-view="local-apps"]');
    if (nav) {
      const title = document.getElementById('pageTitle');
      if (title) title.textContent = 'Local Apps';
      load().catch((error) => window.flash(error.message, true));
    }
    if (event.target.id === 'refreshButton' && document.getElementById('view-local-apps')?.classList.contains('active')) {
      load(true).catch((error) => window.flash(error.message, true));
    }
  });

  window.addEventListener('hashchange', () => {
    if (location.hash === '#local-apps') {
      window.openView('local-apps');
      const title = document.getElementById('pageTitle');
      if (title) title.textContent = 'Local Apps';
      load().catch((error) => window.flash(error.message, true));
    }
  });

  if (location.hash === '#local-apps') {
    window.openView('local-apps');
    const title = document.getElementById('pageTitle');
    if (title) title.textContent = 'Local Apps';
    load().catch((error) => window.flash(error.message, true));
  }
})();
