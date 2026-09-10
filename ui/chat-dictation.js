(() => {
  'use strict';

  const byId = id => document.getElementById(id);
  const AudioContextCtor = () => window.AudioContext || window.webkitAudioContext || null;
  const RecognitionCtor = () => window.SpeechRecognition || window.webkitSpeechRecognition || null;

  const STATUS_ENDPOINT = '/api/v1/control/voice/status';
  const TRANSCRIBE_ENDPOINT = '/api/v1/control/voice/transcribe';
  const SILENCE_MS = 900;
  const NO_SPEECH_MS = 8000;
  const MAX_SEGMENT_MS = 30000;
  const VAD_THRESHOLD = 0.025;

  let active = false;
  let mode = null;
  let generation = 0;
  let recognition = null;
  let recorder = null;
  let stream = null;
  let audioContext = null;
  let analyser = null;
  let monitorTimer = null;
  let chunks = [];
  let voiceStarted = false;
  let savedSelection = {start: 0, end: 0};
  let cachedStatus = null;

  function ensureStyles() {
    if (document.querySelector('link[href="/assets/chat-dictation.css"]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/chat-dictation.css';
    document.head.appendChild(link);
  }

  function flash(message, error = false) {
    const node = byId('flash');
    if (!node) return;
    node.textContent = message;
    node.className = `flash show${error ? ' error' : ''}`;
    clearTimeout(flash.timer);
    flash.timer = setTimeout(() => { node.className = 'flash'; }, 4200);
  }

  function strictLocalEnabled() {
    return Boolean(byId('strictLocalVoice')?.checked);
  }

  function localCaptureSupported() {
    return Boolean(navigator.mediaDevices?.getUserMedia && window.MediaRecorder && AudioContextCtor());
  }

  async function readStatus() {
    try {
      const response = await fetch(STATUS_ENDPOINT, {cache: 'no-store', credentials: 'same-origin'});
      if (!response.ok) return null;
      cachedStatus = await response.json();
      return cachedStatus;
    } catch (_) {
      cachedStatus = null;
      return null;
    }
  }

  async function responseError(response, fallback) {
    try {
      const data = await response.json();
      return new Error(data.detail || fallback || `Request failed (${response.status})`);
    } catch (_) {
      return new Error(fallback || `Request failed (${response.status})`);
    }
  }

  function ensureControl() {
    if (byId('dictateInputButton')) return true;
    const options = document.querySelector('#chatForm .chat-voice-options');
    const badge = byId('localVoiceBadge');
    if (!options || !badge) return false;

    const button = document.createElement('button');
    button.id = 'dictateInputButton';
    button.type = 'button';
    button.className = 'chat-dictate-button';
    button.setAttribute('aria-label', 'Start dictation');
    button.setAttribute('aria-pressed', 'false');
    button.setAttribute('aria-describedby', 'voicePrivacyNote');
    button.title = 'Dictate into the message box without sending';
    button.innerHTML = '<span class="dictate-dot" aria-hidden="true"></span><span class="dictate-label">Dictate</span>';
    options.insertBefore(button, badge);
    return true;
  }

  function captureSelection() {
    const input = byId('chatInput');
    if (!input) return;
    const start = Number.isInteger(input.selectionStart) ? input.selectionStart : input.value.length;
    const end = Number.isInteger(input.selectionEnd) ? input.selectionEnd : start;
    savedSelection = {start, end};
  }

  function setState(state) {
    const button = byId('dictateInputButton');
    if (!button) return;
    button.classList.remove('listening', 'transcribing');
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
    button.setAttribute('aria-label', active ? 'Stop dictation' : 'Start dictation');
    const label = button.querySelector('.dictate-label');
    if (!active) {
      if (label) label.textContent = 'Dictate';
      return;
    }
    if (state === 'transcribing') {
      button.classList.add('transcribing');
      if (label) label.textContent = 'Transcribing';
    } else {
      button.classList.add('listening');
      if (label) label.textContent = 'Listening';
    }
  }

  function stopCapture() {
    clearInterval(monitorTimer);
    monitorTimer = null;
    if (recorder && recorder.state !== 'inactive') {
      recorder.ondataavailable = null;
      recorder.onstop = null;
      recorder.onerror = null;
      try { recorder.stop(); } catch (_) {}
    }
    recorder = null;
    if (stream) stream.getTracks().forEach(track => track.stop());
    stream = null;
    analyser = null;
    if (audioContext) {
      try { audioContext.close(); } catch (_) {}
    }
    audioContext = null;
    chunks = [];
    voiceStarted = false;
  }

  function stopBrowserRecognition() {
    if (!recognition) return;
    recognition.onresult = null;
    recognition.onerror = null;
    recognition.onend = null;
    try { recognition.abort(); } catch (_) {}
    recognition = null;
  }

  function stopDictation(message = '') {
    active = false;
    mode = null;
    generation += 1;
    stopCapture();
    stopBrowserRecognition();
    setState('idle');
    if (message) flash(message);
  }

  function separatorBefore(prefix, transcript) {
    if (!prefix || /\s$/.test(prefix) || /^[,.;:!?)}\]]/.test(transcript)) return '';
    return ' ';
  }

  function separatorAfter(transcript, suffix) {
    if (!suffix || /^\s/.test(suffix) || /[\s([{\-/'"]$/.test(transcript)) return '';
    return ' ';
  }

  function insertTranscript(transcript) {
    const input = byId('chatInput');
    const text = String(transcript || '').trim();
    if (!input || !text) return false;

    const maxLength = Number(input.maxLength || 32000);
    const start = Math.max(0, Math.min(savedSelection.start, input.value.length));
    const end = Math.max(start, Math.min(savedSelection.end, input.value.length));
    const prefix = input.value.slice(0, start);
    const suffix = input.value.slice(end);
    const before = separatorBefore(prefix, text);
    const after = separatorAfter(text, suffix);
    const available = Math.max(0, maxLength - prefix.length - suffix.length - before.length - after.length);
    if (!available) {
      flash('The message box is already at its maximum length.', true);
      return false;
    }

    const clipped = text.slice(0, available);
    const insertion = `${before}${clipped}${after}`;
    input.setRangeText(insertion, start, end, 'end');
    input.dispatchEvent(new Event('input', {bubbles: true}));
    captureSelection();
    input.focus({preventScroll: true});
    return true;
  }

  function recorderOptions() {
    const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'];
    const mimeType = candidates.find(type => window.MediaRecorder.isTypeSupported?.(type));
    return mimeType ? {mimeType, audioBitsPerSecond: 64000} : {audioBitsPerSecond: 64000};
  }

  function rmsLevel(target) {
    const samples = new Uint8Array(target.fftSize);
    target.getByteTimeDomainData(samples);
    let sum = 0;
    for (const sample of samples) {
      const normalized = (sample - 128) / 128;
      sum += normalized * normalized;
    }
    return Math.sqrt(sum / samples.length);
  }

  function mixToMono(buffer) {
    const mono = new Float32Array(buffer.length);
    for (let channel = 0; channel < buffer.numberOfChannels; channel += 1) {
      const channelData = buffer.getChannelData(channel);
      for (let i = 0; i < channelData.length; i += 1) mono[i] += channelData[i] / buffer.numberOfChannels;
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
      const bytes = await blob.arrayBuffer();
      const decoded = await context.decodeAudioData(bytes.slice(0));
      return encodePcm16Wav(resampleLinear(mixToMono(decoded), decoded.sampleRate, 16000), 16000);
    } finally {
      try { await context.close(); } catch (_) {}
    }
  }

  async function transcribeLocal(blob, runGeneration) {
    if (!active || runGeneration !== generation) return;
    setState('transcribing');
    const wav = await recordingToWav(blob);
    if (!active || runGeneration !== generation) return;
    const formData = new FormData();
    formData.append('file', wav, 'dictation.wav');
    const response = await fetch(TRANSCRIBE_ENDPOINT, {
      method: 'POST',
      body: formData,
      cache: 'no-store',
      credentials: 'same-origin',
    });
    if (!response.ok) throw await responseError(response, 'Local Whisper dictation failed.');
    const payload = await response.json();
    if (!active || runGeneration !== generation) return;
    const transcript = String(payload.text || '').trim();
    if (!transcript) {
      stopDictation('No speech detected. Nothing was added.');
      return;
    }
    const inserted = insertTranscript(transcript);
    stopDictation(inserted ? 'Dictation added. Review or edit it, then send when ready.' : '');
  }

  function localFailure(error) {
    if (!active) return;
    if (strictLocalEnabled()) {
      stopDictation('');
      flash(error.message || 'Strict Local Whisper dictation failed.', true);
      return;
    }
    if (RecognitionCtor()) {
      stopCapture();
      mode = 'browser';
      flash('Local Whisper failed; browser dictation fallback is ready. Speak again.', true);
      startBrowserDictation();
      return;
    }
    stopDictation('');
    flash(error.message || 'Local Whisper dictation failed.', true);
  }

  async function startLocalDictation() {
    const Context = AudioContextCtor();
    if (!active || !localCaptureSupported() || !Context) {
      localFailure(new Error('Local microphone capture is unavailable.'));
      return;
    }
    const runGeneration = generation;
    chunks = [];
    voiceStarted = false;

    try {
      stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true}});
      if (!active || runGeneration !== generation) {
        stream.getTracks().forEach(track => track.stop());
        stream = null;
        return;
      }
      audioContext = new Context();
      const source = audioContext.createMediaStreamSource(stream);
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 1024;
      source.connect(analyser);
      recorder = new MediaRecorder(stream, recorderOptions());
      const startedAt = performance.now();
      let lastVoiceAt = startedAt;

      recorder.ondataavailable = event => { if (event.data?.size) chunks.push(event.data); };
      recorder.onerror = () => localFailure(new Error('Local microphone recording failed.'));
      recorder.onstop = () => {
        const finishedChunks = chunks.slice();
        const mimeType = recorder?.mimeType || finishedChunks[0]?.type || 'audio/webm';
        const hadSpeech = voiceStarted;
        recorder = null;
        clearInterval(monitorTimer);
        monitorTimer = null;
        if (stream) stream.getTracks().forEach(track => track.stop());
        stream = null;
        analyser = null;
        if (audioContext) {
          try { audioContext.close(); } catch (_) {}
        }
        audioContext = null;
        chunks = [];
        if (!active || runGeneration !== generation) return;
        if (!hadSpeech || !finishedChunks.length) {
          stopDictation('No speech detected. Nothing was added.');
          return;
        }
        transcribeLocal(new Blob(finishedChunks, {type: mimeType}), runGeneration).catch(localFailure);
      };
      recorder.start(250);
      setState('listening');

      monitorTimer = setInterval(() => {
        if (!active || runGeneration !== generation || !recorder || recorder.state === 'inactive') return;
        const now = performance.now();
        const level = rmsLevel(analyser);
        if (level >= VAD_THRESHOLD) {
          voiceStarted = true;
          lastVoiceAt = now;
        }
        const silenceDone = voiceStarted && now - lastVoiceAt >= SILENCE_MS;
        const noSpeechDone = !voiceStarted && now - startedAt >= NO_SPEECH_MS;
        const maxDone = now - startedAt >= MAX_SEGMENT_MS;
        if (silenceDone || noSpeechDone || maxDone) {
          try { recorder.stop(); } catch (_) {}
        }
      }, 100);
    } catch (error) {
      stopCapture();
      if (!active || runGeneration !== generation) return;
      localFailure(error);
    }
  }

  function startBrowserDictation() {
    if (!active) return;
    const Recognition = RecognitionCtor();
    if (!Recognition) {
      stopDictation('');
      flash('Browser speech recognition is unavailable.', true);
      return;
    }

    recognition = new Recognition();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = navigator.language || document.documentElement.lang || 'en-US';
    recognition.maxAlternatives = 1;
    setState('listening');

    recognition.onresult = event => {
      let finalText = '';
      for (let i = 0; i < event.results.length; i += 1) {
        if (event.results[i].isFinal) finalText += event.results[i][0]?.transcript || '';
      }
      const text = finalText.trim();
      if (!text) return;
      const inserted = insertTranscript(text);
      stopDictation(inserted ? 'Dictation added. Review or edit it, then send when ready.' : '');
    };
    recognition.onerror = event => {
      const denied = event.error === 'not-allowed' || event.error === 'service-not-allowed';
      stopDictation('');
      if (denied) flash('Microphone access was not allowed. Enable microphone permission to dictate.', true);
      else if (event.error !== 'aborted') flash(`Dictation error: ${event.error || 'speech service error'}.`, true);
    };
    recognition.onend = () => {
      recognition = null;
      if (active) stopDictation('No speech detected. Nothing was added.');
    };
    try { recognition.start(); }
    catch (error) {
      stopDictation('');
      flash(error.message || 'Dictation could not start.', true);
    }
  }

  async function startDictation() {
    const input = byId('chatInput');
    if (!input || !ensureControl()) return;

    const conversationButton = byId('voiceInputButton');
    if (conversationButton?.getAttribute('aria-pressed') === 'true') conversationButton.click();

    captureSelection();
    const status = await readStatus();
    const localReady = Boolean(status?.stt?.available && localCaptureSupported());
    const browserReady = Boolean(RecognitionCtor());

    if (strictLocalEnabled() && !localReady) {
      flash('Strict Local Dictation requires healthy Whisper STT plus local microphone capture support.', true);
      return;
    }
    if (!localReady && !browserReady) {
      flash('No speech-to-text path is available. Install Whisper STT or use a browser with speech recognition.', true);
      return;
    }

    active = true;
    generation += 1;
    mode = localReady ? 'local' : 'browser';
    setState('listening');
    flash(localReady
      ? 'Dictation on · local Whisper. Speak once; the transcript will be inserted without sending.'
      : 'Dictation on · browser speech fallback. Speak once; the transcript will be inserted without sending.');
    if (mode === 'local') await startLocalDictation();
    else startBrowserDictation();
  }

  async function toggleDictation() {
    if (active) {
      stopDictation('Dictation cancelled.');
      byId('chatInput')?.focus({preventScroll: true});
      return;
    }
    await startDictation();
  }

  function boot() {
    ensureStyles();
    if (!ensureControl()) {
      setTimeout(boot, 0);
      return;
    }
    const input = byId('chatInput');
    if (input) {
      ['select', 'keyup', 'click', 'input'].forEach(type => input.addEventListener(type, captureSelection));
      input.addEventListener('focus', captureSelection);
      captureSelection();
    }
  }

  document.addEventListener('pointerdown', event => {
    if (event.target.closest('#dictateInputButton')) captureSelection();
  }, true);

  document.addEventListener('click', event => {
    if (event.target.closest('#dictateInputButton')) {
      toggleDictation().catch(error => {
        stopDictation('');
        flash(error.message || 'Dictation could not start.', true);
      });
    }
  });

  // Conversation and dictation are mutually exclusive. Stop dictation before
  // the existing conversation click handler sees the Talk button.
  document.addEventListener('click', event => {
    if (active && event.target.closest('#voiceInputButton')) stopDictation('');
  }, true);

  document.addEventListener('change', event => {
    if (active && event.target.closest('#strictLocalVoice')) {
      stopDictation('Dictation stopped because the local voice privacy setting changed.');
    }
  });

  window.addEventListener('beforeunload', () => stopDictation(''));

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})();
