(() => {
  const $ = id => document.getElementById(id);
  const fmt = value => {
    if (!value) return 'Never';
    try { return new Date(value).toLocaleString(); } catch (_) { return String(value); }
  };
  const yesNo = value => value ? 'Available' : 'Not reported';
  let dirty = false, actionBusy = false, latestStatus = null;

  async function request(path, options = {}) {
    const response = await fetch(path, {
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || payload.error || `Request failed (${response.status})`);
    return payload;
  }

  function selectedLevels() {
    return [...document.querySelectorAll('[data-ambient-level]:checked')].map(node => node.value);
  }

  function settingsFromForm() {
    return {
      enabled: $('ambientEnabled').checked,
      wake_enabled: $('ambientWakeEnabled').checked,
      proactive_voice: $('ambientProactiveVoice').checked,
      presence_policy: $('ambientPresencePolicy').value,
      announcement_levels: selectedLevels(),
      announcement_detail: $('ambientAnnouncementDetail').value,
      cooldown_seconds: Number($('ambientCooldown').value || 30),
      wake_timeout_seconds: Number($('ambientWakeTimeout').value || 20),
      max_announcements_per_hour: Number($('ambientMaxPerHour').value || 6),
    };
  }

  function render(data) {
    if (!data) return;
    latestStatus = data;
    const settings = data.settings || {};
    if (!dirty) {
    $('ambientEnabled').checked = Boolean(settings.enabled);
    $('ambientWakeEnabled').checked = Boolean(settings.wake_enabled);
    $('ambientProactiveVoice').checked = Boolean(settings.proactive_voice);
    $('ambientPresencePolicy').value = settings.presence_policy || 'sensor_required';
    $('ambientAnnouncementDetail').value = settings.announcement_detail || 'title_only';
    $('ambientCooldown').value = Number(settings.cooldown_seconds || 30);
    $('ambientWakeTimeout').value = Number(settings.wake_timeout_seconds || 20);
    $('ambientMaxPerHour').value = Number(settings.max_announcements_per_hour || 6);
    const levels = new Set(Array.isArray(settings.announcement_levels) ? settings.announcement_levels : ['warning']);
    document.querySelectorAll('[data-ambient-level]').forEach(node => { node.checked = levels.has(node.value); });

    }
    renderSpeechHint();
    const state = data.state || 'disabled';
    const badge = $('ambientStateBadge');
    badge.textContent = state.replaceAll('_', ' ');
    badge.dataset.state = state;
    $('ambientRuntimeState').textContent = state.replaceAll('_', ' ');
    $('ambientPresence').textContent = data.presence || 'unknown';
    $('ambientWakeActive').textContent = data.wake_active ? 'Active' : 'Idle';
    $('ambientLastWake').textContent = fmt(data.last_wake_at);
    $('ambientLastAnnouncement').textContent = data.last_announcement_id ? `Notification #${data.last_announcement_id} · ${fmt(data.last_announcement_at)}` : 'Never';
    const hardware = data.hardware || {};
    const sensor = hardware.presence_sensor || {};
    $('ambientPresenceHardware').textContent = sensor.present ? (sensor.ready ? 'Ready' : 'Present, not ready') : 'Not reported';
    $('ambientWakeHardware').textContent = yesNo(hardware.wake_word_event);
    $('ambientVadHardware').textContent = yesNo(hardware.voice_activity_event);
  }

  function renderSpeechHint() {
    const hint = $('ambientSpeechHint');
    if (!$('ambientEnabled').checked) hint.textContent = 'Enable Ambient Agent to allow speech.';
    else if ($('ambientPresencePolicy').value === 'sensor_required' && latestStatus?.presence !== 'present') hint.textContent = 'Speech is waiting for a presence sensor. Choose Assume present if you use HomeServer without one.';
    else if (!$('ambientProactiveVoice').checked) hint.textContent = 'Voice tests are available. Enable Proactive voice to hear notifications automatically.';
    else hint.textContent = 'Speech is enabled for the selected notification levels. Info includes ordinary reminders. The privacy switch, busy audio and rate limits still apply.';
  }

  function setBusy(value) {
    actionBusy = value;
    $('ambientSave').disabled = value;
    $('ambientTestVoice').disabled = value;
  }

  let loadPromise = null;
  async function load() {
    if (!$('ambientEnabled')) return;
    if (loadPromise) return loadPromise;
    loadPromise = (async () => {
    try {
      render(await request('/api/v1/control/vp3-os/ambient'));
      if (!actionBusy) $('ambientFeedback').textContent = '';
    } catch (error) {
      if (!actionBusy) $('ambientFeedback').textContent = error.message;
    } finally {
      loadPromise = null;
    }
    })();
    return loadPromise;
  }

  async function save() {
    const levels = selectedLevels();
    if (!levels.length) {
      $('ambientFeedback').textContent = 'Choose at least one notification level.';
      return false;
    }
    const submitted = JSON.stringify(settingsFromForm());
    $('ambientFeedback').textContent = 'Saving…';
    try {
      const result = await request('/api/v1/control/vp3-os/ambient/settings', {
        method: 'PUT',
        body: submitted,
      });
      dirty = JSON.stringify(settingsFromForm()) !== submitted;
      render(result.status);
      $('ambientFeedback').textContent = 'Ambient settings saved.';
      return true;
    } catch (error) {
      $('ambientFeedback').textContent = error.message;
      return false;
    }
  }

  async function testVoice() {
    if (actionBusy) return;
    setBusy(true);
    try {
      if (!await save()) return;
      $('ambientFeedback').textContent = 'Testing local voice…';
      await request('/api/v1/control/vp3-os/ambient/announce-test', { method: 'POST', body: '{}' });
      $('ambientFeedback').textContent = 'Local Ambient Agent voice played.';
      await load();
    } catch (error) {
      $('ambientFeedback').textContent = error.message;
    } finally { setBusy(false); }
  }

  document.addEventListener('DOMContentLoaded', () => {
    if (!$('ambientEnabled')) return;
    $('ambientSave').addEventListener('click', async () => { if (actionBusy) return; setBusy(true); try { await save(); } finally { setBusy(false); } });
    document.querySelectorAll('#view-ambient input, #view-ambient select').forEach(node => node.addEventListener('change', () => { dirty = true; renderSpeechHint(); }));
    $('ambientTestVoice').addEventListener('click', testVoice);
    document.querySelectorAll('.nav-item[data-view="ambient"]').forEach(node => node.addEventListener('click', load));
    load();
    window.setInterval(() => {
      const view = $('view-ambient');
      if (!document.hidden && !actionBusy && view && view.classList.contains('active')) load();
    }, 5000);
  });
})();