const state = { view: 'dashboard', apps: [], activityCategory: '', activityAttention: false, activityUnreadOnly: false };
const $ = (id) => document.getElementById(id);
const esc = (value = '') => String(value).replace(/[&<>'\"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','\"':'&quot;'}[c]));
const fmt = (value) => value ? new Date(value).toLocaleString() : 'Never';
const formatBytes = (value) => {
  const bytes = Number(value || 0);
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

async function api(path, options = {}) {
  const headers = {...(options.headers || {})};
  const isForm = options.body instanceof FormData;
  if (options.body && !isForm && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, {...options, headers});
  let data = {};
  try { data = await response.json(); } catch (_) {}
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

function flash(message, error = false) {
  const node = $('flash');
  node.textContent = message;
  node.className = `flash show${error ? ' error' : ''}`;
  clearTimeout(flash.timer);
  flash.timer = setTimeout(() => node.className = 'flash', 3500);
}

function ensureBackupWorkspace() {
  if ($('view-backups')) return;

  if (!document.querySelector('link[href="/assets/backups.css"]')) {
    const stylesheet = document.createElement('link');
    stylesheet.rel = 'stylesheet';
    stylesheet.href = '/assets/backups.css';
    document.head.append(stylesheet);
  }

  const activityNav = document.querySelector('.nav-item[data-view="activity"]');
  if (activityNav && !document.querySelector('.nav-item[data-view="backups"]')) {
    const button = document.createElement('button');
    button.className = 'nav-item';
    button.dataset.view = 'backups';
    button.textContent = 'Backup & Restore';
    activityNav.parentNode.insertBefore(button, activityNav);
  }

  const activityView = $('view-activity');
  if (activityView) {
    const section = document.createElement('section');
    section.className = 'view';
    section.id = 'view-backups';
    section.innerHTML = `
      <div class="section-intro split">
        <div><h2>Backup & Restore</h2><p>Create portable local snapshots of HomeServer data, users, permissions, knowledge, and installed-app data.</p></div>
        <div class="backup-actions"><button class="button secondary" id="stageRestoreButton" type="button">Stage restore</button><button class="button primary" id="createBackupButton" type="button">Create backup</button><input class="hidden" id="restoreBackupFile" type="file" accept=".zip,application/zip"></div>
      </div>
      <div class="backup-warning"><strong>Private archive:</strong> backup ZIPs contain private HomeServer data and are not encrypted by the ZIP format. Keep exported copies somewhere you control and protect.</div>
      <div id="restoreStatus"></div>
      <div id="backupHealth"></div>
      <form id="backupPolicyForm" class="panel backup-policy">
        <div><p class="eyebrow">PROTECTION POLICY</p><h3>Backup coverage & retention</h3></div>
        <label><input id="backupIncludeAppData" type="checkbox"> Include installed-app data</label>
        <label>Manual backups <input id="backupRetainManual" type="number" min="1" max="100"></label>
        <label>Automatic backups <input id="backupRetainAutomatic" type="number" min="1" max="100"></label>
        <label>Pre-restore backups <input id="backupRetainPreRestore" type="number" min="1" max="20"></label>
        <button class="button secondary" type="submit">Save policy</button>
      </form>
      <div class="panel backup-explainer"><div><span>1</span><p><strong>Consistent snapshot</strong><small>HomeServer and installed-app SQLite databases use SQLite's backup API; app files are captured from isolated app storage.</small></p></div><div><span>2</span><p><strong>Integrity manifest</strong><small>Every protected database, knowledge file, and app-data file is SHA-256 checked before restore can be staged.</small></p></div><div><span>3</span><p><strong>Restart-safe restore</strong><small>HomeServer makes a pre-restore backup, swaps protected app data atomically, and revokes restored member sessions before serving requests.</small></p></div></div>
      <div class="panel-head backup-list-head"><div><p class="eyebrow">LOCAL ARCHIVES</p><h3>Backups</h3></div><span id="backupCount" class="muted"></span></div>
      <div id="backupList" class="backup-list"><div class="panel empty-state">No backups yet.</div></div>`;
    activityView.parentNode.insertBefore(section, activityView);
  }

  const providerCopy = document.querySelector('#providerForm > p.muted');
  if (providerCopy && /HomeServer v\d+\.\d+/.test(providerCopy.textContent)) {
    providerCopy.textContent = providerCopy.textContent.replace(/HomeServer v\d+\.\d+/, 'HomeServer v0.11');
  }
}

function openView(name) {
  state.view = name;
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === `view-${name}`));
  document.querySelectorAll('.nav-item').forEach(v => v.classList.toggle('active', v.dataset.view === name));
  const labels = {dashboard:'Overview',agent:'My Agent',chat:'Agent Chat',tools:'Skills & Tools',approvals:'Approvals',knowledge:'Knowledge',memory:'Memory',contacts:'Contacts',members:'Users','homeserver-apps':'Apps',apps:'Connected Apps',backups:'Backup & Restore',ambient:'Ambient Agent',automation:'Rooms & Devices',tracky:'Tracky',federation:'Physical Network','physical-world':'Physical World',activity:'Activity'};
  $('pageTitle').textContent = labels[name] || 'HomeServer';
  loadView(name).catch(err => flash(err.message, true));
}

