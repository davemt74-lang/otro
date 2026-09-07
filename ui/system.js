const byId = id => document.getElementById(id);
const escSystem = (value = '') => String(value).replace(/[&<>'\"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','\"':'&quot;'}[c]));
const bytes = value => { const n=Number(value||0); if(n<1024)return `${n} B`; if(n<1024*1024)return `${(n/1024).toFixed(1)} KB`; if(n<1024*1024*1024)return `${(n/(1024*1024)).toFixed(1)} MB`; return `${(n/(1024*1024*1024)).toFixed(1)} GB`; };

async function systemApi(path, options = {}) {
  const headers = {...(options.headers || {})};
  if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, {...options, headers});
  let payload = {};
  try { payload = await response.json(); } catch (_) {}
  if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
  return payload;
}

function systemFlash(message, error = false) {
  const node = byId('systemFlash');
  node.textContent = message;
  node.className = `system-flash show${error ? ' error' : ''}`;
  clearTimeout(systemFlash.timer);
  systemFlash.timer = setTimeout(() => node.className = 'system-flash', 4200);
}

function setupCard(label, description, done, href, action) {
  return `<article class="setup-card${done ? ' done' : ''}"><span class="setup-icon">${done ? '✓' : '·'}</span><h3>${escSystem(label)}</h3><p>${escSystem(description)}</p><a class="button secondary" href="${href}">${escSystem(action)}</a></article>`;
}

function renderSetup(setup) {
  const steps = setup.steps || {};
  byId('setupState').textContent = setup.complete ? 'Setup complete' : 'Setup in progress';
  byId('setupState').classList.toggle('complete', Boolean(setup.complete));
  byId('completeSetup').textContent = setup.complete ? 'Setup complete' : 'Mark setup complete';
  byId('completeSetup').disabled = Boolean(setup.complete);
  byId('setupGrid').innerHTML = [
    setupCard('Primary agent', 'Choose the local agent name, instructions and optional model override.', Boolean(steps.agent_configured), '/#agent', 'Open My Agent'),
    setupCard('Local model', 'Detect Ollama and enable a local model if you want HomeServer Agent Chat.', Boolean(steps.local_model_enabled), '/#agent', 'Configure Ollama'),
    setupCard('Connected app', 'Pair VP3 or another client only if you want it to use selected HomeServer capabilities.', Boolean(steps.app_connected), '/#apps', 'Connected Apps'),
    setupCard('Recovery backup', 'Create at least one validated local backup before relying on this device for persistent knowledge.', Boolean(steps.backup_created), '/#backups', 'Backup & Restore'),
  ].join('');
}

function diagnosticCard(title, status, rows) {
  const state = status === true ? 'good' : status === false ? 'bad' : 'warn';
  return `<article class="diagnostic-card"><header><h3>${escSystem(title)}</h3><span class="health-dot ${state}"></span></header><dl>${rows.map(([k,v]) => `<dt>${escSystem(k)}</dt><dd>${escSystem(v == null ? '—' : v)}</dd>`).join('')}</dl></article>`;
}

