(() => {
  'use strict';

  const SETTINGS_ENDPOINT = '/api/v1/control/voice/settings';
  const SYNTHESIZE_ENDPOINT = '/api/v1/control/voice/synthesize';
  const INPUT_DEVICE_KEY = 'homeserver.voice.inputDeviceId';
  const OUTPUT_DEVICE_KEY = 'homeserver.voice.outputDeviceId';
  const STRICT_LOCAL_KEY = 'homeserver.strictLocalVoice';

  const DEFAULTS = Object.freeze({
    stt_model: 'tiny.en-q8_0',
    tts_voice: 'en_US-lessac-medium',
    speaking_rate: 1,
    sentence_silence: 0.2,
    listen_silence_ms: 900,
    no_speech_timeout_ms: 8000,
    max_segment_ms: 30000,
    default_mode: 'conversation',
    strict_local_default: false,
  });

  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));

  function storageGet(key) {
    try { return localStorage.getItem(key) || ''; } catch (_) { return ''; }
  }

  function storageSet(key, value) {
    try {
      if (value) localStorage.setItem(key, value);
      else localStorage.removeItem(key);
    } catch (_) {}
  }

  const state = {
    preferences: {...DEFAULTS},
    choices: {stt_models: [], tts_voices: [], default_modes: []},
    loaded: false,
    loadPromise: null,
    inputDeviceId: storageGet(INPUT_DEVICE_KEY),
    outputDeviceId: storageGet(OUTPUT_DEVICE_KEY),
    inputDevices: [],
    outputDevices: [],
    devicesLoaded: false,
    opener: null,
  };

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

  function normalizeClientPreferences(value) {
    const source = value && typeof value === 'object' ? value : {};
    return {
      stt_model: String(source.stt_model || DEFAULTS.stt_model),
      tts_voice: String(source.tts_voice || DEFAULTS.tts_voice),
      speaking_rate: Number(source.speaking_rate ?? DEFAULTS.speaking_rate),
      sentence_silence: Number(source.sentence_silence ?? DEFAULTS.sentence_silence),
      listen_silence_ms: Number(source.listen_silence_ms ?? DEFAULTS.listen_silence_ms),
      no_speech_timeout_ms: Number(source.no_speech_timeout_ms ?? DEFAULTS.no_speech_timeout_ms),
      max_segment_ms: Number(source.max_segment_ms ?? DEFAULTS.max_segment_ms),
      default_mode: source.default_mode === 'dictation' ? 'dictation' : 'conversation',
      strict_local_default: Boolean(source.strict_local_default),
    };
  }

  function applyChatDefaults() {
    const strict = byId('strictLocalVoice');
    if (strict && storageGet(STRICT_LOCAL_KEY) === '') {
      strict.checked = Boolean(state.preferences.strict_local_default);
    }

    const form = byId('chatForm');
    if (form) form.dataset.voiceDefaultMode = state.preferences.default_mode;
    const talk = byId('voiceInputButton');
    const dictate = byId('dictateInputButton');
    talk?.classList.toggle('voice-preferred', state.preferences.default_mode === 'conversation');
    dictate?.classList.toggle('voice-preferred', state.preferences.default_mode === 'dictation');
  }

  async function loadSettings(force = false) {
    if (state.loaded && !force) return state.preferences;
    if (state.loadPromise && !force) return state.loadPromise;
    state.loadPromise = requestJson(SETTINGS_ENDPOINT)
      .then(payload => {
        state.preferences = normalizeClientPreferences(payload.preferences);
        state.choices = payload.choices || state.choices;
        state.loaded = true;
        applyChatDefaults();
        window.dispatchEvent(new CustomEvent('homeserver:voice-settings-loaded', {detail: getPreferences()}));
        return state.preferences;
      })
      .finally(() => { state.loadPromise = null; });
    return state.loadPromise;
  }

  function getPreferences() {
    return {...state.preferences};
  }

  function getCaptureTiming() {
    const p = state.preferences;
    return {
      listenSilenceMs: Number(p.listen_silence_ms || DEFAULTS.listen_silence_ms),
      noSpeechTimeoutMs: Number(p.no_speech_timeout_ms || DEFAULTS.no_speech_timeout_ms),
      maxSegmentMs: Number(p.max_segment_ms || DEFAULTS.max_segment_ms),
    };
  }

  function getInputDeviceId() {
    return state.inputDeviceId || '';
  }

  function getOutputDeviceId() {
    return state.outputDeviceId || '';
  }

  function captureConstraints(base = {}) {
    const audio = {...base};
    if (state.inputDeviceId) audio.deviceId = {exact: state.inputDeviceId};
    return {audio};
  }

  async function applyOutputSink(target) {
    const deviceId = state.outputDeviceId;
    if (!deviceId) return false;
    if (!target || typeof target.setSinkId !== 'function') return false;
    try {
      await target.setSinkId(deviceId);
      return true;
    } catch (_) {
      return false;
    }
  }

  function optionMarkup(items, selected) {
    return (items || []).map(item => `<option value="${esc(item.key)}"${item.key === selected ? ' selected' : ''}>${esc(item.label || item.key)}</option>`).join('');
  }

  function selectedDeviceMarkup(devices, selected, kind) {
    const rows = [`<option value="">System default ${kind}</option>`];
    let found = !selected;
    devices.forEach((device, index) => {
      const label = device.label || `${kind === 'microphone' ? 'Microphone' : 'Speaker'} ${index + 1}`;
      rows.push(`<option value="${esc(device.deviceId)}"${device.deviceId === selected ? ' selected' : ''}>${esc(label)}</option>`);
      if (device.deviceId === selected) found = true;
    });
    if (selected && !found) rows.push(`<option value="${esc(selected)}" selected>Previously selected ${kind} (currently unavailable)</option>`);
    return rows.join('');
  }

  function renderDevices() {
    const input = byId('voiceInputDevice');
    const output = byId('voiceOutputDevice');
    if (input) input.innerHTML = selectedDeviceMarkup(state.inputDevices, state.inputDeviceId, 'microphone');
    if (output) output.innerHTML = selectedDeviceMarkup(state.outputDevices, state.outputDeviceId, 'speaker');
    const status = byId('voiceDeviceStatus');
    if (status) {
      if (!navigator.mediaDevices?.enumerateDevices) {
        status.textContent = 'This browser does not expose selectable audio devices.';
      } else if (!state.devicesLoaded) {
        status.textContent = 'Device names are loaded only when you ask. HomeServer does not store device IDs on the server.';
      } else {
        const named = [...state.inputDevices, ...state.outputDevices].some(item => item.label);
        status.textContent = named
          ? `${state.inputDevices.length} microphone${state.inputDevices.length === 1 ? '' : 's'} · ${state.outputDevices.length} speaker${state.outputDevices.length === 1 ? '' : 's'}`
          : 'Devices found. Allow microphone access if you want the browser to reveal device names.';
      }
    }
  }

  async function enumerateDevices(requestPermission = false) {
    if (!navigator.mediaDevices?.enumerateDevices) {
      state.inputDevices = [];
      state.outputDevices = [];
      state.devicesLoaded = true;
      renderDevices();
      return;
    }

    let permissionStream = null;
    if (requestPermission && navigator.mediaDevices.getUserMedia) {
      try {
        permissionStream = await navigator.mediaDevices.getUserMedia(captureConstraints({channelCount: 1}));
      } catch (error) {
        flash(error?.message || 'Microphone permission was not granted.', true);
      } finally {
        permissionStream?.getTracks?.().forEach(track => track.stop());
      }
    }

    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      state.inputDevices = devices.filter(item => item.kind === 'audioinput');
      state.outputDevices = devices.filter(item => item.kind === 'audiooutput');
      state.devicesLoaded = true;
    } catch (_) {
      state.inputDevices = [];
      state.outputDevices = [];
      state.devicesLoaded = true;
    }
    renderDevices();
  }

  function updateRangeOutput(inputId, outputId, formatter = value => value) {
    const input = byId(inputId);
    const output = byId(outputId);
    if (!input || !output) return;
    output.value = formatter(input.value);
    output.textContent = formatter(input.value);
  }

  function renderForm() {
    const p = state.preferences;
    const stt = byId('voiceSttModel');
    const tts = byId('voiceTtsVoice');
    const mode = byId('voiceDefaultMode');
    if (stt) stt.innerHTML = optionMarkup(state.choices.stt_models, p.stt_model);
    if (tts) tts.innerHTML = optionMarkup(state.choices.tts_voices, p.tts_voice);
    if (mode) mode.innerHTML = optionMarkup(state.choices.default_modes, p.default_mode);

    const values = {
      voiceSpeakingRate: p.speaking_rate,
      voiceSentenceSilence: p.sentence_silence,
      voiceListenSilence: p.listen_silence_ms,
      voiceNoSpeechTimeout: p.no_speech_timeout_ms,
      voiceMaxSegment: p.max_segment_ms,
    };
    Object.entries(values).forEach(([id, value]) => { const input = byId(id); if (input) input.value = String(value); });
    const strict = byId('voiceStrictLocalDefault');
    if (strict) strict.checked = Boolean(p.strict_local_default);
    renderDevices();
    updateRangeOutput('voiceSpeakingRate', 'voiceSpeakingRateValue', value => `${Number(value).toFixed(2)}×`);
    updateRangeOutput('voiceSentenceSilence', 'voiceSentenceSilenceValue', value => `${Number(value).toFixed(2)} s`);
    updateRangeOutput('voiceListenSilence', 'voiceListenSilenceValue', value => `${Number(value)} ms`);
    updateRangeOutput('voiceNoSpeechTimeout', 'voiceNoSpeechTimeoutValue', value => `${(Number(value) / 1000).toFixed(1)} s`);
    updateRangeOutput('voiceMaxSegment', 'voiceMaxSegmentValue', value => `${(Number(value) / 1000).toFixed(0)} s`);
  }

  function ensureDialog() {
    if (byId('voiceSettingsOverlay')) return;
    const overlay = document.createElement('div');
    overlay.id = 'voiceSettingsOverlay';
    overlay.className = 'voice-settings-overlay hidden';
    overlay.innerHTML = `
      <section id="voiceSettingsDialog" class="voice-settings-dialog" role="dialog" aria-modal="true" aria-labelledby="voiceSettingsTitle" tabindex="-1">
        <div class="voice-settings-head">
          <div><p class="eyebrow">LOCAL VOICE</p><h3 id="voiceSettingsTitle">Voice Settings</h3><p>Configure Talk, Dictate, Whisper and Piper without sending device identifiers to HomeServer.</p></div>
          <button id="closeVoiceSettings" class="chat-drawer-close" type="button" aria-label="Close Voice Settings">×</button>
        </div>
        <form id="voiceSettingsForm" class="voice-settings-form">
          <section class="voice-settings-card">
            <h4>Voice engines</h4>
            <div class="voice-settings-grid">
              <label>Whisper transcription model<select id="voiceSttModel"></select></label>
              <label>Piper speaking voice<select id="voiceTtsVoice"></select></label>
              <label>Preferred microphone mode<select id="voiceDefaultMode"></select></label>
              <label class="voice-settings-check"><input id="voiceStrictLocalDefault" type="checkbox"><span><strong>Strict Local by default</strong><small>Blocks browser/OS speech-service fallback on browsers that have not chosen a local override.</small></span></label>
            </div>
          </section>
          <section class="voice-settings-card">
            <div class="voice-settings-section-head"><div><h4>Microphone & speaker</h4><p>Selections stay in this browser's local storage and are not written to the HomeServer database.</p></div><button id="refreshVoiceDevices" class="button secondary" type="button">Allow / refresh devices</button></div>
            <div class="voice-settings-grid">
              <label>Microphone<select id="voiceInputDevice"></select></label>
              <label>Speaker<select id="voiceOutputDevice"></select></label>
            </div>
            <p id="voiceDeviceStatus" class="muted" aria-live="polite"></p>
            <p class="voice-settings-note">Selected microphone applies to HomeServer local capture. Browser speech-recognition fallback uses the browser/OS default input. Selected speaker applies when the browser supports audio output routing; browser speech-synthesis fallback uses the OS default output.</p>
          </section>
          <section class="voice-settings-card">
            <h4>Conversation timing</h4>
            <label class="voice-range">Speaking speed <output id="voiceSpeakingRateValue"></output><input id="voiceSpeakingRate" type="range" min="0.6" max="1.6" step="0.05"></label>
            <label class="voice-range">Pause between Piper sentences <output id="voiceSentenceSilenceValue"></output><input id="voiceSentenceSilence" type="range" min="0" max="1.5" step="0.05"></label>
            <label class="voice-range">Silence before finishing an utterance <output id="voiceListenSilenceValue"></output><input id="voiceListenSilence" type="range" min="400" max="3000" step="100"></label>
            <label class="voice-range">No-speech timeout <output id="voiceNoSpeechTimeoutValue"></output><input id="voiceNoSpeechTimeout" type="range" min="2000" max="30000" step="500"></label>
            <label class="voice-range">Maximum utterance length <output id="voiceMaxSegmentValue"></output><input id="voiceMaxSegment" type="range" min="5000" max="60000" step="1000"></label>
          </section>
          <div class="voice-settings-actions">
            <button id="testVoiceOutput" class="button secondary" type="button">Test local voice</button>
            <div><button id="resetVoiceSettings" class="text-button" type="button">Reset defaults</button><button class="button primary" type="submit">Save Voice Settings</button></div>
          </div>
        </form>
      </section>`;
    document.body.appendChild(overlay);
  }

  function ensureControl() {
    if (byId('chatVoiceSettingsButton')) return true;
    const options = document.querySelector('#chatForm .chat-voice-options');
    const badge = byId('localVoiceBadge');
    if (!options || !badge) return false;
    const button = document.createElement('button');
    button.id = 'chatVoiceSettingsButton';
    button.type = 'button';
    button.className = 'text-button chat-voice-settings-button';
    button.setAttribute('aria-haspopup', 'dialog');
    button.setAttribute('aria-controls', 'voiceSettingsDialog');
    button.textContent = 'Voice settings';
    options.insertBefore(button, badge);
    applyChatDefaults();
    return true;
  }

  function stopActiveVoiceModes() {
    const talk = byId('voiceInputButton');
    const dictate = byId('dictateInputButton');
    if (talk?.getAttribute('aria-pressed') === 'true') talk.click();
    if (dictate?.getAttribute('aria-pressed') === 'true') dictate.click();
  }

  async function openDialog(opener) {
    stopActiveVoiceModes();
    state.opener = opener || document.activeElement;
    ensureDialog();
    try { await loadSettings(); } catch (error) { flash(error.message, true); }
    renderForm();
    enumerateDevices(false).catch(() => null);
    const overlay = byId('voiceSettingsOverlay');
    overlay?.classList.remove('hidden');
    byId('voiceSettingsDialog')?.focus({preventScroll: true});
  }

  function closeDialog() {
    byId('voiceSettingsOverlay')?.classList.add('hidden');
    const opener = state.opener;
    state.opener = null;
    opener?.focus?.({preventScroll: true});
  }

  function readFormPreferences() {
    return {
      stt_model: byId('voiceSttModel')?.value || DEFAULTS.stt_model,
      tts_voice: byId('voiceTtsVoice')?.value || DEFAULTS.tts_voice,
      speaking_rate: Number(byId('voiceSpeakingRate')?.value || DEFAULTS.speaking_rate),
      sentence_silence: Number(byId('voiceSentenceSilence')?.value || DEFAULTS.sentence_silence),
      listen_silence_ms: Number(byId('voiceListenSilence')?.value || DEFAULTS.listen_silence_ms),
      no_speech_timeout_ms: Number(byId('voiceNoSpeechTimeout')?.value || DEFAULTS.no_speech_timeout_ms),
      max_segment_ms: Number(byId('voiceMaxSegment')?.value || DEFAULTS.max_segment_ms),
      default_mode: byId('voiceDefaultMode')?.value === 'dictation' ? 'dictation' : 'conversation',
      strict_local_default: Boolean(byId('voiceStrictLocalDefault')?.checked),
    };
  }

  async function saveSettings(event) {
    event.preventDefault();
    const inputId = byId('voiceInputDevice')?.value || '';
    const outputId = byId('voiceOutputDevice')?.value || '';
    const payload = readFormPreferences();
    try {
      const response = await requestJson(SETTINGS_ENDPOINT, {method: 'PUT', body: JSON.stringify(payload)});
      state.preferences = normalizeClientPreferences(response.preferences);
      state.choices = response.choices || state.choices;
      state.loaded = true;
      state.inputDeviceId = inputId;
      state.outputDeviceId = outputId;
      storageSet(INPUT_DEVICE_KEY, inputId);
      storageSet(OUTPUT_DEVICE_KEY, outputId);
      storageSet(STRICT_LOCAL_KEY, state.preferences.strict_local_default ? '1' : '0');
      const strict = byId('strictLocalVoice');
      if (strict) strict.checked = state.preferences.strict_local_default;
      applyChatDefaults();
      window.dispatchEvent(new CustomEvent('homeserver:voice-settings-changed', {detail: getPreferences()}));
      closeDialog();
      flash('Voice Settings saved.');
    } catch (error) {
      flash(error.message, true);
    }
  }

  function resetForm() {
    state.preferences = {...DEFAULTS};
    renderForm();
  }

  async function testOutput() {
    const button = byId('testVoiceOutput');
    if (button) button.disabled = true;
    try {
      const response = await fetch(SYNTHESIZE_ENDPOINT, {
        method: 'POST',
        cache: 'no-store',
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text: 'HomeServer local voice is ready.'}),
      });
      if (!response.ok) {
        let detail = '';
        try { detail = (await response.json()).detail || ''; } catch (_) {}
        throw new Error(detail || `Voice test failed (${response.status})`);
      }
      const Context = window.AudioContext || window.webkitAudioContext;
      if (!Context) throw new Error('This browser cannot play the local voice test.');
      const context = new Context();
      try {
        await applyOutputSink(context);
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
    } catch (error) {
      flash(error.message, true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  function boot() {
    ensureDialog();
    loadSettings().catch(() => null);
    let attempts = 0;
    const timer = setInterval(() => {
      attempts += 1;
      if (ensureControl() || attempts >= 120) clearInterval(timer);
    }, 100);

    document.addEventListener('click', event => {
      const open = event.target.closest('#chatVoiceSettingsButton');
      if (open) { openDialog(open); return; }
      if (event.target.closest('#closeVoiceSettings')) { closeDialog(); return; }
      if (event.target.id === 'voiceSettingsOverlay') { closeDialog(); return; }
      if (event.target.closest('#refreshVoiceDevices')) { enumerateDevices(true).catch(error => flash(error.message, true)); return; }
      if (event.target.closest('#resetVoiceSettings')) { resetForm(); return; }
      if (event.target.closest('#testVoiceOutput')) { testOutput(); }
    });

    document.addEventListener('input', event => {
      if (event.target.id === 'voiceSpeakingRate') updateRangeOutput('voiceSpeakingRate', 'voiceSpeakingRateValue', value => `${Number(value).toFixed(2)}×`);
      if (event.target.id === 'voiceSentenceSilence') updateRangeOutput('voiceSentenceSilence', 'voiceSentenceSilenceValue', value => `${Number(value).toFixed(2)} s`);
      if (event.target.id === 'voiceListenSilence') updateRangeOutput('voiceListenSilence', 'voiceListenSilenceValue', value => `${Number(value)} ms`);
      if (event.target.id === 'voiceNoSpeechTimeout') updateRangeOutput('voiceNoSpeechTimeout', 'voiceNoSpeechTimeoutValue', value => `${(Number(value) / 1000).toFixed(1)} s`);
      if (event.target.id === 'voiceMaxSegment') updateRangeOutput('voiceMaxSegment', 'voiceMaxSegmentValue', value => `${(Number(value) / 1000).toFixed(0)} s`);
    });

    byId('voiceSettingsForm')?.addEventListener('submit', saveSettings);
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !byId('voiceSettingsOverlay')?.classList.contains('hidden')) closeDialog();
    });
    navigator.mediaDevices?.addEventListener?.('devicechange', () => {
      if (!byId('voiceSettingsOverlay')?.classList.contains('hidden')) enumerateDevices(false).catch(() => null);
    });
  }

  window.HomeServerVoiceSettings = Object.freeze({
    load: loadSettings,
    getPreferences,
    getCaptureTiming,
    getInputDeviceId,
    getOutputDeviceId,
    captureConstraints,
    applyOutputSink,
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})();
