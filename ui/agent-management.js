(() => {
  'use strict';

  const API = '/api/v1/control/agents';
  const VOICE_API = '/api/v1/control/voice/agents';
  const CATALOG_API = '/api/v1/control/voice/catalog';
  const PREVIEW_TEXT = 'Hello. This is my HomeServer Agent persona voice.';
  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));

  const state = {
    items: [],
    catalog: null,
    details: new Map(),
    openId: null,
    loading: null,
    busy: false,
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

  function ensureStyles() {
    if (document.querySelector('link[href="/assets/agent-management.css"]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/agent-management.css';
    document.head.appendChild(link);
  }

  function ensurePanel() {
    if (byId('additionalAgentsPanel')) return true;
    const providerForm = byId('providerForm');
    const agentForm = byId('agentForm');
    if (!providerForm || !agentForm) return false;

    const panel = document.createElement('section');
    panel.id = 'additionalAgentsPanel';
    panel.className = 'panel agent-manager';
    panel.innerHTML = `
      <div class="panel-head agent-manager-head">
        <div>
          <p class="eyebrow">ADDITIONAL AGENTS · v0.46</p>
          <h3>Multi-Agent Personas</h3>
          <p class="muted agent-manager-note">Create independent local Agent identities and voice personas. The Primary Agent above remains the default for standard Agent Chat.</p>
        </div>
        <div class="agent-manager-head-actions">
          <button class="button secondary" id="duplicatePrimaryAgent" type="button">Duplicate primary</button>
          <button class="button primary" id="showAddAgent" type="button">Add Agent</button>
        </div>
      </div>
      <form id="addAgentForm" class="agent-manager-create hidden">
        <div class="agent-manager-grid">
          <label>Agent name<input id="newAgentName" maxlength="120" required placeholder="Research Agent"></label>
          <label>Model override <span class="muted">optional</span><input id="newAgentModel" maxlength="120" placeholder="Inherit selected provider"></label>
        </div>
        <label>Instructions<textarea id="newAgentInstructions" rows="5" maxlength="12000" placeholder="Describe this Agent's role, priorities, and boundaries."></textarea></label>
        <div class="form-actions"><button class="button secondary" id="cancelAddAgent" type="button">Cancel</button><button class="button primary" type="submit">Create Agent</button></div>
      </form>
      <div id="additionalAgentsList" class="agent-manager-list"><div class="agent-manager-empty muted">Loading additional Agents…</div></div>`;
    providerForm.insertAdjacentElement('beforebegin', panel);
    bindPanel(panel);
    return true;
  }

  function voiceStatusClass(voice) {
    if (!voice?.ready) return 'error';
    if (voice.fallback) return 'fallback';
    return 'ready';
  }

  function voiceSourceLabel(source) {
    return ({
      agent_override: 'Agent override',
      global: 'Global voice',
      fallback_global: 'Fallback to global',
      fallback_default: 'Fallback to bundled voice',
    })[source] || source || 'Unknown source';
  }

  function optionLabel(voice) {
    const status = voice.available ? 'Ready' : voice.management_state === 'repair' ? 'Needs repair' : 'Not installed';
    return `${voice.label || voice.key} · ${status}`;
  }

  function editorMarkup(item, detail) {
    if (!detail) return '<div class="agent-card-editor"><span class="muted">Loading Agent persona…</span></div>';
    const profile = detail.voice_profile || {};
    const overrides = profile.overrides || {};
    const effective = profile.effective || {};
    const customRate = overrides.speaking_rate != null;
    const customSilence = overrides.sentence_silence != null;
    const rate = customRate ? Number(overrides.speaking_rate) : Number(effective.speaking_rate ?? 1);
    const silence = customSilence ? Number(overrides.sentence_silence) : Number(effective.sentence_silence ?? 0.2);
    const voiceOptions = (state.catalog?.voices || []).map(voice =>
      `<option value="${esc(voice.key)}"${overrides.voice === voice.key ? ' selected' : ''}>${esc(optionLabel(voice))}</option>`
    ).join('');
    const warning = effective.warning || '';

    return `
      <form class="agent-card-editor" data-agent-editor="${item.id}">
        <div class="agent-manager-grid">
          <label>Agent name<input name="name" maxlength="120" required value="${esc(detail.name)}"></label>
          <label>Model override <span class="muted">optional</span><input name="model" maxlength="120" value="${esc(detail.model || '')}" placeholder="Inherit selected provider"></label>
        </div>
        <label>Instructions<textarea name="instructions" rows="7" maxlength="12000">${esc(detail.instructions || '')}</textarea></label>
        <div class="agent-manager-voice-grid">
          <label>Speaking voice
            <select name="voice"><option value=""${!overrides.voice ? ' selected' : ''}>Use global voice</option>${voiceOptions}</select>
            <small class="muted">Voice models come only from the trusted shared HomeServer Voice Catalog.</small>
          </label>
          <div class="agent-manager-voice-state">
            <span class="muted">Effective voice</span>
            <strong>${esc(item.voice?.label || effective.voice || 'Unknown')}</strong>
            <small class="muted">${esc(voiceSourceLabel(effective.voice_source))}</small>
          </div>
        </div>
        <div class="agent-manager-characteristics">
          <div class="agent-manager-characteristic">
            <label class="check-inline"><input name="rate_enabled" type="checkbox"${customRate ? ' checked' : ''}> Custom speaking speed</label>
            <label class="agent-manager-range">Speed <output data-rate-output>${rate.toFixed(2)}×</output><input name="speaking_rate" type="range" min="0.6" max="1.6" step="0.05" value="${rate}"${customRate ? '' : ' disabled'}></label>
            <small class="muted">Off = inherit global speed.</small>
          </div>
          <div class="agent-manager-characteristic">
            <label class="check-inline"><input name="silence_enabled" type="checkbox"${customSilence ? ' checked' : ''}> Custom sentence pause</label>
            <label class="agent-manager-range">Pause <output data-silence-output>${silence.toFixed(2)} s</output><input name="sentence_silence" type="range" min="0" max="1.5" step="0.05" value="${silence}"${customSilence ? '' : ' disabled'}></label>
            <small class="muted">Off = inherit global pause.</small>
          </div>
        </div>
        <div class="agent-manager-warning${warning ? '' : ' hidden'}">${esc(warning)}</div>
        <div class="agent-manager-editor-actions">
          <span class="muted">Secondary Agent · local owner-managed persona</span>
          <div class="agent-manager-editor-buttons">
            <button class="button secondary" type="button" data-preview-agent="${item.id}">Preview voice</button>
            <button class="button secondary" type="button" data-close-agent="${item.id}">Close</button>
            <button class="button primary" type="submit">Save Agent</button>
          </div>
        </div>
      </form>`;
  }

  function render() {
    if (!ensurePanel()) return;
    const list = byId('additionalAgentsList');
    if (!list) return;
    const secondary = state.items.filter(item => !item.is_primary);
    if (!secondary.length) {
      list.innerHTML = '<div class="agent-manager-empty"><strong>No additional Agents yet.</strong><br><span class="muted">Create one from scratch or duplicate the Primary Agent as a starting persona.</span></div>';
      return;
    }

    list.innerHTML = secondary.map(item => {
      const voice = item.voice || {};
      const open = Number(state.openId) === Number(item.id);
      const detail = state.details.get(Number(item.id))?.agent || null;
      const model = item.model || 'Selected Agent Brain provider';
      return `
        <article class="agent-card" data-agent-card="${item.id}">
          <div class="agent-card-summary">
            <div>
              <div class="agent-card-title"><h4>${esc(item.name)}</h4><span class="agent-persona-badge ${voiceStatusClass(voice)}">${voice.ready ? (voice.fallback ? 'Fallback ready' : 'Voice ready') : 'Voice unavailable'}</span></div>
              <div class="agent-card-meta muted"><span>${esc(model)}</span><span>${esc(voice.label || voice.key || 'Global voice')}</span><span>${voice.customized ? 'Custom persona' : 'Inherits global voice'}</span></div>
            </div>
            <div class="agent-card-actions">
              <button class="button secondary" type="button" data-edit-agent="${item.id}">${open ? 'Refresh' : 'Edit persona'}</button>
              <button class="text-button" type="button" data-duplicate-agent="${item.id}">Duplicate</button>
              <button class="text-button danger" type="button" data-delete-agent="${item.id}">Delete</button>
            </div>
          </div>
          ${open ? editorMarkup(item, detail) : ''}
        </article>`;
    }).join('');
    updateEditorControls(list);
  }

  function updateEditorControls(root = document) {
    root.querySelectorAll('[data-agent-editor]').forEach(form => {
      const rateEnabled = form.elements.rate_enabled?.checked;
      const silenceEnabled = form.elements.silence_enabled?.checked;
      if (form.elements.speaking_rate) form.elements.speaking_rate.disabled = !rateEnabled;
      if (form.elements.sentence_silence) form.elements.sentence_silence.disabled = !silenceEnabled;
      const rateOut = form.querySelector('[data-rate-output]');
      const silenceOut = form.querySelector('[data-silence-output]');
      if (rateOut) rateOut.textContent = `${Number(form.elements.speaking_rate?.value || 1).toFixed(2)}×`;
      if (silenceOut) silenceOut.textContent = `${Number(form.elements.sentence_silence?.value || 0.2).toFixed(2)} s`;
    });
  }

  async function load(force = false) {
    if (state.loading && !force) return state.loading;
    state.loading = (async () => {
      ensureStyles();
      ensurePanel();
      const [agents, catalog] = await Promise.all([requestJson(API), requestJson(CATALOG_API)]);
      state.items = agents.items || [];
      state.catalog = catalog;
      render();
      return agents;
    })().finally(() => { state.loading = null; });
    return state.loading;
  }

  async function openEditor(agentId) {
    const id = Number(agentId);
    state.openId = id;
    render();
    const detail = await requestJson(`${API}/${id}`);
    state.details.set(id, detail);
    render();
  }

  function readVoice(form) {
    return {
      voice: form.elements.voice?.value || null,
      speaking_rate: form.elements.rate_enabled?.checked ? Number(form.elements.speaking_rate.value) : null,
      sentence_silence: form.elements.silence_enabled?.checked ? Number(form.elements.sentence_silence.value) : null,
    };
  }

  function readIdentity(form) {
    return {
      name: form.elements.name.value,
      model: form.elements.model.value,
      instructions: form.elements.instructions.value,
    };
  }

  async function saveEditor(form) {
    const id = Number(form.dataset.agentEditor);
    const submit = form.querySelector('button[type="submit"]');
    if (submit) submit.disabled = true;
    try {
      await requestJson(`${API}/${id}`, {method: 'PUT', body: JSON.stringify(readIdentity(form))});
      const profile = await requestJson(`${VOICE_API}/${id}/profile`, {method: 'PUT', body: JSON.stringify(readVoice(form))});
      window.dispatchEvent(new CustomEvent('homeserver:agent-voice-profile-changed', {detail: profile}));
      state.details.delete(id);
      await load(true);
      await openEditor(id);
      flash('Agent identity and voice persona saved locally.');
    } catch (error) {
      flash(error.message, true);
    } finally {
      if (submit) submit.disabled = false;
    }
  }

  async function createAgent(form) {
    if (state.busy) return;
    state.busy = true;
    const submit = form.querySelector('button[type="submit"]');
    if (submit) submit.disabled = true;
    try {
      const payload = await requestJson(API, {
        method: 'POST',
        body: JSON.stringify({
          name: byId('newAgentName')?.value || '',
          model: byId('newAgentModel')?.value || '',
          instructions: byId('newAgentInstructions')?.value || '',
        }),
      });
      form.reset();
      form.classList.add('hidden');
      await load(true);
      await openEditor(payload.agent.id);
      flash('Additional Agent created.');
    } catch (error) {
      flash(error.message, true);
    } finally {
      state.busy = false;
      if (submit) submit.disabled = false;
    }
  }

  async function duplicateAgent(agentId) {
    if (state.busy) return;
    state.busy = true;
    try {
      const payload = await requestJson(`${API}/${Number(agentId)}/duplicate`, {method: 'POST'});
      await load(true);
      await openEditor(payload.agent.id);
      flash('Agent duplicated. Identity and voice persona were copied; memory was not copied.');
    } catch (error) {
      flash(error.message, true);
    } finally {
      state.busy = false;
    }
  }

  async function duplicatePrimary() {
    let primary = state.items.find(item => item.is_primary);
    if (!primary) {
      await load(true);
      primary = state.items.find(item => item.is_primary);
    }
    if (!primary) throw new Error('Primary Agent is not configured.');
    return duplicateAgent(primary.id);
  }

  async function deleteAgent(agentId) {
    if (state.busy) return;
    const item = state.items.find(candidate => Number(candidate.id) === Number(agentId));
    if (!item || item.is_primary) return;
    const confirmed = typeof window.confirm !== 'function' || window.confirm(`Delete ${item.name}? Its Agent Voice Profile will be removed. Any linked memories remain stored locally and become unassigned.`);
    if (!confirmed) return;
    state.busy = true;
    try {
      const result = await requestJson(`${API}/${Number(agentId)}`, {method: 'DELETE'});
      state.details.delete(Number(agentId));
      if (Number(state.openId) === Number(agentId)) state.openId = null;
      await load(true);
      const detached = Number(result.detached_memory_items || 0);
      flash(detached ? `Agent deleted. ${detached} memory item${detached === 1 ? '' : 's'} kept locally and unassigned.` : 'Agent deleted.');
    } catch (error) {
      flash(error.message, true);
    } finally {
      state.busy = false;
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

  async function previewAgent(agentId) {
    const form = document.querySelector(`[data-agent-editor="${Number(agentId)}"]`);
    if (!form) return;
    const button = form.querySelector(`[data-preview-agent="${Number(agentId)}"]`);
    if (button) button.disabled = true;
    try {
      const response = await fetch(`${VOICE_API}/${Number(agentId)}/preview`, {
        method: 'POST',
        cache: 'no-store',
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({...readVoice(form), text: PREVIEW_TEXT}),
      });
      if (!response.ok) {
        let detail = '';
        try { detail = (await response.json()).detail || ''; } catch (_) {}
        throw new Error(detail || `Agent voice preview failed (${response.status})`);
      }
      await playResponse(response);
      flash('Persona voice preview played without changing saved settings.');
    } catch (error) {
      flash(error.message, true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  function bindPanel(panel) {
    byId('showAddAgent')?.addEventListener('click', () => byId('addAgentForm')?.classList.remove('hidden'));
    byId('cancelAddAgent')?.addEventListener('click', () => {
      byId('addAgentForm')?.reset();
      byId('addAgentForm')?.classList.add('hidden');
    });
    byId('duplicatePrimaryAgent')?.addEventListener('click', () => duplicatePrimary().catch(error => flash(error.message, true)));
    byId('addAgentForm')?.addEventListener('submit', event => {
      event.preventDefault();
      createAgent(event.currentTarget);
    });

    panel.addEventListener('submit', event => {
      const editor = event.target.closest('[data-agent-editor]');
      if (!editor) return;
      event.preventDefault();
      saveEditor(editor);
    });
    panel.addEventListener('input', event => {
      if (event.target.closest('[data-agent-editor]')) updateEditorControls(panel);
    });
    panel.addEventListener('change', event => {
      if (event.target.closest('[data-agent-editor]')) updateEditorControls(panel);
    });
    panel.addEventListener('click', event => {
      const edit = event.target.closest('[data-edit-agent]');
      if (edit) { openEditor(edit.dataset.editAgent).catch(error => flash(error.message, true)); return; }
      const close = event.target.closest('[data-close-agent]');
      if (close) { state.openId = null; render(); return; }
      const duplicate = event.target.closest('[data-duplicate-agent]');
      if (duplicate) { duplicateAgent(duplicate.dataset.duplicateAgent); return; }
      const remove = event.target.closest('[data-delete-agent]');
      if (remove) { deleteAgent(remove.dataset.deleteAgent); return; }
      const preview = event.target.closest('[data-preview-agent]');
      if (preview) previewAgent(preview.dataset.previewAgent);
    });
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
    window.addEventListener('homeserver:agent-voice-profile-changed', () => load(true).catch(() => null));
  }

  window.HomeServerAgentManagement = Object.freeze({load, openEditor, duplicateAgent});

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})();