function renderDiagnostics(data) {
  const db = data.database || {};
  const ollama = data.ollama || {};
  const storage = data.data || {};
  const backup = data.backups || {};
  const security = data.owner_security || {};
  const runtime = data.runtime_control || {};
  const bootstrap = storage.bootstrap || {};
  const startup = data.startup || {};

  byId('systemVersion').textContent = data.version ? `v${data.version}` : 'Version unavailable';
  byId('diagnosticGrid').innerHTML = [
    diagnosticCard('SQLite', Boolean(db.ok), [['Integrity', db.quick_check], ['Schema', `${db.schema_version ?? '—'} / ${db.supported_schema_version ?? '—'}`], ['Foreign keys', db.foreign_key_violations === 0 ? 'clean' : db.foreign_key_violations]]),
    diagnosticCard('Ollama', ollama.enabled ? Boolean(ollama.reachable) : null, [['Enabled', ollama.enabled ? 'yes' : 'no'], ['Reachable', ollama.reachable ? 'yes' : 'no'], ['Model', ollama.model || 'not selected']]),
    diagnosticCard('Storage', storage.free_bytes > 512 * 1024 * 1024, [['Data folder', storage.path], ['Free space', bytes(storage.free_bytes)], ['Migration', bootstrap.migration || 'not recorded']]),
    diagnosticCard('Backups', backup.error ? false : (backup.count > 0 ? true : null), [['Archives', backup.count ?? 0], ['Pending restore', backup.pending_restore ? (backup.pending_restore.valid === false ? 'invalid' : 'yes') : 'no'], ['Latest', backup.latest?.name || 'none']]),
    diagnosticCard('Owner security', Boolean(security.exists), [['Protection', security.protection], ['Secret store', security.exists ? 'ready' : 'missing'], ['Recovered store', security.recovered_corrupt_secret ? 'yes' : 'no']]),
    diagnosticCard('Runtime', runtime.available ? true : null, [['Control', runtime.available ? 'supervised' : 'external/dev'], ['Start with Windows', startup.enabled ? 'enabled' : 'disabled'], ['Endpoint', data.endpoint]]),
  ].join('');

  byId('startupEnabled').checked = Boolean(startup.enabled);
  byId('startupEnabled').disabled = !startup.supported;
  byId('startupHelp').textContent = startup.supported ? 'Managed through your Windows user startup registry.' : 'Available from the installed HomeServer.exe on Windows.';

  if (bootstrap.warning) systemFlash(bootstrap.warning, true);
}

async function refreshSystem() {
  const data = await systemApi('/api/v1/control/system');
  renderSetup(data.setup || {});
  renderDiagnostics(data.diagnostics || {});
}

byId('completeSetup').addEventListener('click', async () => {
  try {
    const result = await systemApi('/api/v1/control/system/setup', {method:'POST', body:JSON.stringify({complete:true})});
    renderSetup(result.setup);
    systemFlash('First-run setup marked complete.');
  } catch (err) { systemFlash(err.message, true); }
});

byId('refreshDiagnostics').addEventListener('click', () => refreshSystem().then(() => systemFlash('Diagnostics refreshed.')).catch(err => systemFlash(err.message, true)));

byId('startupEnabled').addEventListener('change', async event => {
  const checkbox = event.target;
  const desired = checkbox.checked;
  checkbox.disabled = true;
  try {
    const result = await systemApi('/api/v1/control/system/startup', {method:'PUT', body:JSON.stringify({enabled:desired})});
    checkbox.checked = Boolean(result.startup.enabled);
    systemFlash(result.startup.enabled ? 'HomeServer will start with Windows.' : 'Start with Windows disabled.');
  } catch (err) {
    checkbox.checked = !desired;
    systemFlash(err.message, true);
  } finally { checkbox.disabled = false; }
});

byId('openDataFolder').addEventListener('click', async () => {
  try { await systemApi('/api/v1/control/system/open-data-folder', {method:'POST'}); }
  catch (err) { systemFlash(err.message, true); }
});

byId('restartHomeServer').addEventListener('click', async () => {
  if (!confirm('Restart HomeServer now? Active local requests will finish before shutdown.')) return;
  try {
    systemFlash('Restart requested. HomeServer will reopen with a new owner session.');
    await systemApi('/api/v1/control/system/restart', {method:'POST'});
  } catch (err) { systemFlash(err.message, true); }
});

byId('shutdownHomeServer').addEventListener('click', async () => {
  if (!confirm('Quit HomeServer? Paired apps will be unavailable until it is launched again.')) return;
  try {
    systemFlash('Shutdown requested.');
    await systemApi('/api/v1/control/system/shutdown', {method:'POST'});
  } catch (err) { systemFlash(err.message, true); }
});

refreshSystem().catch(err => systemFlash(err.message, true));
