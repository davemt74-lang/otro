(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const dialog = byId('chatOptionsDialog');
  if (!dialog) return;
  const trigger = byId('chatOptionsButton');
  let opener = trigger;
  let voicePrepared = false;
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
        captureObserver.observe(button, {attributes: true, attributeFilter: ['aria-pressed']});
      }
    }
    updateCapture();
    return ['voiceInputButton', 'dictateInputButton', 'chatVoiceSettingsButton', 'voiceSettingsForm', 'chatOnboardingToggle', 'agentBrainDrawerToggle'].every(id => byId(id));
  }

  function updateCapture() {
    const active = ['voiceInputButton', 'dictateInputButton'].filter(id => byId(id)?.getAttribute('aria-pressed') === 'true');
    byId('chatCaptureStop').hidden = active.length === 0;
    byId('chatCaptureStatus').hidden = active.length === 0;
    byId('chatCaptureStatus').textContent = active.includes('voiceInputButton') ? 'Conversation mode' : active.length ? 'Dictating' : '';
  }

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
    for (const id of ['voiceInputButton', 'dictateInputButton']) if (byId(id)?.getAttribute('aria-pressed') === 'true') byId(id).click();
  });

  window.HomeServerChatOptions = Object.freeze({open, close, isVoiceOpen: () => dialog.open && !byId('chatOptionsVoice').hidden});
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
})();
