(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const REQUIRED_PACKAGES = ['whisper-stt', 'piper-tts'];
  const state = {packages: [], busy: false, loading: true, setup: null};
  const button = byId('startSetup');
  const enter = byId('enterAgent');

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options, credentials: 'same-origin', cache: 'no-store',
      headers: options.headers || {},
    });
    let result;
    try { result = await response.json(); } catch (_) { result = {}; }
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'HomeServer could not complete this request.');
    return result;
  }

  function announce(message, failed = false) {
    const node = byId('installFeedback');
    node.hidden = false;
    node.classList.toggle('error', failed);
    node.textContent = message;
  }

  function packageFor(key) { return state.packages.find(item => item.key === key); }
  function ready(item) { return !!(item && item.installed && item.installed.status === 'installed' && item.installed.healthy === true); }
  function label(item) {
    if (!item) return 'Unavailable';
    if (ready(item)) return 'Installed & verified';
    if (!item.supported) return 'Not supported on this device';
    if (item.installed && item.installed.status === 'failed') return 'Retry available';
    if (item.installed && item.installed.status === 'installed') return 'Repair available';
    return 'Ready to install';
  }
  function byteSize(value) {
    const num = Number(value || 0);
    return num >= 1048576 ? (num / 1048576).toFixed(0) + ' MB' : Math.ceil(num / 1024) + ' KB';
  }

  function render() {
    const items = REQUIRED_PACKAGES.map(packageFor);
    const supported = items.filter(item => item && item.supported);
    const readyCount = supported.filter(ready).length;
    const done = items.every(item => !item || !item.supported || ready(item));
    const voice = byId('voiceState');
    const skipped = items.filter(item => !item || !item.supported);
    voice.textContent = state.busy ? 'Preparing…' : done && supported.length ? 'Installed & verified' : done ? 'Unavailable' : readyCount ? readyCount + ' of ' + supported.length + ' ready' : 'Optional install';
    voice.className = 'component-status ' + (done && readyCount ? 'ready' : state.busy ? 'waiting' : '');
    const packagesText = items.filter(Boolean).map(item => item.name + ': ' + label(item)).join(' · ');
    byId('packageDetail').textContent = packagesText;
    byId('overallState').textContent = state.busy ? 'Preparing' : done ? 'Essentials checked' : 'Ready to prepare';
    const bytes = supported.filter(item => !ready(item)).reduce((n, item) => n + Number(item.download_bytes || 0), 0);
    const intro = byId('setupIntroduction');
    if (state.loading) intro.textContent = 'Checking your HomeServer installation…';
    else if (skipped.length) intro.textContent = 'Compatible voice features are optional. Unsupported packages will not be installed.';
    else if (done) intro.textContent = 'Your local voice packages are ready. Your Agent is one click away.';
    else intro.textContent = 'Recommended setup downloads verified voice components' + (bytes ? ' (about ' + byteSize(bytes) + ')' : '') + '.';
    button.disabled = state.busy || state.loading || done;
    button.textContent = state.busy ? 'Preparing essentials…' : done ? 'Voice essentials ready ✓' : 'Prepare my HomeServer →';
    enter.disabled = state.busy || state.loading;
    enter.textContent = state.setup && state.setup.complete ? 'Open Agent Chat →' : 'Continue to Agent Chat →';
  }

  async function refresh() {
    const [system, packages] = await Promise.all([
      api('/api/v1/control/system'),
      api('/api/v1/control/local-apps'),
    ]);
    state.setup = system.setup || {};
    state.packages = Array.isArray(packages.packages) ? packages.packages : [];
    state.loading = false;
    render();
  }

  async function prepare() {
    if (state.busy || state.loading) return;
    state.busy = true;
    render();
    let failures = 0;
    for (const key of REQUIRED_PACKAGES) {
      const item = packageFor(key);
      if (!item || !item.supported || ready(item)) continue;
      const verb = item.installed && item.installed.status === 'installed' ? 'repairing' : 'installing';
      announce('Securely ' + verb + ' ' + item.name + '. Keep HomeServer open while the download finishes.');
      try {
        await api('/api/v1/control/local-apps/' + encodeURIComponent(key) + '/' + (verb === 'repairing' ? 'update' : 'install'), {method:'POST'});
        // The canonical catalog checks package integrity and activates it only on success.
        await refresh();
        if (!ready(packageFor(key))) throw new Error('The package did not pass its health check.');
      } catch (error) {
        failures += 1;
        announce(item.name + ': ' + error.message + ' You can retry or continue without local voice.', true);
      }
    }
    try { await refresh(); }
    catch (error) { announce('Package status could not be refreshed: ' + error.message, true); }
    state.busy = false;
    render();
    if (!failures) announce('Preparation complete. You can meet your Agent now.');
  }

  async function openAgent() {
    if (state.busy || state.loading) return;
    enter.disabled = true;
    enter.textContent = 'Opening your Agent…';
    try {
      if (!state.setup || !state.setup.complete) {
        await api('/api/v1/control/system/setup', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({complete:true}),
        });
      }
      window.location.assign('/#chat');
    } catch (error) {
      announce('Could not finish setup: ' + error.message + '. Try again or open advanced setup.', true);
      enter.disabled = false;
      enter.textContent = 'Continue to Agent Chat →';
    }
  }

  button.addEventListener('click', prepare);
  enter.addEventListener('click', openAgent);
  refresh().catch(error => {
    state.loading = false;
    // Fail closed for provisioning; offer a link to diagnostics instead of claiming installation.
    button.disabled = true;
    enter.disabled = true;
    byId('overallState').textContent = 'Needs attention';
    byId('setupIntroduction').textContent = 'Could not read your installed HomeServer state.';
    announce(error.message + ' Reopen HomeServer from the tray, or visit Advanced setup.', true);
  });
})();
