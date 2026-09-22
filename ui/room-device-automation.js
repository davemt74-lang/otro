(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[ch]));
  const fmt = value => {
    if (!value) return '—';
    try { return new Date(value).toLocaleString(); } catch (_) { return String(value); }
  };

  async function request(path, options = {}) {
    const response = await fetch(path, {
      credentials: 'same-origin',
      headers: {'Content-Type':'application/json', ...(options.headers || {})},
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || payload.error || `Request failed (${response.status})`);
    return payload;
  }

  function parseObject(id) {
    const raw = $(id).value.trim();
    if (!raw) return {};
    const value = JSON.parse(raw);
    if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error(`${id} must be a JSON object.`);
    return value;
  }

  function parseArray(id) {
    const raw = $(id).value.trim();
    if (!raw) return [];
    const value = JSON.parse(raw);
    if (!Array.isArray(value)) throw new Error(`${id} must be a JSON array.`);
    return value;
  }

  function commandControls(device) {
    if (!device.controllable || !device.currently_executable) {
      const why = !device.controllable ? 'Discovery only' : 'Provider driver not ready';
      return `<div class="automation-locked">${esc(why)} · no physical command can execute.</div>`;
    }
    const key = esc(device.device_key);
    if (['light','outlet','fan'].includes(device.category)) {
      let extra = '';
      if (device.category === 'light') {
        extra = `<input type="number" min="0" max="100" value="50" data-brightness="${key}" aria-label="Brightness"><button class="button secondary" data-device-request="${key}" data-command="set_brightness">Request brightness</button>`;
      }
      return `<div class="automation-command-bar"><button class="button secondary" data-device-request="${key}" data-command="on">Request on</button><button class="button secondary" data-device-request="${key}" data-command="off">Request off</button><button class="button secondary" data-device-request="${key}" data-command="toggle">Request toggle</button>${extra}</div>`;
    }
    if (device.category === 'thermostat') {
      return `<div class="automation-command-bar"><input type="number" min="50" max="90" step="0.5" value="72" data-temperature="${key}" aria-label="Temperature Fahrenheit"><button class="button secondary" data-device-request="${key}" data-command="set_temperature">Request temperature</button><button class="button secondary" data-device-request="${key}" data-command="set_mode" data-mode="off">Request off mode</button></div>`;
    }
    return '<div class="automation-locked">No v0.60 command mapping for this category.</div>';
  }

  function render(data) {
    const rooms = data.rooms || [];
    const providers = data.providers || [];
    const devices = data.devices || [];
    const suggestions = data.suggestions || [];
    const actions = data.recent_actions || [];

    $('automationRoomCount').textContent = rooms.length;
    $('automationDeviceCount').textContent = devices.length;
    $('automationControllableCount').textContent = devices.filter(item => item.controllable).length;
    $('automationSuggestionCount').textContent = suggestions.length;

    $('automationRooms').innerHTML = rooms.length ? rooms.map(room => `
      <div class="automation-mini-item"><div><strong>${esc(room.name)}</strong><small>${esc(room.room_key)} · ${room.device_count || 0} devices</small></div><span class="automation-ready ${room.enabled ? '' : 'no'}">${room.enabled ? 'Enabled' : 'Disabled'}</span></div>
    `).join('') : '<div class="empty-state">No rooms yet.</div>';

    $('automationProviders').innerHTML = providers.length ? providers.map(provider => `
      <div class="automation-mini-item"><div><strong>${esc(provider.name)}</strong><small>${esc(provider.provider_type)} · ${esc(provider.status)}</small></div><span class="automation-ready ${provider.currently_executable ? '' : 'no'}">${provider.currently_executable ? 'Driver ready' : (provider.driver_registered ? 'Not executable' : 'No driver')}</span></div>
    `).join('') : '<div class="empty-state">No providers yet.</div>';

    $('automationDevices').innerHTML = devices.length ? devices.map(device => {
      const state = Object.entries(device.state || {}).slice(0,8).map(([k,v]) => `<span class="automation-state-chip">${esc(k)}: ${esc(typeof v === 'object' ? JSON.stringify(v) : v)}</span>`).join('');
      return `<article class="automation-device">
        <div class="automation-device-head"><div><h4>${esc(device.name)}</h4><div class="automation-device-meta">${esc(device.category)} · ${esc(device.room_name || 'Unassigned')} · ${esc(device.provider?.name || device.provider_key)}</div></div><span class="automation-ready ${device.currently_executable ? '' : 'no'}">${device.currently_executable ? 'Executable after approval' : (device.controllable ? 'Driver unavailable' : 'Discovery only')}</span></div>
        <div class="automation-device-state">${state || '<span class="automation-state-chip">No state reported</span>'}</div>
        ${commandControls(device)}
      </article>`;
    }).join('') : '<div class="empty-state">No devices registered.</div>';

    $('automationSuggestions').innerHTML = suggestions.length ? suggestions.map(item => `
      <div class="automation-suggestion"><strong>${esc(item.device_name || 'Automation suggestion')}</strong><p>${esc(item.reason)}${item.command ? ` · ${esc(item.command)}` : ''}</p><div class="automation-suggestion-actions"><button class="button secondary" data-suggestion-request="${item.id}">Request approval</button><button class="text-button danger" data-suggestion-dismiss="${item.id}">Dismiss</button></div></div>
    `).join('') : '<div class="empty-state">No pending suggestions.</div>';

    $('automationActions').innerHTML = actions.length ? actions.map(item => `
      <div class="automation-action"><strong>${esc(item.device_name)} · ${esc(item.command)}</strong><p><span class="automation-action-status">${esc(item.status)}</span> · ${fmt(item.completed_at || item.created_at)}${item.error ? ` · ${esc(item.error)}` : ''}</p></div>
    `).join('') : '<div class="empty-state">No device actions yet.</div>';
  }

  function renderRules(data) {
    const routines = data.routines || [];
    const rules = data.rules || [];
    const executions = data.recent_executions || [];
    const settings = data.settings || {};

    $('automationRoutines').innerHTML = routines.length ? routines.map(item => {
      const controls = item.enabled
        ? '<div class="automation-rule-actions"><button class="button secondary" type="button" data-routine-run="' + esc(item.routine_key) + '">Run</button><button class="text-button" type="button" data-routine-enabled="' + esc(item.routine_key) + '" data-enabled="false">Disable</button></div>'
        : '<button class="button secondary" type="button" data-routine-enabled="' + esc(item.routine_key) + '" data-enabled="true">Enable</button>';
      return '<div class="automation-rule-item"><div><strong>' + esc(item.name) + '</strong><small>' + esc(item.routine_key) + ' · ' + esc(item.approval_mode) + ' · ' + item.steps.length + ' steps · ' + (item.enabled ? 'enabled' : 'disabled') + '</small></div>' + controls + '</div>';
    }).join('') : '<div class="empty-state">No routines yet.</div>';

    $('automationRules').innerHTML = rules.length ? rules.map(item => {
      let controls = '<button class="button secondary" type="button" data-rule-enabled="' + esc(item.rule_key) + '" data-enabled="' + (item.enabled ? 'false' : 'true') + '">' + (item.enabled ? 'Disable' : 'Enable') + '</button>';
      if (item.trigger_kind === 'manual' && item.enabled) {
        controls = '<div class="automation-rule-actions"><button class="button secondary" type="button" data-rule-run="' + esc(item.rule_key) + '">Run</button>' + controls + '</div>';
      }
      return '<div class="automation-rule-item"><div><strong>' + esc(item.name) + '</strong><small>' + esc(item.rule_key) + ' · ' + esc(item.trigger_kind) + ' · ' + esc(item.routine_name) + ' · ' + (item.enabled ? 'enabled' : 'disabled') + '</small></div>' + controls + '</div>';
    }).join('') : '<div class="empty-state">No rules yet.</div>';

    $('automationRuleExecutions').innerHTML = executions.length ? executions.slice(0, 12).map(item =>
      '<div class="automation-rule-item"><div><strong>' + esc(item.rule_name || item.routine_name) + '</strong><small>' + esc(item.status) + ' · ' + (item.action_count || 0) + ' actions · ' + fmt(item.completed_at || item.created_at) + '</small></div></div>'
    ).join('') : '<div class="empty-state">No rule executions yet.</div>';

    $('automationRuntimeEnabled').checked = Boolean(settings.enabled);
    $('automationRuntimePoll').value = settings.poll_seconds ?? 15;
    $('automationRuntimeMaxActions').value = settings.max_actions_per_run ?? 12;
    $('automationRuntimeRate').value = settings.max_rule_fires_per_minute ?? 20;
  }

  function proposalActions(item) {
    if (item.status === 'proposed') {
      return '<div class="automation-intelligence-actions"><button class="button secondary" type="button" data-intelligence-simulate="' + item.id + '">Simulate</button><button class="button primary" type="button" data-intelligence-materialize="' + item.id + '">Create disabled draft</button><button class="text-button danger" type="button" data-intelligence-dismiss="' + item.id + '">Dismiss</button></div>';
    }
    if (item.status === 'materialized') {
      return '<div class="automation-intelligence-actions"><button class="button primary" type="button" data-intelligence-enable="' + item.id + '">Enable reviewed draft</button><span class="muted">Still approval-gated when it fires.</span></div>';
    }
    if (item.status === 'active') {
      return '<span class="automation-intelligence-state">Active · governed by v0.70</span>';
    }
    return '<span class="automation-intelligence-state">' + esc(item.status) + (item.suppression_until ? ' until ' + fmt(item.suppression_until) : '') + '</span>';
  }

  function renderOrchestration(data) {
    const settings = data.settings || {};
    const modes = data.modes || [];
    const sessions = data.sessions || [];
    const suggested = sessions.filter(item => item.state === 'suggested').length;
    const requested = sessions.filter(item => item.state === 'requested').length;
    const active = sessions.filter(item => item.state === 'active').length;

    $('orchestrationModeCount').textContent = modes.length;
    $('orchestrationSuggestedCount').textContent = suggested;
    $('orchestrationRequestedCount').textContent = requested;
    $('orchestrationActiveCount').textContent = active;

    $('orchestrationEnabled').checked = Boolean(settings.enabled);
    $('orchestrationPoll').value = settings.poll_seconds ?? 30;
    $('orchestrationCooldown').value = settings.suggestion_cooldown_seconds ?? 14400;
    $('orchestrationMaxSessions').value = settings.max_open_sessions ?? 12;

    $('orchestrationModes').innerHTML = modes.length ? modes.map(item =>
      '<article class="orchestration-card">' +
        '<div class="automation-intelligence-card-head"><div><strong>' + esc(item.name) + '</strong><small>' + esc(item.mode_key) + ' · priority ' + item.priority + ' · ' + esc(item.routine_name) + '</small></div><span class="automation-intelligence-status">' + (item.enabled ? 'enabled' : 'disabled') + '</span></div>' +
        '<p>' + esc(item.description || 'Coordinates ' + item.device_keys.length + ' device(s) across ' + (item.room_keys.length || 0) + ' room(s).') + '</p>' +
        '<div class="automation-intelligence-actions">' +
          '<button class="button secondary" type="button" data-mode-simulate="' + esc(item.mode_key) + '">Simulate</button>' +
          '<button class="button primary" type="button" data-mode-activate="' + esc(item.mode_key) + '">Activate</button>' +
          '<button class="text-button" type="button" data-mode-supersede="' + esc(item.mode_key) + '">Supersede conflicts</button>' +
          '<button class="text-button" type="button" data-mode-enabled="' + esc(item.mode_key) + '" data-enabled="' + (item.enabled ? 'false' : 'true') + '">' + (item.enabled ? 'Disable' : 'Enable') + '</button>' +
        '</div>' +
      '</article>'
    ).join('') : '<div class="empty-state">No Room Modes yet.</div>';

    $('orchestrationSessions').innerHTML = sessions.length ? sessions.map(item => {
      let actions = '';
      if (item.state === 'suggested') {
        actions = '<div class="automation-intelligence-actions"><button class="button primary" type="button" data-mode-session-accept="' + item.id + '">Accept</button><button class="text-button danger" type="button" data-mode-session-dismiss="' + item.id + '">Dismiss</button></div>';
      } else if (item.state === 'requested' || item.state === 'active') {
        actions = '<div class="automation-intelligence-actions"><button class="button secondary" type="button" data-mode-session-refresh="' + item.id + '">Refresh</button><button class="text-button" type="button" data-mode-session-suspend="' + item.id + '">Suspend</button><button class="text-button danger" type="button" data-mode-session-end="' + item.id + '">End</button></div>';
      } else if (item.state === 'suspended') {
        actions = '<div class="automation-intelligence-actions"><button class="text-button danger" type="button" data-mode-session-end="' + item.id + '">End</button></div>';
      }
      return '<article class="orchestration-card">' +
        '<div class="automation-intelligence-card-head"><div><strong>' + esc(item.mode_name) + '</strong><small>Session #' + item.id + ' · priority ' + item.priority + ' · ' + esc(item.source_kind) + '</small></div><span class="orchestration-state state-' + esc(item.state) + '">' + esc(item.state) + '</span></div>' +
        '<p>' + esc(item.reason || '') + '</p>' +
        (item.request_ids?.length ? '<small class="automation-intelligence-context">' + item.request_ids.length + ' governed approval request(s)</small>' : '') +
        actions +
      '</article>';
    }).join('') : '<div class="empty-state">No Room Mode sessions yet.</div>';
  }

  function renderIntelligence(data) {
    const settings = data.settings || {};
    const counts = data.counts || {};
    const proposals = data.proposals || [];

    $('automationPatternCount').textContent = counts.patterns || 0;
    $('automationProposalCount').textContent = counts.proposed || 0;
    $('automationDraftCount').textContent = counts.materialized || 0;
    $('automationActiveCount').textContent = counts.active || 0;

    $('automationIntelligenceEnabled').checked = Boolean(settings.enabled);
    $('automationIntelligenceLookback').value = settings.lookback_days ?? 21;
    $('automationIntelligenceMinOccurrences').value = settings.min_occurrences ?? 4;
    $('automationIntelligenceBucket').value = String(settings.time_bucket_minutes ?? 30);
    $('automationIntelligenceInterval').value = settings.scan_interval_seconds ?? 3600;
    $('automationIntelligenceSuppression').value = settings.suppression_days ?? 30;
    $('automationIntelligenceMaxProposals').value = settings.max_proposals_per_scan ?? 12;

    $('automationIntelligenceProposals').innerHTML = proposals.length ? proposals.map(item => {
      const simulation = item.simulation || {};
      const context = ((item.evidence || {}).nearby_context || []).slice(0, 3).map(entry => esc(entry.event_type) + ': ' + esc(entry.state)).join(' · ');
      return '<article class="automation-intelligence-card">' +
        '<div class="automation-intelligence-card-head"><div><strong>' + esc(item.title) + '</strong><small>' + Math.round(Number(item.confidence || 0) * 100) + '% confidence · observed ' + (item.occurrence_count || 0) + ' times · ' + esc(item.pattern_kind) + '</small></div><span class="automation-intelligence-status">' + esc(item.status) + '</span></div>' +
        '<p>' + esc(item.rationale) + '</p>' +
        '<div class="automation-intelligence-evidence"><span>Historical triggers: ' + (simulation.would_have_triggered || 0) + '</span><span>Approval requests: ' + (simulation.approval_requests_if_enabled || 0) + '</span><span>Unapproved physical actions: ' + (simulation.physical_actions_without_owner_approval || 0) + '</span></div>' +
        (context ? '<small class="automation-intelligence-context">Nearby context: ' + context + '</small>' : '') +
        proposalActions(item) +
      '</article>';
    }).join('') : '<div class="empty-state">No learned opportunities yet. VP3 needs repeated completed device actions before it proposes anything.</div>';
  }

  async function load() {
    if (!$('automationDevices')) return;
    try {
      const [devices, rules, intelligence, orchestration] = await Promise.all([
        request('/api/v1/control/vp3-os/automation'),
        request('/api/v1/control/vp3-os/automation/rules-runtime'),
        request('/api/v1/control/vp3-os/automation/intelligence'),
        request('/api/v1/control/vp3-os/orchestration'),
      ]);
      render(devices);
      renderRules(rules);
      renderIntelligence(intelligence);
      renderOrchestration(orchestration);
      $('automationFeedback').textContent = '';
    } catch (error) {
      $('automationFeedback').textContent = error.message;
    }
  }

  async function saveRoom(event) {
    event.preventDefault();
    const key = $('automationRoomKey').value.trim().toLowerCase();
    try {
      await request(`/api/v1/control/vp3-os/automation/rooms/${encodeURIComponent(key)}`, {method:'PUT',body:JSON.stringify({room_key:key,name:$('automationRoomName').value.trim(),description:'',enabled:true})});
      event.target.reset(); await load();
    } catch (error) { $('automationFeedback').textContent = error.message; }
  }

  async function saveProvider(event) {
    event.preventDefault();
    const key = $('automationProviderKey').value.trim().toLowerCase();
    try {
      await request(`/api/v1/control/vp3-os/automation/providers/${encodeURIComponent(key)}`, {method:'PUT',body:JSON.stringify({provider_key:key,name:$('automationProviderName').value.trim(),provider_type:$('automationProviderType').value.trim().toLowerCase(),enabled:true,executable:$('automationProviderExecutable').checked,status:'connected',metadata:{}})});
      event.target.reset(); await load();
    } catch (error) { $('automationFeedback').textContent = error.message; }
  }

  async function saveDevice(event) {
    event.preventDefault();
    const key = $('automationDeviceKey').value.trim().toLowerCase();
    try {
      const body = {
        device_key:key,
        provider_key:$('automationDeviceProvider').value.trim().toLowerCase(),
        provider_device_id:$('automationProviderDeviceId').value.trim(),
        name:$('automationDeviceName').value.trim(),
        category:$('automationDeviceCategory').value,
        room_key:$('automationDeviceRoom').value.trim().toLowerCase() || null,
        enabled:true,
        controllable:$('automationDeviceControllable').checked,
        capabilities:parseObject('automationDeviceCapabilities'),
        state:parseObject('automationDeviceState'),
        metadata:{},
      };
      await request(`/api/v1/control/vp3-os/automation/devices/${encodeURIComponent(key)}`, {method:'PUT',body:JSON.stringify(body)});
      $('automationFeedback').textContent = 'Device saved.';
      await load();
    } catch (error) { $('automationFeedback').textContent = error.message; }
  }

  async function saveRoutine(event) {
    event.preventDefault();
    const key = $('automationRoutineKey').value.trim().toLowerCase();
    try {
      await request(`/api/v1/control/vp3-os/automation/routines/${encodeURIComponent(key)}`, {
        method:'PUT',
        body:JSON.stringify({
          routine_key:key,
          name:$('automationRoutineName').value.trim(),
          description:'',
          enabled:true,
          approval_mode:$('automationRoutineApproval').value,
          steps:parseArray('automationRoutineSteps'),
        }),
      });
      event.target.reset();
      $('automationRoutineApproval').value = 'ask_every_time';
      await load();
    } catch (error) { $('automationFeedback').textContent = error.message; }
  }

  async function saveRule(event) {
    event.preventDefault();
    const key = $('automationRuleKey').value.trim().toLowerCase();
    try {
      await request(`/api/v1/control/vp3-os/automation/rules/${encodeURIComponent(key)}`, {
        method:'PUT',
        body:JSON.stringify({
          rule_key:key,
          name:$('automationRuleName').value.trim(),
          description:'',
          enabled:true,
          routine_key:$('automationRuleRoutine').value.trim().toLowerCase(),
          trigger_kind:$('automationRuleTriggerKind').value,
          trigger:parseObject('automationRuleTrigger'),
          conditions:parseArray('automationRuleConditions'),
          cooldown_seconds:Number($('automationRuleCooldown').value || 60),
        }),
      });
      event.target.reset();
      $('automationRuleCooldown').value = '60';
      await load();
    } catch (error) { $('automationFeedback').textContent = error.message; }
  }

  async function saveRuntime(event) {
    event.preventDefault();
    try {
      await request('/api/v1/control/vp3-os/automation/rules-runtime/settings', {
        method:'PUT',
        body:JSON.stringify({
          enabled:$('automationRuntimeEnabled').checked,
          poll_seconds:Number($('automationRuntimePoll').value || 15),
          max_actions_per_run:Number($('automationRuntimeMaxActions').value || 12),
          max_rule_fires_per_minute:Number($('automationRuntimeRate').value || 20),
        }),
      });
      $('automationFeedback').textContent = 'Automation runtime settings saved.';
      await load();
    } catch (error) { $('automationFeedback').textContent = error.message; }
  }

  async function runRoutine(key) {
    try {
      const result = await request(`/api/v1/control/vp3-os/automation/routines/${encodeURIComponent(key)}/run`, {method:'POST',body:'{}'});
      $('automationFeedback').textContent = result.approval_required ? `${result.action_count} approval request(s) created.` : `${result.action_count} suggestion(s) created.`;
      await load();
    } catch (error) { $('automationFeedback').textContent = error.message; }
  }

  async function runRule(key) {
    try {
      const result = await request(`/api/v1/control/vp3-os/automation/rules/${encodeURIComponent(key)}/run`, {method:'POST',body:'{}'});
      $('automationFeedback').textContent = result.fired ? 'Rule evaluated and created governed actions.' : `Rule did not fire: ${result.reason || 'not eligible'}.`;
      await load();
    } catch (error) { $('automationFeedback').textContent = error.message; }
  }

  async function saveOrchestrationMode(event) {
    event.preventDefault();
    const key = $('orchestrationModeKey').value.trim().toLowerCase();
    try {
      const rooms = $('orchestrationModeRooms').value.split(',').map(value => value.trim().toLowerCase()).filter(Boolean);
      await request('/api/v1/control/vp3-os/orchestration/modes/' + encodeURIComponent(key), {
        method:'PUT',
        body:JSON.stringify({
          mode_key:key,
          name:$('orchestrationModeName').value.trim(),
          routine_key:$('orchestrationModeRoutine').value.trim().toLowerCase(),
          description:'',
          room_keys:rooms,
          priority:Number($('orchestrationModePriority').value || 50),
          suggest_trigger:parseObject('orchestrationModeTrigger'),
          enabled:true,
        }),
      });
      event.target.reset();
      $('orchestrationModePriority').value = '50';
      $('automationFeedback').textContent = 'Room Mode saved.';
      await load();
    } catch (error) { $('automationFeedback').textContent = error.message; }
  }

  async function saveOrchestrationSettings(event) {
    event.preventDefault();
    try {
      await request('/api/v1/control/vp3-os/orchestration/settings', {
        method:'PUT',
        body:JSON.stringify({
          enabled:$('orchestrationEnabled').checked,
          poll_seconds:Number($('orchestrationPoll').value || 30),
          suggestion_cooldown_seconds:Number($('orchestrationCooldown').value || 14400),
          max_open_sessions:Number($('orchestrationMaxSessions').value || 12),
        }),
      });
      $('automationFeedback').textContent = 'Orchestration settings saved.';
      await load();
    } catch (error) { $('automationFeedback').textContent = error.message; }
  }

  async function evaluateOrchestration() {
    try {
      const result = await request('/api/v1/control/vp3-os/orchestration/evaluate', {method:'POST',body:'{}'});
      $('automationFeedback').textContent = 'Context evaluation complete: ' + (result.suggestions || []).length + ' matching mode suggestion(s).';
      await load();
    } catch (error) { $('automationFeedback').textContent = error.message; }
  }

  async function simulateMode(key) {
    try {
      const result = await request('/api/v1/control/vp3-os/orchestration/modes/' + encodeURIComponent(key) + '/simulate');
      const conflictText = result.conflicts?.length ? result.conflicts.length + ' conflict(s)' : 'no conflicts';
      $('automationFeedback').textContent = 'Simulation: ' + result.approval_requests_if_activated + ' approval request(s), ' + conflictText + ', 0 unapproved physical actions.';
    } catch (error) { $('automationFeedback').textContent = error.message; }
  }

  async function activateMode(key, supersede) {
    try {
      const result = await request('/api/v1/control/vp3-os/orchestration/modes/' + encodeURIComponent(key) + '/activate', {
        method:'POST',
        body:JSON.stringify({reason:'Owner activation from Control Center.',supersede_conflicts:Boolean(supersede)}),
      });
      $('automationFeedback').textContent = 'Room Mode requested: ' + (result.session?.request_ids?.length || 0) + ' governed approval request(s).';
      await load();
    } catch (error) { $('automationFeedback').textContent = error.message; }
  }

  async function setModeEnabled(key, enabled) {
    try {
      await request('/api/v1/control/vp3-os/orchestration/modes/' + encodeURIComponent(key) + '/enabled', {
        method:'PUT',
        body:JSON.stringify({enabled}),
      });
      $('automationFeedback').textContent = 'Room Mode ' + (enabled ? 'enabled.' : 'disabled.');
      await load();
    } catch (error) { $('automationFeedback').textContent = error.message; }
  }

  async function modeSessionAction(id, action) {
    try {
      let body = '{}';
      if (action === 'accept') body = JSON.stringify({reason:'Owner accepted Room Mode suggestion.',supersede_conflicts:false});
      if (action === 'suspend') body = JSON.stringify({reason:'Owner suspended Room Mode from Control Center.'});
      if (action === 'end') body = JSON.stringify({reason:'Owner ended Room Mode from Control Center.'});
      await request('/api/v1/control/vp3-os/orchestration/sessions/' + encodeURIComponent(id) + '/' + action, {method:'POST',body});
      $('automationFeedback').textContent = 'Room Mode session updated.';
      await load();
    } catch (error) { $('automationFeedback').textContent = error.message; }
  }

  async function saveIntelligenceSettings(event) {
    event.preventDefault();
    try {
      await request('/api/v1/control/vp3-os/automation/intelligence/settings', {
        method:'PUT',
        body:JSON.stringify({
          enabled:$('automationIntelligenceEnabled').checked,
          scan_interval_seconds:Number($('automationIntelligenceInterval').value || 3600),
          lookback_days:Number($('automationIntelligenceLookback').value || 21),
          min_occurrences:Number($('automationIntelligenceMinOccurrences').value || 4),
          time_bucket_minutes:Number($('automationIntelligenceBucket').value || 30),
          max_proposals_per_scan:Number($('automationIntelligenceMaxProposals').value || 12),
          suppression_days:Number($('automationIntelligenceSuppression').value || 30),
        }),
      });
      $('automationFeedback').textContent = 'Automation learning settings saved.';
      await load();
    } catch (error) { $('automationFeedback').textContent = error.message; }
  }

  async function scanIntelligence() {
    try {
      const result = await request('/api/v1/control/vp3-os/automation/intelligence/scan', {method:'POST',body:'{}'});
      $('automationFeedback').textContent = 'Learning scan complete: ' + (result.patterns_found || 0) + ' pattern(s) found.';
      await load();
    } catch (error) { $('automationFeedback').textContent = error.message; }
  }

  async function intelligenceAction(id, action) {
    try {
      const body = action === 'dismiss' ? JSON.stringify({note:''}) : '{}';
      const result = await request('/api/v1/control/vp3-os/automation/intelligence/proposals/' + encodeURIComponent(id) + '/' + action, {method:'POST',body});
      if (action === 'simulate') {
        $('automationFeedback').textContent = 'Simulation: ' + (result.would_have_triggered || 0) + ' historical trigger(s), ' + (result.approval_requests_if_enabled || 0) + ' approval request(s), 0 unapproved physical actions.';
      } else if (action === 'materialize') {
        $('automationFeedback').textContent = 'Disabled rule and routine drafts created. Review them before enabling.';
      } else if (action === 'enable') {
        $('automationFeedback').textContent = 'Reviewed draft enabled. Physical commands still require the configured v0.70/v0.60 governance path.';
      } else {
        $('automationFeedback').textContent = 'Automation opportunity suppressed.';
      }
      await load();
    } catch (error) { $('automationFeedback').textContent = error.message; }
  }

  async function setAutomationEnabled(kind, key, enabled) {
    try {
      await request('/api/v1/control/vp3-os/automation/' + kind + '/' + encodeURIComponent(key) + '/enabled', {
        method:'PUT',
        body:JSON.stringify({enabled}),
      });
      $('automationFeedback').textContent = (kind === 'rules' ? 'Rule' : 'Routine') + (enabled ? ' enabled.' : ' disabled.');
      await load();
    } catch (error) { $('automationFeedback').textContent = error.message; }
  }

  async function requestCommand(button) {
    const key = button.dataset.deviceRequest;
    const command = button.dataset.command;
    const args = {};
    if (command === 'set_brightness') args.brightness = Number(document.querySelector(`[data-brightness="${CSS.escape(key)}"]`)?.value || 50);
    if (command === 'set_temperature') args.temperature_f = Number(document.querySelector(`[data-temperature="${CSS.escape(key)}"]`)?.value || 72);
    if (command === 'set_mode') args.mode = button.dataset.mode || 'off';
    try {
      const result = await request(`/api/v1/control/vp3-os/automation/devices/${encodeURIComponent(key)}/request`, {method:'POST',body:JSON.stringify({command,arguments:args})});
      $('automationFeedback').textContent = `Approval request created: ${result.result?.request_id || 'pending'}`;
      await load();
    } catch (error) { $('automationFeedback').textContent = error.message; }
  }

  async function suggestionAction(id, action) {
    try {
      const result = await request(`/api/v1/control/vp3-os/automation/suggestions/${id}/${action}`, {method:'POST',body:'{}'});
      $('automationFeedback').textContent = action === 'request' ? `Approval request created: ${result.result?.request_id || 'pending'}` : 'Suggestion dismissed.';
      await load();
    } catch (error) { $('automationFeedback').textContent = error.message; }
  }

  document.addEventListener('DOMContentLoaded', () => {
    if (!$('automationDevices')) return;
    $('automationRoomForm').addEventListener('submit', saveRoom);
    $('automationProviderForm').addEventListener('submit', saveProvider);
    $('automationDeviceForm').addEventListener('submit', saveDevice);
    $('automationRoutineForm').addEventListener('submit', saveRoutine);
    $('automationRuleForm').addEventListener('submit', saveRule);
    $('automationRuntimeForm').addEventListener('submit', saveRuntime);
    $('automationIntelligenceSettingsForm').addEventListener('submit', saveIntelligenceSettings);
    $('automationIntelligenceScan').addEventListener('click', scanIntelligence);
    $('orchestrationModeForm').addEventListener('submit', saveOrchestrationMode);
    $('orchestrationSettingsForm').addEventListener('submit', saveOrchestrationSettings);
    $('orchestrationEvaluate').addEventListener('click', evaluateOrchestration);
    $('automationRefresh').addEventListener('click', load);
    document.querySelectorAll('.nav-item[data-view="automation"]').forEach(node => node.addEventListener('click', load));
    $('view-automation').addEventListener('click', event => {
      const command = event.target.closest('[data-device-request]');
      if (command) requestCommand(command);
      const requestSuggestion = event.target.closest('[data-suggestion-request]');
      if (requestSuggestion) suggestionAction(requestSuggestion.dataset.suggestionRequest, 'request');
      const dismissSuggestion = event.target.closest('[data-suggestion-dismiss]');
      if (dismissSuggestion) suggestionAction(dismissSuggestion.dataset.suggestionDismiss, 'dismiss');
      const routineRun = event.target.closest('[data-routine-run]');
      if (routineRun) runRoutine(routineRun.dataset.routineRun);
      const ruleRun = event.target.closest('[data-rule-run]');
      if (ruleRun) runRule(ruleRun.dataset.ruleRun);
      const routineEnabled = event.target.closest('[data-routine-enabled]');
      if (routineEnabled) setAutomationEnabled('routines', routineEnabled.dataset.routineEnabled, routineEnabled.dataset.enabled === 'true');
      const ruleEnabled = event.target.closest('[data-rule-enabled]');
      if (ruleEnabled) setAutomationEnabled('rules', ruleEnabled.dataset.ruleEnabled, ruleEnabled.dataset.enabled === 'true');
      const simulate = event.target.closest('[data-intelligence-simulate]');
      if (simulate) intelligenceAction(simulate.dataset.intelligenceSimulate, 'simulate');
      const materialize = event.target.closest('[data-intelligence-materialize]');
      if (materialize) intelligenceAction(materialize.dataset.intelligenceMaterialize, 'materialize');
      const enable = event.target.closest('[data-intelligence-enable]');
      if (enable) intelligenceAction(enable.dataset.intelligenceEnable, 'enable');
      const dismiss = event.target.closest('[data-intelligence-dismiss]');
      if (dismiss) intelligenceAction(dismiss.dataset.intelligenceDismiss, 'dismiss');
      const modeSimulate = event.target.closest('[data-mode-simulate]');
      if (modeSimulate) simulateMode(modeSimulate.dataset.modeSimulate);
      const modeActivate = event.target.closest('[data-mode-activate]');
      if (modeActivate) activateMode(modeActivate.dataset.modeActivate, false);
      const modeSupersede = event.target.closest('[data-mode-supersede]');
      if (modeSupersede) activateMode(modeSupersede.dataset.modeSupersede, true);
      const modeEnabled = event.target.closest('[data-mode-enabled]');
      if (modeEnabled) setModeEnabled(modeEnabled.dataset.modeEnabled, modeEnabled.dataset.enabled === 'true');
      const modeAccept = event.target.closest('[data-mode-session-accept]');
      if (modeAccept) modeSessionAction(modeAccept.dataset.modeSessionAccept, 'accept');
      const modeDismiss = event.target.closest('[data-mode-session-dismiss]');
      if (modeDismiss) modeSessionAction(modeDismiss.dataset.modeSessionDismiss, 'dismiss');
      const modeRefresh = event.target.closest('[data-mode-session-refresh]');
      if (modeRefresh) modeSessionAction(modeRefresh.dataset.modeSessionRefresh, 'refresh');
      const modeSuspend = event.target.closest('[data-mode-session-suspend]');
      if (modeSuspend) modeSessionAction(modeSuspend.dataset.modeSessionSuspend, 'suspend');
      const modeEnd = event.target.closest('[data-mode-session-end]');
      if (modeEnd) modeSessionAction(modeEnd.dataset.modeSessionEnd, 'end');
    });
    load();
  });
})();