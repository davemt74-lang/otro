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
  remoteById('brokerUrl').value = settings.broker_url || '';
  remoteById('bridgeEnabled').checked = Boolean(settings.enabled);
  remoteById('deviceId').textContent = identity.device_id || '—';
  remoteById('identityProtection').textContent = identity.protection || '—';
  remoteById('connectedState').textContent = runtime.connected ? 'Connected' : (settings.enabled ? 'Disconnected' : 'Disabled');
  remoteById('pairingState').textContent = runtime.claimed ? (pendingVp3 ? 'Waiting for local approval' : 'Claimed by Cloud') : (runtime.connected && runtime.claim_code ? 'Ready for VP3 token' : 'Not paired');
  remoteById('lastConnected').textContent = remoteFmt(runtime.last_connected_at);

  const status = remoteById('remoteStatus');
  status.textContent = runtime.connected ? 'Connected' : (settings.enabled ? 'Disconnected' : 'Disabled');
  status.className = `remote-status${runtime.connected ? ' connected' : settings.enabled ? ' warning' : ''}`;

  const pairingStatus = remoteById('pairingStartStatus');
  if (pendingVp3) {
    pairingStatus.textContent = 'VP3 account and device matched. Review the local permission request below and approve it to finish pairing.';
  } else if (runtime.claimed) {
    pairingStatus.textContent = 'This HomeServer is claimed by a Cloud connection. Manage its permissions in Connected Apps or disconnect it from VP3 Cloud before pairing another account.';
  } else if (settings.enabled && runtime.connected && runtime.claim_code) {
    pairingStatus.textContent = 'Secure relay device proof is ready. Paste the pairing token generated in your VP3 Cloud account.';
  } else if (settings.enabled && !runtime.connected) {
    pairingStatus.textContent = 'Remote Bridge is connecting to the secure relay.';
  } else if (settings.enabled) {
    pairingStatus.textContent = 'Connected to the relay. Waiting for private device proof…';
  } else {
    pairingStatus.textContent = 'Paste your VP3 pairing token. HomeServer will configure the official VP3 relay automatically.';
  }

  const pairButton = remoteById('pairVp3');
  if (pairButton) pairButton.disabled = Boolean(runtime.claimed || pendingVp3);
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
  const [bridge, apps] = await Promise.all([
    remoteApi('/api/v1/control/remote-bridge?limit=100'),
    remoteApi('/api/v1/control/connected-apps').catch(() => ({pending: []})),
  ]);
  pendingVp3 = (apps.pending || []).find(item => String(item.app_key || '').toLowerCase() === 'vp3') || null;
  renderRemote(bridge);
  return bridge;
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

async function waitForRelayProof() {
  for (let attempt = 0; attempt < 30; attempt++) {
    const current = await refreshRemote();
    if (current.runtime?.connected && current.runtime?.claim_code) return current;
    if (current.runtime?.last_error) throw new Error(current.runtime.last_error);
    await remoteSleep(500);
  }
  throw new Error('HomeServer could not establish the secure VP3 relay connection. Check Bridge activity and try again.');
}

remoteById('vp3PairingForm').addEventListener('submit', async event => {
  event.preventDefault();
  const button = remoteById('pairVp3');
  const input = remoteById('vp3PairingToken');
  const token = (input.value || '').trim().toUpperCase();
  if (!token) return remoteFlash('Paste the pairing token generated in VP3 Cloud.', true);
  button.disabled = true;
  try {
    const current = latestRemote || await refreshRemote();
    if (current.runtime?.claimed && !pendingVp3) throw new Error('This HomeServer is already claimed by a Cloud account.');

    if (!current.settings?.broker_url) {
      remoteById('pairingStartStatus').textContent = 'Loading the official VP3 relay configuration…';
      await remoteApi('/api/v1/control/remote-bridge/bootstrap-vp3', {method: 'POST'});
    } else if (!current.settings?.enabled) {
      await saveRemoteSettings(true);
    }

    remoteById('pairingStartStatus').textContent = 'Connecting HomeServer to the secure VP3 relay…';
    await waitForRelayProof();
    remoteById('pairingStartStatus').textContent = 'Validating the VP3 account token and binding this HomeServer…';
    await remoteApi('/api/v1/control/remote-bridge/pair-vp3', {
      method: 'POST',
      body: JSON.stringify({pairing_token: token}),
    });
    input.value = '';
    await refreshRemote();
    if (!pendingVp3) throw new Error('VP3 was matched, but the local permission request is not visible yet. Refresh and try again.');
    remoteFlash('VP3 account matched. Review the requested permissions and approve locally.');
  } catch (err) {
    remoteFlash(err.message, true);
  } finally {
    button.disabled = Boolean(latestRemote?.runtime?.claimed || pendingVp3);
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
