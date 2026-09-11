(() => {
  'use strict';

  const VERSION = 'v0.55';
  const API = '/api/v1/control/agent-workflows/resume';
  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const state = {payload: null, loading: false, bootAttempts: 0};

  async function readResumeIndex() {
    const response = await fetch(`${API}?limit=50`, {
      cache: 'no-store',
      credentials: 'same-origin',
      method: 'GET',
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
    return payload;
  }

  function ensureStyles() {
    if (document.querySelector('link[data-agent-workflow-resume-v055]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/agent-workflow-resume.css';
    link.dataset.agentWorkflowResumeV055 = '1';
    document.head.appendChild(link);
  }

  function ensureCard() {
    const panel = document.querySelector('#view-chat .conversation-panel');
    const newChat = byId('newChat');
    if (!panel || !newChat) return false;
    if (byId('agentWorkflowResumeV055')) return true;
    const card = document.createElement('section');
    card.id = 'agentWorkflowResumeV055';
    card.className = 'workflow-resume-card hidden';
    card.dataset.workflowResume = VERSION;
    card.setAttribute('aria-live', 'polite');
    newChat.insertAdjacentElement('afterend', card);
    return true;
  }

  function conversationButton(conversationId) {
    return [...document.querySelectorAll('[data-brain-conversation]')]
      .find(node => String(node.dataset.brainConversation || '') === String(conversationId || '')) || null;
  }

  function clearBadges() {
    document.querySelectorAll('[data-workflow-resume-badge]').forEach(node => node.remove());
    document.querySelectorAll('[data-brain-conversation].has-workflow-resume').forEach(node => node.classList.remove('has-workflow-resume'));
  }

  function decorateConversations(items) {
    clearBadges();
    for (const item of items || []) {
      const button = conversationButton(item.conversation_id);
      if (!button) continue;
      button.classList.add('has-workflow-resume');
      const badge = document.createElement('span');
      badge.className = 'workflow-resume-badge';
      badge.dataset.workflowResumeBadge = String(item.conversation_id || '');
      badge.textContent = item.next_action?.label || 'Resume workflow';
      button.appendChild(badge);
    }
  }

  function render() {
    if (!ensureCard()) return;
    const card = byId('agentWorkflowResumeV055');
    const items = Array.isArray(state.payload?.items) ? state.payload.items : [];
    decorateConversations(items);
    const suggested = state.payload?.suggested || null;
    if (!card || !suggested) {
      if (card) {
        card.classList.add('hidden');
        card.innerHTML = '';
      }
      return;
    }
    const count = Number(state.payload?.resumable_count || items.length || 0);
    card.innerHTML = `
      <p class="eyebrow">RESUME WORKFLOW · ${VERSION}</p>
      <strong>${esc(suggested.title || 'Active workflow')}</strong>
      <p>${esc(suggested.next_action?.label || 'Return to this conversation to continue the workflow explicitly.')}${count > 1 ? ` · ${count} resumable conversations` : ''}</p>
      <button class="button secondary" type="button" data-workflow-resume-conversation="${esc(suggested.conversation_id)}">Resume workflow</button>`;
    card.classList.remove('hidden');
  }

  async function refresh() {
    if (state.loading || !ensureCard()) return;
    state.loading = true;
    try {
      state.payload = await readResumeIndex();
    } catch (_) {
      state.payload = null;
    } finally {
      state.loading = false;
      render();
    }
  }

  function navigateToConversation(conversationId) {
    const target = conversationButton(conversationId);
    if (!target) return;
    // This is navigation only and occurs only after the user presses Resume.
    // The existing conversation handler performs GET-only conversation loading.
    target.click();
    target.focus({preventScroll: true});
    target.scrollIntoView({behavior: 'smooth', block: 'nearest'});
  }

  document.addEventListener('click', event => {
    const resume = event.target.closest('[data-workflow-resume-conversation]');
    if (resume) {
      navigateToConversation(String(resume.dataset.workflowResumeConversation || ''));
      return;
    }
    if (event.target.closest('[data-view="chat"], [data-go="chat"], #newChat, [data-brain-conversation]')) {
      setTimeout(() => refresh().catch(() => null), 280);
    }
  });

  window.addEventListener('homeserver:chat-agent-changed', () => {
    setTimeout(() => refresh().catch(() => null), 120);
  });

  const conversationObserver = new MutationObserver(mutations => {
    const changed = mutations.some(mutation => [...mutation.addedNodes].some(node => {
      if (node.nodeType !== 1) return false;
      if (node.matches?.('[data-workflow-resume-badge]')) return false;
      return Boolean(node.matches?.('[data-conversation-row]') || node.querySelector?.('[data-conversation-row]'));
    }));
    if (!changed) return;
    clearTimeout(conversationObserver._resumeRefresh);
    conversationObserver._resumeRefresh = setTimeout(() => refresh().catch(() => null), 120);
  });

  function boot() {
    ensureStyles();
    if (!window.HomeServerAgentWorkflowContinuation || !ensureCard()) {
      state.bootAttempts += 1;
      if (state.bootAttempts < 180) setTimeout(boot, 80);
      return;
    }
    const list = byId('conversationList');
    if (list) conversationObserver.observe(list, {childList: true, subtree: true});
    refresh().catch(() => null);
  }

  window.HomeServerAgentWorkflowResume = Object.freeze({version: VERSION, refresh});
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})();
