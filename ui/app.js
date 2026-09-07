const state = { view: 'dashboard', apps: [] };
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

function openView(name) {
  state.view = name;
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === `view-${name}`));
  document.querySelectorAll('.nav-item').forEach(v => v.classList.toggle('active', v.dataset.view === name));
  const labels = {dashboard:'Overview',agent:'My Agent',chat:'Agent Chat',tools:'Skills & Tools',knowledge:'Knowledge',memory:'Memory',apps:'Connected Apps',activity:'Activity'};
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
  if (description) {
    description.textContent = 'Import local documents or add notes. HomeServer extracts text, chunks it, and builds a private SQLite full-text index.';
  }

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

async function loadOverview() {
  const [data, status] = await Promise.all([
    api('/api/v1/control/overview'),
    api('/api/v1/status'),
  ]);
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
    } catch (err) {
      failures.push(`${file.name}: ${err.message}`);
    }
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
  const data = await api('/api/v1/control/activity?limit=200');
  $('activityTable').innerHTML = data.items.length ? data.items.map(item => `<tr><td>${esc(fmt(item.created_at))}</td><td>${esc(item.actor_type)}${item.actor_key ? ` · ${esc(item.actor_key)}` : ''}</td><td>${esc(item.action)}</td><td>${esc([item.resource_type, item.resource_key].filter(Boolean).join(' · '))}</td></tr>`).join('') : '<tr><td colspan="4">No activity yet.</td></tr>';
}

async function loadView(name) {
  if (name === 'dashboard') return loadOverview();
  if (name === 'agent') return loadAgent();
  if (name === 'knowledge') return loadKnowledge();
  if (name === 'memory') return loadMemory();
  if (name === 'apps') return loadApps();
  if (name === 'activity') return loadActivity();
  return Promise.resolve();
}

document.addEventListener('click', async (event) => {
  const nav = event.target.closest('[data-view]'); if (nav) openView(nav.dataset.view);
  const go = event.target.closest('[data-go]'); if (go) openView(go.dataset.go);
  if (event.target.id === 'showKnowledgeForm') $('knowledgeForm').classList.remove('hidden');
  if (event.target.id === 'cancelKnowledge') $('knowledgeForm').classList.add('hidden');
  if (event.target.id === 'showMemoryForm') $('memoryForm').classList.remove('hidden');
  if (event.target.id === 'cancelMemory') $('memoryForm').classList.add('hidden');
  if (event.target.id === 'importKnowledgeFiles') $('knowledgeFiles')?.click();
  if (event.target.id === 'reindexKnowledge') {
    try {
      const result = await api('/api/v1/control/knowledge/reindex', {method:'POST'});
      await loadKnowledge();
      flash(`Reindexed ${result.items} items into ${result.chunks} chunks.`);
    } catch (err) { flash(err.message, true); }
  }
  const deleteKnowledge = event.target.closest('[data-delete-knowledge]');
  if (deleteKnowledge && confirm('Delete this knowledge item?')) { try { await api(`/api/v1/control/knowledge/${deleteKnowledge.dataset.deleteKnowledge}`, {method:'DELETE'}); await loadKnowledge(); flash('Knowledge item deleted.'); } catch (err) { flash(err.message, true); } }
  const deleteMemory = event.target.closest('[data-delete-memory]');
  if (deleteMemory && confirm('Delete this memory?')) { try { await api(`/api/v1/control/memory/${deleteMemory.dataset.deleteMemory}`, {method:'DELETE'}); await loadMemory(); flash('Memory deleted.'); } catch (err) { flash(err.message, true); } }
});

document.addEventListener('change', async (event) => {
  if (event.target.id === 'knowledgeFiles') { await importKnowledgeFiles(); return; }
  const status = event.target.closest('[data-app-status]');
  if (status) { try { await api(`/api/v1/control/apps/${status.dataset.appStatus}`, {method:'PATCH', body:JSON.stringify({status:status.value})}); flash('Application status updated.'); await loadApps(); } catch (err) { flash(err.message, true); } }
  const permission = event.target.closest('[data-app-permission]');
  if (permission) { try { await api(`/api/v1/control/apps/${permission.dataset.appPermission}/permission`, {method:'PUT', body:JSON.stringify({permission:permission.dataset.permission, allowed:permission.checked})}); flash('Permission updated.'); } catch (err) { permission.checked = !permission.checked; flash(err.message, true); } }
});

$('agentForm').addEventListener('submit', async (event) => { event.preventDefault(); try { await api('/api/v1/control/agent', {method:'PUT', body:JSON.stringify({name:$('agentName').value, model:$('agentModel').value, instructions:$('agentInstructions').value})}); flash('Primary agent saved.'); await loadAgent(); } catch (err) { flash(err.message, true); } });
$('knowledgeForm').addEventListener('submit', async (event) => { event.preventDefault(); try { await api('/api/v1/control/knowledge', {method:'POST', body:JSON.stringify({title:$('knowledgeTitle').value, kind:$('knowledgeKind').value, content:$('knowledgeContent').value, source_path:$('knowledgeSource').value || null})}); event.target.reset(); $('knowledgeForm').classList.add('hidden'); await loadKnowledge(); flash('Knowledge added and indexed.'); } catch (err) { flash(err.message, true); } });
$('memoryForm').addEventListener('submit', async (event) => { event.preventDefault(); try { await api('/api/v1/control/memory', {method:'POST', body:JSON.stringify({memory_key:$('memoryKey').value || null, content:$('memoryContent').value, importance:Number($('memoryImportance').value)})}); event.target.reset(); $('memoryImportance').value = '0.5'; $('memoryForm').classList.add('hidden'); await loadMemory(); flash('Memory added.'); } catch (err) { flash(err.message, true); } });
$('pairingForm').addEventListener('submit', async (event) => { event.preventDefault(); try { const data = await api('/api/v1/pairing/approve', {method:'POST', body:JSON.stringify({code:$('pairingCode').value})}); $('pairingToken').classList.remove('hidden'); if (data.delivery === 'claim_token') { $('pairingToken').innerHTML = `<strong>Pairing approved.</strong><span class="muted">Return to ${esc(data.app_key)}. It can complete the connection automatically; there is no token to copy.</span>`; } else { $('pairingToken').innerHTML = `<strong>Legacy pairing approved — copy this token into the requesting app now.</strong>${esc(data.token || '')}<br><span class="muted">For security, HomeServer will not display this token again.</span>`; } $('pairingCode').value = ''; await loadApps(); flash(`${data.app_key} paired successfully.`); } catch (err) { flash(err.message, true); } });
$('knowledgeSearch').addEventListener('input', () => { clearTimeout(state.searchTimer); state.searchTimer = setTimeout(() => loadKnowledge().catch(err => flash(err.message, true)), 180); });
$('refreshButton').addEventListener('click', () => loadView(state.view).then(() => flash('HomeServer refreshed.')).catch(err => flash(err.message, true)));
const viewNames = ['dashboard','agent','chat','tools','knowledge','memory','apps','activity'];
window.addEventListener('hashchange', () => { const next = location.hash.replace('#',''); if (viewNames.includes(next)) openView(next); });

ensureKnowledgeControls();
const initial = location.hash.replace('#','') || 'dashboard';
openView(viewNames.includes(initial) ? initial : 'dashboard');