function ensureKnowledgeControls() {
  if ($('knowledgeFiles')) return;
  const intro = document.querySelector('#view-knowledge .section-intro.split');
  const addButton = $('showKnowledgeForm');
  if (!intro || !addButton) return;

  const actions = document.createElement('div');
  actions.className = 'top-actions';

  const input = document.createElement('input');
  input.id = 'knowledgeFiles';
  input.type = 'file';
  input.multiple = true;
  input.className = 'hidden';
  input.accept = '.txt,.md,.markdown,.json,.csv,.html,.htm,.pdf,.docx';

  const importButton = document.createElement('button');
  importButton.id = 'importKnowledgeFiles';
  importButton.type = 'button';
  importButton.className = 'button secondary';
  importButton.textContent = 'Import files';

  addButton.remove();
  actions.append(importButton, addButton, input);
  intro.append(actions);

  const description = intro.querySelector('p');
  if (description) description.textContent = 'Import local documents or add notes. HomeServer extracts text, chunks it, and builds a private SQLite full-text index.';

  const toolbar = document.querySelector('#view-knowledge .toolbar');
  if (toolbar && !$('reindexKnowledge')) {
    const reindex = document.createElement('button');
    reindex.id = 'reindexKnowledge';
    reindex.type = 'button';
    reindex.className = 'text-button';
    reindex.textContent = 'Reindex';
    toolbar.append(reindex);
  }
}

let vp3CloudRefreshPromise = null;
async function refreshVp3CloudState() {
  if (vp3CloudRefreshPromise) return vp3CloudRefreshPromise;
  const node = $('vp3CloudState');
  const label = $('vp3CloudStateLabel');
  if (!node || !label) return;
  vp3CloudRefreshPromise = (async () => {
  try {
    const data = await api('/api/v1/control/cloud-connection');
    const cloud = data.cloud || {};
    node.dataset.state = cloud.state || 'not_connected';
    label.textContent = cloud.connected
      ? 'VP3 Cloud connected'
      : (cloud.state === 'reconnecting' ? 'VP3 Cloud reconnecting…' : 'VP3 Cloud not connected');
    node.title = cloud.transport_label || 'VP3 Cloud';
  } catch (_) {
    node.dataset.state = 'offline';
    label.textContent = 'VP3 Cloud status unavailable';
  } finally {
    vp3CloudRefreshPromise = null;
  }
  })();
  return vp3CloudRefreshPromise;
}

async function loadOverview() {
  const [data, status] = await Promise.all([api('/api/v1/control/overview'), api('/api/v1/status')]);
  $('heroAgentName').textContent = data.agent?.name || 'HomeServer Agent';
  $('statKnowledge').textContent = data.counts.knowledge_items;
  $('statMemory').textContent = data.counts.memory_items;
  $('statApps').textContent = data.counts.paired_apps;
  $('statPairing').textContent = data.counts.pending_pairing;
  $('version').textContent = `v${status.version}`;
  $('recentActivity').innerHTML = data.activity.length ? data.activity.map(item => `<div class="activity-row"><strong>${esc(item.action)}</strong><span>${esc(fmt(item.created_at))}</span></div>`).join('') : '<div class="empty-state">No activity yet.</div>';
}

