(() => {
  'use strict';

  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = value => value ? new Date(value).toLocaleString() : '—';
  const AudioContextCtor = () => window.AudioContext || window.webkitAudioContext || null;

  let recognition = null;
  let conversationMode = false;
  let conversationStarting = false;
  let awaitingAgent = false;
  let speaking = false;
  let pendingTranscript = '';
  let assistantBaselineCount = 0;
  let lastSpokenText = '';
  let drawerRefreshTimer = null;
  let localVoiceStatus = null;
  let voicePath = {stt: null, tts: null};

  let mediaRecorder = null;
  let mediaStream = null;
  let captureAudioContext = null;
  let captureAnalyser = null;
  let captureTimer = null;
  let captureChunks = [];
  let captureGeneration = 0;
  let captureVoiceStarted = false;
  let playbackContext = null;
  let playbackSource = null;

  const LOCAL_STT_ENDPOINT = '/api/v1/control/voice/transcribe';
  const LOCAL_TTS_ENDPOINT = '/api/v1/control/voice/synthesize';
  const LOCAL_STATUS_ENDPOINT = '/api/v1/control/voice/status';
  const SILENCE_MS = 900;
  const NO_SPEECH_MS = 8000;
  const MAX_SEGMENT_MS = 30000;
  const VAD_THRESHOLD = 0.025;

  async function readJson(path) {
    const response = await fetch(path, {cache: 'no-store', credentials: 'same-origin'});
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }

  async function responseError(response, fallback) {
    try {
      const data = await response.json();
      return new Error(data.detail || fallback || `Request failed (${response.status})`);
    } catch (_) {
      return new Error(fallback || `Request failed (${response.status})`);
    }
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

      const options = document.createElement('div');
      options.className = 'chat-voice-options';
      options.innerHTML = '<label><input id="strictLocalVoice" type="checkbox"> <span>Strict local voice</span></label><span id="localVoiceBadge" class="chat-local-voice-badge">Checking local voice…</span>';
      compose.appendChild(options);

      const note = document.createElement('div');
      note.id = 'voicePrivacyNote';
      note.className = 'chat-voice-note';
      note.textContent = 'Conversation mode prefers installed HomeServer voice apps. Strict Local blocks browser/OS speech-service fallback.';
      compose.appendChild(note);

      try { byId('strictLocalVoice').checked = localStorage.getItem('homeserver.strictLocalVoice') === '1'; } catch (_) {}
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

  function hasBrowserConversationVoice() {
    return Boolean(recognitionConstructor() && window.speechSynthesis && window.SpeechSynthesisUtterance);
  }

  function hasLocalCapture() {
    return Boolean(navigator.mediaDevices?.getUserMedia && window.MediaRecorder && AudioContextCtor());
  }

  function strictLocalEnabled() {
    return Boolean(byId('strictLocalVoice')?.checked);
  }

  function captureTiming() {
    return window.HomeServerVoiceSettings?.getCaptureTiming?.() || {
      listenSilenceMs: SILENCE_MS,
      noSpeechTimeoutMs: NO_SPEECH_MS,
      maxSegmentMs: MAX_SEGMENT_MS,
    };
  }

  function localCaptureConstraints() {
    const base = {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true};
    return window.HomeServerVoiceSettings?.captureConstraints?.(base) || {audio: base};
  }

  async function refreshLocalVoiceStatus() {
    try {
      localVoiceStatus = await readJson(LOCAL_STATUS_ENDPOINT);
    } catch (_) {
      localVoiceStatus = null;
    }
    const badge = byId('localVoiceBadge');
    const note = byId('voicePrivacyNote');
    const ready = Boolean(localVoiceStatus?.conversation_ready && hasLocalCapture());
    if (badge) {
      badge.textContent = ready ? 'Whisper + Piper local' : 'Local voice apps not ready';
      badge.classList.toggle('ready', ready);
    }
    if (note) {
      note.textContent = ready
        ? 'Whisper STT and Piper TTS process voice locally on this HomeServer. Transcribed chat still follows your configured Agent inference route; Strict Local blocks browser/OS voice fallback.'
        : 'Install/repair Whisper STT and Piper TTS in Local Apps for private local voice. With Strict Local off, browser/OS speech services may be used as fallback.';
    }
    return localVoiceStatus;
  }

  function setVoiceState(state) {
    const button = byId('voiceInputButton');
    if (!button) return;
    button.classList.remove('listening', 'transcribing', 'thinking', 'speaking');
    const engaged = conversationMode || conversationStarting;
    button.setAttribute('aria-pressed', engaged ? 'true' : 'false');
    const label = button.querySelector('.chat-control-label');
    if (conversationStarting) {
      button.setAttribute('aria-label', 'Cancel conversation mode setup');
      if (label) label.textContent = 'Checking';
      return;
    }
    if (!conversationMode) {
      button.setAttribute('aria-label', 'Start conversation mode');
      if (label) label.textContent = 'Talk';
      return;
    }
    button.setAttribute('aria-label', 'Stop conversation mode');
    if (state === 'transcribing') {
      button.classList.add('transcribing');
      if (label) label.textContent = 'Transcribing';
    } else if (state === 'thinking') {
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

  function cleanupPlayback() {
    const source = playbackSource;
    playbackSource = null;
    if (!source) return;
    source.onended = null;
    try { source.stop(0); } catch (_) {}
    try { source.disconnect(); } catch (_) {}
  }

  function unlockLocalAudio() {
    const Context = AudioContextCtor();
    if (!Context) return false;
    if (!playbackContext || playbackContext.state === 'closed') playbackContext = new Context();
    playbackContext.resume().catch(() => null);
    return true;
  }

  function closePlaybackContext() {
    cleanupPlayback();
    const context = playbackContext;
    playbackContext = null;
    if (context) {
      try { context.close(); } catch (_) {}
    }
  }

  function cleanupLocalCapture() {
    clearInterval(captureTimer);
    captureTimer = null;
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
      try { mediaRecorder.stop(); } catch (_) {}
    }
    mediaRecorder = null;
    if (mediaStream) mediaStream.getTracks().forEach(track => track.stop());
    mediaStream = null;
    captureAnalyser = null;
    if (captureAudioContext) {
      try { captureAudioContext.close(); } catch (_) {}
    }
    captureAudioContext = null;
    captureChunks = [];
  }

  function stopConversationMode(message = 'Conversation mode stopped.') {
    conversationStarting = false;
    conversationMode = false;
    pendingTranscript = '';
    awaitingAgent = false;
    speaking = false;
    voicePath = {stt: null, tts: null};
    captureGeneration += 1;
    cleanupLocalCapture();
    if (recognition) {
      try { recognition.abort(); } catch (_) {}
      recognition = null;
    }
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    closePlaybackContext();
    setVoiceState('idle');
    if (message) flash(message);
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

  function resumeListening() {
    if (!conversationMode || awaitingAgent || speaking) return;
    if (voicePath.stt === 'local') setTimeout(() => startLocalListening(), 250);
    else setTimeout(() => startBrowserListening(), 250);
  }

  function startBrowserListening() {
    if (!conversationMode || awaitingAgent || speaking || recognition) return;
    const Recognition = recognitionConstructor();
    const input = byId('chatInput');
    if (!Recognition || !input) {
      stopConversationMode('');
      flash('Browser speech recognition is unavailable.', true);
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
        stopConversationMode('');
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
      resumeListening();
    };

    try { recognition.start(); }
    catch (err) {
      recognition = null;
      stopConversationMode('');
      flash(err.message || 'Conversation mode could not start.', true);
    }
  }

  function recorderOptions() {
    const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'];
    const mimeType = candidates.find(type => window.MediaRecorder.isTypeSupported?.(type));
    return mimeType ? {mimeType, audioBitsPerSecond: 64000} : {audioBitsPerSecond: 64000};
  }

  function rmsLevel(analyser) {
    const samples = new Uint8Array(analyser.fftSize);
    analyser.getByteTimeDomainData(samples);
    let sum = 0;
    for (const sample of samples) {
      const normalized = (sample - 128) / 128;
      sum += normalized * normalized;
    }
    return Math.sqrt(sum / samples.length);
  }

  async function startLocalListening() {
    if (!conversationMode || awaitingAgent || speaking || mediaRecorder) return;
    const input = byId('chatInput');
    const Context = AudioContextCtor();
    if (!hasLocalCapture() || !input || !Context) {
      if (strictLocalEnabled()) {
        stopConversationMode('');
        flash('Strict Local Voice requires microphone capture support in this browser.', true);
      } else {
        voicePath.stt = 'browser';
        startBrowserListening();
      }
      return;
    }

    input.value = '';
    input.dispatchEvent(new Event('input', {bubbles: true}));
    const generation = ++captureGeneration;
    captureChunks = [];
    captureVoiceStarted = false;

    try {
      mediaStream = await navigator.mediaDevices.getUserMedia(localCaptureConstraints());
      if (!conversationMode || generation !== captureGeneration) {
        mediaStream.getTracks().forEach(track => track.stop());
        mediaStream = null;
        return;
      }
      captureAudioContext = new Context();
      const source = captureAudioContext.createMediaStreamSource(mediaStream);
      captureAnalyser = captureAudioContext.createAnalyser();
      captureAnalyser.fftSize = 1024;
      source.connect(captureAnalyser);

      mediaRecorder = new MediaRecorder(mediaStream, recorderOptions());
      const timing = captureTiming();
      const startedAt = performance.now();
      let lastVoiceAt = startedAt;
      mediaRecorder.ondataavailable = event => { if (event.data?.size) captureChunks.push(event.data); };
      mediaRecorder.onerror = () => {
        if (generation !== captureGeneration || !conversationMode) return;
        cleanupLocalCapture();
        if (strictLocalEnabled()) {
          stopConversationMode('');
          flash('Local microphone recording failed.', true);
        } else {
          voicePath.stt = 'browser';
          startBrowserListening();
        }
      };
      mediaRecorder.onstop = () => {
        const chunks = captureChunks.slice();
        const mimeType = mediaRecorder?.mimeType || chunks[0]?.type || 'audio/webm';
        const hadSpeech = captureVoiceStarted;
        mediaRecorder = null;
        clearInterval(captureTimer);
        captureTimer = null;
        if (mediaStream) mediaStream.getTracks().forEach(track => track.stop());
        mediaStream = null;
        captureAnalyser = null;
        if (captureAudioContext) {
          try { captureAudioContext.close(); } catch (_) {}
        }
        captureAudioContext = null;
        captureChunks = [];
        if (!conversationMode || generation !== captureGeneration) return;
        if (!hadSpeech || !chunks.length) {
          resumeListening();
          return;
        }
        processLocalRecording(new Blob(chunks, {type: mimeType}), generation).catch(err => handleLocalSttFailure(err));
      };
      mediaRecorder.start(250);
      setVoiceState('listening');

      captureTimer = setInterval(() => {
        if (!conversationMode || generation !== captureGeneration || !mediaRecorder || mediaRecorder.state === 'inactive') return;
        const now = performance.now();
        const level = rmsLevel(captureAnalyser);
        if (level >= VAD_THRESHOLD) {
          captureVoiceStarted = true;
          lastVoiceAt = now;
        }
        const silenceDone = captureVoiceStarted && now - lastVoiceAt >= timing.listenSilenceMs;
        const noSpeechDone = !captureVoiceStarted && now - startedAt >= timing.noSpeechTimeoutMs;
        const maxDone = now - startedAt >= timing.maxSegmentMs;
        if (silenceDone || noSpeechDone || maxDone) {
          try { mediaRecorder.stop(); } catch (_) {}
        }
      }, 100);
    } catch (err) {
      cleanupLocalCapture();
      if (generation !== captureGeneration || !conversationMode) return;
      if (strictLocalEnabled()) {
        stopConversationMode('');
        flash('Microphone access was not allowed or local capture could not start.', true);
      } else if (recognitionConstructor()) {
        voicePath.stt = 'browser';
        flash('Local microphone capture was unavailable; using browser speech recognition fallback.', true);
        startBrowserListening();
      } else {
        stopConversationMode('');
        flash(err.message || 'Conversation mode could not access the microphone.', true);
      }
    }
  }

  function mixToMono(buffer) {
    const mono = new Float32Array(buffer.length);
    for (let channel = 0; channel < buffer.numberOfChannels; channel += 1) {
      const source = buffer.getChannelData(channel);
      for (let i = 0; i < source.length; i += 1) mono[i] += source[i] / buffer.numberOfChannels;
    }
    return mono;
  }

  function resampleLinear(samples, sourceRate, targetRate = 16000) {
    if (sourceRate === targetRate) return samples;
    const targetLength = Math.max(1, Math.round(samples.length * targetRate / sourceRate));
    const output = new Float32Array(targetLength);
    const ratio = sourceRate / targetRate;
    for (let i = 0; i < targetLength; i += 1) {
      const position = i * ratio;
      const left = Math.floor(position);
      const right = Math.min(left + 1, samples.length - 1);
      const fraction = position - left;
      output[i] = samples[left] + (samples[right] - samples[left]) * fraction;
    }
    return output;
  }

  function encodePcm16Wav(samples, sampleRate = 16000) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    const writeText = (offset, text) => { for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i)); };
    writeText(0, 'RIFF');
    view.setUint32(4, 36 + samples.length * 2, true);
    writeText(8, 'WAVE');
    writeText(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeText(36, 'data');
    view.setUint32(40, samples.length * 2, true);
    let offset = 44;
    for (const value of samples) {
      const sample = Math.max(-1, Math.min(1, value));
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
      offset += 2;
    }
    return new Blob([buffer], {type: 'audio/wav'});
  }

  async function recordingToWav(blob) {
    const Context = AudioContextCtor();
    if (!Context) throw new Error('This browser cannot decode microphone audio locally.');
    const context = new Context();
    try {
      const sourceBuffer = await blob.arrayBuffer();
      const decoded = await context.decodeAudioData(sourceBuffer.slice(0));
      const mono = mixToMono(decoded);
      const samples = resampleLinear(mono, decoded.sampleRate, 16000);
      return encodePcm16Wav(samples, 16000);
    } finally {
      try { await context.close(); } catch (_) {}
    }
  }

  async function processLocalRecording(blob, generation) {
    if (!conversationMode || generation !== captureGeneration) return;
    setVoiceState('transcribing');
    const wav = await recordingToWav(blob);
    if (!conversationMode || generation !== captureGeneration) return;
    const formData = new FormData();
    formData.append('file', wav, 'conversation.wav');
    const response = await fetch(LOCAL_STT_ENDPOINT, {
      method: 'POST',
      body: formData,
      cache: 'no-store',
      credentials: 'same-origin',
    });
    if (!response.ok) throw await responseError(response, 'Local Whisper transcription failed.');
    const payload = await response.json();
    if (!conversationMode || generation !== captureGeneration) return;
    const transcript = String(payload.text || '').trim();
    if (!transcript) {
      flash('No speech detected. Listening again.');
      resumeListening();
      return;
    }
    autoSubmitSpeech(transcript);
  }

  function handleLocalSttFailure(err) {
    if (!conversationMode) return;
    if (strictLocalEnabled()) {
      stopConversationMode('');
      flash(err.message || 'Strict Local Whisper transcription failed.', true);
      return;
    }
    if (recognitionConstructor()) {
      voicePath.stt = 'browser';
      flash('Local Whisper failed; using browser speech recognition fallback for this conversation.', true);
      startBrowserListening();
      return;
    }
    stopConversationMode('');
    flash(err.message || 'Local Whisper transcription failed.', true);
  }

  function finishSpeaking() {
    speaking = false;
    cleanupPlayback();
    if (conversationMode) resumeListening();
  }

  function speakBrowserReply(text) {
    if (!window.speechSynthesis || !window.SpeechSynthesisUtterance) {
      if (strictLocalEnabled()) {
        stopConversationMode('');
        flash('Strict Local Voice does not permit browser speech synthesis fallback.', true);
      } else {
        finishSpeaking();
      }
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = navigator.language || document.documentElement.lang || 'en-US';
    utterance.rate = Number(window.HomeServerVoiceSettings?.getPreferences?.().speaking_rate || 1);
    utterance.pitch = 1;
    utterance.onend = finishSpeaking;
    utterance.onerror = finishSpeaking;
    window.speechSynthesis.speak(utterance);
  }

  async function speakLocalReply(text) {
    const response = await fetch(LOCAL_TTS_ENDPOINT, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text}),
      cache: 'no-store',
      credentials: 'same-origin',
    });
    if (!response.ok) throw await responseError(response, 'Local Piper speech synthesis failed.');
    const audioBytes = await response.arrayBuffer();
    if (!conversationMode) return;
    const context = playbackContext;
    if (!context || context.state === 'closed') throw new Error('Local audio playback context is unavailable.');
    await context.resume();
    await window.HomeServerVoiceSettings?.applyOutputSink?.(context);
    const decoded = await context.decodeAudioData(audioBytes.slice(0));
    if (!conversationMode || context !== playbackContext) return;
    cleanupPlayback();
    const source = context.createBufferSource();
    playbackSource = source;
    source.buffer = decoded;
    source.connect(context.destination);
    source.onended = () => {
      if (playbackSource === source) playbackSource = null;
      try { source.disconnect(); } catch (_) {}
      finishSpeaking();
    };
    source.start(0);
  }

  function handleLocalTtsFailure(err) {
    cleanupPlayback();
    if (!conversationMode) return;
    if (strictLocalEnabled()) {
      stopConversationMode('');
      flash(err.message || 'Strict Local Piper speech synthesis failed.', true);
      return;
    }
    if (window.speechSynthesis && window.SpeechSynthesisUtterance) {
      voicePath.tts = 'browser';
      flash('Local Piper failed; using browser speech synthesis fallback for this conversation.', true);
      speakBrowserReply(lastSpokenText);
      return;
    }
    finishSpeaking();
  }

  function speakAgentReply(text) {
    if (!conversationMode || !text || text === lastSpokenText) return;
    lastSpokenText = text;
    speaking = true;
    setVoiceState('speaking');
    if (voicePath.tts === 'local') speakLocalReply(text).catch(handleLocalTtsFailure);
    else speakBrowserReply(text);
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

  async function toggleConversationMode() {
    if (conversationMode || conversationStarting) {
      stopConversationMode(conversationStarting ? 'Conversation mode setup cancelled.' : 'Conversation mode stopped.');
      return;
    }

    // Mark setup as engaged before awaiting status so Dictate, Voice Settings,
    // or a second Talk click can reliably cancel this startup transaction.
    conversationStarting = true;
    setVoiceState('checking');

    // Unlock local audio synchronously inside the user's click gesture. This
    // prevents delayed Piper playback from being rejected by autoplay policy.
    unlockLocalAudio();
    const settingsController = window.HomeServerVoiceSettings;
    if (settingsController?.load) {
      try { await settingsController.load(); } catch (_) {}
    }
    if (!conversationStarting) return;
    const status = await refreshLocalVoiceStatus();
    if (!conversationStarting) return;
    const strict = strictLocalEnabled();
    const localStt = Boolean(status?.stt?.available && hasLocalCapture());
    const localTts = Boolean(status?.tts?.available && AudioContextCtor());
    const browserRecognition = Boolean(recognitionConstructor());
    const browserTts = Boolean(window.speechSynthesis && window.SpeechSynthesisUtterance);

    if (strict && (!localStt || !localTts)) {
      conversationStarting = false;
      closePlaybackContext();
      setVoiceState('idle');
      flash('Strict Local Voice requires healthy Whisper STT and Piper TTS Local Apps plus browser microphone capture and audio playback support.', true);
      return;
    }
    if (!localStt && !browserRecognition) {
      conversationStarting = false;
      closePlaybackContext();
      setVoiceState('idle');
      flash('No speech-to-text path is available. Install Whisper STT or use a browser with speech recognition.', true);
      return;
    }
    if (!localTts && !browserTts) {
      conversationStarting = false;
      closePlaybackContext();
      setVoiceState('idle');
      flash('No speech-output path is available. Install Piper TTS or use a browser with speech synthesis.', true);
      return;
    }

    voicePath = {
      stt: localStt ? 'local' : 'browser',
      tts: localTts ? 'local' : 'browser',
    };
    if (voicePath.tts !== 'local') closePlaybackContext();
    conversationStarting = false;
    conversationMode = true;
    awaitingAgent = false;
    speaking = false;
    lastSpokenText = '';
    const route = `${voicePath.stt === 'local' ? 'Whisper' : 'browser STT'} + ${voicePath.tts === 'local' ? 'Piper' : 'browser TTS'}`;
    flash(`Conversation mode on · ${route}. Speak naturally; speech sends automatically and listening resumes after the reply.`);
    resumeListening();
  }

  async function configureVoiceAvailability() {
    const button = byId('voiceInputButton');
    if (!button) return;
    const status = await refreshLocalVoiceStatus();
    const localPossible = Boolean(status?.conversation_ready && hasLocalCapture());
    const browserPossible = hasBrowserConversationVoice();
    if (!localPossible && !browserPossible) {
      button.disabled = true;
      button.title = 'Install local voice apps or use a browser with speech recognition and synthesis';
      button.setAttribute('aria-label', 'Conversation mode unavailable');
    } else {
      button.disabled = false;
      button.title = localPossible ? 'Conversation mode · local Whisper + Piper ready' : 'Conversation mode · browser fallback available';
    }
  }

  function scheduleDrawerRefresh() {
    if (byId('chatBrainDrawer')?.getAttribute('aria-hidden') !== 'false') return;
    clearTimeout(drawerRefreshTimer);
    drawerRefreshTimer = setTimeout(() => loadBrainActivity().catch(() => null), 450);
  }

  document.addEventListener('click', event => {
    if (event.target.closest('#voiceInputButton')) { toggleConversationMode().catch(err => flash(err.message, true)); return; }
    if (event.target.closest('#chatBrainToggle')) { setDrawer(byId('chatBrainDrawer')?.getAttribute('aria-hidden') !== 'false'); return; }
    if (event.target.closest('#closeChatBrain, #chatBrainBackdrop')) { setDrawer(false); return; }
    if (event.target.closest('#refreshChatBrain')) { loadBrainActivity().catch(err => flash(err.message, true)); }
  });

  document.addEventListener('change', event => {
    if (!event.target.closest('#strictLocalVoice')) return;
    try { localStorage.setItem('homeserver.strictLocalVoice', event.target.checked ? '1' : '0'); } catch (_) {}
    if (conversationMode) stopConversationMode('Conversation mode stopped because the local voice privacy setting changed.');
  });

  window.addEventListener('homeserver:voice-settings-changed', () => {
    localVoiceStatus = null;
    refreshLocalVoiceStatus().catch(() => null);
    if (conversationMode || conversationStarting) stopConversationMode('Conversation mode stopped because Voice Settings changed.');
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
    configureVoiceAvailability().catch(() => null);
    const messages = byId('chatMessages');
    const context = byId('chatContext');
    if (messages) new MutationObserver(onChatMutation).observe(messages, {childList: true, subtree: true, characterData: true});
    if (context) new MutationObserver(scheduleDrawerRefresh).observe(context, {childList: true, subtree: true, characterData: true});
  }

  window.addEventListener('beforeunload', () => {
    conversationStarting = false;
    conversationMode = false;
    captureGeneration += 1;
    cleanupLocalCapture();
    closePlaybackContext();
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})();
