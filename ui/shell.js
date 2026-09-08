(() => {
  'use strict';

  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
  const fmt = value => value ? new Date(value).toLocaleString() : 'Never';
  const num = value => Number(value || 0).toLocaleString();

  async function shellApi(path, options = {}) {
    const headers = {...(options.headers || {})};
    if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, {...options, headers});
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }

  function ensureStyles() {
    if (document.querySelector('link[href="/assets/shell.css"]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/shell.css';
    document.head.appendChild(link);
  }

  function ensureContextEngine() {
    if (document.querySelector('script[data-homeserver-context-engine]')) return;
    const script = document.createElement('script');
    script.src = '/assets/context-engine.js';
    script.dataset.homeserverContextEngine = '1';
    script.async = false;
    document.head.appendChild(script);
  }

  function ensureAppScopes() {
    if (document.querySelector('script[data-homeserver-app-scopes]')) return;
    const script = document.createElement('script');
    script.src = '/assets/app-scopes-v026.js';
    script.dataset.homeserverAppScopes = '1';
    script.async = false;
    document.head.appendChild(script);
  }

  function ensureUsageView() {
    if (byId('view-usage')) return;
    const main = document.querySelector('.main');
    if (!main) return;
    const section = document.createElement('section');
    section.className = 'view';
    section.id = 'view-usage';
    section.innerHTML = `
      <div class="section-intro split">
        <div><h2>Token Usage History</h2><p>Track VP3 cloud-token debits and HomeServer-served inference that did not consume VP3 cloud tokens.</p></div>
        <div class="top-actions"><button class="button secondary active" type="button" data-usage-filter="all">All</button><button class="button secondary" type="button" data-usage-filter="vp3_cloud">VP3 cloud</button></div>
      </div>
      <div class="stats-grid usage-stats">
        <article class="stat"><span>Cloud tokens debited</span><strong id="usageCloudTokens">0</strong><small>VP3 purchased balance</small></article>
        <article class="stat"><span>Cloud balance</span><strong id="usageBalance">—</strong><small>last reported balance</small></article>
        <article class="stat"><span>Cloud requests</span><strong id="usageCloudRequests">0</strong><small>fallback requests</small></article>
        <article class="stat"><span>HomeServer requests</span><strong id="usageHomeRequests">0</strong><small>no VP3 token debit</small></article>
      </div>
      <div class="panel table-wrap">
        <table>
          <thead><tr><th>Time</th><th>Source</th><th>Provider / model</th><th>Input</th><th>Output</th><th>Total</th><th>VP3 debit</th><th>Balance</th></tr></thead>
          <tbody id="usageHistoryTable"><tr><td colspan="8">No token history yet.</td></tr></tbody>
        </table>
      </div>`;
    main.appendChild(section);
  }

  async function loadUsage(source = null) {
    ensureUsageView();
    const query = source ? `?limit=500&source=${encodeURIComponent(source)}` : '?limit=500';
    const data = await shellApi(`/api/v1/control/usage${query}`);
    const summary = data.summary || {};
    if (byId('usageCloudTokens')) byId('usageCloudTokens').textContent = num(summary.cloud_tokens_debited);
    if (byId('usageBalance')) byId('usageBalance').textContent = summary.balance_tokens == null ? '—' : num(summary.balance_tokens);
    if (byId('usageCloudRequests')) byId('usageCloudRequests').textContent = num(summary.cloud_requests);
    if (byId('usageHomeRequests')) byId('usageHomeRequests').textContent = num(summary.homeserver_requests);
    const table = byId('usageHistoryTable');
    if (!table) return;
    table.innerHTML = data.items?.length ? data.items.map(item => {
      const sourceLabel = item.compute_source === 'vp3_cloud' ? 'VP3 cloud' : item.compute_source === 'homeserver_local' ? 'HomeServer local' : 'User provider';
      const provider = [item.provider_key, item.model].filter(Boolean).join(' · ') || '—';
      return `<tr><td>${esc(fmt(item.created_at))}</td><td>${esc(sourceLabel)}</td><td>${esc(provider)}</td><td>${num(item.prompt_tokens)}</td><td>${num(item.completion_tokens)}</td><td>${num(item.total_tokens)}</td><td>${num(item.billable_tokens)}</td><td>${item.balance_after_tokens == null ? '—' : num(item.balance_after_tokens)}</td></tr>`;
    }).join('') : '<tr><td colspan="8">No token history yet.</td></tr>';
  }

  function buildConnectionModal() {
    if (byId('connectionModalBackdrop')) return;
    const backdrop = document.createElement('div');
    backdrop.className = 'connection-modal-backdrop hidden';
    backdrop.id = 'connectionModalBackdrop';
    backdrop.innerHTML = `
      <div class="connection-modal" role="dialog" aria-modal="true" aria-labelledby="connectionModalTitle">
        <div class="connection-modal-head"><h2 id="connectionModalTitle">HomeServer Connection</h2><button type="button" id="closeConnectionModal" aria-label="Close">×</button></div>
        <div class="connection-modal-body" id="connectionModalBody"><div class="empty-state">Loading connection details…</div></div>
      </div>`;
    document.body.appendChild(backdrop);
  }

  async function loadConnectionModal() {
    buildConnectionModal();
    const body = byId('connectionModalBody');
    const button = byId('homeServerConnectionButton');
    if (body) body.innerHTML = '<div class="empty-state">Loading connection details…</div>';
    try {
      const [status, apps, bridge, inference] = await Promise.all([
        shellApi('/api/v1/status'),
        shellApi('/api/v1/control/apps'),
        shellApi('/api/v1/control/remote-bridge?limit=10').catch(() => null),
        shellApi('/api/v1/control/inference').catch(() => null),
      ]);
      button?.classList.add('online');
      const runtime = bridge?.runtime || {};
      const selected = inference?.selected_provider ? `${inference.selected_provider}${inference.model ? ` · ${inference.model}` : ''}` : 'No inference provider ready';
      const compute = inference?.compute_source === 'homeserver_local' ? 'Local HomeServer' : inference?.compute_source === 'user_provider' ? 'User provider' : 'VP3 cloud fallback required';
      const appRows = (apps.apps || []).map(app => `<div class="connection-app"><span><strong>${esc(app.name)}</strong><br><small>${esc(app.app_key)}</small></span><span>${esc(app.status)} · last seen ${esc(fmt(app.last_seen_at))}</span></div>`).join('') || '<div class="empty-state">No paired apps yet.</div>';
      if (body) body.innerHTML = `
        <div class="connection-modal-grid">
          <div class="connection-stat"><span>HomeServer</span><strong>Online · v${esc(status.version)}</strong></div>
          <div class="connection-stat"><span>Remote bridge</span><strong>${bridge ? esc(runtime.connected ? 'Connected' : runtime.stage || 'Disconnected') : 'Unavailable'}</strong></div>
          <div class="connection-stat"><span>Agent compute</span><strong>${esc(compute)}</strong></div>
          <div class="connection-stat"><span>Provider</span><strong>${esc(selected)}</strong></div>
        </div>
        <p class="eyebrow">APP CONNECTIONS</p>
        <div class="connection-apps">${appRows}</div>
        <div class="connection-update"><strong>Update tracking</strong><br>HomeServer v${esc(status.version)} is reporting its installed version. VP3 cloud update notifications can plug into this status surface when the cloud integration is enabled.</div>
        <div class="form-actions"><a class="button secondary" href="/remote">Remote Bridge</a><button class="button secondary" type="button" data-view="apps">Manage Apps</button></div>`;
    } catch (err) {
      button?.classList.remove('online');
      if (body) body.innerHTML = `<div class="empty-state">${esc(err.message)}</div>`;
    }
  }

  function primaryButton(label, view, extraClass = '') {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = extraClass;
    button.dataset.view = view;
    button.textContent = label;
    return button;
  }

  function buildSidebar() {
    const sidebar = document.querySelector('.sidebar');
    const brand = sidebar?.querySelector('.brand');
    if (!sidebar || !brand || sidebar.querySelector('.brand-row')) return;

    document.body.classList.add('shell-agent-first');
    brand.href = '#chat';
    brand.setAttribute('aria-label', 'VP3 HomeServer Agent Chat');
    brand.innerHTML = '<span class="brand-mark"><span></span></span><span class="brand-copy"><strong>VP3</strong><small>HomeServer</small></span>';

    const brandRow = document.createElement('div');
    brandRow.className = 'brand-row';
    brand.parentNode.insertBefore(brandRow, brand);
    brandRow.appendChild(brand);
    const connectionButton = document.createElement('button');
    connectionButton.type = 'button';
    connectionButton.id = 'homeServerConnectionButton';
    connectionButton.className = 'connection-button';
    connectionButton.title = 'HomeServer connection status';
    connectionButton.setAttribute('aria-label', 'HomeServer connection status');
    brandRow.appendChild(connectionButton);

    const primary = document.createElement('div');
    primary.className = 'primary-sidebar-nav';
    const newChat = document.createElement('button');
    newChat.type = 'button';
    newChat.id = 'sidebarNewChat';
    newChat.className = 'sidebar-new-chat';
    newChat.textContent = '+  New Chat';
    primary.appendChild(newChat);
    primary.appendChild(primaryButton('Approvals', 'approvals'));
    primary.appendChild(primaryButton('Knowledge', 'knowledge'));
    primary.appendChild(primaryButton('Memory', 'memory'));
    primary.appendChild(primaryButton('Contacts', 'contacts'));
    brandRow.insertAdjacentElement('afterend', primary);

    const divider = document.createElement('div');
    divider.className = 'sidebar-divider';
    primary.insertAdjacentElement('afterend', divider);

    const history = document.createElement('div');
    history.className = 'sidebar-chat-history';
    history.innerHTML = '<div class="chat-history-head"><span>Chats</span></div>';
    const conversationList = byId('conversationList');
    if (conversationList) history.appendChild(conversationList);
    divider.insertAdjacentElement('afterend', history);

    const userWrap = document.createElement('div');
    userWrap.className = 'sidebar-user-wrap';
    userWrap.innerHTML = `
      <div id="sidebarUserMenu" class="user-menu hidden">
        <div class="menu-label">HomeServer</div>
        <button type="button" data-view="agent">AGENT BRAIN</button>
        <button type="button" data-view="dashboard">Overview</button>
        <button type="button" data-view="tools">Skills & Tools</button>
        <button type="button" data-view="apps">Connected Apps</button>
        <button type="button" data-view="usage">Token Usage History</button>
        <button type="button" data-view="backups">Backup & Restore</button>
        <button type="button" data-view="activity">Activity</button>
        <div class="menu-label">System</div>
        <a href="/tasks">Tasks & Notifications</a>
        <a href="/remote">Remote Bridge</a>
        <a href="/system">Setup & Diagnostics</a>
        <a href="/docs" target="_blank" rel="noreferrer">API Documentation</a>
      </div>
      <button id="sidebarUserButton" class="sidebar-user-button" type="button" aria-expanded="false">
        <span class="user-avatar">VP3</span>
        <span class="user-label"><strong id="sidebarAgentName">HomeServer Owner</strong><small>Agent & system menu</small></span>
        <span aria-hidden="true">⋯</span>
      </button>`;
    sidebar.appendChild(userWrap);

    shellApi('/api/v1/control/agent').then(data => {
      const name = data.agent?.name;
      if (name && byId('sidebarAgentName')) byId('sidebarAgentName').textContent = name;
    }).catch(() => {});
  }

  function closeMenus() {
    byId('sidebarUserMenu')?.classList.add('hidden');
    byId('sidebarUserButton')?.setAttribute('aria-expanded', 'false');
    document.querySelectorAll('.conversation-menu').forEach(menu => menu.classList.add('hidden'));
  }

  function openDefaultChat() {
    ensureUsageView();
    const agentNav = document.querySelector('[data-view="agent"]');
    if (agentNav && agentNav.textContent.trim() === 'My Agent') agentNav.textContent = 'AGENT BRAIN';
    const title = document.querySelector('#view-agent .section-intro h2');
    if (title) title.textContent = 'AGENT BRAIN';
    const chatNav = document.querySelector('.nav [data-view="chat"]');
    if (chatNav) {
      history.replaceState(null, '', '#chat');
      chatNav.click();
    }
    byId('chatInput')?.focus();
  }

  document.addEventListener('click', async event => {
    if (event.target.closest('#homeServerConnectionButton')) {
      closeMenus();
      byId('connectionModalBackdrop')?.classList.remove('hidden');
      await loadConnectionModal();
      byId('connectionModalBackdrop')?.classList.remove('hidden');
      return;
    }
    if (event.target.closest('#closeConnectionModal') || event.target.id === 'connectionModalBackdrop') {
      byId('connectionModalBackdrop')?.classList.add('hidden');
      return;
    }
    if (event.target.closest('#sidebarUserButton')) {
      const menu = byId('sidebarUserMenu');
      const isOpen = menu && !menu.classList.contains('hidden');
      closeMenus();
      if (!isOpen) {
        menu?.classList.remove('hidden');
        byId('sidebarUserButton')?.setAttribute('aria-expanded', 'true');
      }
      return;
    }
    if (event.target.closest('#sidebarNewChat')) {
      document.querySelector('.nav [data-view="chat"]')?.click();
      byId('newChat')?.click();
      closeMenus();
      return;
    }
    const view = event.target.closest('[data-view]');
    if (view) {
      closeMenus();
      if (view.dataset.view === 'usage') {
        setTimeout(() => {
          if (byId('pageTitle')) byId('pageTitle').textContent = 'Token Usage History';
          loadUsage().catch(() => {});
        }, 0);
      } else if (view.dataset.view === 'agent') {
        setTimeout(() => { if (byId('pageTitle')) byId('pageTitle').textContent = 'AGENT BRAIN'; }, 0);
      }
    }
    const filter = event.target.closest('[data-usage-filter]');
    if (filter) {
      document.querySelectorAll('[data-usage-filter]').forEach(button => button.classList.toggle('active', button === filter));
      await loadUsage(filter.dataset.usageFilter === 'all' ? null : filter.dataset.usageFilter);
      return;
    }
    if (!event.target.closest('.sidebar-user-wrap') && !event.target.closest('.conversation-menu')) closeMenus();
  });

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      closeMenus();
      byId('connectionModalBackdrop')?.classList.add('hidden');
    }
  });

  ensureStyles();
  ensureContextEngine();
  ensureAppScopes();
  ensureUsageView();
  buildConnectionModal();
  buildSidebar();
  setTimeout(openDefaultChat, 0);
})();