(() => {
  'use strict';

  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = value => value ? new Date(value).toLocaleString() : '—';

  let recognition = null;
  let conversationMode = false;
  let awaitingAgent = false;
  let speaking = false;
  let pendingTranscript = '';
  let assistantBaselineCount = 0;
  let lastSpokenText = '';
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

  function micIcon() {
    return '<span class="chat-mic-icon" aria-hidden="true"><svg viewBox="0 0 24 24" focusable="false"><path d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Z"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3M9 21h6"/></svg></span>';
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
      voice.setAttribute('aria-label', 'Start conversation mode');
      voice.setAttribute('aria-pressed', 'false');
      voice.setAttribute('aria-describedby', 'voicePrivacyNote');
      voice.title = 'Conversation mode';
      voice.innerHTML = `${micIcon()}<span class="chat-control-label">Talk</span>`;
      compose.insertBefore(voice, compose.querySelector('button[type="submit"]'));

      const note = document.createElement('div');
      note.id = 'voicePrivacyNote';
      note.className = 'chat-voice-note';
      note.textContent = 'Conversation mode auto-sends final speech, speaks the agent reply, then listens again. Browser/OS speech recognition may use a non-local speech service.';
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
      toggle.title = 'Open Brain activity';
      toggle.innerHTML = '<span class="chat-brain-icon" aria-hidden="true">⚙</span><span>Brain activity</span>';
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
          <div><p class="eyebrow">AGENT BRAIN & HISTORY</p><h3>Goals, actions & activity</h3></div>
          <div class="chat-brain-drawer-actions"><button class="text-button" id="refreshChatBrain" type="button">Refresh</button><button class="chat-drawer-close" id="closeChatBrain" type="button" aria-label="Close Brain activity">×</button></div>
        </div>
        <p class="chat-brain-disclosure">This view shows inspectable goals, provider/context summaries, cognitive events, tool runs and audited actions. It does not expose hidden model reasoning or chain-of-thought.</p>
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
      readJson('/api/v1/control/tasks?q='),
    ]);

    const inference = settledValue(results[0]);
    const cognition = settledValue(results[1]);
    const events = settledValue(results[2], {items: []});
    const tools = settledValue(results[3], {items: []});
    const activity = settledValue(results[4], {items: []});
    const tasks = settledValue(results[5], {items: []});
    const failures = results.filter(result => result.status === 'rejected').length;
    const runtime = cognition.runtime || {};
    const context = byId('chatContext')?.textContent?.trim() || 'No chat context summary yet. Send a message to populate it.';
    const provider = inference.available
      ? `${inference.compute_source === 'homeserver_local' ? 'HomeServer local' : 'User provider'} · ${inference.selected_provider || 'auto'}${inference.model ? ` · ${inference.model}` : ''}`
      : 'No HomeServer inference provider is currently ready.';
    const activeGoals = (tasks.items || []).filter(item => ['pending', 'in_progress'].includes(item.status)).slice(0, 8);
    const latestDecision = (events.items || []).find(item => item.summary)?.summary || 'No current decision summary has been recorded yet.';

    content.innerHTML = `
      ${failures ? `<div class="chat-brain-warning">${failures} activity source${failures === 1 ? '' : 's'} could not be read. Available data is shown below.</div>` : ''}
      <section class="chat-brain-card"><p class="eyebrow">CURRENT BRAIN</p><strong>${esc(provider)}</strong><p>${esc(context)}</p></section>
      <section class="chat-brain-card"><p class="eyebrow">CURRENT PLAN / DECISION SUMMARY</p><strong>${esc(latestDecision)}</strong><p>Only concise, inspectable summaries are shown here—not private chain-of-thought.</p></section>
      <div class="chat-brain-stats">
        <div><strong>${Number(activeGoals.length).toLocaleString()}</strong><span>active goals</span></div>
        <div><strong>${Number(runtime.events || 0).toLocaleString()}</strong><span>cognitive events</span></div>
        <div><strong>${Number(runtime.open_awareness || 0).toLocaleString()}</strong><span>open signals</span></div>
      </div>
      <section class="chat-brain-section"><div class="chat-brain-section-head"><div><p class="eyebrow">GOALS</p><h4>Active goals & tasks</h4></div></div>${renderList(activeGoals, item => `<article><div><strong>${esc(item.title || 'Goal')}</strong><span>${esc(item.status || 'pending')}</span></div><p>${esc(item.description || 'No description')}</p><small>${item.due_at ? `Due ${esc(fmt(item.due_at))}` : 'No due date'}${item.priority ? ` · ${esc(item.priority)} priority` : ''}</small></article>`, 'No active goals or tasks yet. User and agent-created goals will appear here as they are persisted.')}</section>
      <section class="chat-brain-section"><div class="chat-brain-section-head"><div><p class="eyebrow">COGNITION</p><h4>Recent decision/activity summaries</h4></div></div>${renderList(events.items || [], item => `<article><div><strong>${esc(item.title || item.event_type || 'Cognitive event')}</strong><span>${esc(fmt(item.occurred_at || item.created_at))}</span></div><p>${esc(item.summary || '')}</p><small>${esc(item.source_app_key || 'HomeServer')}${item.event_type ? ` · ${esc(item.event_type)}` : ''}</small></article>`, 'No cognitive events yet.')}</section>
      <section class="chat-brain-section"><div class="chat-brain-section-head"><div><p class="eyebrow">TOOLS</p><h4>Recent agent tool runs</h4></div></div>${renderList(tools.items || [], item => `<article><div><strong>${esc(item.tool_key || 'Tool')}</strong><span>${esc(fmt(item.created_at))}</span></div><p>${esc(item.status || 'unknown')}${item.duration_ms == null ? '' : ` · ${Number(item.duration_ms)} ms`}</p><small>${esc(item.source_app_key || 'HomeServer')}</small></article>`, 'No tool runs yet.')}</section>
      <section class="chat-brain-section"><div class="chat-brain-section-head"><div><p class="eyebrow">HISTORY</p><h4>Recent audited actions</h4></div></div>${renderList(activity.items || [], item => `<article><div><strong>${esc(item.action || 'Activity')}</strong><span>${esc(fmt(item.created_at))}</span></div><p>${esc([item.resource_type, item.resource_key].filter(Boolean).join(' · ') || 'HomeServer')}</p><small>${esc(item.actor_type || 'owner')}${item.actor_key ? ` · ${esc(item.actor_key)}` : ''}</small></article>`, 'No audited activity yet.')}</section>`;
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

  function recognitionConstructor() {
    return window.SpeechRecognition || window.webkitSpeechRecognition || null;
  }

  function hasConversationVoice() {
    return Boolean(recognitionConstructor() && window.speechSynthesis && window.SpeechSynthesisUtterance);
  }

  function setVoiceState(state) {
    const button = byId('voiceInputButton');
    if (!button) return;
    button.classList.remove('listening', 'thinking', 'speaking');
    button.setAttribute('aria-pressed', conversationMode ? 'true' : 'false');
    const label = button.querySelector('.chat-control-label');
    if (!conversationMode) {
      button.setAttribute('aria-label', 'Start conversation mode');
      if (label) label.textContent = 'Talk';
      return;
    }
    button.setAttribute('aria-label', 'Stop conversation mode');
    if (state === 'thinking') {
      button.classList.add('thinking');
      if (label) label.textContent = 'Thinking';
    } else if (state === 'speaking') {
      button.classList.add('speaking');
      if (label) label.textContent = 'Speaking';
    } else {
      button.classList.add('listening');
      if (label) label.textContent = 'Listening';
    }
  }

  function stopConversationMode() {
    conversationMode = false;
    pendingTranscript = '';
    awaitingAgent = false;
    speaking = false;
    if (recognition) {
      try { recognition.stop(); } catch (_) {}
      recognition = null;
    }
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    setVoiceState('idle');
    flash('Conversation mode stopped.');
  }

  function autoSubmitSpeech(transcript) {
    const input = byId('chatInput');
    const form = byId('chatForm');
    if (!conversationMode || !input || !form || !transcript.trim()) return;
    assistantBaselineCount = document.querySelectorAll('#chatMessages .chat-message.assistant').length;
    awaitingAgent = true;
    pendingTranscript = '';
    input.value = transcript.trim().slice(0, Number(input.maxLength || 32000));
    input.dispatchEvent(new Event('input', {bubbles: true}));
    setVoiceState('thinking');
    form.requestSubmit();
  }

  function startListening() {
    if (!conversationMode || awaitingAgent || speaking || recognition) return;
    const Recognition = recognitionConstructor();
    const input = byId('chatInput');
    if (!Recognition || !input) {
      stopConversationMode();
      flash('Conversation mode is not supported by this browser.', true);
      return;
    }

    pendingTranscript = '';
    input.value = '';
    input.dispatchEvent(new Event('input', {bubbles: true}));
    recognition = new Recognition();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = navigator.language || document.documentElement.lang || 'en-US';
    recognition.maxAlternatives = 1;
    setVoiceState('listening');

    recognition.onresult = event => {
      let finalText = '';
      let interimText = '';
      for (let i = 0; i < event.results.length; i += 1) {
        const text = event.results[i][0]?.transcript || '';
        if (event.results[i].isFinal) finalText += text;
        else interimText += text;
      }
      input.value = `${finalText}${interimText}`.trimStart().slice(0, Number(input.maxLength || 32000));
      input.dispatchEvent(new Event('input', {bubbles: true}));
      if (finalText.trim()) {
        pendingTranscript = finalText.trim();
        try { recognition.stop(); } catch (_) {}
      }
    };

    recognition.onerror = event => {
      const denied = event.error === 'not-allowed' || event.error === 'service-not-allowed';
      if (denied) {
        conversationMode = false;
        flash('Microphone access was not allowed. Enable microphone permission to use conversation mode.', true);
      } else if (event.error !== 'no-speech' && event.error !== 'aborted') {
        flash(`Conversation listening error: ${event.error || 'speech service error'}.`, true);
      }
    };

    recognition.onend = () => {
      recognition = null;
      if (!conversationMode) {
        setVoiceState('idle');
        return;
      }
      if (pendingTranscript) {
        const transcript = pendingTranscript;
        setTimeout(() => autoSubmitSpeech(transcript), 0);
        return;
      }
      if (!awaitingAgent && !speaking) setTimeout(startListening, 300);
    };

    try { recognition.start(); }
    catch (err) {
      recognition = null;
      conversationMode = false;
      setVoiceState('idle');
      flash(err.message || 'Conversation mode could not start.', true);
    }
  }

  function speakAgentReply(text) {
    if (!conversationMode || !text || text === lastSpokenText) return;
    if (!window.speechSynthesis || !window.SpeechSynthesisUtterance) {
      speaking = false;
      setTimeout(startListening, 250);
      return;
    }
    lastSpokenText = text;
    speaking = true;
    setVoiceState('speaking');
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = navigator.language || document.documentElement.lang || 'en-US';
    utterance.rate = 1;
    utterance.pitch = 1;
    utterance.onend = () => {
      speaking = false;
      if (conversationMode) setTimeout(startListening, 250);
    };
    utterance.onerror = () => {
      speaking = false;
      if (conversationMode) setTimeout(startListening, 250);
    };
    window.speechSynthesis.speak(utterance);
  }

  function checkForAgentReply() {
    if (!conversationMode || !awaitingAgent) return;
    const assistants = [...document.querySelectorAll('#chatMessages .chat-message.assistant')];
    if (assistants.length <= assistantBaselineCount) return;
    const latest = assistants[assistants.length - 1];
    const reply = latest?.childNodes?.[0]?.textContent?.trim() || latest?.textContent?.trim() || '';
    if (!reply) return;
    awaitingAgent = false;
    speakAgentReply(reply);
  }

  function toggleConversationMode() {
    if (conversationMode) {
      stopConversationMode();
      return;
    }
    if (!hasConversationVoice()) {
      flash('Conversation mode needs browser speech recognition and speech synthesis. This browser does not provide both.', true);
      return;
    }
    conversationMode = true;
    awaitingAgent = false;
    speaking = false;
    lastSpokenText = '';
    flash('Conversation mode on. Speak naturally; final speech sends automatically, the agent reply is spoken, then listening resumes.');
    startListening();
  }

  function configureVoiceAvailability() {
    const button = byId('voiceInputButton');
    if (!button) return;
    if (!hasConversationVoice()) {
      button.disabled = true;
      button.title = 'Conversation mode is not supported by this browser';
      button.setAttribute('aria-label', 'Conversation mode unavailable in this browser');
    }
  }

  function scheduleDrawerRefresh() {
    if (byId('chatBrainDrawer')?.getAttribute('aria-hidden') !== 'false') return;
    clearTimeout(drawerRefreshTimer);
    drawerRefreshTimer = setTimeout(() => loadBrainActivity().catch(() => null), 450);
  }

  document.addEventListener('click', event => {
    if (event.target.closest('#voiceInputButton')) { toggleConversationMode(); return; }
    if (event.target.closest('#chatBrainToggle')) { setDrawer(byId('chatBrainDrawer')?.getAttribute('aria-hidden') !== 'false'); return; }
    if (event.target.closest('#closeChatBrain, #chatBrainBackdrop')) { setDrawer(false); return; }
    if (event.target.closest('#refreshChatBrain')) { loadBrainActivity().catch(err => flash(err.message, true)); }
  });

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && byId('chatBrainDrawer')?.getAttribute('aria-hidden') === 'false') setDrawer(false);
  });

  function onChatMutation() {
    checkForAgentReply();
    scheduleDrawerRefresh();
  }

  function boot() {
    if (!ensureEnhancements()) return;
    configureVoiceAvailability();
    const messages = byId('chatMessages');
    const context = byId('chatContext');
    if (messages) new MutationObserver(onChatMutation).observe(messages, {childList: true, subtree: true, characterData: true});
    if (context) new MutationObserver(scheduleDrawerRefresh).observe(context, {childList: true, subtree: true, characterData: true});
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})();
