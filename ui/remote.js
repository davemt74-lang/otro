const byId = id => document.getElementById(id);

let latestStatus = null;
let replaceMode = false;
let busy = false;

function fmt(value) {
  if (!value) return 'Never';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function flash(message, error = false) {
  const node = byId('remoteFlash');
  node.textContent = message;
  node.className = `remote-flash show${error ? ' error' : ''}`;
  clearTimeout(flash.timer);
  flash.timer = setTimeout(() => node.className = 'remote-flash', 3600);
}

async function api(path, options = {}) {
  const headers = {...(options.headers || {})};
  if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, {cache: 'no-store', ...options, headers});
  let payload = {};
  try { payload = await response.json(); } catch (_) {}
  if (!response.ok) throw new Error(payload.detail || payload.error || `Request failed (${response.status})`);
  return payload;
}

function setBusy(value) {
  busy = Boolean(value);
  document.querySelectorAll('button,input').forEach(node => {
    node.disabled = busy;
  });
}

function stateLabel(cloud) {
  if (cloud.connected) return 'Connected';
  if (cloud.state === 'reconnecting') return 'Reconnecting';
  if (cloud.paired) return 'Offline';
  return 'Not paired';
}

function render(status) {
  latestStatus = status || {};
  const cloud = latestStatus.cloud || {};
  const service = latestStatus.service || {};
  const app = latestStatus.vp3_app || {};
  const paired = Boolean(cloud.paired);

  const label = stateLabel(cloud);
  const badge = byId('connectionBadge');
  const savedBadge = byId('savedPairingState');
  badge.textContent = label;
  savedBadge.textContent = label;
  badge.dataset.state = cloud.connected ? 'connected' : paired ? 'reconnecting' : 'offline';
  savedBadge.dataset.state = badge.dataset.state;

  byId('pairingFormCard').hidden = paired && !replaceMode;
  byId('savedPairingCard').hidden = !paired;
  byId('cancelReplace').hidden = !replaceMode;

  byId('connectionState').textContent = label;
  byId('deviceId').textContent = latestStatus.identity?.device_id || app.device_id || '—';
  byId('transportMode').textContent = cloud.transport_label || 'VP3 HTTPS Relay';
  byId('lastConnected').textContent = fmt(cloud.last_seen_at);
  byId('homeServerVersion').textContent = service.version ? `v${service.version}` : '—';
  byId('identityProtection').textContent = latestStatus.identity?.protection || 'windows-dpapi';

  const message = byId('connectionMessage');
  if (cloud.connected) {
    message.textContent = 'Pairing is saved and the secure VP3 HTTPS connection is active.';
  } else if (paired) {
    message.textContent = cloud.last_error
      ? `Pairing is saved. HomeServer is retrying automatically: ${cloud.last_error}`
      : 'Pairing is saved. HomeServer is reconnecting automatically.';
  } else {
    message.textContent = 'No VP3 pairing is saved on this HomeServer.';
  }

  const error = byId('pairingError');
  if (paired && cloud.last_error && !cloud.connected) {
    error.hidden = false;
    error.textContent = cloud.last_error;
  } else {
    error.hidden = true;
    error.textContent = '';
  }
}

async function refresh() {
  const [status, bridge] = await Promise.all([
    api('/api/v1/control/cloud-connection'),
    api('/api/v1/control/remote-bridge?limit=1').catch(() => ({identity: {}})),
  ]);
  status.identity = bridge.identity || {};
  render(status);
  return status;
}

byId('vp3PairingForm').addEventListener('submit', async event => {
  event.preventDefault();
  if (busy) return;
  const tokenInput = byId('vp3PairingToken');
  const pairingToken = String(tokenInput.value || '').trim().toUpperCase();
  if (!pairingToken) {
    flash('Paste the pairing key from VP3 Cloud.', true);
    return;
  }

  setBusy(true);
  try {
    byId('pairingHelp').textContent = 'Saving pairing and starting the VP3 HTTPS connection…';
    await api('/api/v1/control/cloud-connection/pair', {
      method: 'POST',
      body: JSON.stringify({pairing_token: pairingToken}),
    });
    tokenInput.value = '';
    replaceMode = false;
    await refresh();

    for (let attempt = 0; attempt < 10 && !latestStatus?.cloud?.connected; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 500));
      await refresh();
    }

    flash(
      latestStatus?.cloud?.connected
        ? 'Paired with VP3. Connection is active.'
        : 'Pairing saved. HomeServer is connecting automatically.'
    );
  } catch (error) {
    flash(error.message, true);
    await refresh().catch(() => {});
  } finally {
    byId('pairingHelp').textContent = 'The pairing key is used once. HomeServer securely stores the resulting VP3 session, not the one-time key.';
    setBusy(false);
  }
});

byId('replacePairing').addEventListener('click', () => {
  replaceMode = true;
  render(latestStatus);
  byId('vp3PairingToken').focus();
});

byId('cancelReplace').addEventListener('click', () => {
  replaceMode = false;
  byId('vp3PairingToken').value = '';
  render(latestStatus);
});

byId('disconnectPairing').addEventListener('click', async () => {
  if (busy || !confirm('Disconnect this HomeServer from VP3 Cloud?')) return;
  setBusy(true);
  try {
    await api('/api/v1/control/cloud-connection', {method: 'DELETE'});
    replaceMode = false;
    await refresh();
    flash('HomeServer disconnected from VP3 Cloud.');
  } catch (error) {
    flash(error.message, true);
  } finally {
    setBusy(false);
  }
});

byId('refreshPairing').addEventListener('click', async () => {
  if (busy) return;
  setBusy(true);
  try {
    await refresh();
    flash('Connection status refreshed.');
  } catch (error) {
    flash(error.message, true);
  } finally {
    setBusy(false);
  }
});

refresh().catch(error => {
  byId('connectionBadge').textContent = 'Status unavailable';
  byId('pairingError').hidden = false;
  byId('pairingError').textContent = error.message;
});
setInterval(() => {
  if (document.visibilityState === 'visible' && !busy) refresh().catch(() => {});
}, 3000);