async function loadAgent() {
  const data = await api('/api/v1/control/agent');
  const agent = data.agent || {};
  $('agentName').value = agent.name || '';
  $('agentModel').value = agent.model || '';
  $('agentInstructions').value = agent.instructions || '';
  $('agentUpdated').textContent = agent.updated_at ? `Last updated ${fmt(agent.updated_at)}` : '';
}

async function loadKnowledge() {
  ensureKnowledgeControls();
  const q = encodeURIComponent($('knowledgeSearch')?.value || '');
  const data = await api(`/api/v1/control/knowledge?q=${q}`);
  $('knowledgeCount').textContent = `${data.items.length} item${data.items.length === 1 ? '' : 's'}`;
  $('knowledgeList').innerHTML = data.items.length ? data.items.map(item => {
    const preview = item.snippet || (item.content || '').slice(0, 900);
    const source = item.original_name || item.source_path || '';
    const size = item.size_bytes ? formatBytes(item.size_bytes) : '';
    const chunks = Number(item.chunk_count || 0);
    return `<article class="item-card"><div><h3>${esc(item.title)}</h3><p>${esc(preview)}</p><div class="item-meta"><span class="tag">${esc(item.kind)}</span>${source ? `<span>${esc(source)}</span>` : ''}${size ? `<span>${esc(size)}</span>` : ''}<span>${chunks} chunk${chunks === 1 ? '' : 's'}</span><span>${esc(fmt(item.updated_at))}</span></div></div><div><button class="icon-button danger" data-delete-knowledge="${item.id}">Delete</button></div></article>`;
  }).join('') : '<div class="panel empty-state">No knowledge items found.</div>';
}

async function importKnowledgeFiles() {
  const input = $('knowledgeFiles');
  const files = [...(input?.files || [])];
  if (!files.length) return;
  let imported = 0;
  let duplicates = 0;
  const failures = [];
  for (const file of files) {
    const form = new FormData();
    form.append('file', file, file.name);
    try {
      const result = await api('/api/v1/control/knowledge/import', {method:'POST', body:form});
      if (result.duplicate) duplicates += 1;
      else imported += 1;
    } catch (err) { failures.push(`${file.name}: ${err.message}`); }
  }
  input.value = '';
  await loadKnowledge();
  const parts = [];
  if (imported) parts.push(`${imported} imported`);
  if (duplicates) parts.push(`${duplicates} duplicate${duplicates === 1 ? '' : 's'} skipped`);
  if (failures.length) parts.push(`${failures.length} failed`);
  flash(parts.join(' · ') || 'No files imported.', failures.length > 0);
  if (failures.length) console.warn('Knowledge import failures', failures);
}

async function loadMemory() {
  const data = await api('/api/v1/control/memory');
  $('memoryList').innerHTML = data.items.length ? data.items.map(item => `<article class="item-card"><div><h3>${esc(item.memory_key || 'Memory')}</h3><p>${esc(item.content)}</p><div class="item-meta"><span class="tag">importance ${Number(item.importance).toFixed(1)}</span><span>${esc(item.agent_name || 'Primary agent')}</span><span>${esc(fmt(item.updated_at))}</span></div></div><div><button class="icon-button danger" data-delete-memory="${item.id}">Delete</button></div></article>`).join('') : '<div class="panel empty-state">No memories stored yet.</div>';
}

