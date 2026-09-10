(() => {
  'use strict';

  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = value => value ? new Date(value).toLocaleString() : '—';
  const OPEN_TASK_STATUSES = new Set(['pending', 'in_progress']);
  let recognition = null;
  let voiceDraft = '';
  let listening = false;
  let drawerReturnFocus = null;

  async function api(path) {
    const response = await fetch(path, {cache:'no-store', credentials:'same-origin'});
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }

  function setVoiceState(active, message) {
    listening = active;
    const button = byId('chatVoiceButton');
    const status = byId('chatVoiceStatus');
    if (button) {
      button.classList.toggle('listening', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
      button.setAttribute('aria-label', active ? 'Stop listening' : 'Use voice input');
      button.title = active ? 'Stop listening' : 'Use browser speech input';
    }
    if (status && message) status.textContent = message;
  }

  function mergeTranscript(base, transcript) {
    const left = String(base || '').trimEnd();
    const right = String(transcript || '').trim();
    if (!left) return right;
    if (!right) return left;
    return `${left} ${right}`;
  }

  function configureVoice() {
    const button = byId('chatVoiceButton');
    const input = byId('chatInput');
    const status = byId('chatVoiceStatus');
    if (!button || !input || !status) return;

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      button.disabled = true;
      button.title = 'Voice input is not supported by this browser';
      status.textContent = 'Voice input is unavailable in this browser. You can continue typing normally.';
      return;
    }

    recognition = new SpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = document.documentElement.lang || navigator.language || 'en-US';

    recognition.addEventListener('start', () => {
      setVoiceState(true, 'Listening… Speak naturally. Your transcript will be inserted here for review before sending.');
    });

    recognition.addEventListener('result', event => {
      let transcript = '';
      for (let i = 0; i < event.results.length; i += 1) transcript += event.results[i][0]?.transcript || '';
      input.value = mergeTranscript(voiceDraft, transcript);
      input.dispatchEvent(new Event('input', {bubbles:true}));
    });

    recognition.addEventListener('error', event => {
      const messages = {
        'not-allowed': 'Microphone permission was not granted. Voice input remains off.',
        'service-not-allowed': 'Browser speech recognition is blocked by browser policy.',
        'audio-capture': 'No microphone is available to the browser.',
        'no-speech': 'No speech was detected. Your typed draft is unchanged.',
        'network': 'The browser speech service could not be reached.'
      };
      setVoiceState(false, messages[event.error] || 'Voice input stopped. You can continue typing.');
    });

    recognition.addEventListener('end', () => {
      if (listening) setVoiceState(false, 'Voice input stopped. Review or edit the transcript, then press Send when ready.');
    });

    button.addEventListener('click', () => {
      if (listening) {
        recognition.stop();
        return;
      }
      voiceDraft = input.value;
      try {
        recognition.start();
      } catch (_) {
        setVoiceState(false, 'Voice input is already changing state. Try again in a moment.');
      }
    });
  }

  function renderTasks(tasks) {
    const node = byId('brainActivityGoals');
    const progress = byId('brainActivityProgress');
    const items = Array.isArray(tasks) ? tasks : [];
    const open = items.filter(task => OPEN_TASK_STATUSES.has(task.status));
    const inProgress = open.filter(task => task.status === 'in_progress');
    const completed = items.filter(task => task.status === 'completed');
    const denominator = completed.length + open.length;
    const percent = denominator ? Math.round((completed.length / denominator) * 100) : 0;

    if (node) {
      node.innerHTML = open.length ? open.slice(0, 6).map(task => `
        <div class="brain-activity-row"><div><strong>${esc(task.title || 'Untitled task')}</strong><span>${esc(task.status === 'in_progress' ? 'In progress' : 'Pending')}${task.due_at ? ` · due ${esc(fmt(task.due_at))}` : ''}</span></div></div>`).join('') : '<div class="brain-activity-empty">No active tasks are currently recorded.</div>';
    }
    if (progress) {
      const planText = inProgress.length
        ? `${inProgress.length} task${inProgress.length === 1 ? '' : 's'} in progress · ${open.length - inProgress.length} pending`
        : `${open.length} task${open.length === 1 ? '' : 's'} pending`;
      progress.innerHTML = `<div class="brain-plan-summary"><strong>${esc(planText)}</strong><span>${completed.length} completed in the current task set</span></div><div class="brain-progress-track" role="progressbar" aria-label="Task completion" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}"><span style="width:${percent}%"></span></div>`;
    }
  }

  function renderActivity(items) {
    const node = byId('brainActivityActions');
    const list = Array.isArray(items) ? items : [];
    if (!node) return;
    node.innerHTML = list.length ? list.slice(0, 8).map(item => `
      <div class="brain-activity-row"><div><strong>${esc(item.action || 'Activity')}</strong><span>${esc([item.actor_type, item.actor_key].filter(Boolean).join(' · ') || 'HomeServer')}</span></div><time>${esc(fmt(item.created_at))}</time></div>`).join('') : '<div class="brain-activity-empty">No recent audit activity.</div>';
  }

  function renderToolRuns(items) {
    const node = byId('brainActivityTools');
    const list = Array.isArray(items) ? items : [];
    if (!node) return;
    node.innerHTML = list.length ? list.slice(0, 8).map(run => `
      <div class="brain-activity-row"><div><strong>${esc(run.tool_key || 'Tool')}</strong><span>${esc(run.source_app_key || 'HomeServer')} · ${esc(run.status || 'unknown')}${run.duration_ms == null ? '' : ` · ${Number(run.duration_ms)} ms`}</span></div><time>${esc(fmt(run.created_at))}</time></div>`).join('') : '<div class="brain-activity-empty">No recent tool runs.</div>';
  }

  function renderCognition(data) {
    const node = byId('brainActivityDecisions');
    const summary = byId('brainActivitySummary');
    const awareness = Array.isArray(data?.awareness) ? data.awareness : [];
    const runtime = data?.runtime || {};
    if (summary) {
      summary.textContent = `${Number(runtime.open_awareness || 0)} open signal${Number(runtime.open_awareness || 0) === 1 ? '' : 's'} · ${Number(runtime.memory_candidates || 0)} memory candidate${Number(runtime.memory_candidates || 0) === 1 ? '' : 's'}`;
    }
    if (!node) return;
    node.innerHTML = awareness.length ? awareness.slice(0, 5).map(item => `
      <div class="brain-decision-card"><strong>${esc(item.title || item.event_type || 'Cognition summary')}</strong><p>${esc(item.summary || 'No summary available.')}</p><span>${Math.round(Number(item.importance || 0) * 100)}% importance · ${esc(fmt(item.last_seen_at))}</span></div>`).join('') : '<div class="brain-activity-empty">No owner-visible decision/context summaries are currently available.</div>';
  }

  function renderLoadError(id, message) {
    const node = byId(id);
    if (node) node.innerHTML = `<div class="brain-activity-empty error">${esc(message)}</div>`;
  }

  async function loadBrainActivity() {
    const refresh = byId('brainActivityRefresh');
    if (refresh) refresh.disabled = true;
    const requests = await Promise.allSettled([
      api('/api/v1/control/tasks?q='),
      api('/api/v1/control/activity?limit=8'),
      api('/api/v1/control/tool-runs?limit=8'),
      api('/api/v1/control/cognition')
    ]);
    const [tasks, activity, tools, cognition] = requests;
    if (tasks.status === 'fulfilled') renderTasks(tasks.value.items || []); else renderLoadError('brainActivityGoals', tasks.reason.message);
    if (activity.status === 'fulfilled') renderActivity(activity.value.items || []); else renderLoadError('brainActivityActions', activity.reason.message);
    if (tools.status === 'fulfilled') renderToolRuns(tools.value.items || []); else renderLoadError('brainActivityTools', tools.reason.message);
    if (cognition.status === 'fulfilled') renderCognition(cognition.value); else renderLoadError('brainActivityDecisions', cognition.reason.message);
    if (refresh) refresh.disabled = false;
  }

  function closeDrawer() {
    const drawer = byId('brainActivityDrawer');
    const backdrop = byId('brainActivityBackdrop');
    const toggle = byId('brainActivityToggle');
    if (!drawer || !drawer.classList.contains('open')) return;
    drawer.classList.remove('open');
    backdrop?.classList.remove('open');
    drawer.setAttribute('aria-hidden', 'true');
    drawer.inert = true;
    document.querySelector('.shell')?.removeAttribute('inert');
    toggle?.setAttribute('aria-expanded', 'false');
    document.body.classList.remove('brain-activity-open');
    drawerReturnFocus?.focus?.();
    drawerReturnFocus = null;
  }

  function openDrawer() {
    const drawer = byId('brainActivityDrawer');
    const backdrop = byId('brainActivityBackdrop');
    const toggle = byId('brainActivityToggle');
    if (!drawer) return;
    drawerReturnFocus = document.activeElement;
    drawer.inert = false;
    document.querySelector('.shell')?.setAttribute('inert', '');
    drawer.classList.add('open');
    backdrop?.classList.add('open');
    drawer.setAttribute('aria-hidden', 'false');
    toggle?.setAttribute('aria-expanded', 'true');
    document.body.classList.add('brain-activity-open');
    drawer.focus();
    loadBrainActivity().catch(() => null);
  }

  function ensureExperience() {
    const view = byId('view-chat');
    const form = byId('chatForm');
    const input = byId('chatInput');
    if (!view || !form || !input || byId('chatVoiceButton')) return;

    const intro = view.querySelector('.section-intro.split');
    const context = byId('chatContext');
    if (intro) {
      const actions = document.createElement('div');
      actions.className = 'chat-experience-actions';
      if (context) actions.appendChild(context);
      const toggle = document.createElement('button');
      toggle.id = 'brainActivityToggle';
      toggle.type = 'button';
      toggle.className = 'button secondary brain-activity-toggle';
      toggle.setAttribute('aria-controls', 'brainActivityDrawer');
      toggle.setAttribute('aria-expanded', 'false');
      toggle.textContent = 'Brain / Activity';
      actions.appendChild(toggle);
      intro.appendChild(actions);
    }

    const mic = document.createElement('button');
    mic.id = 'chatVoiceButton';
    mic.type = 'button';
    mic.className = 'chat-voice-button';
    mic.setAttribute('aria-label', 'Use voice input');
    mic.setAttribute('aria-pressed', 'false');
    mic.innerHTML = '<span aria-hidden="true">🎙</span>';
    form.insertBefore(mic, input);

    const status = document.createElement('span');
    status.id = 'chatVoiceStatus';
    status.className = 'chat-voice-status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    status.textContent = 'Voice uses your browser speech service and may be processed by your browser or speech provider. Nothing is sent to HomeServer until you press Send.';
    form.appendChild(status);

    const backdrop = document.createElement('div');
    backdrop.id = 'brainActivityBackdrop';
    backdrop.className = 'brain-activity-backdrop';
    backdrop.hidden = false;

    const drawer = document.createElement('aside');
    drawer.id = 'brainActivityDrawer';
    drawer.className = 'brain-activity-drawer';
    drawer.tabIndex = -1;
    drawer.inert = true;
    drawer.setAttribute('role', 'dialog');
    drawer.setAttribute('aria-modal', 'true');
    drawer.setAttribute('aria-hidden', 'true');
    drawer.setAttribute('aria-labelledby', 'brainActivityTitle');
    drawer.innerHTML = `
      <div class="brain-activity-head"><div><p class="eyebrow">OWNER-VISIBLE RUNTIME</p><h2 id="brainActivityTitle">Brain / Activity</h2><span id="brainActivitySummary">Safe operational summaries</span></div><button id="brainActivityClose" class="brain-activity-close" type="button" aria-label="Close Brain Activity">×</button></div>
      <div class="brain-activity-privacy"><strong>Privacy boundary:</strong> this panel shows recorded goals, status, events and tool activity. It never exposes hidden chain-of-thought or private model reasoning.</div>
      <div class="brain-activity-scroll">
        <section><div class="brain-activity-section-head"><h3>Goals & active work</h3><span>from Tasks</span></div><div id="brainActivityGoals"><div class="brain-activity-empty">Loading…</div></div></section>
        <section><div class="brain-activity-section-head"><h3>Plan / progress</h3><span>recorded task status</span></div><div id="brainActivityProgress"><div class="brain-activity-empty">Loading…</div></div></section>
        <section><div class="brain-activity-section-head"><h3>Decision summaries</h3><span>safe Cognition summaries</span></div><div id="brainActivityDecisions"><div class="brain-activity-empty">Loading…</div></div></section>
        <section><div class="brain-activity-section-head"><h3>Recent actions</h3><span>audit trail</span></div><div id="brainActivityActions"><div class="brain-activity-empty">Loading…</div></div></section>
        <section><div class="brain-activity-section-head"><h3>Tool activity</h3><span>audited runs</span></div><div id="brainActivityTools"><div class="brain-activity-empty">Loading…</div></div></section>
      </div>
      <div class="brain-activity-foot"><button id="brainActivityRefresh" class="button secondary" type="button">Refresh activity</button></div>`;
    document.body.append(backdrop, drawer);

    byId('brainActivityToggle')?.addEventListener('click', () => drawer.classList.contains('open') ? closeDrawer() : openDrawer());
    byId('brainActivityClose')?.addEventListener('click', closeDrawer);
    byId('brainActivityRefresh')?.addEventListener('click', () => loadBrainActivity().catch(() => null));
    backdrop.addEventListener('click', closeDrawer);
    configureVoice();
  }

  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    if (listening && recognition) recognition.stop();
    closeDrawer();
  });

  function boot() {
    ensureExperience();
    if (!byId('chatVoiceButton')) {
      const observer = new MutationObserver(() => {
        ensureExperience();
        if (byId('chatVoiceButton')) observer.disconnect();
      });
      observer.observe(document.body, {childList:true, subtree:true});
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once:true});
  else boot();
})();
