(() => {
  'use strict';

  const byId = id => document.getElementById(id);
  const escTool = (value = '') => String(value).replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
  const fmtTool = value => value ? new Date(value).toLocaleString() : '';

  async function toolsApi(path, options = {}) {
    const headers = {...(options.headers || {})};
    if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, {...options, headers});
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }

  function notify(message, error = false) {
    const node = byId('flash');
    if (!node) return;
    node.textContent = message;
    node.className = `flash show${error ? ' error' : ''}`;
    clearTimeout(notify.timer);
    notify.timer = setTimeout(() => { node.className = 'flash'; }, 3500);
  }

  function renderSkills(items) {
    const node = byId('skillsList');
    if (!node) return;
    node.innerHTML = items.map(skill => `<article class="skill-card"><h3>${escTool(skill.name)}</h3><p>${escTool(skill.description)}</p><div class="skill-tools">${skill.tools.map(tool => `<span class="tag">${escTool(tool)}</span>`).join('')}</div></article>`).join('');
  }

  function renderTools(items) {
    const node = byId('toolsList');
    if (!node) return;
    node.innerHTML = items.map(tool => `<article class="tool-card"><div><h3>${escTool(tool.name)}</h3><p>${escTool(tool.description)}</p><div class="tool-meta"><span class="tool-mode ${escTool(tool.mode)}">${escTool(tool.mode)}</span><span>${escTool(tool.key)}</span><span>requires ${escTool(tool.required_permissions.join(', ') || 'owner')}</span></div></div><label class="tool-toggle"><input type="checkbox" data-tool-policy="${escTool(tool.key)}" ${tool.enabled ? 'checked' : ''}> Enabled</label></article>`).join('');
  }

  function renderRuns(items) {
    const node = byId('toolRunsTable');
    if (!node) return;
    node.innerHTML = items.length ? items.map(run => `<tr><td>${escTool(fmtTool(run.created_at))}</td><td>${escTool(run.tool_key)}</td><td>${escTool(run.source_app_key)}</td><td><span class="tool-status ${escTool(run.status)}">${escTool(run.status)}</span></td><td>${run.duration_ms == null ? '—' : `${Number(run.duration_ms)} ms`}</td></tr>`).join('') : '<tr><td colspan="5">No tool runs yet.</td></tr>';
  }

  async function loadToolsView() {
    const [tools, skills, runs] = await Promise.all([
      toolsApi('/api/v1/control/tools'),
      toolsApi('/api/v1/control/skills'),
      toolsApi('/api/v1/control/tool-runs?limit=100'),
    ]);
    renderTools(tools.items || []);
    renderSkills(skills.items || []);
    renderRuns(runs.items || []);
  }

  document.addEventListener('click', event => {
    const nav = event.target.closest('[data-view="tools"], [data-go="tools"]');
    if (!nav) return;
    const title = byId('pageTitle');
    if (title) title.textContent = 'Skills & Tools';
    loadToolsView().catch(err => notify(err.message, true));
  });

  document.addEventListener('change', async event => {
    const toggle = event.target.closest('[data-tool-policy]');
    if (!toggle) return;
    toggle.disabled = true;
    try {
      await toolsApi(`/api/v1/control/tools/${encodeURIComponent(toggle.dataset.toolPolicy)}`, {
        method: 'PUT',
        body: JSON.stringify({enabled: toggle.checked}),
      });
      notify(`${toggle.dataset.toolPolicy} ${toggle.checked ? 'enabled' : 'disabled'}.`);
      await loadToolsView();
    } catch (err) {
      toggle.checked = !toggle.checked;
      notify(err.message, true);
    } finally {
      toggle.disabled = false;
    }
  });

  byId('refreshButton')?.addEventListener('click', () => {
    if (byId('view-tools')?.classList.contains('active')) loadToolsView().catch(err => notify(err.message, true));
  });

  window.addEventListener('hashchange', () => {
    if (location.hash === '#tools') document.querySelector('[data-view="tools"]')?.click();
  });

  if (location.hash === '#tools') setTimeout(() => document.querySelector('[data-view="tools"]')?.click(), 0);
})();