async function loadApps() {
  const data = await api('/api/v1/control/apps');
  state.apps = data.apps;
  $('pendingPairing').innerHTML = data.pending.length ? data.pending.map(item => `<div class="pending"><strong>${esc(item.app_name)}</strong> is waiting to pair · requested: ${esc(item.requested_permissions.join(', ') || 'no permissions')} · expires ${esc(fmt(item.expires_at))}</div>`).join('') : '';
  $('appsList').innerHTML = data.apps.length ? data.apps.map(app => `<article class="item-card"><div><h3>${esc(app.name)}</h3><div class="status-row"><span class="status-dot ${app.status === 'active' ? 'active' : ''}"></span><span class="muted">${esc(app.status)} · ${esc(app.app_key)} · last seen ${esc(fmt(app.last_seen_at))}</span></div><div class="permission-list">${data.available_permissions.map(permission => { const current = app.permissions.find(p => p.permission === permission); const checked = current?.allowed ? 'checked' : ''; return `<label class="permission"><input type="checkbox" data-app-permission="${app.id}" data-permission="${esc(permission)}" ${checked}>${esc(permission)}</label>`; }).join('')}</div></div><div><select class="app-status" data-app-status="${app.id}"><option value="active" ${app.status==='active'?'selected':''}>Active</option><option value="paused" ${app.status==='paused'?'selected':''}>Paused</option><option value="revoked" ${app.status==='revoked'?'selected':''}>Revoked</option></select></div></article>`).join('') : '<div class="panel empty-state">No applications connected yet.</div>';
}

async function loadActivity() {
  const query = new URLSearchParams({limit:'250'});
  if (state.activityCategory) query.set('category', state.activityCategory);
  if (state.activityAttention) query.set('needs_attention','true');
  if (state.activityUnreadOnly) query.set('unread_only','true');
  await api('/api/v1/control/activity-center/sync',{method:'POST'});
  const [data, summary] = await Promise.all([
    api('/api/v1/control/activity?' + query.toString()),
    api('/api/v1/control/activity-center/summary')
  ]);
  if ($('activityUnread')) $('activityUnread').textContent = summary.unread ?? 0;
  if ($('activityAttention')) $('activityAttention').textContent = summary.needs_attention ?? 0;
  if ($('activityApprovals')) $('activityApprovals').textContent = summary.pending_approvals ?? 0;
  if ($('activityFailures')) $('activityFailures').textContent = Number(summary.failed_automations || 0) + Number(summary.failed_agent_runs || 0);
  const feed=$('activityFeed');
  if (!feed) return;
  feed.innerHTML = data.items.length ? data.items.map(item => {
    const notificationActions = item.notification_id ? `
      <button class="text-button" data-activity-read="${item.notification_id}" data-read-value="${item.read ? 'false' : 'true'}">${item.read ? 'Mark unread' : 'Mark read'}</button>
      <button class="text-button" data-activity-dismiss="${item.notification_id}">Dismiss</button>` : '';
    const approvalAction = item.action_payload?.type === 'approval' ? '<button class="button secondary" data-go="approvals">Open approval</button>' : '';
    const openAction = item.action_payload?.type === 'open' && item.action_payload?.target_view ? `<button class="button secondary" data-go="${esc(item.action_payload.target_view)}">Open</button>` : '';
    const resource=[item.resource_type,item.resource_key].filter(Boolean).join(' · ');
    return `<article class="panel activity-card level-${esc(item.level)} ${item.read ? '' : 'unread'}">
      <span class="activity-card-marker" aria-hidden="true"></span>
      <div class="activity-card-main">
        <div class="activity-card-head"><h3>${esc(item.title)}</h3><span class="activity-badge">${esc(item.category)}</span><span class="activity-badge">${esc(item.level)}</span></div>
        ${item.body ? `<p>${esc(item.body)}</p>` : ''}
        <div class="activity-meta"><span>${esc(fmt(item.created_at))}</span><span>${esc(item.source_key || item.actor_key || item.source_kind)}</span>${resource ? `<span>${esc(resource)}</span>` : ''}</div>
      </div>
      <div class="activity-card-actions">${approvalAction}${openAction}${notificationActions}</div>
    </article>`;
  }).join('') : '<div class="panel empty-state">No activity matches these filters.</div>';
  document.querySelectorAll('[data-activity-filter],[data-activity-attention]').forEach(button => {
    const selected = button.hasAttribute('data-activity-attention') ? state.activityAttention : (!state.activityAttention && button.dataset.activityFilter === state.activityCategory);
    button.classList.toggle('active', selected);
  });
  if ($('activityUnreadOnly')) $('activityUnreadOnly').checked=state.activityUnreadOnly;
}

