const state = { view: 'dashboard', apps: [] };
const $ = (id) => document.getElementById(id);
const esc = (value = '') => String(value).replace(/[&<>'\"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','\"':'&quot;'}[c]));
const fmt = (value) => value ? new Date(value).toLocaleString() : 'Never';

async function api(path, options = {}) {
  const response = await fetch(path, {headers: {'Content-Type':'application/json', ...(options.headers || {})}, ...options});
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
  const labels = {dashboard:'Overview',agent:'My Agent',knowledge:'Knowledge',memory:'Memory',apps:'Connected Apps',activity:'Activity'};
  $('pageTitle').textContent = labels[name] || 'HomeServer';
  loadView(name).catch(err => flash(err.message, true));
}

async function loadOverview() {
  const data = await api('/api/v1/control/overview');
  $('heroAgentName').textContent = data.agent?.name || 'HomeServer Agent';
  $('statKnowledge').textContent = data.counts.knowledge_items;
  $('statMemory').textContent = data.counts.memory_items;
  $('statApps').textContent = data.counts.paired_apps;
  $('statPairing').textContent = data.counts.pending_pairing;
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
  const q = encodeURIComponent($('knowledgeSearch')?.value || '');
  const data = await api(`/api/v1/control/knowledge?q=${q}`);
  $('knowledgeCount').textContent = `${data.items.length} item${data.items.length === 1 ? '' : 's'}`;
  $('knowledgeList').innerHTML = data.items.length ? data.items.map(item => `<article class="item-card"><div><h3>${esc(item.title)}</h3><p>${esc((item.content || '').slice(0, 900))}</p><div class="item-meta"><span class="tag">${esc(item.kind)}</span>${item.source_path ? `<span>${esc(item.source_path)}</span>` : ''}<span>${esc(fmt(item.updated_at))}</span></div></div><div><button class="icon-button danger" data-delete-knowledge="${item.id}">Delete</button></div></article>`).join('') : '<div class="panel empty-state">No knowledge items found.</div>';
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
}

document.addEventListener('click', async (event) => {
  const nav = event.target.closest('[data-view]'); if (nav) openView(nav.dataset.view);
  const go = event.target.closest('[data-go]'); if (go) openView(go.dataset.go);
  if (event.target.id === 'showKnowledgeForm') $('knowledgeForm').classList.remove('hidden');
  if (event.target.id === 'cancelKnowledge') $('knowledgeForm').classList.add('hidden');
  if (event.target.id === 'showMemoryForm') $('memoryForm').classList.remove('hidden');
  if (event.target.id === 'cancelMemory') $('memoryForm').classList.add('hidden');
  const deleteKnowledge = event.target.closest('[data-delete-knowledge]');
  if (deleteKnowledge && confirm('Delete this knowledge item?')) { try { await api(`/api/v1/control/knowledge/${deleteKnowledge.dataset.deleteKnowledge}`, {method:'DELETE'}); await loadKnowledge(); flash('Knowledge item deleted.'); } catch (err) { flash(err.message, true); } }
  const deleteMemory = event.target.closest('[data-delete-memory]');
  if (deleteMemory && confirm('Delete this memory?')) { try { await api(`/api/v1/control/memory/${deleteMemory.dataset.deleteMemory}`, {method:'DELETE'}); await loadMemory(); flash('Memory deleted.'); } catch (err) { flash(err.message, true); } }
});

document.addEventListener('change', async (event) => {
  const status = event.target.closest('[data-app-status]');
  if (status) { try { await api(`/api/v1/control/apps/${status.dataset.appStatus}`, {method:'PATCH', body:JSON.stringify({status:status.value})}); flash('Application status updated.'); await loadApps(); } catch (err) { flash(err.message, true); } }
  const permission = event.target.closest('[data-app-permission]');
  if (permission) { try { await api(`/api/v1/control/apps/${permission.dataset.appPermission}/permission`, {method:'PUT', body:JSON.stringify({permission:permission.dataset.permission, allowed:permission.checked})}); flash('Permission updated.'); } catch (err) { permission.checked = !permission.checked; flash(err.message, true); } }
});

$('agentForm').addEventListener('submit', async (event) => { event.preventDefault(); try { await api('/api/v1/control/agent', {method:'PUT', body:JSON.stringify({name:$('agentName').value, model:$('agentModel').value, instructions:$('agentInstructions').value})}); flash('Primary agent saved.'); await loadAgent(); } catch (err) { flash(err.message, true); } });
$('knowledgeForm').addEventListener('submit', async (event) => { event.preventDefault(); try { await api('/api/v1/control/knowledge', {method:'POST', body:JSON.stringify({title:$('knowledgeTitle').value, kind:$('knowledgeKind').value, content:$('knowledgeContent').value, source_path:$('knowledgeSource').value || null})}); event.target.reset(); $('knowledgeForm').classList.add('hidden'); await loadKnowledge(); flash('Knowledge added.'); } catch (err) { flash(err.message, true); } });
$('memoryForm').addEventListener('submit', async (event) => { event.preventDefault(); try { await api('/api/v1/control/memory', {method:'POST', body:JSON.stringify({memory_key:$('memoryKey').value || null, content:$('memoryContent').value, importance:Number($('memoryImportance').value)})}); event.target.reset(); $('memoryImportance').value = '0.5'; $('memoryForm').classList.add('hidden'); await loadMemory(); flash('Memory added.'); } catch (err) { flash(err.message, true); } });
$('pairingForm').addEventListener('submit', async (event) => { event.preventDefault(); try { const data = await api('/api/v1/pairing/approve', {method:'POST', body:JSON.stringify({code:$('pairingCode').value})}); $('pairingToken').classList.remove('hidden'); $('pairingToken').innerHTML = `<strong>Pairing approved — copy this token into the requesting app now.</strong>${esc(data.token)}<br><span class="muted">For security, HomeServer will not display this token again.</span>`; $('pairingCode').value = ''; await loadApps(); flash(`${data.app_key} paired successfully.`); } catch (err) { flash(err.message, true); } });
$('knowledgeSearch').addEventListener('input', () => { clearTimeout(state.searchTimer); state.searchTimer = setTimeout(() => loadKnowledge().catch(err => flash(err.message, true)), 180); });
$('refreshButton').addEventListener('click', () => loadView(state.view).then(() => flash('HomeServer refreshed.')).catch(err => flash(err.message, true)));
window.addEventListener('hashchange', () => { const next = location.hash.replace('#',''); if (['dashboard','agent','knowledge','memory','apps','activity'].includes(next)) openView(next); });
const initial = location.hash.replace('#','') || 'dashboard';
openView(['dashboard','agent','knowledge','memory','apps','activity'].includes(initial) ? initial : 'dashboard');
