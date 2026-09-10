(() => {
  'use strict';

  const CATALOG_ENDPOINT = '/api/v1/control/voice/catalog';
  const PREVIEW_ENDPOINT = '/api/v1/control/voice/preview';
  const PREVIEW_TEXT = 'Hello. This is your HomeServer local voice preview.';
  let catalog = null;
  let busyAction = '';
  let statusMessage = '';
  let statusIsError = false;

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

  function selectedVoice() {
    const select = document.getElementById('voiceTtsVoice');
    const key = select?.value || '';
    return (catalog?.voices || []).find(item => item.key === key) || null;
  }

  function formatBytes(value) {
    const bytes = Number(value || 0);
    if (!Number.isFinite(bytes) || bytes <= 0) return 'Bundled';
    if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${bytes} B`;
  }

  function ensureStatusUi() {
    const select = document.getElementById('voiceTtsVoice');
    if (!select || document.getElementById('voicePackStatus')) return;
    const row = document.createElement('div');
    row.id = 'voicePackStatus';
    row.className = 'voice-pack-card';
    row.setAttribute('aria-live', 'polite');
    row.innerHTML = `
      <div class="voice-pack-card-head">
        <div>
          <div class="voice-pack-title-row"><strong id="voicePackTitle">Voice pack</strong><span id="voicePackBadge" class="voice-pack-badge">Checking…</span></div>
          <p id="voicePackMeta" class="muted"></p>
        </div>
        <span id="voicePackActiveBadge" class="voice-pack-active" hidden>Active</span>
      </div>
      <p id="voicePackStatusText" class="voice-pack-message muted"></p>
      <div id="voicePackSource" class="voice-pack-source muted"></div>
      <div class="voice-pack-actions">
        <button id="voicePackPreviewButton" class="button secondary" type="button">Preview voice</button>
        <button id="voicePackManageButton" class="button secondary" type="button" hidden></button>
        <button id="voicePackUninstallButton" class="text-button danger" type="button" hidden>Uninstall pack</button>
      </div>`;
    const label = select.closest('label');
    (label || select).insertAdjacentElement('afterend', row);
  }

  function decorateOptions() {
    const select = document.getElementById('voiceTtsVoice');
    if (!select || !catalog) return;
    [...select.options].forEach(option => {
      const voice = catalog.voices.find(item => item.key === option.value);
      if (!voice) return;
      const suffix = voice.available ? 'Installed' : voice.management_state === 'repair' ? 'Repair required' : 'Install required';
      option.textContent = `${voice.label || voice.key} · ${suffix}`;
    });
  }

  function defaultStatusMessage(voice) {
    if (voice.available) {
      return voice.bundled_with_runtime
        ? 'Installed with the shared Piper runtime and ready to use.'
        : 'Voice pack installed and ready to use.';
    }
    if (voice.management_state === 'repair') {
      return voice.install_reason || 'This voice or its shared Piper runtime needs repair.';
    }
    return voice.runtime_installed
      ? 'This trusted voice pack is not installed yet.'
      : 'The shared Piper runtime and this voice pack must be installed locally before use.';
  }

  function renderStatus() {
    ensureStatusUi();
    decorateOptions();
    const voice = selectedVoice();
    const title = document.getElementById('voicePackTitle');
    const badge = document.getElementById('voicePackBadge');
    const active = document.getElementById('voicePackActiveBadge');
    const meta = document.getElementById('voicePackMeta');
    const message = document.getElementById('voicePackStatusText');
    const source = document.getElementById('voicePackSource');
    const preview = document.getElementById('voicePackPreviewButton');
    const manage = document.getElementById('voicePackManageButton');
    const uninstall = document.getElementById('voicePackUninstallButton');
    if (!voice || !title || !badge || !active || !meta || !message || !source || !preview || !manage || !uninstall) return;

    title.textContent = voice.label || voice.key;
    active.hidden = !voice.active;
    meta.textContent = `${voice.region || voice.language || 'Local'} · ${String(voice.quality || 'voice')} quality · ${formatBytes(voice.download_bytes)}`;
    source.textContent = voice.source_label ? `Source: ${voice.source_label}${voice.installed_version ? ` · Installed ${voice.installed_version}` : ''}` : '';

    badge.className = 'voice-pack-badge';
    if (voice.available) {
      badge.textContent = 'Ready';
      badge.classList.add('ready');
    } else if (voice.management_state === 'repair') {
      badge.textContent = 'Needs repair';
      badge.classList.add('repair');
    } else {
      badge.textContent = 'Not installed';
      badge.classList.add('missing');
    }

    message.textContent = statusMessage || defaultStatusMessage(voice);
    message.classList.toggle('voice-pack-error', statusIsError);

    const busy = Boolean(busyAction);
    preview.hidden = false;
    preview.disabled = busy || !voice.can_preview;
    preview.textContent = busyAction === 'preview' ? 'Playing preview…' : 'Preview voice';

    manage.hidden = voice.available;
    manage.disabled = busy;
    if (voice.management_state === 'repair') {
      manage.dataset.action = 'repair';
      manage.textContent = busyAction === 'repair' ? 'Repairing…' : 'Repair voice';
    } else {
      manage.dataset.action = 'install';
      manage.textContent = busyAction === 'install' ? 'Installing…' : 'Install voice';
    }

    uninstall.hidden = Boolean(voice.bundled_with_runtime || !voice.installed);
    uninstall.disabled = busy || !voice.can_uninstall;
    uninstall.title = voice.active ? 'Select and save another voice before uninstalling this pack.' : '';
    uninstall.textContent = busyAction === 'uninstall' ? 'Uninstalling…' : voice.active ? 'Active voice cannot be removed' : 'Uninstall pack';
  }

  async function loadCatalog() {
    catalog = await requestJson(CATALOG_ENDPOINT);
    renderStatus();
    return catalog;
  }

  async function refreshAfterChange() {
    await loadCatalog();
    await window.HomeServerVoiceSettings?.load?.(true);
    renderStatus();
    window.dispatchEvent(new CustomEvent('homeserver:voice-catalog-changed', {detail: catalog}));
  }

  async function manageSelectedVoice(action) {
    const voice = selectedVoice();
    if (!voice || busyAction) return;
    if (!['install', 'repair'].includes(action)) return;
    busyAction = action;
    statusMessage = '';
    statusIsError = false;
    renderStatus();
    try {
      const payload = await requestJson(`${CATALOG_ENDPOINT}/${encodeURIComponent(voice.key)}/${action}`, {method: 'POST'});
      catalog = payload.catalog || catalog;
      statusMessage = action === 'repair' ? 'Voice repaired and ready.' : 'Voice installed and ready.';
      await refreshAfterChange();
    } catch (error) {
      statusMessage = error?.message || `Voice ${action} failed.`;
      statusIsError = true;
    } finally {
      busyAction = '';
      renderStatus();
    }
  }

  async function uninstallSelectedVoice() {
    const voice = selectedVoice();
    if (!voice || busyAction || !voice.can_uninstall) return;
    const confirmed = typeof window.confirm !== 'function' || window.confirm(`Uninstall ${voice.label || voice.key}? The shared Piper runtime will remain installed.`);
    if (!confirmed) return;
    busyAction = 'uninstall';
    statusMessage = '';
    statusIsError = false;
    renderStatus();
    try {
      const payload = await requestJson(`${CATALOG_ENDPOINT}/${encodeURIComponent(voice.key)}`, {method: 'DELETE'});
      catalog = payload.catalog || catalog;
      statusMessage = 'Voice pack uninstalled. The shared Piper runtime was kept.';
      await refreshAfterChange();
    } catch (error) {
      statusMessage = error?.message || 'Voice pack uninstall failed.';
      statusIsError = true;
    } finally {
      busyAction = '';
      renderStatus();
    }
  }

  async function previewSelectedVoice() {
    const voice = selectedVoice();
    if (!voice || busyAction || !voice.can_preview) return;
    busyAction = 'preview';
    statusMessage = '';
    statusIsError = false;
    renderStatus();
    try {
      const speakingRate = Number(document.getElementById('voiceSpeakingRate')?.value || 1);
      const sentenceSilence = Number(document.getElementById('voiceSentenceSilence')?.value || 0.2);
      const response = await fetch(PREVIEW_ENDPOINT, {
        method: 'POST',
        cache: 'no-store',
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          voice: voice.key,
          text: PREVIEW_TEXT,
          speaking_rate: speakingRate,
          sentence_silence: sentenceSilence,
        }),
      });
      if (!response.ok) {
        let detail = '';
        try { detail = (await response.json()).detail || ''; } catch (_) {}
        throw new Error(detail || `Voice preview failed (${response.status})`);
      }
      const Context = window.AudioContext || window.webkitAudioContext;
      if (!Context) throw new Error('This browser cannot play the local voice preview.');
      const context = new Context();
      try {
        await window.HomeServerVoiceSettings?.applyOutputSink?.(context);
        const bytes = await response.arrayBuffer();
        const decoded = await context.decodeAudioData(bytes.slice(0));
        const sourceNode = context.createBufferSource();
        sourceNode.buffer = decoded;
        sourceNode.connect(context.destination);
        await new Promise((resolve, reject) => {
          sourceNode.onended = resolve;
          try { sourceNode.start(0); } catch (error) { reject(error); }
        });
      } finally {
        try { await context.close(); } catch (_) {}
      }
      statusMessage = `Previewed ${voice.label || voice.key} without changing your saved voice.`;
    } catch (error) {
      statusMessage = error?.message || 'Voice preview failed.';
      statusIsError = true;
    } finally {
      busyAction = '';
      renderStatus();
    }
  }

  document.addEventListener('change', event => {
    if (event.target?.id === 'voiceTtsVoice') {
      statusMessage = '';
      statusIsError = false;
      renderStatus();
    }
  });
  document.addEventListener('click', event => {
    if (event.target?.closest?.('#voicePackPreviewButton')) { previewSelectedVoice(); return; }
    const manage = event.target?.closest?.('#voicePackManageButton');
    if (manage) { manageSelectedVoice(manage.dataset.action || 'install'); return; }
    if (event.target?.closest?.('#voicePackUninstallButton')) uninstallSelectedVoice();
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

  window.HomeServerVoiceCatalog = Object.freeze({
    load: loadCatalog,
    previewSelectedVoice,
    manageSelectedVoice,
    uninstallSelectedVoice,
  });
})();
