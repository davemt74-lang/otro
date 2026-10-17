(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const dialog = byId('chatOptionsDialog');
  if (!dialog) return;
  const trigger = byId('chatOptionsButton');
  let opener = trigger;
  let voicePrepared = false;
  const form = byId('chatForm');
  const chatPanel = form?.parentElement;
  const captureObserver = new MutationObserver(updateCapture);
  const observedCapture = new WeakSet();

  function move(id, hostId) {
    const node = byId(id);
    const host = byId(hostId);
    if (node && host && node.parentNode !== host) host.appendChild(node);
    return Boolean(node);
  }

  function adoptControls() {
    move('voiceInputButton', 'chatVoiceControls');
    const options = document.querySelector('.chat-voice-options');
    if (options && options.parentNode !== byId('chatVoiceControls')) byId('chatVoiceControls').appendChild(options);
    move('voicePrivacyNote', 'chatVoiceControls');
    move('voiceSettingsForm', 'chatVoicePreferences');
    move('chatConversationTitle', 'chatConversationDetails');
    for (const id of ['chatBrainToggle', 'deleteChat', 'chatOnboardingToggle']) move(id, 'chatMoreActions');
    // The live Brain toggle remains available when Chat's topbar is removed.
    const brain = byId('agentBrainDrawerToggle');
    const nav = document.querySelector('.primary-sidebar-nav');
    if (brain && nav && brain.parentNode !== nav) nav.appendChild(brain);
    const settings = byId('chatVoiceSettingsButton');
    if (settings) settings.hidden = true;
    for (const id of ['voiceInputButton', 'dictateInputButton']) {
      const button = byId(id);
      if (button && !observedCapture.has(button)) {
        observedCapture.add(button);
        captureObserver.observe(button, {attributes: true, attributeFilter: ['aria-pressed','data-voice-state']});
      }
    }
    updateCapture();
    return ['voiceInputButton', 'dictateInputButton', 'chatVoiceSettingsButton', 'voiceSettingsForm', 'chatOnboardingToggle', 'agentBrainDrawerToggle'].every(id => byId(id));
  }

  // Reuse each module's nodes, handlers and draft state. Only watch this
  // container's direct children; message updates never trigger this observer.
  function dockTools() {
    if (!chatPanel) return;
    for (const child of [...chatPanel.children]) {
      if (child.matches('.project-knowledge-r5,.project-knowledge-r6,.project-knowledge-r7,.knowledge-actions-r8,.browser-captures-r4')) {
        byId('chatKnowledgeControls').appendChild(child);
      } else if (child.matches('.vp3-chat-task-mode,.vp3-agent-teams-a3,.vp3-chat-research,.vp3-mission-followup')) {
        byId('chatToolControls').appendChild(child);
      }
    }
  }
  dockTools();
  const toolsObserver = new MutationObserver(dockTools);
  if (chatPanel) toolsObserver.observe(chatPanel, {childList: true});

  function updateCapture() {
    const active = ['voiceInputButton', 'dictateInputButton'].filter(id => byId(id)?.getAttribute('aria-pressed') === 'true');
    const busy=window.HomeServerChatTurn?.isBusy?.();
    byId('chatCaptureStop').hidden = active.length === 0 && !busy;
    byId('chatCaptureStatus').hidden = active.length === 0 && !busy;
    const state=byId('voiceInputButton')?.dataset.voiceState;
    const labels={connecting:'Connecting microphone…',listening:'Listening…',transcribing:'Transcribing…',thinking:'Thinking…',speaking:'Speaking…'};
    byId('chatCaptureStatus').textContent = active.includes('voiceInputButton') ? labels[state] || 'Conversation mode' : busy ? 'Thinking…' : active.length ? 'Dictating' : '';
  }

  window.addEventListener('homeserver:chat-turn',()=>queueMicrotask(updateCapture));

  function selectTab(name, focus = false) {
    if (!['context', 'voice', 'more'].includes(name)) name = 'context';
    dialog.querySelectorAll('[data-chat-options-tab]').forEach(tab => {
      const active = tab.dataset.chatOptionsTab === name;
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
      byId(tab.getAttribute('aria-controls')).hidden = !active;
      if (active && focus) tab.focus();
    });
    if (name === 'voice' && !voicePrepared) {
      voicePrepared = true;
      Promise.resolve(window.HomeServerVoiceSettings?.prepare?.()).catch(error => {
        const status = byId('chatOptionsStatus');
        status.textContent = error.message || 'Voice settings could not be loaded.';
        status.hidden = false;
      });
    }
  }

  function open(name = 'context', source = trigger) {
    adoptControls();
    if (!dialog.open) {
      opener = source || trigger;
      voicePrepared = false;
      byId('chatOptionsStatus').hidden = true;
      dialog.showModal();
      trigger.setAttribute('aria-expanded', 'true');
    }
    selectTab(name, true);
    window.dispatchEvent(new CustomEvent('homeserver:chat-options-open', {detail: {tab: name}}));
  }

  function close() { if (dialog.open) dialog.close(); }
  trigger.addEventListener('click', () => open());
  byId('chatOptionsClose').addEventListener('click', close);
  dialog.addEventListener('close', () => {
    trigger.setAttribute('aria-expanded', 'false');
    opener?.focus?.({preventScroll: true});
  });
  // Source tools prepare text through the canonical input event. Return to
  // the composer without submitting or replacing their retained panel state.
  byId('chatInput').addEventListener('input', event => {
    if (!event.isTrusted && dialog.open && !form.contains(document.activeElement)) {
      opener = byId('chatInput');
      close();
    }
  });
  document.addEventListener('submit', event => {
    if (event.target !== form) return;
    if (byId('vp3ChatTaskMode')?.value === 'chrome') open('more');
    else close();
  }, true);
  window.addEventListener('homeserver:chat-route', event => {
    if (['review', 'failed'].includes(event.detail?.state)) open('more');
  });
  dialog.addEventListener('click', event => {
    const tab = event.target.closest('[data-chat-options-tab]');
    if (tab) selectTab(tab.dataset.chatOptionsTab);
    if (event.target === dialog) {
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) close();
    }
  });
  // Close before the canonical handler opens Setup, a drawer, or another view.
  dialog.addEventListener('click', event => {
    if (event.target.closest('#chatBrainToggle, #chatOnboardingToggle, #deleteChat, #voiceInputButton, #dictateInputButton, #agentWorkflowToggle, #hsTranscriptOpen, [data-view]')) close();
  }, true);
  dialog.querySelector('[role="tablist"]').addEventListener('keydown', event => {
    const tabs = [...dialog.querySelectorAll('[data-chat-options-tab]')];
    const index = tabs.indexOf(event.target);
    if (index < 0 || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    selectTab(tabs[next].dataset.chatOptionsTab, true);
  });
  byId('chatCaptureStop').addEventListener('click', () => {
    window.HomeServerChatTurn?.cancel?.();
    for (const id of ['voiceInputButton', 'dictateInputButton']) if (byId(id)?.getAttribute('aria-pressed') === 'true') byId(id).click();
  });

  window.HomeServerChatOptions = Object.freeze({open, close, isVoiceOpen: () => dialog.open && !byId('chatOptionsVoice').hidden});
  const diagnosticEvents=[];
  function renderDiagnostics(){
    const body=byId('chatBrainDrawer');if(!body)return;
    let host=byId('chatSessionDiagnostics');if(!host){host=document.createElement('details');host.id='chatSessionDiagnostics';body.appendChild(host);}
    host.replaceChildren();const title=document.createElement('summary');title.textContent='Chat session diagnostics';host.appendChild(title);
    for(const entry of diagnosticEvents){const row=document.createElement('p');row.textContent=entry.time+' · '+entry.state+' · '+entry.message;host.appendChild(row);}
  }
  window.addEventListener('homeserver:chat-diagnostic',event=>{const d=event.detail||{};diagnosticEvents.unshift({time:String(d.time||new Date().toISOString()).slice(0,40),state:String(d.state||'status').slice(0,40),message:String(d.message||'').slice(0,400)});diagnosticEvents.splice(10);renderDiagnostics();});
  document.addEventListener('click',event=>{if(event.target.closest('#chatBrainToggle'))renderDiagnostics();});
  const composerObserver = new ResizeObserver(entries => {
    const height = Math.ceil(entries[0].target.getBoundingClientRect().height);
    document.documentElement.style.setProperty('--chat-composer-height', `${height}px`);
  });
  composerObserver.observe(byId('chatForm'));
  // Existing voice modules attach controls asynchronously. Bound discovery to
  // startup; never observe the whole page or poll throughout the chat session.
  if (!adoptControls()) {
    let attempts = 0;
    const timer = setInterval(() => {
      if (adoptControls() || ++attempts >= 100) clearInterval(timer);
    }, 100);
    window.addEventListener('pagehide', () => clearInterval(timer), {once: true});
  }
  window.addEventListener('pagehide', () => captureObserver.disconnect(), {once: true});
  window.addEventListener('pagehide', () => composerObserver.disconnect(), {once: true});
  window.addEventListener('pagehide', () => toolsObserver.disconnect(), {once: true});
})();