function restoreStatusMarkup(data) {
  const pending = data.pending_restore;
  const last = data.last_restore;
  const parts = [];
  if (pending) {
    if (pending.valid) {
      parts.push(`<div class="panel restore-state pending"><div><p class="eyebrow">RESTORE STAGED</p><h3>Restart required</h3><p>${esc(pending.original_name || 'HomeServer backup')} passed integrity validation and will be applied before the server starts next time.</p><div class="item-meta"><span>Backup ${esc(fmt(pending.backup_created_at))}</span><span>format v${esc(pending.format_version || 1)}</span><span>schema v${esc(pending.schema_version)}</span><span>${pending.app_data?.included ? esc(String(pending.app_data.files || 0)) + ' app files' : 'app data preserved from current system'}</span><span>${esc(formatBytes(pending.upload_size_bytes))}</span></div>${pending.app_data?.warning ? `<p class="muted">${esc(pending.app_data.warning)}</p>` : ''}</div><button class="button secondary danger" id="cancelRestoreButton" type="button">Cancel restore</button></div>`);
    } else {
      parts.push(`<div class="panel restore-state failed"><div><p class="eyebrow">RESTORE INVALID</p><h3>Staged restore needs attention</h3><p>${esc(pending.error || 'The staged restore could not be revalidated.')}</p></div><button class="button secondary danger" id="cancelRestoreButton" type="button">Clear staged restore</button></div>`);
    }
  }
  if (last?.status === 'applied') {
    parts.push(`<div class="restore-result success"><strong>Last restore applied ${esc(fmt(last.applied_at))}.</strong>${last.pre_restore_backup ? ` A safety backup was created first: ${esc(last.pre_restore_backup)}.` : ''}</div>`);
  } else if (last?.status === 'failed') {
    parts.push(`<div class="restore-result failure"><strong>Last restore was rolled back safely.</strong> ${esc(last.error || 'The staged data could not be applied.')}</div>`);
  }
  return parts.join('');
}

async function loadBackups() {
  ensureBackupWorkspace();
  const data = await api('/api/v1/control/backups');
  $('restoreStatus').innerHTML = restoreStatusMarkup(data);
  const health=data.health||{};
  const coverage=health.coverage||{};
  if ($('backupHealth')) $('backupHealth').innerHTML =
    '<div class="panel backup-health"><div><p class="eyebrow">BACKUP HEALTH</p><h3>'+(health.invalid_backup_count?'Needs attention':'Protected')+'</h3></div>'+
    '<div class="backup-health-grid"><span><strong>'+Number(health.backup_count||0)+'</strong> valid backups</span>'+
    '<span><strong>'+Number(health.invalid_backup_count||0)+'</strong> invalid</span>'+
    '<span><strong>'+(coverage.app_data?'Yes':'No')+'</strong> app data</span>'+
    '<span><strong>'+(health.session_invalidation_after_restore?'Yes':'No')+'</strong> session reset</span></div>'+
    '<p class="muted">Secrets, runtime state, restore staging, existing backup archives, and app migration-recovery snapshots are intentionally excluded.</p></div>';
  const policy=health.policy||{};
  if ($('backupIncludeAppData')) $('backupIncludeAppData').checked=Boolean(policy.include_app_data);
  if ($('backupRetainManual')) $('backupRetainManual').value=policy.retain_manual ?? 10;
  if ($('backupRetainAutomatic')) $('backupRetainAutomatic').value=policy.retain_automatic ?? 7;
  if ($('backupRetainPreRestore')) $('backupRetainPreRestore').value=policy.retain_pre_restore ?? 3;
  $('backupCount').textContent = `${data.items.length} archive${data.items.length === 1 ? '' : 's'}`;
  $('backupList').innerHTML = data.items.length ? data.items.map(item => {
    const invalid = item.invalid ? '<span class="tag danger">invalid manifest</span>' : '';
    const legacy = Number(item.format_version||1)<2 ? '<span class="tag">legacy v1 · app data not included</span>' : '';
    const appMeta = Number(item.format_version||1)>=2 ? `<span>${Number(item.app_count||0)} apps · ${Number(item.app_data_files||0)} app files</span>` : '';
    return `<article class="panel backup-card"><div><h3>${esc(item.name)}</h3><div class="backup-meta"><span>${esc(fmt(item.created_at))}</span><span>${esc(formatBytes(item.size_bytes))}</span><span>format v${esc(item.format_version||1)}</span><span>schema ${item.schema_version == null ? 'unknown' : `v${esc(item.schema_version)}`}</span><span>${esc(item.reason || 'manual')}</span>${appMeta}${legacy}${invalid}</div></div><div class="backup-card-actions"><a class="button secondary" href="/api/v1/control/backups/download/${encodeURIComponent(item.name)}">Download</a><button class="text-button danger" data-delete-backup="${esc(item.name)}" type="button">Delete</button></div></article>`;
  }).join('') : '<div class="panel empty-state">No backups yet. Create one before major changes or moving HomeServer to another machine.</div>';
}

