const remoteById = id => document.getElementById(id);
const remoteEsc = (value = '') => String(value).replace(/[&<>'\"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','\"':'&quot;'}[c]));
const remoteFmt = value => value ? new Date(value).toLocaleString() : 'Never';

let latestRemote = null;
let pairingPollUntil = 0;

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
  latestRemote = data;
  const settings = data.settings || {};
  const runtime = data.runtime || {};
  const identity = data.identity || {};
  remoteById('brokerUrl').value = settings.broker_url || '';
  remoteById('bridgeEnabled').checked = Boolean(settings.enabled);
  remoteById('deviceId').textContent = identity.device_id || '—';
  remoteById('identityProtection').textContent = identity.protection || '—';
  remoteById('connectedState').textContent = runtime.connected ? 'Connected' : (settings.enabled ? 'Disconnected' : 'Disabled');
  remoteById('pairingState').textContent = runtime.claimed ? 'Claimed by Cloud' : (runtime.claim_code ? 'Ready to pair' : 'Not paired');
  remoteById('lastConnected').textContent = remoteFmt(runtime.last_connected_at);

  const status = remoteById('remoteStatus');
  status.textContent = runtime.connected ? 'Connected' : (settings.enabled ? 'Disconnected' : 'Disabled');
  status.className = `remote-status${runtime.connected ? ' connected' : settings.enabled ? ' warning' : ''}`;

  const claim = remoteById('claimPanel');
  const pairingStatus = remoteById('pairingStartStatus');
  if (runtime.claim_code && !runtime.claimed) {
    claim.classList.remove('hidden');
    remoteById('claimCode').textContent = runtime.claim_code;
    pairingStatus.textContent = 'Connection code ready. Continue in VP3 Cloud → Settings → HomeServer.';
    pairingPollUntil = 0;
  } else {
    claim.classList.add('hidden');
    remoteById('claimCode').textContent = '';
    if (runtime.claimed) {
      pairingStatus.textContent = 'This HomeServer is already claimed by a Cloud connection. Use Re-pair in VP3 Cloud for app authorization, or Disconnect → Remove Cloud pairing to release it for a new account.';
      pairingPollUntil = 0;
    } else if (settings.enabled && !runtime.connected) {
      pairingStatus.textContent = 'Remote Bridge is enabled but not connected. Check the relay URL and bridge activity below.';
    } else if (settings.enabled && runtime.connected) {
      pairingStatus.textContent = 'Connected to the relay. Waiting for a HomeServer connection code…';
    } else {
      pairingStatus.textContent = 'Start Pairing enables the outbound bridge using the configured relay URL and waits for a one-time HomeServer connection code.';
    }
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
  return data;
}

async function saveRemoteSettings(enabled) {
  const brokerUrl = remoteById('brokerUrl').value.trim();
  if (!brokerUrl) throw new Error('Configure the VP3 relay WebSocket URL before starting pairing.');
  const result = await remoteApi('/api/v1/control/remote-bridge', {
    method: 'PUT',
    body: JSON.stringify({enabled, broker_url: brokerUrl}),
  });
  renderRemote({...result.status, events: latestRemote?.events || []});
  return result;
}

remoteById('startPairing').addEventListener('click', async event => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const current = latestRemote || await refreshRemote();
    if (current.runtime?.claimed) {
      remoteFlash('This HomeServer is already claimed. Use VP3 Cloud → HomeServer → Re-pair, or remove the existing Cloud pairing first.', true);
      return;
    }
    remoteById('pairingStartStatus').textContent = 'Starting the outbound bridge and requesting a one-time connection code…';
    await saveRemoteSettings(true);
    pairingPollUntil = Date.now() + 30000;
    await refreshRemote();
    if (latestRemote?.runtime?.claim_code) {
      remoteFlash('HomeServer connection code ready.');
    } else {
      remoteFlash('Pairing started. Waiting for the secure relay connection.');
    }
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
setInterval(() => {
  if (pairingPollUntil && Date.now() > pairingPollUntil) pairingPollUntil = 0;
  refreshRemote().catch(() => {});
}, 3000);
