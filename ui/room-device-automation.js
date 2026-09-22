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

    $('automationRoutines').innerHTML = routines.length ? routines.map(item => `
      <div class="automation-rule-item">
        <div><strong>${esc(item.name)}</strong><small>${esc(item.routine_key)} · ${esc(item.approval_mode)} · ${item.steps.length} steps</small></div>
        <button class="button secondary" type="button" data-routine-run="${esc(item.routine_key)}">Run</button>
      </div>
    `).join('') : '<div class="empty-state">No routines yet.</div>';

    $('automationRules').innerHTML = rules.length ? rules.map(item => `
      <div class="automation-rule-item">
        <div><strong>${esc(item.name)}</strong><small>${esc(item.rule_key)} · ${esc(item.trigger_kind)} · ${esc(item.routine_name)}</small></div>
        ${item.trigger_kind === 'manual' ? `<button class="button secondary" type="button" data-rule-run="${esc(item.rule_key)}">Run</button>` : `<span class="automation-ready ${item.enabled ? '' : 'no'}">${item.enabled ? 'Enabled' : 'Disabled'}</span>`}
      </div>
    `).join('') : '<div class="empty-state">No rules yet.</div>';

    $('automationRuleExecutions').innerHTML = executions.length ? executions.slice(0, 12).map(item => `
      <div class="automation-rule-item"><div><strong>${esc(item.rule_name || item.routine_name)}</strong><small>${esc(item.status)} · ${item.action_count || 0} actions · ${fmt(item.completed_at || item.created_at)}</small></div></div>
    `).join('') : '<div class="empty-state">No rule executions yet.</div>';

    $('automationRuntimeEnabled').checked = Boolean(settings.enabled);
    $('automationRuntimePoll').value = settings.poll_seconds ?? 15;
    $('automationRuntimeMaxActions').value = settings.max_actions_per_run ?? 12;
    $('automationRuntimeRate').value = settings.max_rule_fires_per_minute ?? 20;
  }

  async function load() {
    if (!$('automationDevices')) return;
    try {
      const [devices, rules] = await Promise.all([
        request('/api/v1/control/vp3-os/automation'),
        request('/api/v1/control/vp3-os/automation/rules-runtime'),
      ]);
      render(devices);
      renderRules(rules);
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
    });
    load();
  });
})();