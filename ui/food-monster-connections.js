(() => {
  'use strict';

  // Owner-only loopback API. Never send pairing credentials to any client-
  // supplied URL; only HomeServer makes the outbound HTTPS connection.
  const API = '/api/v1/control/food-monster';
  const byId = id => document.getElementById(id);
  let busy = false;

  function normalizeOrigin(raw) {
    if (typeof raw !== 'string' || raw.length > 255) throw new Error('Enter the HTTPS subdomain of your Food Monster.');
    let url;
    try { url = new URL(raw.trim()); } catch { throw new Error('Enter a valid HTTPS Food Monster URL.'); }
    const host = url.hostname.toLowerCase();
    if (url.protocol !== 'https:' || !host || url.username || url.password ||
        url.port || !['', '/'].includes(url.pathname) || url.search || url.hash ||
        !host.includes('.') || host.includes('..') || host.startsWith('.') ||
        host.endsWith('.') || !/^[a-z0-9.-]+$/.test(host) ||
        /^(?:[0-9.]+|localhost)$/.test(host) ||
        /\.(?:local|localhost|internal)$/.test(host) ||
        host.split('.').some(part => !part || part.startsWith('-') || part.endsWith('-'))) {
      throw new Error('Use the public HTTPS domain only, without a path, credentials or port.');
    }
    return url.origin;
  }

  async function localApi(path, init = {}) {
    const response = await fetch(API + path, {
      credentials: 'same-origin',
      cache: 'no-store',
      ...init,
      headers: { Accept: 'application/json', ...(init.headers || {}) },
    });
    let data = {};
    try { data = await response.json(); } catch { /* Local API returned no JSON. */ }
    if (!response.ok) {
      const raw = typeof data.detail === 'string' ? data.detail : 'HomeServer could not complete this request.';
      throw new Error(raw.slice(0, 220));
    }
    return data;
  }

  function notice(message, error = false) {
    const el = byId('fmLinksStatus');
    if (!el) return;
    el.textContent = String(message || '');
    el.dataset.level = error ? 'error' : 'info';
  }

  function create(tag, className, textContent) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (textContent !== undefined) element.textContent = String(textContent);
    return element;
  }

  function setBusy(value) {
    busy = Boolean(value);
    const button = byId('fmLinkSubmit');
    if (button) button.disabled = busy;
    const list = byId('fmLinksList');
    if (list) list.setAttribute('aria-busy', String(busy));
  }

  function connectionCard(record) {
    const row = create('div', 'fm-link-row');
    row.setAttribute('role', 'listitem');
    const details = create('div', 'fm-link-information');
    details.append(create('strong', '', record.label || 'Food Monster'));
    details.append(create('small', '', record.origin || 'Unknown source'));
    const scopes = Array.isArray(record.scopes) ? record.scopes.join(', ') : '';
    details.append(create('small', '', 'Read-only: ' + (scopes || 'No access granted')));
    row.append(details);
    const remove = create('button', 'button secondary fm-link-remove', 'Disconnect');
    remove.type = 'button';
    remove.setAttribute('aria-label', 'Disconnect ' + String(record.label || 'Food Monster'));
    remove.addEventListener('click', async () => {
      if (busy || !record.id || !/^[a-f0-9]{32}$/.test(record.id)) return;
      if (!window.confirm('Disconnect ' + (record.label || 'this Food Monster') + '? Your assistant will lose access to this restaurant.')) return;
      setBusy(true);
      remove.disabled = true;
      try {
        const done = await localApi('/connections/' + encodeURIComponent(record.id), {method: 'DELETE'});
        notice(done.message || 'Connection removed.', Boolean(done.remoteRevocationRequired));
        await refresh({preserveNotice:true});
      } catch {
        notice('Unable to disconnect. Check the HomeServer connection and retry.', true);
      } finally {
        setBusy(false);
        remove.disabled = false;
      }
    });
    row.append(remove);

    const snapshot = create('small', 'fm-link-snapshot', 'Knowledge snapshot: checking…');
    details.append(snapshot);
    if (record.status === 'active') {
      const syncButton = create('button', 'button secondary fm-link-sync', 'Sync knowledge');
      syncButton.type = 'button';
      syncButton.setAttribute('aria-label','Synchronize ' + String(record.label || 'Food Monster') + ' knowledge');
      syncButton.addEventListener('click', async () => {
        if (busy || !record.id || !/^[a-f0-9]{32}$/.test(record.id)) return;
        setBusy(true);
        syncButton.disabled = true;
        snapshot.textContent = 'Syncing authorized knowledge…';
        try {
          const result = await localApi('/connections/' + encodeURIComponent(record.id) + '/sync', {method:'POST'});
          const categories = result.collections || {};
          const total = Object.values(categories).reduce((n, item) => n + (Number(item.created || 0) + Number(item.updated || 0)), 0);
          notice('Completed private Food Monster snapshot for ' + (record.label || 'restaurant') +
                 '. ' + total + ' created or updated. Cached records are snapshots, not verified live data.');
          await refresh({preserveNotice:true});
        } catch {
          snapshot.textContent = 'Sync failed; previous snapshot may be stale.';
          notice('Knowledge synchronization could not be completed. Existing evidence remains unverified.', true);
        } finally {
          setBusy(false);
          syncButton.disabled = false;
        }
      });
      row.append(syncButton);

      const autoLabel = create('label', 'fm-link-auto-label');
      const autoEnabled = create('input', 'fm-link-auto-checkbox');
      autoEnabled.type = 'checkbox';
      autoEnabled.setAttribute('aria-label', 'Automatically synchronize ' + String(record.label || 'Food Monster'));
      const cadence = create('select', 'fm-link-interval');
      cadence.setAttribute('aria-label', 'Automatic synchronization interval for ' + String(record.label || 'Food Monster'));
      for (const [seconds, display] of [[3600, 'Every hour'], [21600, 'Every 6 hours'], [86400, 'Daily']]) {
        const option = create('option', '', display);
        option.value = String(seconds);
        cadence.append(option);
      }
      cadence.value = '21600';
      const savePolicy = create('button', 'button secondary fm-link-save-policy', 'Save schedule');
      savePolicy.type = 'button';
      autoLabel.append(autoEnabled, create('span', '', 'Automatic sync'));
      details.append(autoLabel, cadence);
      row.append(savePolicy);
      const updatePolicy = async () => {
        if (busy || !/^[a-f0-9]{32}$/.test(record.id)) return;
        const enabled = Boolean(autoEnabled.checked);
        const interval = Number(cadence.value);
        if (![3600, 21600, 86400].includes(interval)) {
          notice('Choose a valid synchronization interval.', true);
          return;
        }
        setBusy(true);
        savePolicy.disabled = true;
        try {
          await localApi('/connections/' + encodeURIComponent(record.id) + '/auto-sync', {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({enabled, interval_seconds: interval}),
          });
          notice(enabled ? 'Automatic Food Monster synchronization enabled for ' +
                 (record.label || 'restaurant') + '.' :
                 'Automatic Food Monster synchronization disabled for ' +
                 (record.label || 'restaurant') + '.');
        } catch {
          notice('Schedule could not be updated. The previous setting remains in effect.', true);
        } finally {
          setBusy(false);
          savePolicy.disabled = false;
        }
      };
      savePolicy.addEventListener('click', updatePolicy);
      if (/^[a-f0-9]{32}$/.test(record.id)) {
        localApi('/connections/' + encodeURIComponent(record.id) + '/auto-sync')
          .then(policy => {
            autoEnabled.checked = policy.enabled === true;
            if ([3600, 21600, 86400].includes(Number(policy.intervalSeconds))) {
              cadence.value = String(policy.intervalSeconds);
            }
          })
          .catch(() => {
            savePolicy.disabled = true;
            notice('Automatic sync settings could not be loaded.', true);
          });
      }

      if (/^[a-f0-9]{32}$/.test(record.id)) {
        localApi('/connections/' + encodeURIComponent(record.id) + '/sync-status')
          .then(result => {
            if (!row.parentNode && !row.children.length) return;
            const categories = Object.values(result.categories || {});
            const count = categories.reduce((n, item) => n + Number(item.documents || 0), 0);
            const latest = categories.map(item => item.lastSyncedAt).filter(Boolean).sort().at(-1);
            const problems = categories.some(item => Boolean(item.lastError));
            snapshot.textContent = latest
              ? count + ' locally indexed · Last complete sync ' + latest + ' · Not live-verified' +
                (problems ? ' · Sync warning' : '')
              : 'Knowledge not yet synchronized';
          })
          .catch(() => { snapshot.textContent = 'Knowledge sync status unavailable'; });
      }
    }
    return row;
  }

  async function refresh(options = {}) {
    const container = byId('fmLinksList');
    if (!container) return;
    try {
      const result = await localApi('/connections');
      if (!Array.isArray(result.items)) throw new Error('Invalid connection list.');
      container.replaceChildren();
      if (!result.items.length) {
        container.append(create('p', 'fm-link-empty', 'No Food Monster connections yet.'));
      } else {
        result.items.forEach(item => container.append(connectionCard(item)));
      }
      if (!options.preserveNotice) notice('');
    } catch {
      container.replaceChildren(create('p', 'fm-link-empty', 'Connections could not be loaded.'));
      notice('Unable to load Food Monster connections.', true);
    }
  }

  async function submit(event) {
    event.preventDefault();
    if (busy) return;
    const form = byId('fmLinkForm');
    const origin = byId('fmLinkOrigin');
    const label = byId('fmLinkLabel');
    const code = byId('fmLinkCode');
    if (!form || !origin || !label || !code) return;
    setBusy(true);
    try {
      const site = normalizeOrigin(origin.value);
      const codeValue = code.value.trim();
      if (!/^fmlink_[A-Za-z0-9_-]{43}$/.test(codeValue)) {
        throw new Error('Enter the one-time pairing code generated by your Food Monster administrator.');
      }
      const result = await localApi('/connections', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({origin: site, label: label.value.trim(), pairing_code: codeValue}),
      });
      if (!result.connection || typeof result.connection.id !== 'string') {
        throw new Error('The connection response was incomplete.');
      }
      origin.value = '';
      label.value = '';
      notice('Food Monster connected. The main VP3 assistant can now search the approved knowledge.');
      byId('fmLinkSetup').open = false;
      await refresh({preserveNotice:true});
    } catch (error) {
      notice(error?.message || 'The private connection could not be established.', true);
    } finally {
      // Never keep single-use authorization codes in the browser UI.
      code.value = '';
      setBusy(false);
    }
  }

  function init() {
    const form = byId('fmLinkForm');
    if (!form || !byId('fmLinksList')) return;
    form.addEventListener('submit', submit);
    byId('fmLinksRefresh')?.addEventListener('click', () => { if (!busy) refresh(); });
    byId('chatOptionsTabMore')?.addEventListener('click', () => { if (!busy) refresh(); });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, {once: true});
  } else {
    init();
  }

  // No secrets are exposed through this small testable frontend helper.
  window.HomeServerFoodMonsterOrigin = normalizeOrigin;
})();