async function loadView(name) {
  if (name === 'dashboard') return loadOverview();
  if (name === 'agent') return loadAgent();
  if (name === 'knowledge') return loadKnowledge();
  if (name === 'memory') return loadMemory();
  if (name === 'contacts' && typeof window.loadHomeServerContacts === 'function') return window.loadHomeServerContacts();
  if (name === 'members' && typeof window.loadHomeServerMembers === 'function') return window.loadHomeServerMembers();
  if (name === 'homeserver-apps' && typeof window.loadHomeServerApps === 'function') return window.loadHomeServerApps();
  if (name === 'tracky' && typeof window.loadTrackyOverview === 'function') return window.loadTrackyOverview();
  if (name === 'apps') return loadApps();
  if (name === 'backups') return loadBackups();
  if (name === 'federation' && typeof window.loadFederationControlCenter === 'function') return window.loadFederationControlCenter();
  if (name === 'physical-world' && typeof window.loadPhysicalWorldDashboard === 'function') return window.loadPhysicalWorldDashboard();
  if (name === 'activity') return loadActivity();
  return Promise.resolve();
}

document.addEventListener('click', async (event) => {
  const nav = event.target.closest('[data-view]'); if (nav) openView(nav.dataset.view);
  const go = event.target.closest('[data-go]'); if (go) openView(go.dataset.go);
  const activityFilter=event.target.closest('[data-activity-filter]');
  if (activityFilter) {
    state.activityCategory=activityFilter.dataset.activityFilter || '';
    state.activityAttention=false;
    loadActivity().catch(error=>flash(error.message,true));
  }
  const activityAttention=event.target.closest('[data-activity-attention]');
  if (activityAttention) {
    state.activityAttention=true;
    state.activityCategory='';
    loadActivity().catch(error=>flash(error.message,true));
  }
  if (event.target.id==='activityRefresh') loadActivity().catch(error=>flash(error.message,true));
  const activityRead=event.target.closest('[data-activity-read]');
  if (activityRead) {
    await api('/api/v1/control/activity-center/notifications/'+encodeURIComponent(activityRead.dataset.activityRead),{
      method:'PATCH',body:JSON.stringify({read:activityRead.dataset.readValue==='true'})
    });
    await loadActivity();
  }
  const activityDismiss=event.target.closest('[data-activity-dismiss]');
  if (activityDismiss) {
    await api('/api/v1/control/activity-center/notifications/'+encodeURIComponent(activityDismiss.dataset.activityDismiss),{
      method:'PATCH',body:JSON.stringify({dismissed:true})
    });
    await loadActivity();
  }
  if (event.target.id === 'showKnowledgeForm') $('knowledgeForm').classList.remove('hidden');
  if (event.target.id === 'cancelKnowledge') $('knowledgeForm').classList.add('hidden');
  if (event.target.id === 'showMemoryForm') $('memoryForm').classList.remove('hidden');
  if (event.target.id === 'cancelMemory') $('memoryForm').classList.add('hidden');
  if (event.target.id === 'importKnowledgeFiles') $('knowledgeFiles')?.click();
  if (event.target.id === 'stageRestoreButton') $('restoreBackupFile')?.click();
  if (event.target.id === 'createBackupButton') {
    const button = event.target;
    button.disabled = true;
    button.textContent = 'Creating…';
    try {
      const result = await api('/api/v1/control/backups/create', {method:'POST'});
      await loadBackups();
      flash(`Backup created: ${result.backup.name}`);
    } catch (err) { flash(err.message, true); }
    finally { button.disabled = false; button.textContent = 'Create backup'; }
  }
  if (event.target.id === 'cancelRestoreButton') {
    if (confirm('Cancel the staged restore? Your current HomeServer data will remain unchanged.')) {
      try { await api('/api/v1/control/restore/pending', {method:'DELETE'}); await loadBackups(); flash('Staged restore cancelled.'); }
      catch (err) { flash(err.message, true); }
    }
  }
  const deleteBackup = event.target.closest('[data-delete-backup]');
  if (deleteBackup && confirm(`Delete ${deleteBackup.dataset.deleteBackup}?`)) {
    try { await api(`/api/v1/control/backups/${encodeURIComponent(deleteBackup.dataset.deleteBackup)}`, {method:'DELETE'}); await loadBackups(); flash('Backup deleted.'); }
    catch (err) { flash(err.message, true); }
  }
  if (event.target.id === 'reindexKnowledge') {
    try { const result = await api('/api/v1/control/knowledge/reindex', {method:'POST'}); await loadKnowledge(); flash(`Reindexed ${result.items} items into ${result.chunks} chunks.`); }
    catch (err) { flash(err.message, true); }
  }
  const deleteKnowledge = event.target.closest('[data-delete-knowledge]');
  if (deleteKnowledge && confirm('Delete this knowledge item?')) { try { await api(`/api/v1/control/knowledge/${deleteKnowledge.dataset.deleteKnowledge}`, {method:'DELETE'}); await loadKnowledge(); flash('Knowledge item deleted.'); } catch (err) { flash(err.message, true); } }
  const deleteMemory = event.target.closest('[data-delete-memory]');
  if (deleteMemory && confirm('Delete this memory?')) { try { await api(`/api/v1/control/memory/${deleteMemory.dataset.deleteMemory}`, {method:'DELETE'}); await loadMemory(); flash('Memory deleted.'); } catch (err) { flash(err.message, true); } }
});

