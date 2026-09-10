(() => {
  'use strict';

  const CATALOG_ENDPOINT = '/api/v1/control/voice/catalog';
  const LOCAL_APPS_ENDPOINT = '/api/v1/control/local-apps';
  let catalog = null;
  let installing = false;

  async function requestJson(path, options = {}) {
    const response = await fetch(path, {
      cache: 'no-store',
      credentials: 'same-origin',
      ...options,
      headers: {...(options.headers || {})},
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
    return payload;
  }

  function selectedVoice() {
    const select = document.getElementById('voiceTtsVoice');
    const key = select?.value || '';
    return (catalog?.voices || []).find(item => item.key === key) || null;
  }

  function ensureStatusUi() {
    const select = document.getElementById('voiceTtsVoice');
    if (!select || document.getElementById('voicePackStatus')) return;
    const row = document.createElement('div');
    row.id = 'voicePackStatus';
    row.className = 'voice-pack-status muted';
    row.setAttribute('aria-live', 'polite');
    row.innerHTML = '<span id="voicePackStatusText"></span><button id="voicePackInstallButton" class="text-button" type="button" hidden></button>';
    select.insertAdjacentElement('afterend', row);
  }

  function decorateOptions() {
    const select = document.getElementById('voiceTtsVoice');
    if (!select || !catalog) return;
    [...select.options].forEach(option => {
      const voice = catalog.voices.find(item => item.key === option.value);
      if (!voice) return;
      option.textContent = `${voice.label || voice.key}${voice.available ? ' · Installed' : ' · Install required'}`;
    });
  }

  function renderStatus() {
    ensureStatusUi();
    decorateOptions();
    const text = document.getElementById('voicePackStatusText');
    const button = document.getElementById('voicePackInstallButton');
    if (!text || !button) return;
    const voice = selectedVoice();
    button.hidden = true;
    button.disabled = installing;
    if (!voice) {
      text.textContent = '';
      return;
    }
    if (voice.available) {
      text.textContent = voice.bundled_with_runtime
        ? 'Installed with the Piper TTS runtime.'
        : 'Voice pack installed and ready.';
      return;
    }
    const needsRuntime = !voice.runtime_installed;
    text.textContent = needsRuntime
      ? 'Piper runtime and this voice pack must be installed locally before use.'
      : 'This voice pack is available in the trusted catalog but is not installed.';
    button.hidden = false;
    button.textContent = installing ? 'Installing…' : `Install ${voice.label || 'voice pack'}`;
  }

  async function loadCatalog() {
    catalog = await requestJson(CATALOG_ENDPOINT);
    renderStatus();
    return catalog;
  }

  async function installSelectedVoice() {
    const voice = selectedVoice();
    if (!voice || voice.available || installing) return;
    installing = true;
    renderStatus();
    try {
      if (!voice.runtime_installed) {
        await requestJson(`${LOCAL_APPS_ENDPOINT}/${encodeURIComponent(voice.runtime_app_key)}/install`, {method: 'POST'});
      }
      if (!voice.bundled_with_runtime && !voice.installed) {
        await requestJson(`${LOCAL_APPS_ENDPOINT}/${encodeURIComponent(voice.install_app_key)}/install`, {method: 'POST'});
      }
      await loadCatalog();
      await window.HomeServerVoiceSettings?.load?.(true);
      renderStatus();
      window.dispatchEvent(new CustomEvent('homeserver:voice-catalog-changed', {detail: catalog}));
    } catch (error) {
      const text = document.getElementById('voicePackStatusText');
      if (text) text.textContent = error?.message || 'Voice pack installation failed.';
    } finally {
      installing = false;
      renderStatus();
    }
  }

  document.addEventListener('change', event => {
    if (event.target?.id === 'voiceTtsVoice') renderStatus();
  });
  document.addEventListener('click', event => {
    if (event.target?.closest?.('#voicePackInstallButton')) installSelectedVoice();
  });
  window.addEventListener('homeserver:voice-settings-loaded', () => loadCatalog().catch(() => null));
  window.addEventListener('homeserver:voice-settings-changed', () => loadCatalog().catch(() => null));

  const observer = new MutationObserver(() => {
    if (document.getElementById('voiceTtsVoice')) {
      ensureStatusUi();
      if (catalog) renderStatus();
    }
  });
  observer.observe(document.documentElement, {childList: true, subtree: true});

  window.HomeServerVoiceCatalog = Object.freeze({load: loadCatalog, installSelectedVoice});
})();
