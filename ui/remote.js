const remoteById = id => document.getElementById(id);
const remoteEsc = (value = '') => String(value).replace(/[&<>'\"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','\"':'&quot;'}[c]));
const remoteFmt = value => value ? new Date(value).toLocaleString() : 'Never';

async function remoteApi(path, options = {}) {
  const headers = {...(options.headers || {})};
  if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, {...options, headers});
  let payload = {};
  try { payload = await response.json(); } catch (_) {}
  if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
  return payload;
}

function remoteFlash(message, error = false) {
  const node = remoteById('remoteFlash');
  node.textContent = message;
  node.className = `remote-flash show${error ? ' error' : ''}`;
  clearTimeout(remoteFlash.timer);
  remoteFlash.timer = setTimeout(() => node.className = 'remote-flash', 4200);
}

function renderRemote(data) {
  const settings = data.settings || {};
  const runtime = data.runtime || {};
  const identity = data.identity || {};
  remoteById('brokerUrl').value = settings.broker_url || '';
  remoteById('bridgeEnabled').checked = Boolean(settings.enabled);
  remoteById('deviceId').textContent = identity.device_id || '—';
  remoteById('identityProtection').textContent = identity.protection || '—';
  remoteById('connectedState').textContent = runtime.connected ? 'Connected' : (settings.enabled ? 'Disconnected' : 'Disabled');
  remoteById('lastConnected').textContent = remoteFmt(runtime.last_connected_at);

  const status = remoteById('remoteStatus');
  status.textContent = runtime.connected ? 'Connected' : (settings.enabled ? 'Disconnected' : 'Disabled');
  status.className = `remote-status${runtime.connected ? ' connected' : settings.enabled ? ' warning' : ''}`;

  const claim = remoteById('claimPanel');
  if (runtime.claim_code && !runtime.claimed) {
    claim.classList.remove('hidden');
    remoteById('claimCode').textContent = runtime.claim_code;
  } else {
    claim.classList.add('hidden');
    remoteById('claimCode').textContent = '';
  }

  const error = remoteById('remoteError');
  if (runtime.last_error) {
    error.textContent = runtime.last_error;
    error.classList.remove('hidden');
  } else {
    error.textContent = '';
    error.classList.add('hidden');
  }

  const events = data.events || [];
  remoteById('remoteEvents').innerHTML = events.length ? events.map(item => `
    <div class="remote-event">
      <div><strong>${remoteEsc(item.event)}</strong><span>${remoteEsc(item.status)}</span></div>
      <div><span>${remoteEsc(item.operation || '—')}</span><span>${remoteEsc(item.request_id || '')}</span><span>${remoteEsc(remoteFmt(item.created_at))}</span></div>
    </div>`).join('') : '<p class="muted">No bridge events yet.</p>';
}

async function refreshRemote() {
  const data = await remoteApi('/api/v1/control/remote-bridge?limit=100');
  renderRemote(data);
}

remoteById('remoteForm').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.submitter;
  if (button) button.disabled = true;
  try {
    const result = await remoteApi('/api/v1/control/remote-bridge', {
      method: 'PUT',
      body: JSON.stringify({
        enabled: remoteById('bridgeEnabled').checked,
        broker_url: remoteById('brokerUrl').value.trim(),
      }),
    });
    renderRemote({...result.status, events: []});
    await refreshRemote();
    remoteFlash(result.settings.enabled ? 'Remote Bridge enabled. The outbound worker will apply the settings locally.' : 'Remote Bridge disabled.');
  } catch (err) {
    remoteFlash(err.message, true);
  } finally {
    if (button) button.disabled = false;
  }
});

remoteById('refreshRemote').addEventListener('click', () => refreshRemote().then(() => remoteFlash('Remote bridge status refreshed.')).catch(err => remoteFlash(err.message, true)));

refreshRemote().catch(err => remoteFlash(err.message, true));
setInterval(() => refreshRemote().catch(() => {}), 5000);
