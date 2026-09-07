(() => {
  'use strict';

  const byId = id => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = value => value ? new Date(value).toLocaleString() : 'Never';
  let loadedOnce = false;

  async function sourceApi(path, options = {}) {
    const headers = {...(options.headers || {})};
    if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, {...options, headers});
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }

  function notify(message, error = false) {
    if (typeof window.flash === 'function') {
      window.flash(message, error);
      return;
    }
    const node = byId('flash');
    if (!node) return;
    node.textContent = message;
    node.className = `flash show${error ? ' error' : ''}`;
    clearTimeout(notify.timer);
    notify.timer = setTimeout(() => { node.className = 'flash'; }, 3500);
  }

  async function refreshKnowledge() {
    if (typeof window.loadKnowledge === 'function') await window.loadKnowledge();
  }

  function ensureStyles() {
    if (byId('knowledgeSourcesStyles')) return;
    const style = document.createElement('style');
    style.id = 'knowledgeSourcesStyles';
    style.textContent = `
      .knowledge-sources-panel{margin:0 0 18px;padding:16px}
      .knowledge-sources-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px}
      .knowledge-sources-head h3{margin:2px 0 4px}.knowledge-sources-head p{margin:0}
      .knowledge-source-form{margin:14px 0 0;padding:14px;border:1px solid var(--border,#e5e7eb);border-radius:12px;background:#fafafa}
      .knowledge-source-form.hidden{display:none}
      .knowledge-source-form .source-path{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
      .knowledge-source-list{display:grid;gap:9px;margin-top:14px}
      .knowledge-source-card{border:1px solid var(--border,#e5e7eb);border-radius:12px;padding:13px;background:#fff}
      .knowledge-source-row{display:flex;justify-content:space-between;gap:14px;align-items:flex-start}
      .knowledge-source-main{min-width:0;flex:1}.knowledge-source-main h4{margin:0 0 4px;font-size:14px}
      .knowledge-source-path{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;color:#6b7280;overflow-wrap:anywhere}
      .knowledge-source-meta{display:flex;flex-wrap:wrap;gap:6px 12px;margin-top:9px;color:#6b7280;font-size:11px}
      .knowledge-source-status{display:inline-flex;align-items:center;gap:6px;font-size:11px;font-weight:650;text-transform:capitalize}
      .knowledge-source-status::before{content:'';width:7px;height:7px;border-radius:50%;background:#9ca3af}
      .knowledge-source-status.ready::before{background:#22c55e}.knowledge-source-status.scanning::before{background:#3b82f6}.knowledge-source-status.error::before{background:#ef4444}.knowledge-source-status.paused::before{background:#f59e0b}
      .knowledge-source-actions{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}
      .knowledge-source-actions button{white-space:nowrap}
      .knowledge-source-error{margin-top:9px;padding:8px 10px;border-radius:8px;background:#fff7ed;color:#9a3412;font-size:11px}
      .knowledge-source-files{margin-top:10px;border-top:1px solid #eef0f2;padding-top:8px;max-height:220px;overflow:auto}
      .knowledge-source-file{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;padding:7px 2px;border-bottom:1px solid #f2f3f4;font-size:11px}
      .knowledge-source-file code{overflow-wrap:anywhere}.knowledge-source-file .error{color:#b91c1c}
      .knowledge-source-foot{margin-top:10px;font-size:11px;color:#6b7280}
      @media(max-width:720px){.knowledge-sources-head,.knowledge-source-row{display:block}.knowledge-source-actions{justify-content:flex-start;margin-top:10px}}
    `;
    document.head.appendChild(style);
  }

  function ensurePanel() {
    ensureStyles();
    if (byId('knowledgeSourcesPanel')) return;
    const view = byId('view-knowledge');
    if (!view) return;
    const intro = view.querySelector('.section-intro');
    const panel = document.createElement('section');
    panel.id = 'knowledgeSourcesPanel';
    panel.className = 'panel knowledge-sources-panel';
    panel.innerHTML = `
      <div class="knowledge-sources-head">
        <div><p class="eyebrow">LOCAL SOURCES</p><h3>Watched folders</h3><p class="muted">Keep selected local folders indexed automatically. HomeServer reads supported documents but never edits or deletes your original files.</p></div>
        <button class="button secondary" id="showKnowledgeSourceForm" type="button">Add folder</button>
      </div>
      <form id="knowledgeSourceForm" class="knowledge-source-form hidden">
        <div class="form-grid">
          <label>Folder path<input class="source-path" id="knowledgeSourcePath" maxlength="4000" required placeholder="C:\\Users\\You\\Documents\\Project"></label>
          <label>Label <span class="muted">optional</span><input id="knowledgeSourceLabel" maxlength="200" placeholder="Project files"></label>
        </div>
        <div class="form-grid">
          <label>Refresh interval<select id="knowledgeSourceInterval"><option value="60">1 minute</option><option value="120" selected>2 minutes</option><option value="300">5 minutes</option><option value="900">15 minutes</option><option value="3600">1 hour</option></select></label>
          <label class="check-inline"><input id="knowledgeSourceRecursive" type="checkbox" checked> Include subfolders</label>
        </div>
        <label>Additional exclusions <span class="muted">comma-separated file/folder names or wildcard patterns</span><input id="knowledgeSourceExcludes" maxlength="4000" placeholder="private, *.tmp, archive/*"></label>
        <div class="form-actions"><button class="button secondary" id="cancelKnowledgeSourceForm" type="button">Cancel</button><button class="button primary" type="submit">Add and index folder</button></div>
      </form>
      <div id="knowledgeSourceList" class="knowledge-source-list"><div class="empty-state">No watched folders yet.</div></div>
      <div id="knowledgeSourceFoot" class="knowledge-source-foot"></div>`;
    if (intro?.nextSibling) view.insertBefore(panel, intro.nextSibling);
    else view.appendChild(panel);
  }

  function sourceCard(source) {
    const status = source.enabled ? source.status : 'paused';
    const label = source.label || source.path.split(/[\\/]/).filter(Boolean).pop() || 'Local folder';
    const error = source.last_error ? `<div class="knowledge-source-error">${esc(source.last_error)}</div>` : '';
    const changes = [
      `${Number(source.indexed_files || 0).toLocaleString()} indexed`,
      source.last_scan_updated_count ? `${source.last_scan_updated_count} updated` : '',
      source.last_scan_moved_count ? `${source.last_scan_moved_count} moved` : '',
      source.last_scan_removed_count ? `${source.last_scan_removed_count} removed` : '',
      source.error_files ? `${source.error_files} errors` : '',
    ].filter(Boolean).join(' · ');
    return `<article class="knowledge-source-card" data-source-card="${source.id}">
      <div class="knowledge-source-row">
        <div class="knowledge-source-main">
          <div class="knowledge-source-status ${esc(status)}">${esc(status)}</div>
          <h4>${esc(label)}</h4>
          <div class="knowledge-source-path">${esc(source.path)}</div>
          <div class="knowledge-source-meta"><span>${esc(changes || 'No indexed files yet')}</span><span>every ${Math.round(Number(source.scan_interval_seconds || 120) / 60)} min</span><span>last scan ${esc(fmt(source.last_scan_completed_at))}</span></div>
          ${error}
        </div>
        <div class="knowledge-source-actions">
          <button class="button secondary" type="button" data-source-files="${source.id}">Files</button>
          <button class="button secondary" type="button" data-source-scan="${source.id}" ${status === 'scanning' ? 'disabled' : ''}>Scan now</button>
          <button class="button secondary" type="button" data-source-toggle="${source.id}" data-enabled="${source.enabled ? '1' : '0'}">${source.enabled ? 'Pause' : 'Resume'}</button>
          <button class="text-button danger" type="button" data-source-delete="${source.id}">Remove</button>
        </div>
      </div>
      <div class="knowledge-source-files hidden" id="knowledgeSourceFiles-${source.id}"></div>
    </article>`;
  }

  async function loadSources() {
    ensurePanel();
    const list = byId('knowledgeSourceList');
    if (!list) return;
    try {
      const data = await sourceApi('/api/v1/control/knowledge/sources');
      list.innerHTML = data.items?.length ? data.items.map(sourceCard).join('') : '<div class="empty-state">No watched folders yet. Add a project or Documents folder to keep its supported files synchronized.</div>';
      const foot = byId('knowledgeSourceFoot');
      if (foot) foot.textContent = `Supported: ${(data.supported_extensions || []).join(', ')} · Default exclusions: ${(data.default_excludes || []).join(', ')}`;
      loadedOnce = true;
    } catch (err) {
      list.innerHTML = `<div class="empty-state">${esc(err.message)}</div>`;
    }
  }

  async function loadSourceFiles(sourceId) {
    const target = byId(`knowledgeSourceFiles-${sourceId}`);
    if (!target) return;
    if (!target.classList.contains('hidden')) {
      target.classList.add('hidden');
      return;
    }
    target.classList.remove('hidden');
    target.innerHTML = '<div class="empty-state">Loading indexed files…</div>';
    try {
      const data = await sourceApi(`/api/v1/control/knowledge/sources/${sourceId}/files?limit=500`);
      target.innerHTML = data.items?.length ? data.items.map(item => `<div class="knowledge-source-file"><code>${esc(item.relative_path)}</code><span class="${item.status === 'error' ? 'error' : ''}">${item.status === 'error' ? esc(item.last_error || 'error') : 'indexed'}</span></div>`).join('') : '<div class="empty-state">No tracked files.</div>';
    } catch (err) {
      target.innerHTML = `<div class="empty-state">${esc(err.message)}</div>`;
    }
  }

  document.addEventListener('click', async event => {
    if (event.target.id === 'showKnowledgeSourceForm') {
      byId('knowledgeSourceForm')?.classList.remove('hidden');
      byId('knowledgeSourcePath')?.focus();
      return;
    }
    if (event.target.id === 'cancelKnowledgeSourceForm') {
      byId('knowledgeSourceForm')?.classList.add('hidden');
      return;
    }

    const files = event.target.closest('[data-source-files]');
    if (files) {
      await loadSourceFiles(files.dataset.sourceFiles);
      return;
    }

    const scan = event.target.closest('[data-source-scan]');
    if (scan) {
      scan.disabled = true;
      scan.textContent = 'Scanning…';
      try {
        const result = await sourceApi(`/api/v1/control/knowledge/sources/${scan.dataset.sourceScan}/scan`, {method:'POST'});
        await loadSources();
        await refreshKnowledge();
        const info = result.scan || {};
        notify(`Folder scan complete · ${info.indexed || 0} new · ${info.updated || 0} updated · ${info.removed || 0} removed.`);
      } catch (err) { notify(err.message, true); }
      finally { scan.disabled = false; scan.textContent = 'Scan now'; }
      return;
    }

    const toggle = event.target.closest('[data-source-toggle]');
    if (toggle) {
      const enabled = toggle.dataset.enabled !== '1';
      try {
        await sourceApi(`/api/v1/control/knowledge/sources/${toggle.dataset.sourceToggle}`, {method:'PATCH', body:JSON.stringify({enabled})});
        await loadSources();
        notify(enabled ? 'Knowledge source resumed.' : 'Knowledge source paused.');
      } catch (err) { notify(err.message, true); }
      return;
    }

    const remove = event.target.closest('[data-source-delete]');
    if (remove) {
      if (!confirm('Remove this watched source and its indexed copies from HomeServer? Your original files will not be changed or deleted.')) return;
      try {
        await sourceApi(`/api/v1/control/knowledge/sources/${remove.dataset.sourceDelete}`, {method:'DELETE'});
        await loadSources();
        await refreshKnowledge();
        notify('Knowledge source removed. Original local files were left untouched.');
      } catch (err) { notify(err.message, true); }
    }
  });

  document.addEventListener('submit', async event => {
    if (event.target.id !== 'knowledgeSourceForm') return;
    event.preventDefault();
    const submit = event.target.querySelector('button[type="submit"]');
    submit.disabled = true;
    submit.textContent = 'Indexing…';
    try {
      const excludes = (byId('knowledgeSourceExcludes')?.value || '').split(',').map(value => value.trim()).filter(Boolean);
      const data = await sourceApi('/api/v1/control/knowledge/sources', {
        method:'POST',
        body:JSON.stringify({
          path:byId('knowledgeSourcePath').value,
          label:byId('knowledgeSourceLabel').value,
          recursive:byId('knowledgeSourceRecursive').checked,
          scan_interval_seconds:Number(byId('knowledgeSourceInterval').value || 120),
          excludes,
        }),
      });
      event.target.reset();
      byId('knowledgeSourceRecursive').checked = true;
      byId('knowledgeSourceInterval').value = '120';
      event.target.classList.add('hidden');
      await loadSources();
      await refreshKnowledge();
      const scan = data.scan || {};
      notify(`Folder added · ${scan.indexed || 0} files indexed${scan.errors ? ` · ${scan.errors} errors` : ''}.`);
    } catch (err) { notify(err.message, true); }
    finally { submit.disabled = false; submit.textContent = 'Add and index folder'; }
  });

  function observeKnowledgeView() {
    const view = byId('view-knowledge');
    if (!view) return;
    const observer = new MutationObserver(() => {
      if (view.classList.contains('active')) loadSources();
    });
    observer.observe(view, {attributes:true, attributeFilter:['class']});
    if (view.classList.contains('active') || !loadedOnce) loadSources();
  }

  ensurePanel();
  observeKnowledgeView();
  window.loadHomeServerKnowledgeSources = loadSources;
})();