const byId = id => document.getElementById(id);
const escSystem = (value = '') => String(value).replace(/[&<>'\"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','\"':'&quot;'}[c]));
const bytes = value => { const n=Number(value||0); if(n<1024)return `${n} B`; if(n<1024*1024)return `${(n/1024).toFixed(1)} KB`; if(n<1024*1024*1024)return `${(n/(1024*1024)).toFixed(1)} MB`; return `${(n/(1024*1024*1024)).toFixed(1)} GB`; };

async function systemApi(path, options = {}) {
  const headers = {...(options.headers || {})};
  if (options.body && !(options.body instanceof FormData) && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
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



function experienceList(values) {
  return (values || []).length ? (values || []).join(', ') : 'none';
}

function experienceEventCard(item) {
  return '<article class="fleet-card"><div><strong>' +
    escSystem(item.event_type || 'event') + '</strong><span>' +
    escSystem(item.action || '—') + ' · ' + escSystem(item.outcome || 'observed') +
    '</span><small>' + escSystem(item.created_at || '') + '</small></div></article>';
}

function experienceDisplayCard(item) {
  return '<article class="fleet-card"><div><strong>' +
    escSystem(item.title || item.card_key) + '</strong><span>' +
    escSystem(item.card_type) + ' · priority ' + escSystem(item.priority) +
    '</span><small>' + escSystem(item.subtitle || item.state || '') +
    '</small></div><button class="text-button danger" data-experience-dismiss-card="' +
    escSystem(item.card_key) + '" type="button">Dismiss</button></article>';
}

function renderHardwareExperience(data) {
  const experience = data.experience || {};
  const profile = experience.profile || {};
  const settings = data.settings || {};
  const runtime = data.runtime || {};
  const visual = data.visual || {};
  const degraded = data.degraded || {};
  const certification = data.latest_certification || null;

  const state = byId('hardwareExperienceState');
  state.textContent = degraded.degraded ? 'Degraded' : (runtime.started ? 'Experience active' : 'Experience stopped');
  state.classList.toggle('complete', Boolean(runtime.started && !degraded.degraded));

  byId('hardwareExperienceEnabled').checked = settings.enabled !== false;
  byId('experienceBrightness').value = settings.brightness_percent ?? 70;
  byId('experienceVolume').value = settings.volume_percent ?? 65;
  byId('experienceLedIntensity').value = settings.led_intensity_percent ?? 70;
  byId('experienceScreenTimeout').value = settings.screen_timeout_seconds ?? 300;
  byId('experienceWakeBehavior').value = settings.wake_behavior || 'presence';
  byId('experienceAgentButton').value = settings.agent_button_action || 'push_to_talk';
  byId('experienceHoldAction').value = settings.hold_action || 'cancel';
  byId('experienceDisplayDetail').value = settings.display_detail || 'standard';
  byId('experienceQuietVisuals').checked = Boolean(settings.quiet_visuals);

  byId('hardwareExperienceSummary').innerHTML = [
    diagnosticCard('Product experience', !degraded.degraded, [['Profile', profile.label || profile.key || 'custom'], ['Experience', experience.experience || 'generic'], ['Certification', certification ? certification.result : 'not run']]),
    diagnosticCard('Visual state', visual.state !== 'error', [['State', visual.state || 'idle'], ['Reason', visual.reason || 'idle'], ['Light mode', visual.light_mode || 'off']]),
    diagnosticCard('Hardware degradation', degraded.degraded ? false : true, [['Missing', experienceList(degraded.missing_hardware)], ['Not ready', experienceList(degraded.not_ready_hardware)], ['Screen awake', runtime.screen_awake ? 'yes' : 'no']]),
  ].join('');

  const cards = data.cards || [];
  byId('hardwareExperienceCards').innerHTML = cards.length
    ? cards.map(experienceDisplayCard).join('')
    : '<div class="muted">No active display cards.</div>';
}

async function refreshHardwareExperience() {
  const [experience, events] = await Promise.all([
    systemApi('/api/v1/control/vp3-os/hardware-experience'),
    systemApi('/api/v1/control/vp3-os/hardware-experience/events?limit=12'),
  ]);
  renderHardwareExperience(experience);
  const items = events.items || [];
  byId('hardwareExperienceEvents').innerHTML = items.length
    ? items.map(experienceEventCard).join('')
    : '<div class="muted">No physical events recorded.</div>';
}

function rolloutStatusLabel(value) {
  return String(value || 'unknown').replaceAll('_', ' ');
}

function rolloutPackageCard(item) {
  const actions = [];
  if (item.status === 'staged') {
    actions.push('<button class="button secondary" data-rollout-approve="' + item.id + '" type="button">Approve + backup</button>');
  }
  if (item.status === 'approved') {
    actions.push('<button class="button primary" data-rollout-apply="' + item.id + '" type="button">Apply update</button>');
  }
  if (!['applying','applied'].includes(item.status)) {
    actions.push('<button class="text-button danger" data-rollout-discard="' + item.id + '" type="button">Discard</button>');
  }
  return '<article class="rollout-package"><div><strong>' +
    escSystem(item.version) + '</strong><span>' + escSystem(item.channel) + ' · ' +
    escSystem(rolloutStatusLabel(item.status)) + '</span><small>SHA-256 ' +
    escSystem((item.package_sha256 || '').slice(0,16)) + '…</small></div><div class="runtime-actions">' +
    actions.join('') + '</div></article>';
}

function renderRollout(data) {
  const settings = data.settings || {};
  const commissioning = data.commissioning || {};
  const profile = commissioning.profile || {};
  const hardware = commissioning.hardware || {};
  const state = commissioning.state || 'unknown';
  const stateNode = byId('commissioningState');
  stateNode.textContent = state === 'ready' ? 'Commissioned' : rolloutStatusLabel(state);
  stateNode.classList.toggle('complete', state === 'ready');

  byId('rolloutChannel').value = settings.release_channel || 'stable';
  byId('rolloutRing').value = settings.rollout_ring || 'pilot';
  byId('rolloutWatchdog').checked = settings.watchdog_enabled !== false;

  const missing = hardware.missing_hardware || [];
  const notReady = hardware.not_ready_hardware || [];
  const cert = data.latest_certification || null;
  byId('rolloutSummary').innerHTML = [
    diagnosticCard('VP3 OS', state === 'ready', [['Version', data.vp3_os_version || '—'], ['Profile', profile.label || profile.key || 'custom'], ['Commissioning', rolloutStatusLabel(state)]]),
    diagnosticCard('Hardware', hardware.result === 'passed' ? true : (hardware.result === 'failed' ? false : null), [['Certification', cert ? rolloutStatusLabel(cert.result) : 'not run'], ['Missing', missing.length ? missing.join(', ') : 'none'], ['Not ready', notReady.length ? notReady.join(', ') : 'none']]),
    diagnosticCard('Rollout', true, [['Channel', settings.release_channel || 'stable'], ['Ring', settings.rollout_ring || 'pilot'], ['Automatic apply', settings.automatic_apply ? 'enabled' : 'disabled']]),
  ].join('');

  const packages = data.packages || [];
  byId('rolloutPackages').innerHTML = packages.length
    ? packages.map(rolloutPackageCard).join('')
    : '<div class="muted">No staged updates.</div>';
}

async function refreshRollout() {
  const data = await systemApi('/api/v1/control/vp3-os/rollout');
  renderRollout(data);
}


function fleetIssueLabel(value) {
  return String(value || '').replaceAll('_', ' ');
}

function fleetDeviceCard(item) {
  const issues = (item.issues || []).map(fleetIssueLabel).join(', ') || 'none';
  return '<article class="fleet-card"><div><strong>' +
    escSystem(item.label || item.device_id) + '</strong><span>' +
    escSystem(item.profile_key) + ' · ' + escSystem(item.os_version) + ' · ' +
    escSystem(item.release_channel) + '/' + escSystem(item.rollout_ring) +
    '</span><small>' + escSystem(item.health) + ' · issues: ' + escSystem(issues) +
    '</small></div><button class="text-button danger" data-fleet-remove="' +
    escSystem(item.device_id) + '" type="button">Remove</button></article>';
}

function fleetRolloutCard(item) {
  const counts = item.counts || {};
  const actions = [];
  if (item.status === 'planned' || item.status === 'paused') {
    actions.push('<button class="button primary" data-fleet-rollout-status="active" data-fleet-rollout-id="' + item.id + '" type="button">Start / resume</button>');
  }
  if (item.status === 'active') {
    actions.push('<button class="button secondary" data-fleet-rollout-status="paused" data-fleet-rollout-id="' + item.id + '" type="button">Pause</button>');
    actions.push('<button class="button secondary" data-fleet-rollout-status="completed" data-fleet-rollout-id="' + item.id + '" type="button">Complete</button>');
  }
  if (!['completed','cancelled'].includes(item.status)) {
    actions.push('<button class="text-button danger" data-fleet-rollout-status="cancelled" data-fleet-rollout-id="' + item.id + '" type="button">Cancel</button>');
  }
  return '<article class="fleet-card"><div><strong>' + escSystem(item.release_version) +
    '</strong><span>' + escSystem(item.channel) + ' · ' + escSystem(item.rollout_ring) +
    ' · ' + escSystem(item.status) + '</span><small>Eligible ' +
    escSystem(item.eligible_devices) + ' · healthy ' + escSystem(counts.healthy || 0) +
    ' · failures ' + escSystem(counts.failures || 0) + '/' +
    escSystem(item.failure_threshold) + (item.pause_reason ? ' · ' + escSystem(item.pause_reason) : '') +
    '</small></div><div class="runtime-actions">' + actions.join('') + '</div></article>';
}

function fleetUpdateRequestCard(item) {
  const actions = item.status === 'pending_owner'
    ? '<button class="button primary" data-fleet-request-approve="' + item.id + '" type="button">Approve staged release</button>' +
      '<button class="text-button danger" data-fleet-request-dismiss="' + item.id + '" type="button">Dismiss</button>'
    : '';
  return '<article class="fleet-card"><div><strong>' + escSystem(item.release_version) +
    '</strong><span>' + escSystem(item.status) + ' · ' + escSystem(item.requester_app_key) +
    '</span><small>SHA-256 ' + escSystem((item.package_sha256 || '').slice(0,16)) +
    '…</small></div><div class="runtime-actions">' + actions + '</div></article>';
}

function renderFleet(data) {
  const settings = data.settings || {};
  const local = data.local_device || {};
  const inventory = data.inventory || [];
  const rollouts = data.rollouts || [];
  const requests = data.update_requests || [];
  const alerts = data.alerts || [];

  const state = byId('fleetState');
  state.textContent = settings.enabled ? 'Fleet enrolled' : 'Fleet disabled';
  state.classList.toggle('complete', Boolean(settings.enabled));

  byId('fleetEnabled').checked = Boolean(settings.enabled);
  byId('fleetControllerApp').value = settings.controller_app_key || '';
  byId('fleetDeviceLabel').value = settings.device_label || '';
  byId('fleetDiagnostics').checked = Boolean(settings.remote_diagnostics);
  byId('fleetSupportSummary').checked = Boolean(settings.remote_support_summary);
  byId('fleetUpdateRequests').checked = Boolean(settings.remote_update_requests);
  byId('fleetFailureThreshold').value = settings.rollout_failure_threshold || 2;
  byId('fleetStaleAfter').value = settings.stale_after_seconds || 900;
  if (!byId('fleetRolloutVersion').value) {
    byId('fleetRolloutVersion').value = data.vp3_os_version || 'v1.2';
  }

  const critical = alerts.filter(item => item.severity === 'error').length;
  byId('fleetSummary').innerHTML = [
    diagnosticCard('Local appliance', local.commissioning_state === 'ready', [['Version', local.os_version || '—'], ['Profile', local.profile_key || 'custom'], ['Ring', (local.release_channel || 'stable') + '/' + (local.rollout_ring || 'pilot')]]),
    diagnosticCard('Fleet inventory', critical === 0 ? true : false, [['Devices', inventory.length], ['Alerts', alerts.length], ['Critical', critical]]),
    diagnosticCard('Fleet rollouts', true, [['Tracked', rollouts.length], ['Active', rollouts.filter(item => item.status === 'active').length], ['Paused', rollouts.filter(item => item.status === 'paused').length]]),
  ].join('');

  byId('fleetInventory').innerHTML = inventory.length
    ? inventory.map(fleetDeviceCard).join('')
    : '<div class="muted">No fleet check-ins yet.</div>';
  byId('fleetRollouts').innerHTML = rollouts.length
    ? rollouts.map(fleetRolloutCard).join('')
    : '<div class="muted">No fleet rollouts yet.</div>';
  byId('fleetUpdateRequestsList').innerHTML = requests.length
    ? requests.map(fleetUpdateRequestCard).join('')
    : '<div class="muted">No fleet update requests.</div>';
  byId('fleetAlerts').innerHTML = alerts.length
    ? alerts.map(item => '<article class="fleet-alert ' + escSystem(item.severity) + '"><strong>' +
      escSystem(fleetIssueLabel(item.issue)) + '</strong><span>' +
      escSystem(item.label || item.device_id || ('Rollout ' + item.rollout_id)) +
      '</span></article>').join('')
    : '<div class="muted">No fleet alerts.</div>';
}

async function refreshFleet() {
  const data = await systemApi('/api/v1/control/vp3-os/fleet');
  renderFleet(data);
}

function renderPayments(data) {
  const stripe = data?.providers?.stripe || {};
  const state = byId('stripePaymentState');
  state.textContent = stripe.configured ? 'Local Stripe configured' : 'Cloud payments remain available';
  state.classList.toggle('complete', Boolean(stripe.configured));
  const details = [];
  if (stripe.configured) details.push(`Secret key ••••${stripe.secret_key_suffix || 'configured'}`);
  if (stripe.webhook_configured) details.push(`Webhook ••••${stripe.webhook_secret_suffix || 'configured'}`);
  details.push(`Protection: ${stripe.protection || 'local credential store'}`);
  byId('stripePaymentDetails').textContent = details.join(' · ');
  byId('verifyStripePayments').disabled = !stripe.configured;
  byId('clearStripePayments').disabled = !stripe.configured && !stripe.webhook_configured;
}

async function refreshPayments() {
  const data = await systemApi('/api/v1/control/payments');
  renderPayments(data);
}

async function refreshSystem() {
  const [system, payments, rollout, fleet, experience] = await Promise.all([
    systemApi('/api/v1/control/system'),
    systemApi('/api/v1/control/payments'),
    systemApi('/api/v1/control/vp3-os/rollout'),
    systemApi('/api/v1/control/vp3-os/fleet'),
    systemApi('/api/v1/control/vp3-os/hardware-experience'),
  ]);
  renderSetup(system.setup || {});
  renderDiagnostics(system.diagnostics || {});
  renderPayments(payments);
  renderRollout(rollout);
  renderFleet(fleet);
  renderHardwareExperience(experience);
}



byId('saveHardwareExperience').addEventListener('click', async () => {
  try {
    const result = await systemApi('/api/v1/control/vp3-os/hardware-experience/settings', {
      method:'PUT',
      body:JSON.stringify({
        enabled:byId('hardwareExperienceEnabled').checked,
        brightness_percent:Number(byId('experienceBrightness').value || 0),
        volume_percent:Number(byId('experienceVolume').value || 0),
        led_intensity_percent:Number(byId('experienceLedIntensity').value || 0),
        screen_timeout_seconds:Number(byId('experienceScreenTimeout').value || 300),
        wake_behavior:byId('experienceWakeBehavior').value,
        agent_button_action:byId('experienceAgentButton').value,
        hold_action:byId('experienceHoldAction').value,
        display_detail:byId('experienceDisplayDetail').value,
        quiet_visuals:byId('experienceQuietVisuals').checked,
      }),
    });
    await refreshHardwareExperience();
    systemFlash('Hardware experience saved · ' + result.settings.wake_behavior + ' wake.');
  } catch (err) { systemFlash(err.message, true); }
});

byId('certifyHardwareExperience').addEventListener('click', async () => {
  try {
    const result = await systemApi('/api/v1/control/vp3-os/hardware-experience/certifications', {method:'POST'});
    await refreshHardwareExperience();
    systemFlash('Product experience certification ' + result.result + '.');
  } catch (err) { systemFlash(err.message, true); }
});

byId('hardwareExperienceCards').addEventListener('click', async event => {
  const button = event.target.closest('[data-experience-dismiss-card]');
  if (!button) return;
  try {
    await systemApi('/api/v1/control/vp3-os/hardware-experience/cards/' + encodeURIComponent(button.dataset.experienceDismissCard) + '/state', {
      method:'PUT',
      body:JSON.stringify({state:'dismissed'}),
    });
    await refreshHardwareExperience();
    systemFlash('Display card dismissed.');
  } catch (err) { systemFlash(err.message, true); }
});

byId('saveRolloutSettings').addEventListener('click', async () => {
  try {
    const result = await systemApi('/api/v1/control/vp3-os/rollout/settings', {
      method:'PUT',
      body:JSON.stringify({
        release_channel:byId('rolloutChannel').value,
        rollout_ring:byId('rolloutRing').value,
        watchdog_enabled:byId('rolloutWatchdog').checked,
      }),
    });
    await refreshRollout();
    systemFlash('Rollout policy saved · ' + result.settings.release_channel + ' / ' + result.settings.rollout_ring + '.');
  } catch (err) { systemFlash(err.message, true); }
});

byId('certifyHardware').addEventListener('click', async () => {
  try {
    const result = await systemApi('/api/v1/control/vp3-os/certifications', {method:'POST'});
    await refreshRollout();
    systemFlash('Hardware certification ' + rolloutStatusLabel(result.result) + '.');
  } catch (err) { systemFlash(err.message, true); }
});

byId('stageRolloutPackage').addEventListener('click', async () => {
  const file = byId('rolloutPackage').files && byId('rolloutPackage').files[0];
  if (!file) return systemFlash('Choose a VP3 OS release ZIP first.', true);
  const form = new FormData();
  form.append('package', file);
  try {
    const result = await systemApi('/api/v1/control/vp3-os/updates/stage', {method:'POST', body:form});
    byId('rolloutPackage').value = '';
    await refreshRollout();
    systemFlash(result.version + ' validated and staged.');
  } catch (err) { systemFlash(err.message, true); }
});

byId('rolloutPackages').addEventListener('click', async event => {
  const approve = event.target.closest('[data-rollout-approve]');
  const apply = event.target.closest('[data-rollout-apply]');
  const discard = event.target.closest('[data-rollout-discard]');
  try {
    if (approve) {
      const id = approve.dataset.rolloutApprove;
      await systemApi('/api/v1/control/vp3-os/updates/' + id + '/approve', {method:'POST'});
      await refreshRollout();
      return systemFlash('Update approved and pre-update backup created.');
    }
    if (apply) {
      const id = apply.dataset.rolloutApply;
      if (!confirm('Apply this verified VP3 OS update now? HomeServer will shut down, install, restart, and restore the previous executable if health validation fails.')) return;
      systemFlash('Controlled update requested. HomeServer will close and restart.');
      await systemApi('/api/v1/control/vp3-os/updates/' + id + '/apply', {method:'POST'});
      return;
    }
    if (discard) {
      const id = discard.dataset.rolloutDiscard;
      await systemApi('/api/v1/control/vp3-os/updates/' + id + '/discard', {method:'POST'});
      await refreshRollout();
      return systemFlash('Staged update discarded.');
    }
  } catch (err) { systemFlash(err.message, true); }
});

byId('downloadSupportBundle').addEventListener('click', async () => {
  try {
    const response = await fetch('/api/v1/control/vp3-os/support-bundle', {method:'POST'});
    if (!response.ok) {
      let detail = 'Request failed (' + response.status + ')';
      try {
        const payload = await response.json();
        detail = payload.detail || detail;
      } catch (_) {}
      throw new Error(detail);
    }
    const blob = await response.blob();
    const disposition = response.headers.get('Content-Disposition') || '';
    const match = disposition.match(/filename="?([^";]+)"?/i);
    const name = match && match[1] ? match[1] : 'vp3-support.zip';
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    systemFlash('Sanitized support bundle created.');
  } catch (err) { systemFlash(err.message, true); }
});


byId('saveFleetSettings').addEventListener('click', async () => {
  try {
    const result = await systemApi('/api/v1/control/vp3-os/fleet/settings', {
      method:'PUT',
      body:JSON.stringify({
        enabled:byId('fleetEnabled').checked,
        controller_app_key:byId('fleetControllerApp').value.trim() || null,
        device_label:byId('fleetDeviceLabel').value.trim(),
        remote_diagnostics:byId('fleetDiagnostics').checked,
        remote_support_summary:byId('fleetSupportSummary').checked,
        remote_update_requests:byId('fleetUpdateRequests').checked,
        stale_after_seconds:Number(byId('fleetStaleAfter').value || 900),
        rollout_failure_threshold:Number(byId('fleetFailureThreshold').value || 2),
      }),
    });
    await refreshFleet();
    systemFlash(result.settings.enabled ? 'Fleet enrollment saved.' : 'Fleet remains disabled.');
  } catch (err) { systemFlash(err.message, true); }
});

byId('decommissionFleet').addEventListener('click', async () => {
  if (!confirm('Disable fleet access and revoke this controller app\'s fleet permissions? Local private data will not be deleted.')) return;
  try {
    await systemApi('/api/v1/control/vp3-os/fleet/decommission', {method:'POST'});
    await refreshFleet();
    systemFlash('Fleet access decommissioned. Private HomeServer data was left intact.');
  } catch (err) { systemFlash(err.message, true); }
});

byId('createFleetRollout').addEventListener('click', async () => {
  try {
    const rollout = await systemApi('/api/v1/control/vp3-os/fleet/rollouts', {
      method:'POST',
      body:JSON.stringify({
        release_version:byId('fleetRolloutVersion').value.trim(),
        channel:byId('fleetRolloutChannel').value,
        rollout_ring:byId('fleetRolloutRing').value,
        failure_threshold:Number(byId('fleetFailureThreshold').value || 2),
      }),
    });
    await refreshFleet();
    systemFlash('Fleet rollout ' + rollout.release_version + ' created.');
  } catch (err) { systemFlash(err.message, true); }
});

byId('fleetInventory').addEventListener('click', async event => {
  const remove = event.target.closest('[data-fleet-remove]');
  if (!remove) return;
  if (!confirm('Remove this device from the local fleet registry? The remote device and its private data will not be changed.')) return;
  try {
    await systemApi('/api/v1/control/vp3-os/fleet/devices/' + encodeURIComponent(remove.dataset.fleetRemove), {method:'DELETE'});
    await refreshFleet();
    systemFlash('Fleet registry entry removed.');
  } catch (err) { systemFlash(err.message, true); }
});

byId('fleetRollouts').addEventListener('click', async event => {
  const button = event.target.closest('[data-fleet-rollout-status]');
  if (!button) return;
  try {
    await systemApi('/api/v1/control/vp3-os/fleet/rollouts/' + button.dataset.fleetRolloutId + '/status', {
      method:'POST',
      body:JSON.stringify({status:button.dataset.fleetRolloutStatus, reason:'owner'}),
    });
    await refreshFleet();
    systemFlash('Fleet rollout updated.');
  } catch (err) { systemFlash(err.message, true); }
});

byId('fleetUpdateRequestsList').addEventListener('click', async event => {
  const approve = event.target.closest('[data-fleet-request-approve]');
  const dismiss = event.target.closest('[data-fleet-request-dismiss]');
  try {
    if (approve) {
      const result = await systemApi('/api/v1/control/vp3-os/fleet/update-requests/' + approve.dataset.fleetRequestApprove + '/approve', {method:'POST'});
      await refreshFleet();
      await refreshRollout();
      return systemFlash(result.apply_automatic ? 'Update approved.' : 'Fleet update request approved. Apply remains manual.');
    }
    if (dismiss) {
      await systemApi('/api/v1/control/vp3-os/fleet/update-requests/' + dismiss.dataset.fleetRequestDismiss + '/dismiss', {method:'POST'});
      await refreshFleet();
      return systemFlash('Fleet update request dismissed.');
    }
  } catch (err) { systemFlash(err.message, true); }
});

byId('completeSetup').addEventListener('click', async () => {
  try {
    const result = await systemApi('/api/v1/control/system/setup', {method:'POST', body:JSON.stringify({complete:true})});
    renderSetup(result.setup);
    systemFlash('First-run setup marked complete.');
  } catch (err) { systemFlash(err.message, true); }
});

byId('refreshDiagnostics').addEventListener('click', () => refreshSystem().then(() => systemFlash('Diagnostics refreshed.')).catch(err => systemFlash(err.message, true)));

byId('saveStripePayments').addEventListener('click', async () => {
  const secretKey = byId('stripeSecretKey').value.trim();
  const webhookSecret = byId('stripeWebhookSecret').value.trim();
  if (!secretKey && !webhookSecret) return systemFlash('Enter a Stripe secret key or webhook signing secret.', true);
  try {
    const payload = {};
    if (secretKey) payload.secret_key = secretKey;
    if (webhookSecret) payload.webhook_secret = webhookSecret;
    const result = await systemApi('/api/v1/control/payments/stripe', {method:'PUT', body:JSON.stringify(payload)});
    byId('stripeSecretKey').value = '';
    byId('stripeWebhookSecret').value = '';
    renderPayments(result);
    systemFlash('Local Stripe commerce credentials saved.');
  } catch (err) { systemFlash(err.message, true); }
});

byId('verifyStripePayments').addEventListener('click', async () => {
  try {
    const result = await systemApi('/api/v1/control/payments/stripe/verify', {method:'POST'});
    const account = result.account || {};
    systemFlash(`Stripe verified${account.account_id ? ` · ${account.account_id}` : ''}${account.livemode ? ' · live mode' : ' · test mode'}.`);
    await refreshPayments();
  } catch (err) { systemFlash(err.message, true); }
});

byId('clearStripePayments').addEventListener('click', async () => {
  if (!confirm('Remove the locally stored Stripe commerce credentials from this HomeServer? Existing cloud payment connections are not affected.')) return;
  try {
    const result = await systemApi('/api/v1/control/payments/stripe', {method:'DELETE'});
    renderPayments(result);
    byId('stripeSecretKey').value = '';
    byId('stripeWebhookSecret').value = '';
    systemFlash('Local Stripe commerce credentials removed.');
  } catch (err) { systemFlash(err.message, true); }
});

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
