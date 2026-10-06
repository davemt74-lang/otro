(() => {
  'use strict';

  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));

  async function contextApi(path, options = {}) {
    const headers = {...(options.headers || {})};
    if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, {...options, headers});
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }

  function ensureStyles() {
    if (document.querySelector('link[href="/assets/context-engine.css"]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/context-engine.css';
    document.head.appendChild(link);
  }

  function ensureCognitionRuntime() {
    if (document.querySelector('script[data-homeserver-cognition]')) return;
    const script = document.createElement('script');
    script.src = '/assets/cognition.js';
    script.dataset.homeserverCognition = '1';
    script.async = false;
    document.head.appendChild(script);
  }

  function activeConversationId() {
    return document.querySelector('[data-brain-conversation].active')?.dataset.brainConversation || null;
  }

  function ensureControls() {
    if (byId('chatContextControls')) return;
    const host = byId('chatPrivateContextPanel');
    const intro = document.querySelector('#view-chat .section-intro');
    if (!host && !intro) return;
    const panel = document.createElement('div');
    panel.id = 'chatContextControls';
    panel.className = 'context-engine-panel';
    panel.innerHTML = `
      <div class="context-engine-head">
        <div><p class="eyebrow">PRIVATE CONTEXT</p><strong>Agent Brain context</strong></div>
        <span id="contextEngineStatus" class="muted">Start a chat to set per-chat privacy.</span>
      </div>
      <div class="context-engine-controls">
        <label><input id="contextUseMemory" type="checkbox" checked> Memory</label>
        <label><input id="contextUseKnowledge" type="checkbox" checked> Knowledge</label>
        <label><input id="contextUseContacts" type="checkbox" checked> Contacts</label>
        <label><input id="contextUseAgentEyes" type="checkbox"> Agent Eyes · local read-only chat</label>
        <label class="context-cloud"><input id="contextCloudAllowed" type="checkbox" checked> Cloud providers allowed</label>
        <label class="context-budget">Context budget
          <select id="contextBudget">
            <option value="6000">Small · ~1.5K tokens</option>
            <option value="12000" selected>Standard · ~3K tokens</option>
            <option value="18000">Large · ~4.5K tokens</option>
            <option value="24000">Maximum · ~6K tokens</option>
          </select>
        </label>
      </div>
      <div class="context-engine-foot">
        <span>Agent Eyes reads recent permitted observations and requires local Ollama. Once enabled, this conversation stays local and read-only, including after you turn it off. Start a new chat to use cloud providers.</span>
        <div id="contextSources" class="context-sources"></div>
      </div>
      <div class="context-agent-eyes" aria-label="Agent Eyes context availability">
        <div class="context-agent-eyes-head">
          <strong id="contextAgentEyesStatus" role="status">Agent Eyes context is off for this chat.</strong>
          <button id="contextAgentEyesRefresh" class="button secondary" type="button" disabled>Refresh status</button>
          <button class="button secondary" type="button" data-view="tracky">Open Tracky</button>
        </div>
        <p id="contextAgentEyesEvidence"></p>
        <p id="contextAgentEyesGuidance">Enable Agent Eyes context to use recent permitted observations.</p>
        <p id="contextAgentEyesLimits" class="muted">Status is a checked snapshot, not a live view. Refreshing status never starts the camera or renews consent.</p>
      </div>`;
    if (host) host.appendChild(panel);
    else intro.insertAdjacentElement('afterend', panel);
    setControlsEnabled(false);
  }

  function setControlsEnabled(enabled) {
    for (const id of ['contextUseMemory','contextUseKnowledge','contextUseContacts','contextUseAgentEyes','contextCloudAllowed','contextBudget']) {
      const node = byId(id);
      if (node) node.disabled = !enabled;
    }
  }

  function setStatus(text, error = false) {
    const node = byId('contextEngineStatus');
    if (!node) return;
    node.textContent = text;
    node.classList.toggle('context-error', Boolean(error));
  }

  function applySettings(settings = {}) {
    if (byId('contextUseMemory')) byId('contextUseMemory').checked = settings.include_memory !== false;
    if (byId('contextUseKnowledge')) byId('contextUseKnowledge').checked = settings.include_knowledge !== false;
    if (byId('contextUseContacts')) byId('contextUseContacts').checked = settings.include_contacts !== false;
    if (byId('contextCloudAllowed')) byId('contextCloudAllowed').checked = settings.cloud_allowed !== false;
    if (byId('contextUseAgentEyes')) byId('contextUseAgentEyes').checked = settings.include_agent_eyes === true;
    if (settings.agent_eyes_local_only && byId('contextCloudAllowed')) byId('contextCloudAllowed').disabled = true;
    if (byId('contextBudget')) byId('contextBudget').value = String(settings.max_context_chars || 12000);
  }

  function renderSources(history = []) {
    const node = byId('contextSources');
    if (!node) return;
    const latest = history?.[0];
    const sources = latest?.sources || [];
    if (!sources.length) {
      node.innerHTML = '<span class="muted">No retrieved sources yet.</span>';
      return;
    }
    node.innerHTML = sources.slice(0, 12).map(source => {
      const label = source.kind === 'agent_eyes' ? 'Agent Eyes' : source.kind === 'local_transcription' ? 'Transcript' : source.kind === 'meeting_summary' ? 'Meeting' : source.kind === 'knowledge' ? 'Knowledge' : source.kind === 'memory' ? 'Memory' : 'Contact';
      return `<span class="context-source" title="${esc(source.updated_at || '')}"><b>${esc(label)}</b> ${esc(source.title || '')}</span>`;
    }).join('');
  }

  let policyRevision = 0, eyesRevision = 0, eyesPoll = null, eyesAgeTimer = null;
  let policySave = Promise.resolve();
  const chatVisible = () => document.visibilityState === 'visible'
    && byId('view-chat')?.classList.contains('active') === true;
  const eyesEnabled = () => Boolean(activeConversationId() && byId('contextUseAgentEyes')?.checked);

  function clearEyes(text = 'Checking Agent Eyes context…') {
    eyesRevision++;
    window.TrackySceneCorrections?.clear();
    clearTimeout(eyesPoll);
    clearTimeout(eyesAgeTimer);
    if (byId('contextAgentEyesStatus')) byId('contextAgentEyesStatus').textContent = text;
    if (byId('contextAgentEyesEvidence')) byId('contextAgentEyesEvidence').textContent = '';
    if (byId('contextAgentEyesGuidance')) byId('contextAgentEyesGuidance').textContent = '';
    if (byId('contextAgentEyesRefresh')) byId('contextAgentEyesRefresh').disabled = !eyesEnabled();
  }

  function scheduleEyesPoll() {
    clearTimeout(eyesPoll);
    if (eyesEnabled() && chatVisible()) eyesPoll = setTimeout(() => refreshEyes(), 5000);
  }

  function renderEyes(data = {}, requestStarted = Date.now()) {
    if (!byId('contextAgentEyesStatus')) return;
    clearEyes();
    const revision = eyesRevision;
    byId('contextAgentEyesStatus').textContent = data.explanation || 'Agent Eyes status could not be checked.';
    byId('contextAgentEyesGuidance').textContent = data.next_action || 'Refresh status or review Agent Eyes in Tracky.';
    if (data.limitations) byId('contextAgentEyesLimits').textContent = data.limitations;
    const categories = {none: 'No possible face regions', one: 'One possible face region', multiple: 'Multiple possible face regions'};
    const transit = (Date.now() - requestStarted) / 1000;
    const age = Number.isFinite(data.age_seconds) ? data.age_seconds + transit : null;
    // Enforce the local 60s display ceiling even if the network stalls. A
    // previously checked category must never become an indefinitely live view.
    if (data.state === 'recent_observation' && Object.hasOwn(categories, data.possible_face_regions)
        && transit >= 0 && Number.isFinite(age) && age >= 0 && age <= 60 && data.freshness_limit_seconds === 60) {
      const checkedAt = Date.now();
      const tick = () => {
        if (revision !== eyesRevision) return;
        const elapsed = (Date.now() - checkedAt) / 1000;
        const currentAge = age + elapsed;
        if (!eyesEnabled() || !chatVisible() || elapsed < 0 || currentAge > 60) {
          window.TrackySceneCorrections?.clear();
          byId('contextAgentEyesEvidence').textContent = '';
          byId('contextAgentEyesStatus').textContent = 'No current checked observation is available.';
          byId('contextAgentEyesGuidance').textContent = 'Refresh status; complete a new supervised observation in Tracky if needed.';
          return;
        }
        byId('contextAgentEyesEvidence').textContent = `${categories[data.possible_face_regions]}${data.scene && Array.isArray(data.scene.objects) ? ' · possible objects: '+data.scene.objects.join(', ')+' · '+data.scene.setting+' / '+data.scene.lighting+' · owner reports: '+(data.scene.owner_corrections||[]).map(r=>r.object+' '+(r.present?'present':'absent')+(r.conflicts_with_camera?' (differs from camera)':'')).join(', ') : ''} · observed ${Math.ceil(currentAge)}s ago · 60s limit · confidence uncalibrated`;
        eyesAgeTimer = setTimeout(tick, 1000);
      };
      window.TrackySceneCorrections?.render(data, {conversationId: activeConversationId(), requestStarted});
      tick();
    } else if (data.state === 'stale' && Number.isFinite(age) && age >= 0) {
      byId('contextAgentEyesEvidence').textContent = `Expired observation · ${Math.ceil(age)}s old when checked · 60s limit`;
    } else if (data.state === 'recent_observation') {
      byId('contextAgentEyesStatus').textContent = 'No current checked observation is available.';
      byId('contextAgentEyesGuidance').textContent = 'Refresh status; complete a new supervised observation in Tracky if needed.';
    }
    scheduleEyesPoll();
  }

  async function refreshEyes() {
    if (!eyesEnabled() || !chatVisible()) return;
    const id = activeConversationId(), policy = policyRevision;
    clearTimeout(eyesAgeTimer);
    clearEyes();
    const currentRevision = eyesRevision;
    const requestStarted = Date.now();
    try {
      const data = await contextApi(`/api/v1/control/conversations/${encodeURIComponent(id)}/agent-eyes-context`, {cache: 'no-store'});
      if (currentRevision !== eyesRevision || policy !== policyRevision || id !== activeConversationId() || !chatVisible()) return;
      renderEyes(data.agent_eyes_context, requestStarted);
    } catch (_) {
      if (currentRevision !== eyesRevision || policy !== policyRevision || id !== activeConversationId() || !chatVisible()) return;
      renderEyes();
    }
  }

  async function loadActive() {
    ensureControls();
    const id = activeConversationId();
    const revision = ++policyRevision;
    const requestStarted = Date.now();
    clearEyes();
    if (!id) {
      setControlsEnabled(false);
      applySettings({include_memory:true, include_knowledge:true, include_contacts:true, cloud_allowed:true, max_context_chars:12000});
      renderSources([]);
      setStatus('Start a chat to set per-chat privacy.');
      clearEyes('Start a chat to set Agent Eyes context.');
      return;
    }
    try {
      await policySave;
      if (revision !== policyRevision || id !== activeConversationId()) return;
      const data = await contextApi(`/api/v1/control/conversations/${encodeURIComponent(id)}`, {cache: 'no-store'});
      if (revision !== policyRevision || id !== activeConversationId()) return;
      renderSources(data.context_history || []);
      setControlsEnabled(true);
      const settings = data.context_settings || {};
      applySettings(settings);
      setStatus(settings.agent_eyes_local_only ? 'Agent Eyes history · local model required · read-only' : settings.cloud_allowed ? 'Cloud allowed · context policy saved' : 'Private · local model required');
      renderEyes(data.agent_eyes_context, requestStarted);
    } catch (err) {
      if (revision !== policyRevision || id !== activeConversationId()) return;
      setControlsEnabled(false);
      setStatus(err.message, true);
      clearEyes('Agent Eyes status could not be checked.');
    }
  }

  async function saveActive() {
    const id = activeConversationId();
    if (!id) return;
    const revision = ++policyRevision;
    clearEyes();
    setStatus('Saving context policy…');
    const body = JSON.stringify({
      include_memory: Boolean(byId('contextUseMemory')?.checked),
      include_knowledge: Boolean(byId('contextUseKnowledge')?.checked),
      include_agent_eyes: Boolean(byId('contextUseAgentEyes')?.checked),
      include_contacts: Boolean(byId('contextUseContacts')?.checked),
      cloud_allowed: Boolean(byId('contextCloudAllowed')?.checked),
      max_context_chars: Number(byId('contextBudget')?.value || 12000),
    });
    // Keep writes in user order; ignoring a late response alone does not stop
    // an older PUT from restoring an opt-in on the server after opt-out.
    const previousSave = policySave;
    let finishSave;
    policySave = new Promise(resolve => { finishSave = resolve; });
    await previousSave;
    const requestStarted = Date.now();
    try {
      const data = await contextApi(`/api/v1/control/conversations/${encodeURIComponent(id)}/context`, {
        method: 'PUT', body,
      });
      if (revision !== policyRevision || id !== activeConversationId()) return;
      const settings = data.context_settings || {};
      applySettings(settings);
      setStatus(settings.agent_eyes_local_only ? 'Agent Eyes history · local model required · read-only' : settings.cloud_allowed ? 'Cloud allowed · context policy saved' : 'Private · local model required');
      renderEyes(data.agent_eyes_context, requestStarted);
    } catch (err) {
      if (revision !== policyRevision || id !== activeConversationId()) return;
      setStatus(err.message, true);
      clearEyes('Agent Eyes status could not be checked.');
      scheduleRefresh();
    } finally {
      finishSave();
    }
  }

  let refreshTimer = null;
  function scheduleRefresh(delay = 80) {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => loadActive().catch(() => {}), delay);
  }

  document.addEventListener('change', event => {
    if (event.target.closest('#chatContextControls')) saveActive().catch(() => {});
  });

  document.addEventListener('click', event => {
    if (event.target.closest('#contextAgentEyesRefresh')) { refreshEyes().catch(() => {}); return; }
    if (event.target.closest('[data-brain-conversation]') || event.target.closest('#newChat, #sidebarNewChat')) {
      policyRevision++;
      clearEyes();
      scheduleRefresh(120);
    }
    const view = event.target.closest('[data-view]');
    if (view?.dataset?.view) {
      policyRevision++;
      clearEyes('Refresh status to check Agent Eyes context.');
      if (view.dataset.view === 'chat') scheduleRefresh(120);
    }
  });

  document.addEventListener('visibilitychange', () => {
    policyRevision++;
    clearEyes('Refresh status to check Agent Eyes context.');
    if (chatVisible()) scheduleRefresh(0);
  });

  const observer = new MutationObserver(() => scheduleRefresh(120));
  const watch = () => {
    ensureControls();
    ensureCognitionRuntime();
    const conversations = byId('conversationList');
    const messages = byId('chatMessages');
    if (conversations) observer.observe(conversations, {subtree:true, childList:true, attributes:true, attributeFilter:['class']});
    if (messages) observer.observe(messages, {childList:true, subtree:true});
    scheduleRefresh(0);
  };

  ensureStyles();
  ensureCognitionRuntime();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', watch, {once:true});
  else watch();
})();