document.addEventListener('change', async (event) => {
  if (event.target.id === 'activityUnreadOnly') {
    state.activityUnreadOnly=Boolean(event.target.checked);
    await loadActivity();
    return;
  }
  if (event.target.id === 'knowledgeFiles') { await importKnowledgeFiles(); return; }
  if (event.target.id === 'restoreBackupFile') {
    const file = event.target.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append('file', file, file.name);
    event.target.value = '';
    try {
      const result = await api('/api/v1/control/restore/stage', {method:'POST', body:form});
      await loadBackups();
      flash(result.message || 'Restore validated and staged. Restart HomeServer to apply it.');
    } catch (err) { flash(err.message, true); }
    return;
  }
  const status = event.target.closest('[data-app-status]');
  if (status) { try { await api(`/api/v1/control/apps/${status.dataset.appStatus}`, {method:'PATCH', body:JSON.stringify({status:status.value})}); flash('Application status updated.'); await loadApps(); } catch (err) { flash(err.message, true); } }
  const permission = event.target.closest('[data-app-permission]');
  if (permission) { try { await api(`/api/v1/control/apps/${permission.dataset.appPermission}/permission`, {method:'PUT', body:JSON.stringify({permission:permission.dataset.permission, allowed:permission.checked})}); flash('Permission updated.'); } catch (err) { permission.checked = !permission.checked; flash(err.message, true); } }
});

