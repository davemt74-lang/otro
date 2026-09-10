(() => {
  'use strict';

  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = value => value ? new Date(value).toLocaleString() : '—';
  let recognition = null;
  let recognitionBase = '';
  let drawerRefreshTimer = null;

  async function readJson(path) {
    const response = await fetch(path, {cache: 'no-store', credentials: 'same-origin'});
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }

  function flash(message, error = false) {
    const node = byId('flash');
    if (!node) return;
    node.textContent = message;
    node.className = `flash show${error ? ' error' : ''}`;
    clearTimeout(flash.timer);
    flash.timer = setTimeout(() => { node.className = 'flash'; }, 4200);
  }

  function ensureEnhancements() {
    const chatPanel = document.querySelector('#view-chat .chat-panel');
    const compose = byId('chatForm');
    const input = byId('chatInput');
    const head = chatPanel?.querySelector('.chat-head');
    const deleteButton = byId('deleteChat');
    if (!chatPanel || !compose || !input || !head || !deleteButton) return false;

    if (!byId('voiceInputButton')) {
      compose.classList.add('has-chat-enhancements');
      const voice = document.createElement('button');
      voice.id = 'voiceInputButton';
      voice.type = 'button';
      voice.className = 'chat-icon-button';
      voice.setAttribute('aria-label', 'Start voice dictation');
      voice.setAttribute('aria-pressed', 'false');
      voice.setAttribute('aria-describedby', 'voicePrivacyNote');
      voice.title = 'Voice dictation';
      voice.innerHTML = '<span aria-hidden="true">◉</span><span class="chat-control-label">Voice</span>';
      compose.insertBefore(voice, compose.querySelector('button[type="submit"]'));

      const note = document.createElement('div');
      note.id = 'voicePrivacyNote';
      note.className = 'chat-voice-note';
      note.textContent = 'Voice dictation uses your browser/OS speech service and may not be local. HomeServer receives text only when you send the message.';
      compose.appendChild(note);
    }

    if (!byId('chatBrainToggle')) {
      const actions = document.createElement('div');
      actions.className = 'chat-head-actions';
      const toggle = document.createElement('button');
      toggle.id = 'chatBrainToggle';
      toggle.type = 'button';
      toggle.className = 'text-button chat-brain-toggle';
      toggle.setAttribute('aria-controls', 'chatBrainDrawer');
      toggle.setAttribute('aria-expanded', 'false');
      toggle.textContent = 'Brain activity';
      actions.append(toggle, deleteButton);
      head.appendChild(actions);

      const backdrop = document.createElement('button');
      backdrop.id = 'chatBrainBackdrop';
      backdrop.type = 'button';
      backdrop.className = 'chat-brain-backdrop hidden';
      backdrop.setAttribute('aria-label', 'Close Brain activity');

      const drawer = document.createElement('aside');
      drawer.id = 'chatBrainDrawer';
      drawer.className = 'chat-brain-drawer';
      drawer.setAttribute('aria-hidden', 'true');
      drawer.setAttribute('aria-label', 'Inspectable Brain activity');
      drawer.innerHTML = `
        <div class="chat-brain-drawer-head">
          <div><p class="eyebrow">INSPECTABLE RUNTIME</p><h3>Brain activity</h3></div>
          <div class="chat-brain-drawer-actions"><button class="text-button" id="refreshChatBrain" type="button">Refresh</button><button class="chat-drawer-close" id="closeChatBrain" type="button" aria-label="Close Brain activity">×</button></div>
        </div>
        <p class="chat-brain-disclosure">This view shows provider selection, context summaries, cognitive events, tool runs and audited actions. It does not expose hidden model reasoning or chain-of-thought.</p>
        <div id="chatBrainContent" class="chat-brain-content"><div class="chat-brain-loading">Open the drawer to load current activity.</div></div>`;
      chatPanel.append(backdrop, drawer);
    }
    return true;
  }

  function settledValue(result, fallback = {}) {
    return result?.status === 'fulfilled' ? result.value : fallback;
  }

  function renderList(items, renderer, emptyText) {
    return items?.length ? `<div class="chat-brain-list">${items.map(renderer).join('')}</div>` : `<div class="chat-brain-empty">${esc(emptyText)}</div>`;
  }

  async function loadBrainActivity() {
    const content = byId('chatBrainContent');
    if (!content || byId('chatBrainDrawer')?.getAttribute('aria-hidden') === 'true') return;
    content.innerHTML = '<div class="chat-brain-loading">Loading inspectable HomeServer activity…</div>';

    const results = await Promise.allSettled([
      readJson('/api/v1/control/inference'),
      readJson('/api/v1/control/cognition'),
      readJson('/api/v1/control/cognition/events?limit=12'),
      readJson('/api/v1/control/tool-runs?limit=12'),
      readJson('/api/v1/control/activity?limit=12'),
    ]);

    const inference = settledValue(results[0]);
    const cognition = settledValue(results[1]);
    const events = settledValue(results[2], {items: []});
    const tools = settledValue(results[3], {items: []});
    const activity = settledValue(results[4], {items: []});
    const failures = results.filter(result => result.status === 'rejected').length;
    const runtime = cognition.runtime || {};
    const context = byId('chatContext')?.textContent?.trim() || 'No chat context summary yet. Send a message to populate it.';
    const provider = inference.available
      ? `${inference.compute_source === 'homeserver_local' ? 'HomeServer local' : 'User provider'} · ${inference.selected_provider || 'auto'}${inference.model ? ` · ${inference.model}` : ''}`
      : 'No HomeServer inference provider is currently ready.';

    content.innerHTML = `
      ${failures ? `<div class="chat-brain-warning">${failures} activity source${failures === 1 ? '' : 's'} could not be read. Available data is shown below.</div>` : ''}
      <section class="chat-brain-card"><p class="eyebrow">CURRENT BRAIN</p><strong>${esc(provider)}</strong><p>${esc(context)}</p></section>
      <div class="chat-brain-stats">
        <div><strong>${Number(runtime.events || 0).toLocaleString()}</strong><span>cognitive events</span></div>
        <div><strong>${Number(runtime.open_awareness || 0).toLocaleString()}</strong><span>open signals</span></div>
        <div><strong>${Number(runtime.memory_candidates || 0).toLocaleString()}</strong><span>memory candidates</span></div>
      </div>
      <section class="chat-brain-section"><div class="chat-brain-section-head"><div><p class="eyebrow">COGNITION</p><h4>Recent events</h4></div></div>${renderList(events.items || [], item => `<article><div><strong>${esc(item.title || item.event_type || 'Cognitive event')}</strong><span>${esc(fmt(item.occurred_at || item.created_at))}</span></div><p>${esc(item.summary || '')}</p><small>${esc(item.source_app_key || 'HomeServer')}${item.event_type ? ` · ${esc(item.event_type)}` : ''}</small></article>`, 'No cognitive events yet.')}</section>
      <section class="chat-brain-section"><div class="chat-brain-section-head"><div><p class="eyebrow">TOOLS</p><h4>Recent tool runs</h4></div></div>${renderList(tools.items || [], item => `<article><div><strong>${esc(item.tool_key || 'Tool')}</strong><span>${esc(fmt(item.created_at))}</span></div><p>${esc(item.status || 'unknown')}${item.duration_ms == null ? '' : ` · ${Number(item.duration_ms)} ms`}</p><small>${esc(item.source_app_key || 'HomeServer')}</small></article>`, 'No tool runs yet.')}</section>
      <section class="chat-brain-section"><div class="chat-brain-section-head"><div><p class="eyebrow">AUDIT</p><h4>Recent HomeServer actions</h4></div></div>${renderList(activity.items || [], item => `<article><div><strong>${esc(item.action || 'Activity')}</strong><span>${esc(fmt(item.created_at))}</span></div><p>${esc([item.resource_type, item.resource_key].filter(Boolean).join(' · ') || 'HomeServer')}</p><small>${esc(item.actor_type || 'owner')}${item.actor_key ? ` · ${esc(item.actor_key)}` : ''}</small></article>`, 'No audited activity yet.')}</section>`;
  }

  function setDrawer(open) {
    const drawer = byId('chatBrainDrawer');
    const backdrop = byId('chatBrainBackdrop');
    const toggle = byId('chatBrainToggle');
    if (!drawer || !backdrop || !toggle) return;
    drawer.setAttribute('aria-hidden', open ? 'false' : 'true');
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    backdrop.classList.toggle('hidden', !open);
    document.querySelector('#view-chat .chat-panel')?.classList.toggle('brain-drawer-open', open);
    if (open) loadBrainActivity().catch(err => {
      const content = byId('chatBrainContent');
      if (content) content.innerHTML = `<div class="chat-brain-warning">${esc(err.message)}</div>`;
    });
  }

  function speechConstructor() {
    return window.SpeechRecognition || window.webkitSpeechRecognition || null;
  }

  function resetVoiceButton() {
    const button = byId('voiceInputButton');
    if (!button) return;
    button.classList.remove('listening');
    button.setAttribute('aria-pressed', 'false');
    button.setAttribute('aria-label', 'Start voice dictation');
    button.querySelector('.chat-control-label').textContent = 'Voice';
  }

  function startVoice() {
    const ButtonRecognition = speechConstructor();
    const input = byId('chatInput');
    const button = byId('voiceInputButton');
    if (!ButtonRecognition || !input || !button) {
      flash('Voice dictation is not supported by this browser. You can continue typing normally.', true);
      return;
    }
    if (recognition) {
      recognition.stop();
      return;
    }

    recognitionBase = input.value.trimEnd();
    recognition = new ButtonRecognition();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = navigator.language || document.documentElement.lang || 'en-US';
    recognition.maxAlternatives = 1;
    button.classList.add('listening');
    button.setAttribute('aria-pressed', 'true');
    button.setAttribute('aria-label', 'Stop voice dictation');
    button.querySelector('.chat-control-label').textContent = 'Stop';
    flash('Listening. Browser/OS speech recognition may use a non-local speech service; review the text before sending.');

    recognition.onresult = event => {
      let transcript = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) transcript += event.results[i][0]?.transcript || '';
      const separator = recognitionBase && transcript ? ' ' : '';
      input.value = `${recognitionBase}${separator}${transcript}`.slice(0, Number(input.maxLength || 32000));
      input.dispatchEvent(new Event('input', {bubbles: true}));
    };
    recognition.onerror = event => {
      const denied = event.error === 'not-allowed' || event.error === 'service-not-allowed';
      flash(denied ? 'Microphone access was not allowed. Enable microphone permission in the browser to use voice dictation.' : `Voice dictation stopped: ${event.error || 'speech service error'}.`, true);
    };
    recognition.onend = () => {
      recognition = null;
      resetVoiceButton();
      input.focus();
    };
    try { recognition.start(); }
    catch (err) {
      recognition = null;
      resetVoiceButton();
      flash(err.message || 'Voice dictation could not start.', true);
    }
  }

  function configureVoiceAvailability() {
    const button = byId('voiceInputButton');
    if (!button) return;
    if (!speechConstructor()) {
      button.disabled = true;
      button.title = 'Voice dictation is not supported by this browser';
      button.setAttribute('aria-label', 'Voice dictation unavailable in this browser');
    }
  }

  function scheduleDrawerRefresh() {
    if (byId('chatBrainDrawer')?.getAttribute('aria-hidden') !== 'false') return;
    clearTimeout(drawerRefreshTimer);
    drawerRefreshTimer = setTimeout(() => loadBrainActivity().catch(() => null), 450);
  }

  document.addEventListener('click', event => {
    if (event.target.closest('#voiceInputButton')) { startVoice(); return; }
    if (event.target.closest('#chatBrainToggle')) { setDrawer(byId('chatBrainDrawer')?.getAttribute('aria-hidden') !== 'false'); return; }
    if (event.target.closest('#closeChatBrain, #chatBrainBackdrop')) { setDrawer(false); return; }
    if (event.target.closest('#refreshChatBrain')) { loadBrainActivity().catch(err => flash(err.message, true)); }
  });

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && byId('chatBrainDrawer')?.getAttribute('aria-hidden') === 'false') setDrawer(false);
  });

  function boot() {
    if (!ensureEnhancements()) return;
    configureVoiceAvailability();
    const messages = byId('chatMessages');
    const context = byId('chatContext');
    if (messages) new MutationObserver(scheduleDrawerRefresh).observe(messages, {childList: true, subtree: true, characterData: true});
    if (context) new MutationObserver(scheduleDrawerRefresh).observe(context, {childList: true, subtree: true, characterData: true});
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})();
