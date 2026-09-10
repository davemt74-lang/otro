(() => {
  'use strict';

  const AGENT_ENDPOINT = '/api/v1/control/agent';
  const CATALOG_ENDPOINT = '/api/v1/control/voice/catalog';
  const PREVIEW_TEXT = 'Hello. This is the voice I will use as your HomeServer Agent.';
  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));

  const state = {agentId: null, profile: null, catalog: null, loading: null};

  async function requestJson(path, options = {}) {
    const response = await fetch(path, {
      cache: 'no-store',
      credentials: 'same-origin',
      ...options,
      headers: {
        ...(options.body ? {'Content-Type': 'application/json'} : {}),
        ...(options.headers || {}),
      },
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
    return payload;
  }

  function flash(message, error = false) {
    const node = byId('flash');
    if (!node) return;
    node.textContent = message;
    node.className = `flash show${error ? ' error' : ''}`;
    clearTimeout(flash.timer);
    flash.timer = setTimeout(() => { node.className = 'flash'; }, 4200);
  }

  function ensureStyles() {
    if (document.querySelector('link[href="/assets/agent-voice-profile.css"]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/agent-voice-profile.css';
    document.head.appendChild(link);
  }

  function ensurePanel() {
    if (byId('agentVoiceProfileForm')) return true;
    const agentForm = byId('agentForm');
    if (!agentForm) return false;
    const form = document.createElement('form');
    form.id = 'agentVoiceProfileForm';
    form.className = 'panel agent-voice-profile';
    form.innerHTML = `
      <div class="panel-head agent-voice-head">
        <div><p class="eyebrow">VOICE PERSONA · v0.45</p><h3>Agent Voice Profile</h3><p class="muted">Give this Agent its own trusted local Piper voice and speaking characteristics, or inherit the global Voice Settings.</p></div>
        <span id="agentVoiceReady" class="agent-voice-badge">Loading…</span>
      </div>
      <div class="agent-voice-grid">
        <label>Speaking voice
          <select id="agentVoiceChoice"><option value="">Use global voice</option></select>
          <small id="agentVoiceChoiceHelp" class="muted">Global voice is used unless this Agent has an override.</small>
        </label>
        <div class="agent-voice-effective">
          <span>Effective voice</span>
          <strong id="agentVoiceEffective">Loading…</strong>
          <small id="agentVoiceEffectiveSource" class="muted"></small>
        </div>
      </div>
      <div class="agent-voice-characteristics">
        <div class="agent-voice-characteristic">
          <label class="check-inline"><input id="agentVoiceRateEnabled" type="checkbox"> Custom speaking speed</label>
          <label class="agent-voice-range">Speed <output id="agentVoiceRateValue">1.00×</output><input id="agentVoiceRate" type="range" min="0.6" max="1.6" step="0.05" value="1"></label>
          <small class="muted">Off = inherit the global speaking speed.</small>
        </div>
        <div class="agent-voice-characteristic">
          <label class="check-inline"><input id="agentVoiceSilenceEnabled" type="checkbox"> Custom sentence pause</label>
          <label class="agent-voice-range">Pause <output id="agentVoiceSilenceValue">0.20 s</output><input id="agentVoiceSilence" type="range" min="0" max="1.5" step="0.05" value="0.2"></label>
          <small class="muted">Off = inherit the global Piper sentence pause.</small>
        </div>
      </div>
      <div id="agentVoiceWarning" class="agent-voice-warning hidden" role="status"></div>
      <div class="agent-voice-actions">
        <span id="agentVoiceSaved" class="muted"></span>
        <div><button id="agentVoicePreview" class="button secondary" type="button">Preview effective voice</button><button class="button primary" type="submit">Save voice profile</button></div>
      </div>`;
    agentForm.insertAdjacentElement('afterend', form);
    bindPanel(form);
    return true;
  }

  function voiceLabel(key) {
    const voice = state.catalog?.voices?.find(item => item.key === key);
    return voice?.label || key || 'Unknown voice';
  }

  function optionLabel(voice) {
    const stateLabel = voice.available ? 'Ready' : voice.management_state === 'repair' ? 'Needs repair' : 'Not installed';
    return `${voice.label || voice.key} · ${stateLabel}`;
  }

  function updateRangeState() {
    const rateEnabled = Boolean(byId('agentVoiceRateEnabled')?.checked);
    const silenceEnabled = Boolean(byId('agentVoiceSilenceEnabled')?.checked);
    if (byId('agentVoiceRate')) byId('agentVoiceRate').disabled = !rateEnabled;
    if (byId('agentVoiceSilence')) byId('agentVoiceSilence').disabled = !silenceEnabled;
    const rate = Number(byId('agentVoiceRate')?.value || 1);
    const silence = Number(byId('agentVoiceSilence')?.value || 0.2);
    if (byId('agentVoiceRateValue')) byId('agentVoiceRateValue').textContent = `${rate.toFixed(2)}×`;
    if (byId('agentVoiceSilenceValue')) byId('agentVoiceSilenceValue').textContent = `${silence.toFixed(2)} s`;
  }

  function render() {
    if (!state.profile || !state.catalog || !ensurePanel()) return;
    const profile = state.profile;
    const overrides = profile.overrides || {};
    const effective = profile.effective || {};
    const select = byId('agentVoiceChoice');
    if (select) {
      select.innerHTML = `<option value="">Use global voice</option>${(state.catalog.voices || []).map(voice => `<option value="${esc(voice.key)}">${esc(optionLabel(voice))}</option>`).join('')}`;
      select.value = overrides.voice || '';
    }

    const rateEnabled = overrides.speaking_rate != null;
    const silenceEnabled = overrides.sentence_silence != null;
    if (byId('agentVoiceRateEnabled')) byId('agentVoiceRateEnabled').checked = rateEnabled;
    if (byId('agentVoiceSilenceEnabled')) byId('agentVoiceSilenceEnabled').checked = silenceEnabled;
    if (byId('agentVoiceRate')) byId('agentVoiceRate').value = String(rateEnabled ? overrides.speaking_rate : effective.speaking_rate ?? 1);
    if (byId('agentVoiceSilence')) byId('agentVoiceSilence').value = String(silenceEnabled ? overrides.sentence_silence : effective.sentence_silence ?? 0.2);
    updateRangeState();

    if (byId('agentVoiceEffective')) byId('agentVoiceEffective').textContent = voiceLabel(effective.voice);
    const sourceLabels = {
      agent_override: 'Agent override',
      global: 'Inherited from global Voice Settings',
      fallback_global: 'Fallback to global Voice Settings',
      fallback_default: 'Fallback to bundled HomeServer voice',
    };
    if (byId('agentVoiceEffectiveSource')) {
      const traits = [
        sourceLabels[effective.voice_source] || effective.voice_source,
        effective.speaking_rate_source === 'agent_override' ? 'custom speed' : 'global speed',
        effective.sentence_silence_source === 'agent_override' ? 'custom pause' : 'global pause',
      ];
      byId('agentVoiceEffectiveSource').textContent = traits.join(' · ');
    }
    const badge = byId('agentVoiceReady');
    if (badge) {
      badge.textContent = effective.ready ? (effective.fallback ? 'Fallback ready' : 'Ready') : 'Voice unavailable';
      badge.className = `agent-voice-badge${effective.ready ? ' ready' : ' error'}${effective.fallback ? ' fallback' : ''}`;
    }
    const warning = byId('agentVoiceWarning');
    if (warning) {
      warning.textContent = effective.warning || '';
      warning.classList.toggle('hidden', !effective.warning);
    }
    const selected = state.catalog.voices.find(item => item.key === overrides.voice);
    if (byId('agentVoiceChoiceHelp')) {
      byId('agentVoiceChoiceHelp').textContent = !overrides.voice
        ? `Using global selection: ${voiceLabel(state.catalog.active_voice)}.`
        : selected?.available
          ? 'This Agent has its own installed local voice.'
          : 'The selected Agent voice is saved but not ready; HomeServer will use a trusted fallback until it is installed or repaired.';
    }
  }

  function readForm() {
    return {
      voice: byId('agentVoiceChoice')?.value || null,
      speaking_rate: byId('agentVoiceRateEnabled')?.checked ? Number(byId('agentVoiceRate')?.value || 1) : null,
      sentence_silence: byId('agentVoiceSilenceEnabled')?.checked ? Number(byId('agentVoiceSilence')?.value || 0.2) : null,
    };
  }

  async function load(force = false) {
    if (state.loading && !force) return state.loading;
    state.loading = (async () => {
      ensureStyles();
      ensurePanel();
      const [agentPayload, catalog] = await Promise.all([
        requestJson(AGENT_ENDPOINT),
        requestJson(CATALOG_ENDPOINT),
      ]);
      const agent = agentPayload.agent;
      if (!agent?.id) throw new Error('Primary Agent is not configured.');
      state.agentId = Number(agent.id);
      state.catalog = catalog;
      state.profile = await requestJson(`/api/v1/control/voice/agents/${state.agentId}/profile`);
      render();
      return state.profile;
    })().finally(() => { state.loading = null; });
    return state.loading;
  }

  async function save(event) {
    event.preventDefault();
    if (!state.agentId) await load(true);
    const button = event.submitter || event.currentTarget.querySelector('button[type="submit"]');
    if (button) button.disabled = true;
    try {
      state.profile = await requestJson(`/api/v1/control/voice/agents/${state.agentId}/profile`, {
        method: 'PUT',
        body: JSON.stringify(readForm()),
      });
      state.catalog = await requestJson(CATALOG_ENDPOINT);
      render();
      if (byId('agentVoiceSaved')) byId('agentVoiceSaved').textContent = 'Saved locally.';
      window.dispatchEvent(new CustomEvent('homeserver:agent-voice-profile-changed', {detail: state.profile}));
      flash('Agent Voice Profile saved.');
    } catch (error) {
      flash(error.message, true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function playResponse(response) {
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) throw new Error('This browser cannot play the Agent voice preview.');
    const context = new Context();
    try {
      await window.HomeServerVoiceSettings?.applyOutputSink?.(context);
      const bytes = await response.arrayBuffer();
      const decoded = await context.decodeAudioData(bytes.slice(0));
      const source = context.createBufferSource();
      source.buffer = decoded;
      source.connect(context.destination);
      await new Promise((resolve, reject) => {
        source.onended = resolve;
        try { source.start(0); } catch (error) { reject(error); }
      });
    } finally {
      try { await context.close(); } catch (_) {}
    }
  }

  async function preview() {
    if (!state.agentId) await load(true);
    const button = byId('agentVoicePreview');
    if (button) button.disabled = true;
    try {
      const response = await fetch(`/api/v1/control/voice/agents/${state.agentId}/preview`, {
        method: 'POST',
        cache: 'no-store',
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({...readForm(), text: PREVIEW_TEXT}),
      });
      if (!response.ok) {
        let detail = '';
        try { detail = (await response.json()).detail || ''; } catch (_) {}
        throw new Error(detail || `Agent voice preview failed (${response.status})`);
      }
      await playResponse(response);
      flash('Preview played without changing the saved Agent Voice Profile.');
    } catch (error) {
      flash(error.message, true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  function bindPanel(form) {
    form.addEventListener('submit', save);
    form.addEventListener('input', event => {
      if (['agentVoiceRateEnabled', 'agentVoiceSilenceEnabled', 'agentVoiceRate', 'agentVoiceSilence'].includes(event.target.id)) updateRangeState();
    });
    form.addEventListener('change', event => {
      if (event.target.id === 'agentVoiceChoice') {
        const selected = state.catalog?.voices?.find(item => item.key === event.target.value);
        if (byId('agentVoiceChoiceHelp')) {
          byId('agentVoiceChoiceHelp').textContent = !event.target.value
            ? `Will inherit the global voice: ${voiceLabel(state.catalog?.active_voice)}.`
            : selected?.available ? 'Selected voice is installed and ready.' : 'Selected voice is not ready; preview will use the trusted fallback.';
        }
      }
      updateRangeState();
    });
    byId('agentVoicePreview')?.addEventListener('click', () => preview());
  }

  function getEffective() {
    return state.profile?.effective ? {...state.profile.effective} : null;
  }

  function boot() {
    ensureStyles();
    if (!ensurePanel()) {
      setTimeout(boot, 80);
      return;
    }
    load().catch(() => null);
    document.addEventListener('click', event => {
      if (event.target.closest('[data-view="agent"]')) load(true).catch(error => flash(error.message, true));
    });
    window.addEventListener('homeserver:voice-settings-changed', () => load(true).catch(() => null));
    window.addEventListener('homeserver:voice-catalog-changed', () => load(true).catch(() => null));
  }

  window.HomeServerAgentVoiceProfile = Object.freeze({load, getEffective, preview});

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})();
