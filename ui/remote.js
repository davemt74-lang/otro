const remoteById = id => document.getElementById(id);
const remoteEsc = (value = '') => String(value).replace(/[&<>'\"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','\"':'&quot;'}[c]));
const remoteFmt = value => value ? new Date(value).toLocaleString() : 'Never';
const remoteSleep = ms => new Promise(resolve => setTimeout(resolve, ms));

let latestRemote = null;
let pendingVp3 = null;

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

function renderApproval() {
  const panel = remoteById('vp3ApprovalPanel');
  const permissions = remoteById('vp3RequestedPermissions');
  if (!pendingVp3) {
    panel.classList.add('hidden');
    permissions.innerHTML = '';
    return;
  }
  panel.classList.remove('hidden');
  permissions.innerHTML = (pendingVp3.requested_permissions || []).map(permission => `<span>${remoteEsc(permission)}</span>`).join('') || '<span>No capabilities requested</span>';
}

function renderRemote(data) {
  latestRemote = data;
  const settings = data.settings || {};
  const runtime = data.runtime || {};
  const identity = data.identity || {};
  const canonical = data.cloud_connection || {};
  const cloud = canonical.cloud || {};
  remoteById('brokerUrl').value = settings.broker_url || '';
  remoteById('bridgeEnabled').checked = Boolean(settings.enabled);
  remoteById('deviceId').textContent = identity.device_id || '—';
  remoteById('identityProtection').textContent = identity.protection || '—';
  const transport = settings.transport || data.transport || 'custom_websocket';
  const standardHttps = transport === 'vp3_https';
  const connected = standardHttps ? Boolean(cloud.connected) : Boolean(runtime.connected);
  const paired = standardHttps ? Boolean(cloud.paired) : Boolean(runtime.claimed);
  const stateLabel = standardHttps
    ? (cloud.connected ? 'Connected' : cloud.state === 'reconnecting' ? 'Reconnecting' : cloud.paired ? 'Offline' : 'Not connected')
    : (runtime.connected ? 'Connected' : settings.enabled ? 'Disconnected' : 'Disabled');
  remoteById('connectedState').textContent = stateLabel;
  remoteById('transportMode').textContent = standardHttps ? (cloud.transport_label || 'VP3 HTTPS Relay') : 'Custom WebSocket Relay';
  remoteById('pairingState').textContent = paired ? 'Paired with VP3 Cloud' : 'Not paired';
  remoteById('lastConnected').textContent = remoteFmt(standardHttps ? cloud.last_seen_at : runtime.last_connected_at);

  const status = remoteById('remoteStatus');
  status.textContent = stateLabel;
  status.className = `remote-status${connected ? ' connected' : (paired || settings.enabled) ? ' warning' : ''}`;

  const pairingStatus = remoteById('pairingStartStatus');
  if (paired && connected) {
    pairingStatus.textContent = 'Connected to VP3 Cloud. HomeServer is maintaining the secure HTTPS command and heartbeat channel automatically.';
  } else if (paired) {
    pairingStatus.textContent = 'Paired with VP3 Cloud. HomeServer is reconnecting automatically.';
  } else {
    pairingStatus.textContent = 'Paste the pairing key from VP3 and click Pair. Everything else is configured automatically.';
  }

  const pairButton = remoteById('pairVp3');
  if (pairButton) pairButton.disabled = paired;
  renderApproval();

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
  const [bridge, canonical, apps] = await Promise.all([
    remoteApi('/api/v1/control/remote-bridge?limit=100'),
    remoteApi('/api/v1/control/cloud-connection'),
    remoteApi('/api/v1/control/connected-apps').catch(() => ({pending: []})),
  ]);
  pendingVp3 = (apps.pending || []).find(item => String(item.app_key || '').toLowerCase() === 'vp3') || null;
  const data = {...bridge, cloud_connection: canonical};
  renderRemote(data);
  return data;
}

async function saveRemoteSettings(enabled) {
  const brokerUrl = remoteById('brokerUrl').value.trim();
  if (!brokerUrl) throw new Error('Enter a relay WebSocket URL for a custom relay, or use VP3 pairing to configure the official relay automatically.');
  const result = await remoteApi('/api/v1/control/remote-bridge', {
    method: 'PUT',
    body: JSON.stringify({enabled, broker_url: brokerUrl}),
  });
  renderRemote({...result.status, events: latestRemote?.events || []});
  return result;
}

remoteById('vp3PairingForm').addEventListener('submit', async event => {
  event.preventDefault();
  const button = remoteById('pairVp3');
  const input = remoteById('vp3PairingToken');
  const token = (input.value || '').trim().toUpperCase();
  if (!token) return remoteFlash('Paste the pairing key generated in VP3 Cloud.', true);
  button.disabled = true;
  try {
    remoteById('pairingStartStatus').textContent = 'Pairing this HomeServer with VP3 Cloud…';
    await remoteApi('/api/v1/control/remote-bridge/pair-vp3', {
      method: 'POST',
      body: JSON.stringify({pairing_token: token}),
    });
    input.value = '';
    remoteById('pairingStartStatus').textContent = 'Paired. Starting the secure VP3 HTTPS connection…';
    await remoteSleep(800);
    await refreshRemote();
    remoteFlash('HomeServer paired with VP3 Cloud. Connection and reconnection are automatic.');
  } catch (err) {
    remoteFlash(err.message, true);
    await refreshRemote().catch(() => {});
  } finally {
    button.disabled = Boolean(latestRemote?.cloud_connection?.cloud?.paired || latestRemote?.runtime?.claimed);
  }
});

remoteById('approveVp3Pairing').addEventListener('click', async event => {
  const button = event.currentTarget;
  if (!pendingVp3?.id) return remoteFlash('No pending VP3 permission request was found.', true);
  button.disabled = true;
  try {
    await remoteApi('/api/v1/control/remote-bridge/approve-vp3', {
      method: 'POST',
      body: JSON.stringify({pairing_id: Number(pendingVp3.id)}),
    });
    pendingVp3 = null;
    renderApproval();
    await refreshRemote();
    remoteFlash('VP3 approved locally. VP3 Cloud will finish the connection automatically.');
  } catch (err) {
    remoteFlash(err.message, true);
  } finally {
    button.disabled = false;
  }
});

remoteById('remoteForm').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.submitter;
  if (button) button.disabled = true;
  try {
    const result = await saveRemoteSettings(remoteById('bridgeEnabled').checked);
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
setInterval(() => refreshRemote().catch(() => {}), 3000);
