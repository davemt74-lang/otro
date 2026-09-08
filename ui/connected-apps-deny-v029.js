(() => {
  'use strict';

  let pending = [];
  let syncing = false;

  async function sync() {
    if (syncing) return;
    syncing = true;
    try {
      const response = await fetch('/api/v1/control/connected-apps');
      if (!response.ok) return;
      const data = await response.json();
      pending = Array.isArray(data.pending) ? data.pending : [];
      const cards = [...document.querySelectorAll('#pendingPairing .pending-app-card')];
      cards.forEach((card, index) => {
        const item = pending[index];
        if (!item || card.querySelector('[data-v029-deny-pending]')) return;
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'button secondary danger';
        button.dataset.v029DenyPending = String(item.id);
        button.textContent = 'Deny request';
        const column = card.lastElementChild || card;
        column.appendChild(button);
      });
    } catch (_) {
      // The main Connected Apps workspace owns user-visible fetch errors.
    } finally {
      syncing = false;
    }
  }

  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-v029-deny-pending]');
    if (!button) return;
    const item = pending.find(row => String(row.id) === String(button.dataset.v029DenyPending));
    if (!confirm(`Deny the pairing request from ${item?.app_name || 'this application'}?`)) return;
    button.disabled = true;
    try {
      const response = await fetch(`/api/v1/control/connected-apps/pending/${encodeURIComponent(button.dataset.v029DenyPending)}/deny`, {method:'POST'});
      let payload = {};
      try { payload = await response.json(); } catch (_) {}
      if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
      if (typeof window.loadConnectedAppsV029 === 'function') await window.loadConnectedAppsV029();
      if (typeof window.flash === 'function') window.flash(`${payload.app_key || 'Application'} pairing request denied.`);
    } catch (err) {
      if (typeof window.flash === 'function') window.flash(err.message, true);
    } finally {
      button.disabled = false;
    }
  });

  const target = document.getElementById('pendingPairing');
  if (target) new MutationObserver(() => { sync(); }).observe(target, {childList:true, subtree:true});
  sync();
})();