document.addEventListener('submit', async (event) => {
  if (event.target.id !== 'backupPolicyForm') return;
  event.preventDefault();
  try {
    await api('/api/v1/control/backups/policy',{
      method:'PUT',
      body:JSON.stringify({
        include_app_data:Boolean($('backupIncludeAppData').checked),
        retain_manual:Number($('backupRetainManual').value),
        retain_automatic:Number($('backupRetainAutomatic').value),
        retain_pre_restore:Number($('backupRetainPreRestore').value)
      })
    });
    await loadBackups();
    flash('Backup policy updated.');
  } catch(error) { flash(error.message,true); }
});

$('agentForm').addEventListener('submit', async (event) => { event.preventDefault(); try { await api('/api/v1/control/agent', {method:'PUT', body:JSON.stringify({name:$('agentName').value, model:$('agentModel').value, instructions:$('agentInstructions').value})}); flash('Primary agent saved.'); await loadAgent(); } catch (err) { flash(err.message, true); } });
$('knowledgeForm').addEventListener('submit', async (event) => { event.preventDefault(); try { await api('/api/v1/control/knowledge', {method:'POST', body:JSON.stringify({title:$('knowledgeTitle').value, kind:$('knowledgeKind').value, content:$('knowledgeContent').value, source_path:$('knowledgeSource').value || null})}); event.target.reset(); $('knowledgeForm').classList.add('hidden'); await loadKnowledge(); flash('Knowledge added and indexed.'); } catch (err) { flash(err.message, true); } });
$('memoryForm').addEventListener('submit', async (event) => { event.preventDefault(); try { await api('/api/v1/control/memory', {method:'POST', body:JSON.stringify({memory_key:$('memoryKey').value || null, content:$('memoryContent').value, importance:Number($('memoryImportance').value)})}); event.target.reset(); $('memoryImportance').value = '0.5'; $('memoryForm').classList.add('hidden'); await loadMemory(); flash('Memory added.'); } catch (err) { flash(err.message, true); } });
$('pairingForm').addEventListener('submit', async (event) => { event.preventDefault(); try { const data = await api('/api/v1/pairing/approve', {method:'POST', body:JSON.stringify({code:$('pairingCode').value})}); $('pairingToken').classList.remove('hidden'); if (data.delivery === 'claim_token') { $('pairingToken').innerHTML = `<strong>Pairing approved.</strong><span class="muted">Return to ${esc(data.app_key)}. It can complete the connection automatically; there is no token to copy.</span>`; } else { $('pairingToken').innerHTML = `<strong>Legacy pairing approved — copy this token into the requesting app now.</strong>${esc(data.token || '')}<br><span class="muted">For security, HomeServer will not display this token again.</span>`; } $('pairingCode').value = ''; await loadApps(); flash(`${data.app_key} paired successfully.`); } catch (err) { flash(err.message, true); } });
$('knowledgeSearch').addEventListener('input', () => { clearTimeout(state.searchTimer); state.searchTimer = setTimeout(() => loadKnowledge().catch(err => flash(err.message, true)), 180); });
$('refreshButton').addEventListener('click', () => loadView(state.view).then(() => flash('HomeServer refreshed.')).catch(err => flash(err.message, true)));

ensureBackupWorkspace();
const viewNames = ['dashboard','agent','chat','tools','approvals','knowledge','memory','contacts','members','homeserver-apps','apps','backups','ambient','automation','federation','physical-world','activity'];
window.addEventListener('hashchange', () => { const next = location.hash.replace('#',''); if (viewNames.includes(next)) openView(next); });

ensureKnowledgeControls();
const initial = location.hash.replace('#','') || 'dashboard';
openView(viewNames.includes(initial) ? initial : 'dashboard');

refreshVp3CloudState();
setInterval(()=>{if(document.visibilityState==='visible')refreshVp3CloudState();},15000);
setInterval(()=>{if(document.visibilityState==='visible' && state.view==='activity')loadActivity().catch(()=>{});},30000);
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible'){refreshVp3CloudState();if(state.view==='activity')loadActivity().catch(()=>{});}});
