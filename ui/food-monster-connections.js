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
