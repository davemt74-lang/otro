(() => {
  'use strict';

  const esc = (value='') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const bytes = (value=0) => {
    let n=Number(value||0), i=0; const units=['B','KB','MB','GB','TB'];
    while(n>=1024 && i<units.length-1){ n/=1024; i++; }
    return `${n.toFixed(i===0?0:n>=10?1:2)} ${units[i]}`;
  };
  const state={data:null,manager:null,permissionCatalog:null,updateCenter:null,updateReview:null,filter:'all',loading:false,pendingSource:null,pendingDistribution:null};

  function ensureWorkspace(){
    if(document.getElementById('view-homeserver-apps')) return;
    const connected=document.querySelector('.nav-item[data-view="apps"]');
    if(connected && !document.querySelector('.nav-item[data-view="homeserver-apps"]')){
      const button=document.createElement('button');
      button.className='nav-item';
      button.dataset.view='homeserver-apps';
      button.textContent='Apps';
      connected.parentNode.insertBefore(button,connected);
    }
    const connectedView=document.getElementById('view-apps');
    if(!connectedView) return;
    const section=document.createElement('section');
    section.className='view';
    section.id='view-homeserver-apps';
    section.innerHTML=`
      <div class="section-intro split hs-apps-intro">
        <div><p class="eyebrow">HOMESERVER APPS</p><h2>App Manager</h2><p>Installed Apps, available VP3 Apps, shared apps, hosted apps, and user-created apps are managed from one place.</p></div>
        <div class="hs-app-intro-actions"><button class="button secondary" type="button" data-hs-app-import>Import App</button><button class="button primary" type="button" data-hs-app-create>Create App</button></div>
      </div>
      <section class="hs-app-manager-summary" id="hsAppManagerSummary">
        <div class="panel hs-app-kpi"><span>Installed</span><strong>—</strong></div>
        <div class="panel hs-app-kpi"><span>Available</span><strong>—</strong></div>
        <div class="panel hs-app-kpi"><span>Updates</span><strong>—</strong></div>
        <div class="panel hs-app-kpi"><span>Running</span><strong>—</strong></div>
        <div class="panel hs-app-kpi"><span>Hosted</span><strong>—</strong></div>
      </section>
      <section class="panel hs-update-center">
        <div class="panel-head"><div><p class="eyebrow">UPDATE CENTER</p><h3>Apps, updates & recovery</h3><p class="muted">Review version, permissions, data schema, rollback, hosting and Agent compatibility before anything changes.</p></div><button class="button secondary" type="button" data-hs-update-refresh>Refresh</button></div>
        <div id="hsUpdateCenterSummary" class="hs-update-summary"></div>
        <div id="hsUpdateCenterList" class="hs-update-list"><div class="empty-state">Loading Update Center…</div></div>
        <div id="hsUpdateReview" class="hs-update-review hidden"></div>
      </section>
      <div class="hs-apps-toolbar">
        <button class="button secondary active" type="button" data-hs-app-filter="all">All</button>
        <button class="button secondary" type="button" data-hs-app-filter="available">Available</button>
        <button class="button secondary" type="button" data-hs-app-filter="system">VP3 Apps</button>
        <button class="button secondary" type="button" data-hs-app-filter="user">My Apps</button>
        <button class="button secondary" type="button" data-hs-app-filter="shared">Shared</button>
        <button class="button secondary" type="button" data-hs-app-filter="updates">Updates</button>
        <button class="button secondary" type="button" data-hs-app-filter="hosted">Hosted</button>
        <span class="muted" id="hsAppsSummary">Loading…</span>
      </div>
      <div id="hsAppsCreate" class="panel hs-app-create hidden">
        <div class="panel-head"><div><p class="eyebrow">VP3 APP SDK</p><h3>Create a user app</h3><p class="muted">Every app starts with the VP3 SDK shell, manifest, settings, Agent actions, data folders and runtime contracts.</p></div></div>
        <form id="hsAppsCreateForm">
          <div class="form-grid"><label>App name<input id="hsAppName" required maxlength="160" placeholder="Garage Inventory"></label><label>App key<input id="hsAppKey" required maxlength="80" pattern="[a-z0-9][a-z0-9._-]{1,79}" placeholder="garage.inventory"></label></div>
          <div class="form-grid"><label>Runtime<select id="hsAppRuntime"><option value="static">Static</option><option value="php">PHP</option></select></label><label>Source<select id="hsAppSource"><option value="user_created">User created</option><option value="agent_builder">Build with Agent</option></select></label></div>
          <fieldset class="hs-app-permission-picker"><legend>Declared capabilities</legend><p class="muted">Choose only what this app needs. Every capability starts denied until you explicitly allow it after installation.</p><div id="hsAppPermissionChoices"><span class="muted">Loading permission catalog…</span></div></fieldset>
          <div class="form-actions"><button class="button secondary" type="button" data-hs-app-create-cancel>Cancel</button><button class="button primary" type="submit">Create from SDK</button></div>
        </form>
      </div>
      <div id="hsAppsImport" class="panel hs-app-import hidden">
        <div class="panel-head"><div><p class="eyebrow">SOURCE IMPORT</p><h3>Inspect ZIP or Git source</h3><p class="muted">Nothing installs during inspection. VP3 validates identity, runtime, manifest, permissions, paths, package size and exact source provenance first.</p></div><button class="text-button" type="button" data-hs-app-import-cancel>Close</button></div>
        <div class="hs-app-import-grid">
          <form id="hsAppsZipImportForm">
            <h4>ZIP package</h4>
            <label>VP3 app ZIP<input id="hsAppsZipFile" type="file" accept=".zip,application/zip" required></label>
            <button class="button secondary" type="submit">Inspect ZIP</button>
          </form>
          <form id="hsAppsGitImportForm">
            <h4>Git repository</h4>
            <label>HTTPS repository<input id="hsAppsGitUrl" type="url" required maxlength="1000" placeholder="https://github.com/org/app.git"></label>
            <label>Branch, tag or commit<input id="hsAppsGitRef" maxlength="160" value="HEAD" placeholder="main"></label>
            <button class="button secondary" type="submit">Inspect Git</button>
          </form>
          <form id="hsAppsDistributionImportForm">
            <h4>Private VP3 share</h4>
            <label>VP3 app bundle<input id="hsAppsDistributionFile" type="file" accept=".zip,.vp3app.zip,application/zip" required></label>
            <label>Expected package SHA-256<input id="hsAppsDistributionHash" maxlength="64" pattern="[a-fA-F0-9]{64}" placeholder="From the VP3 private-share page"></label>
            <label>Private share ID<input id="hsAppsDistributionShareId" maxlength="64" placeholder="Optional share ID from VP3 Cloud"></label>
            <button class="button secondary" type="submit">Inspect Private Bundle</button>
          </form>
        </div>
        <div id="hsAppsSourcePreview" class="hs-app-source-preview hidden"></div>
      </div>
      <div id="hsAppsGrid" class="hs-apps-grid"><div class="panel empty-state">Loading Apps…</div></div>
      <div id="hsAppDetail" class="panel hs-app-detail hidden"></div>
    `;
    connectedView.parentNode.insertBefore(section,connectedView);
  }

  function statusLabel(app){
    const value=app.lifecycle_state||'unknown';
    return value.replaceAll('_',' ');
  }

  function card(app){
    const meta=app.metadata||{};
    const system=app.app_class==='system';
    const installed=!!app.installed;
    const canOpen=app.actions?.open;
    const permissionCount=app.permissions?.declared_count||0;
    const allowedCount=app.permissions?.allowed_count||0;
    const storage=app.resources?.storage_used_bytes||0;
    const hosted=app.hosting?.bound;
    const shared=!!app.distribution;
    const status=installed?(app.lifecycle_state||'installed'):'available';
    const primary=!installed
      ? `<button class="button primary" type="button" data-hs-update-review="${esc(app.app_key)}">Review & Install</button>`
      : app.update_available
        ? `<button class="button primary" type="button" data-hs-update-review="${esc(app.app_key)}">Review Update</button>`
        : canOpen
          ? `<a class="button primary" href="/api/v1/control/homeserver-apps/${encodeURIComponent(app.app_key)}/preview/" target="_blank" rel="noreferrer">Open</a>`
          : '';
    return `
      <article class="panel hs-app-card ${installed?'is-installed':'is-available'}" data-hs-app-card="${esc(app.app_key)}">
        <div class="hs-app-card-head">
          <div class="hs-app-icon">${system?'VP3':'APP'}</div>
          <div><div class="hs-app-status-row"><span class="hs-app-status ${esc(status)}"></span><span>${esc(statusLabel({lifecycle_state:status}))}</span></div><h3>${esc(app.name)}</h3><p>${esc(app.description||app.app_key)}</p></div>
        </div>
        <div class="hs-app-tags">
          <span>${system?'VP3 Apps':'User App'}</span>
          <span>${esc(app.category||'App')}</span>
          ${shared?'<span>Private Share</span>':''}
          ${hosted?'<span>Hosted</span>':''}
          ${app.update_available?'<span>Update Available</span>':''}
        </div>
        <dl class="hs-app-meta">
          <div><dt>Installed</dt><dd>${esc(app.installed_version||'—')}</dd></div>
          <div><dt>Available</dt><dd>${esc(app.available_version||app.installed_version||'—')}</dd></div>
          <div><dt>Permissions</dt><dd>${permissionCount?`${allowedCount}/${permissionCount}`:'—'}</dd></div>
          <div><dt>Storage</dt><dd>${installed?bytes(storage):'—'}</dd></div>
        </dl>
        <div class="hs-app-actions">
          ${primary}
          ${installed?`<button class="button secondary" type="button" data-hs-app-details="${esc(app.app_key)}">Manage</button>`:''}
          ${app.actions?.rollback?`<button class="text-button" type="button" data-hs-app-rollback="${esc(app.app_key)}">Rollback</button>`:''}
        </div>
      </article>`;
  }

  function renderUpdateCenter(){
    const summary=document.getElementById('hsUpdateCenterSummary');
    const list=document.getElementById('hsUpdateCenterList');
    if(!summary||!list||!state.updateCenter) return;
    const counts=state.updateCenter.counts||{};
    summary.innerHTML=[
      ['Updates',counts.updates||0],['Available',counts.available||0],['Needs attention',counts.attention||0],
      ['Rollback ready',counts.rollback||0],['Source updates',counts.source_updates||0]
    ].map(([label,value])=>`<div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join('');
    const rows=(state.updateCenter.items||[]).filter(item=>['recover','update','review_source_update','install'].includes(item.recommended_action));
    list.innerHTML=rows.length?rows.map(item=>{
      const version=item.installed_version
        ? `${esc(item.installed_version)} → ${esc(item.available_version||'source candidate')}`
        : `Install ${esc(item.available_version||'available')}`;
      return `<article class="hs-update-row">
        <div><div class="hs-update-row-title"><strong>${esc(item.name)}</strong><span class="tag">${esc(item.recommended_action.replaceAll('_',' '))}</span></div>
        <p class="muted">${version}${item.hosting_bound?' · hosted':''}${item.agent_compatible?' · Agent compatible':''}</p></div>
        <button class="button secondary" type="button" data-hs-update-review="${esc(item.app_key)}">Review</button>
      </article>`;
    }).join(''):'<div class="empty-state">All installed apps are current and healthy.</div>';
  }

  function renderUpdateReview(review){
    state.updateReview=review||null;
    const node=document.getElementById('hsUpdateReview');
    if(!node) return;
    if(!review){node.classList.add('hidden');node.innerHTML='';return;}
    const integrity=review.integrity||{}, data=review.data||{}, rollback=review.rollback||{}, perms=review.permissions||{}, hosting=review.hosting||{}, agent=review.agent_control||{};
    const version=review.installed_version?`${review.installed_version} → ${review.available_version||'source candidate'}`:`Install ${review.available_version||'available'}`;
    const notes=(review.release_notes||[]).map(note=>`<li>${esc(note)}</li>`).join('')||'<li>No release notes supplied.</li>';
    const permissionChange=perms.changed
      ? `<div class="system-app-warning"><strong>Permission impact.</strong> Added: ${esc((perms.added||[]).join(', ')||'none')} · Removed: ${esc((perms.removed||[]).join(', ')||'none')}. New permissions default denied.</div>`
      : '<p class="muted">No permission expansion is declared for this reviewed package.</p>';
    const schema=data.migration_required
      ? `<div class="system-app-warning"><strong>Data schema change.</strong> ${esc(data.current_schema_version)} → ${esc(data.target_schema_version)} · ${data.migration_reversible?'reversible':'not reversible'}.</div>`
      : '<p class="muted">No app-data schema migration is required.</p>';
    let action='';
    if(review.actions?.apply_prebuilt){
      action=`<button class="button primary" type="button" data-hs-update-apply="${esc(review.app_key)}" data-version="${esc(review.available_version||'')}" data-sha256="${esc(integrity.package_sha256||'')}">${review.installed?'Approve & Update':'Approve & Install'}</button>`;
    }else if(review.actions?.review_source_update){
      action=`<button class="button primary" type="button" data-hs-app-details="${esc(review.app_key)}">Open source review</button>`;
    }else if(review.actions?.recover){
      action=`<button class="button primary" type="button" data-hs-app-recover="${esc(review.app_key)}">Recover app</button>`;
    }
    node.classList.remove('hidden');
    node.innerHTML=`
      <div class="panel-head"><div><p class="eyebrow">PREFLIGHT REVIEW</p><h3>${esc(review.name)}</h3><p class="muted">${version} · ${esc(review.release_channel||'local source')}</p></div><button class="text-button" type="button" data-hs-update-close>Close</button></div>
      <div class="hs-update-review-grid">
        <div><span>Recommended</span><strong>${esc(review.recommended_action.replaceAll('_',' '))}</strong></div>
        <div><span>Compatibility</span><strong>${esc((review.compatibility?.min_homeserver_version?'HomeServer ≥ '+review.compatibility.min_homeserver_version:'Current HomeServer'))}</strong></div>
        <div><span>Integrity</span><strong>${esc(integrity.trust||'source review')}</strong><code>${esc((integrity.package_sha256||'').slice(0,16))}${integrity.package_sha256?'…':''}</code></div>
        <div><span>Rollback</span><strong>${rollback.available?(rollback.safe?'Available':'Blocked'):'Not available'}</strong></div>
        <div><span>Hosting</span><strong>${hosting.bound?'Preserved · '+hosting.count+' binding(s)':'Not hosted'}</strong></div>
        <div><span>Agent control</span><strong>${agent.compatible?'Compatible':'Review compatibility'}</strong></div>
      </div>
      <section><h4>Release notes</h4><ul class="hs-update-notes">${notes}</ul></section>
      ${permissionChange}
      ${schema}
      <p class="muted">Automatic updates are disabled. VP3 installs/updates are pinned to the reviewed version and SHA-256. Write actions still require owner approval.</p>
      <div class="form-actions">${action}${rollback.available&&rollback.safe?`<button class="button secondary" type="button" data-hs-app-rollback="${esc(review.app_key)}">Rollback</button>`:''}</div>`;
  }

  async function showUpdateReview(key){
    const result=await window.api('/api/v1/control/homeserver-apps/update-center/'+encodeURIComponent(key));
    renderUpdateReview(result.review);
    document.getElementById('hsUpdateReview')?.scrollIntoView({behavior:'smooth',block:'nearest'});
  }

  function renderPermissionChoices(){
    const node=document.getElementById('hsAppPermissionChoices');
    if(!node) return;
    const rows=state.permissionCatalog?.permissions||[];
    node.innerHTML=rows.length?rows.map(p=>`
      <label class="hs-app-permission">
        <input type="checkbox" data-hs-create-permission value="${esc(p.permission)}">
        <span><strong>${esc(p.permission)}</strong><small>${esc(p.risk||'unknown')} risk · ${esc(p.description||'')}</small></span>
      </label>`).join(''):'<span class="muted">No optional capabilities are available.</span>';
  }

  function render(){
    const grid=document.getElementById('hsAppsGrid');
    if(!grid||!state.manager) return;
    const counts=state.manager.counts||{};
    const items=(state.manager.items||[]).filter(app=>{
      if(state.filter==='all') return true;
      if(state.filter==='available') return !app.installed&&app.available;
      if(state.filter==='system') return app.app_class==='system';
      if(state.filter==='user') return app.app_class==='user';
      if(state.filter==='shared') return !!app.distribution;
      if(state.filter==='updates') return !!app.update_available;
      if(state.filter==='hosted') return !!app.hosting?.bound;
      return true;
    });
    document.getElementById('hsAppsSummary').textContent=`${counts.installed||0} installed · ${counts.available||0} available · ${counts.updates||0} updates`;
    const summary=document.getElementById('hsAppManagerSummary');
    if(summary){
      const values=[counts.installed||0,counts.available||0,counts.updates||0,counts.running||0,counts.hosted||0];
      summary.querySelectorAll('strong').forEach((node,i)=>node.textContent=String(values[i]??0));
    }
    grid.innerHTML=items.length?items.map(card).join(''):'<div class="panel empty-state">No Apps match this filter.</div>';
    renderPermissionChoices();
  }

  async function load(force=false){
    if(state.loading) return;
    state.loading=true;
    try{
      if(force||!state.manager||!state.permissionCatalog||!state.updateCenter){
        const loaded=await Promise.all([
          window.api('/api/v1/control/homeserver-apps/manager'),
          window.api('/api/v1/control/homeserver-apps/permissions/catalog'),
          window.api('/api/v1/control/homeserver-apps/update-center')
        ]);
        state.manager=loaded[0];
        state.data={
          apps:(loaded[0].items||[]).filter(x=>x.installed).map(x=>({
            app_key:x.app_key,name:x.name,app_class:x.app_class,lifecycle_state:x.lifecycle_state,
            installed_version:x.installed_version,source_type:x.source_type,metadata:x.metadata||{}
          })),
          counts:{system:loaded[0].counts?.system||0,user:loaded[0].counts?.user||0}
        };
        state.permissionCatalog=loaded[1];
        state.updateCenter=loaded[2];
      }
      render();
      renderUpdateCenter();
    }catch(error){
      const grid=document.getElementById('hsAppsGrid');
      if(grid) grid.innerHTML=`<div class="panel empty-state">${esc(error.message||'Unable to load Apps.')}</div>`;
      throw error;
    }finally{state.loading=false;}
  }

  function renderSourcePreview(source){
    state.pendingSource=source||null;
    const node=document.getElementById('hsAppsSourcePreview');
    if(!node) return;
    if(!source){node.classList.add('hidden');node.innerHTML='';return;}
    const manifest=source.manifest||{};
    const validation=source.validation||{};
    const permissions=manifest.permissions||[];
    node.classList.remove('hidden');
    node.innerHTML=`
      <div class="panel-head"><div><p class="eyebrow">VALIDATED SOURCE</p><h3>${esc(manifest.name||manifest.app_key||'App')}</h3><p class="muted">${esc(manifest.app_key||'')} · v${esc(manifest.version||'—')} · ${esc(source.source_type)}</p></div><span class="hs-source-ready">Ready for approval</span></div>
      <div class="hs-app-source-grid">
        <div><span>Runtime</span><strong>${esc(manifest.runtime||'—')}</strong></div>
        <div><span>Files</span><strong>${esc(validation.file_count||0)}</strong></div>
        <div><span>Package</span><strong>${bytes(validation.compressed_bytes||0)}</strong></div>
        <div><span>SHA-256</span><code>${esc(source.package_sha256||'')}</code></div>
        ${source.source_revision?`<div class="wide"><span>Exact Git commit</span><code>${esc(source.source_revision)}</code></div>`:''}
        <div class="wide"><span>Source</span><code>${esc(source.source_ref||'')}</code></div>
      </div>
      <div class="hs-app-source-permissions"><strong>Declared permissions</strong>${permissions.length?permissions.map(p=>`<span>${esc(p)}</span>`).join(''):'<span>None</span>'}</div>
      <div class="form-actions"><button class="button primary" type="button" data-hs-source-install="${esc(source.source_id)}">Approve & Install</button></div>
    `;
  }

  async function inspectZip(form){
    const file=document.getElementById('hsAppsZipFile')?.files?.[0];
    if(!file) throw new Error('Choose a ZIP package.');
    const body=new FormData(); body.append('file',file);
    const result=await window.api('/api/v1/control/homeserver-apps/sources/zip/inspect',{method:'POST',body});
    renderSourcePreview(result.source);
  }

  async function inspectDistribution(){
    const file=document.getElementById('hsAppsDistributionFile')?.files?.[0];
    if(!file) throw new Error('Choose a VP3 app distribution bundle.');
    const expected=(document.getElementById('hsAppsDistributionHash')?.value||'').trim().toLowerCase();
    if(expected && !/^[a-f0-9]{64}$/.test(expected)) throw new Error('Expected package SHA-256 must be 64 hexadecimal characters.');
    const shareId=(document.getElementById('hsAppsDistributionShareId')?.value||'').trim();
    const body=new FormData(); body.append('file',file);
    const query=new URLSearchParams(); if(expected)query.set('expected_package_sha256',expected);
    const result=await window.api('/api/v1/control/homeserver-apps/distribution/inspect?'+query.toString(),{method:'POST',body});
    const review=result.distribution||{}, descriptor=review.descriptor||{};
    if(expected && descriptor.package_sha256!==expected) throw new Error('This bundle does not match the private-share package hash.');
    state.pendingDistribution={file,expected,shareId,distribution:review};
    const node=document.getElementById('hsAppsSourcePreview');
    node.classList.remove('hidden');
    const pd=review.permission_delta||{}, sc=review.schema_change||{}, vc=review.version_change||{};
    node.innerHTML=`
      <div class="panel-head"><div><p class="eyebrow">${review.update?'PRIVATE UPDATE':'PRIVATE DISTRIBUTION'}</p><h3>${esc(descriptor.name||descriptor.app_key||'App')}</h3><p class="muted">${esc(descriptor.app_key||'')} · ${review.update?esc(vc.from||'—')+' → ':''}v${esc(descriptor.version||'—')}</p></div><span class="hs-source-ready">Integrity verified</span></div>
      <div class="hs-app-source-grid">
        <div><span>Runtime</span><strong>${esc(descriptor.runtime||'—')}</strong></div>
        <div><span>Files</span><strong>${esc(descriptor.file_count||0)}</strong></div>
        <div><span>Package</span><strong>${bytes(descriptor.compressed_bytes||0)}</strong></div>
        <div><span>SHA-256</span><code>${esc(descriptor.package_sha256||'')}</code></div>
        <div class="wide"><span>Publisher fingerprint</span><code>${esc(descriptor.publisher_fingerprint||'')}</code></div>
        <div><span>New permissions</span><strong>${esc((pd.added||[]).length)}</strong></div>
        <div><span>High-risk additions</span><strong>${esc((pd.high_risk_added_details||[]).length)}</strong></div>
        <div><span>Data schema</span><strong>${esc(sc.from||'1')} → ${esc(sc.to||'1')}</strong></div>
        <div><span>Automatic update</span><strong>No</strong></div>
      </div>
      ${(pd.added||[]).length?`<div class="system-app-warning"><strong>Permission review required.</strong> New permissions default denied after install: ${(pd.added||[]).map(x=>esc(x.permission||x)).join(', ')}</div>`:''}
      ${sc.changed?`<div class="system-app-warning"><strong>Data schema change.</strong> HomeServer will use the existing snapshot/recovery lifecycle before activation.</div>`:''}
      <p class="muted">Publisher continuity is verified. App data, secrets, and ownership are not transferred. Existing hosting bindings remain attached to the same app identity.</p>
      <div class="form-actions"><button class="button primary" type="button" data-hs-distribution-install>Approve & ${review.update?'Update':'Install'} Private App</button></div>`;
  }

  async function inspectGit(){
    const result=await window.api('/api/v1/control/homeserver-apps/sources/git/inspect',{
      method:'POST',
      body:JSON.stringify({
        repo_url:document.getElementById('hsAppsGitUrl').value,
        ref:document.getElementById('hsAppsGitRef').value||'HEAD'
      })
    });
    renderSourcePreview(result.source);
  }

  function workspaceTree(files,key){
    const rows=(files||[]).filter(item=>item.type==='file');
    if(!rows.length) return '<p class="muted">No editable project files.</p>';
    return rows.map(item=>`<button class="hs-workspace-file ${item.editable?'':'disabled'}" type="button" data-hs-workspace-open="${esc(key)}" data-path="${esc(item.path)}" ${item.editable?'':'disabled'}><span>${esc(item.path)}</span><small>${item.protected?'protected · ':''}${bytes(item.bytes||0)}</small></button>`).join('');
  }

  function workspacePanel(workspace,key){
    const validation=workspace?.validation;
    const releases=workspace?.releases?.releases||[];
    const permissions=workspace?.permissions;
    return `
      <section class="hs-dev-workspace" data-hs-workspace="${esc(key)}">
        <div class="panel-head"><div><p class="eyebrow">DEVELOPMENT WORKSPACE</p><h3>Build ${esc(workspace?.app?.name||key)}</h3><p class="muted">Edit source, validate, preview, build, and release from the canonical HomeServer app project.</p></div><div class="hs-app-intro-actions"><button class="button secondary" type="button" data-hs-workspace-validate="${esc(key)}">Validate</button><a class="button secondary" href="${esc(workspace?.preview_url||'#')}" target="_blank" rel="noreferrer">Preview</a><button class="button primary" type="button" data-hs-app-build="${esc(key)}">Build & Install</button></div></div>
        <div class="hs-workspace-status">
          <span class="${validation?'ok':'warn'}">${validation?'Project valid':'Needs attention'}</span>
          <span>Releases ${releases.length}</span>
          <span>Permissions ${permissions?.allowed_count||0}/${permissions?.declared_count||0}</span>
          ${validation?.package?.sha256?`<code>${esc(validation.package.sha256.slice(0,16))}…</code>`:''}
        </div>
        ${workspace?.validation_error?`<div class="system-app-error">${esc(workspace.validation_error)}</div>`:''}
        <div class="hs-workspace-layout">
          <aside class="hs-workspace-files"><div class="hs-workspace-files-head"><strong>Project files</strong><button class="text-button" type="button" data-hs-workspace-new="${esc(key)}">+ File</button></div>${workspaceTree(workspace?.project?.files,key)}</aside>
          <section class="hs-workspace-editor">
            <div class="hs-workspace-editor-empty"><strong>Select a source file</strong><p class="muted">Persistent app data is intentionally separate and never appears in this editor.</p></div>
            <div class="hs-workspace-editor-active hidden">
              <div class="hs-workspace-editor-head"><code data-hs-workspace-current></code><div><button class="text-button" type="button" data-hs-workspace-rename="${esc(key)}">Rename</button><button class="text-button danger" type="button" data-hs-workspace-delete="${esc(key)}">Delete</button></div></div>
              <textarea class="hs-workspace-textarea" spellcheck="false"></textarea>
              <div class="form-actions"><span class="muted" data-hs-workspace-save-status></span><button class="button primary" type="button" data-hs-workspace-save="${esc(key)}">Save & Validate</button></div>
            </div>
          </section>
        </div>
      </section>`;
  }

  async function loadWorkspace(key){
    return window.api(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/workspace`);
  }

  async function openWorkspaceFile(key,path){
    const panel=document.querySelector(`[data-hs-workspace="${CSS.escape(key)}"]`);
    if(!panel) return;
    const result=await window.api(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/workspace/file?path=${encodeURIComponent(path)}`);
    panel.querySelector('.hs-workspace-editor-empty')?.classList.add('hidden');
    panel.querySelector('.hs-workspace-editor-active')?.classList.remove('hidden');
    panel.querySelector('[data-hs-workspace-current]').textContent=result.path;
    panel.querySelector('.hs-workspace-textarea').value=result.content||'';
    panel.querySelector('[data-hs-workspace-save-status]').textContent=result.protected?'App identity is protected on save.':'';
  }

  async function detail(key){
    const panel=document.getElementById('hsAppDetail');
    panel.classList.remove('hidden');
    panel.innerHTML='<div class="empty-state">Loading app details…</div>';
    try{
      const detail=await window.api(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}`);
      const app=detail.app, system=app.app_class==='system';
      let permissions=null,resources=null,runtime=null,secrets=null,releases=null,source=null,workspace=null,distribution=null;
      if(!system || (app.metadata||{}).prebuilt_app){
        [permissions,resources,runtime,secrets,releases,source,workspace,distribution]=await Promise.all([
          window.api(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/permissions`).catch(()=>null),
          window.api(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/resources`).catch(()=>null),
          window.api(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/runtime/services`).catch(()=>null),
          window.api(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/secrets`).catch(()=>null),
          window.api(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/releases`).catch(()=>null),
          window.api(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/source`).catch(()=>null),
          !system&&["user_created","agent_builder"].includes(app.source_type)?loadWorkspace(key).catch(()=>null):Promise.resolve(null),
          !system?window.api(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/distribution`).catch(()=>null):Promise.resolve(null),
        ]);
      }
      const permRows=permissions?.permissions?.permissions||[];
      const r=resources?.resources;
      const rt=runtime?.runtime;
      const releaseRows=releases?.releases||[];
      panel.innerHTML=`
        <div class="panel-head"><div><p class="eyebrow">${system?'VP3 APP':'USER APP'}</p><h3>${esc(app.name)}</h3><p class="muted">${esc(app.app_key)} · ${esc(statusLabel(app))}</p></div><button class="text-button" type="button" data-hs-app-detail-close>Close</button></div>
        <div class="hs-app-detail-grid">
          <section><h4>Runtime</h4><p>Version <strong>${esc(app.installed_version||'—')}</strong></p><p>Source <strong>${esc(app.source_type)}</strong></p>${rt?`<p>Jobs <strong>${rt.jobs?.length||0}</strong> · Events <strong>${rt.event_count||0}</strong></p>`:''}</section>
          <section><h4>Permissions</h4>${permRows.length?permRows.map(p=>`<label class="hs-app-permission"><input type="checkbox" data-hs-app-permission="${esc(key)}" data-permission="${esc(p.permission)}" ${p.allowed?'checked':''}> ${esc(p.permission)} <span class="muted">· ${esc(p.risk||'unknown')} risk</span></label>`).join(''):'<p class="muted">No permissions declared.</p>'}</section>
          <section><h4>Resources</h4>${r?`<p>Files ${bytes(r.storage_used_bytes)} / ${bytes(r.storage_limit_bytes)}</p><p>SQLite ${bytes(r.sqlite_used_bytes)} / ${bytes(r.sqlite_limit_bytes)}</p>`:'<p class="muted">Managed by VP3.</p>'}</section>
          <section><h4>Secrets</h4>${secrets?`<p>${secrets.secrets.count} configured · values never displayed</p>`:'<p class="muted">Managed by VP3.</p>'}</section>
          ${!system?`<section><h4>Distribution</h4>${distribution?.distribution?`<p><strong>Private share ready</strong></p><p class="muted">v${esc(distribution.distribution.version||"—")} · ${bytes(distribution.distribution.compressed_bytes||0)}</p><code>${esc((distribution.distribution.package_sha256||"").slice(0,20))}…</code><p class="muted">App data and secrets are excluded.</p>`:`<p class="muted">Build or validate the app before distribution.</p>`}</section><section><h4>Source</h4>${source?.source?.current?`<p><strong>${esc(source.source.current.source_type)}</strong> · ${source.source.update_available?'Update inspected':'Current'}</p><p class="muted">${esc(source.source.current.source_ref||'')}</p>${source.source.current.source_revision?`<code>${esc(source.source.current.source_revision)}</code>`:''}`:'<p class="muted">Local SDK / no attached external source.</p>'}</section>`:''}
        </div>
        ${!system?`<div class="hs-app-detail-actions"><button class="button secondary" data-hs-app-build="${esc(key)}">Build & Install</button><a class="button secondary" href="/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/distribution/export">Export App</a>${source?.source?.current?.source_type==='git'?`<button class="button secondary" data-hs-source-refresh="${esc(key)}">Check Git Update</button>`:''}${source?.source?.current?`<button class="text-button" data-hs-source-detach="${esc(key)}">Detach Source</button>`:''}${releaseRows.some(x=>x.previous)?`<button class="button secondary" data-hs-app-rollback="${esc(key)}">Rollback</button>`:''}${['failed','degraded'].includes(app.lifecycle_state)?`<button class="button secondary" data-hs-app-recover="${esc(key)}">Recover</button>`:''}<button class="text-button danger" data-hs-app-archive="${esc(key)}">Archive App</button></div>`:''}
        ${!system?`<div class="hs-app-history"><h4>Releases</h4>${releaseRows.slice(0,6).map(rel=>`<div><strong>v${esc(rel.version||'—')} ${rel.active?'· Active':''}</strong><span>${esc(rel.release_id)}</span>${!rel.active?`<button class="text-button" data-hs-app-promote="${esc(key)}" data-release-id="${esc(rel.release_id)}">Promote</button>`:''}</div>`).join('')||'<p class="muted">No releases yet.</p>'}</div>`:''}
        ${workspace?workspacePanel(workspace,key):""}
        <div class="hs-app-history"><h4>Recent activity</h4>${(detail.history||[]).slice(0,8).map(e=>`<div><strong>${esc(e.event_type)}</strong><span>${esc(e.created_at||'')}</span></div>`).join('')||'<p class="muted">No activity yet.</p>'}</div>
      `;
    }catch(error){panel.innerHTML=`<div class="empty-state">${esc(error.message)}</div>`;}
  }

  async function post(url,body){
    const options={method:'POST'};
    if(body!==undefined){options.body=JSON.stringify(body);}
    return window.api(url,options);
  }

  async function act(target){
    const key=target.dataset.hsAppStop||target.dataset.hsAppResume||target.dataset.hsAppArchive||target.dataset.hsAppBuild||target.dataset.hsAppRollback||target.dataset.hsAppRecover||target.dataset.hsAppPromote;
    if(!key) return;
    try{
      if(target.dataset.hsAppStop) await post(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/lifecycle`,{state:'stopped',metadata:{reason:'owner_ui'}});
      if(target.dataset.hsAppResume) await post(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/resume`);
      if(target.dataset.hsAppBuild) await post(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/build-install`);
      if(target.dataset.hsAppRollback) await post(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/rollback`);
      if(target.dataset.hsAppRecover) await post(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/recover`);
      if(target.dataset.hsAppPromote) await post(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/releases/${encodeURIComponent(target.dataset.releaseId)}/promote`);
      if(target.dataset.hsAppArchive){
        if(!confirm('Archive this app? The app will stop, but its project and data will be preserved.')) return;
        await post(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/archive`);
      }
      await load(true);
      await detail(key);
      window.flash('App updated.');
    }catch(error){window.flash(error.message||'App action failed.',true);}
  }

  ensureWorkspace();
  window.loadHomeServerApps=load;

  document.addEventListener('click',(event)=>{
    const filter=event.target.closest('[data-hs-app-filter]');
    if(filter){
      state.filter=filter.dataset.hsAppFilter;
      document.querySelectorAll('[data-hs-app-filter]').forEach(n=>n.classList.toggle('active',n===filter));
      render(); return;
    }
    if(event.target.closest('[data-hs-app-create]')) document.getElementById('hsAppsCreate')?.classList.remove('hidden');
    if(event.target.closest('[data-hs-app-import]')) document.getElementById('hsAppsImport')?.classList.remove('hidden');
    if(event.target.closest('[data-hs-app-create-cancel]')) document.getElementById('hsAppsCreate')?.classList.add('hidden');
    if(event.target.closest('[data-hs-app-import-cancel]')){document.getElementById('hsAppsImport')?.classList.add('hidden');renderSourcePreview(null);}
    if(event.target.closest('[data-hs-app-detail-close]')) document.getElementById('hsAppDetail')?.classList.add('hidden');
    const manage=event.target.closest('[data-hs-app-details]');
    if(manage) detail(manage.dataset.hsAppDetails);
    if(event.target.closest('[data-hs-update-refresh]')){load(true).catch(error=>window.flash(error.message,true));return;}
    if(event.target.closest('[data-hs-update-close]')){renderUpdateReview(null);return;}
    const reviewButton=event.target.closest('[data-hs-update-review]');
    if(reviewButton){showUpdateReview(reviewButton.dataset.hsUpdateReview).catch(error=>window.flash(error.message||'Unable to review app update.',true));return;}
    const applyButton=event.target.closest('[data-hs-update-apply]');
    if(applyButton){
      const key=applyButton.dataset.hsUpdateApply, version=applyButton.dataset.version, sha256=applyButton.dataset.sha256;
      if(!confirm(`Apply the reviewed ${version} package for ${key}? HomeServer will verify the pinned SHA-256 before activation.`)) return;
      applyButton.disabled=true;
      window.api('/api/v1/control/homeserver-apps/update-center/'+encodeURIComponent(key)+'/apply',{
        method:'POST',body:JSON.stringify({expected_version:version,expected_sha256:sha256})
      }).then(async()=>{renderUpdateReview(null);await load(true);window.flash('Reviewed VP3 app change applied and verified.');})
        .catch(error=>window.flash(error.message||'Reviewed app change failed.',true))
        .finally(()=>{applyButton.disabled=false;});
      return;
    }
    const distributionInstall=event.target.closest('[data-hs-distribution-install]');
    if(distributionInstall){
      const pending=state.pendingDistribution;
      if(!pending?.file) return;
      if(!confirm('Install this verified private app bundle? The package hash will be checked again before activation.')) return;
      const body=new FormData(); body.append('file',pending.file);
      const query=new URLSearchParams({approved:'true'});
      if(pending.expected) query.set('expected_package_sha256',pending.expected);
      if(pending.shareId) query.set('share_public_id',pending.shareId);
      distributionInstall.disabled=true;
      window.api('/api/v1/control/homeserver-apps/distribution/install?'+query.toString(),{method:'POST',body})
        .then(async result=>{state.pendingDistribution=null;renderSourcePreview(null);document.getElementById('hsAppsImport')?.classList.add('hidden');await load(true);await detail(result.distribution.descriptor.app_key);window.flash('Private app installed from verified distribution bundle.');})
        .catch(error=>window.flash(error.message||'Private app install failed.',true))
        .finally(()=>{distributionInstall.disabled=false;});
      return;
    }
    const sourceInstall=event.target.closest('[data-hs-source-install]');
    if(sourceInstall){
      if(!confirm('Install this validated app source? This creates or updates the user app and activates a new release.')) return;
      sourceInstall.disabled=true;
      post('/api/v1/control/homeserver-apps/sources/'+encodeURIComponent(sourceInstall.dataset.hsSourceInstall)+'/install',{approved:true})
        .then(async result=>{renderSourcePreview(null);document.getElementById('hsAppsImport')?.classList.add('hidden');await load(true);await detail(result.app.app_key);window.flash('Imported app installed.');})
        .catch(error=>window.flash(error.message||'Source install failed.',true))
        .finally(()=>{sourceInstall.disabled=false;});
      return;
    }
    const sourceRefresh=event.target.closest('[data-hs-source-refresh]');
    if(sourceRefresh){
      post('/api/v1/control/homeserver-apps/'+encodeURIComponent(sourceRefresh.dataset.hsSourceRefresh)+'/source/refresh',{ref:''})
        .then(result=>{renderSourcePreview(result.source);document.getElementById('hsAppsImport')?.classList.remove('hidden');window.flash('Git source inspected. Review before installing.');})
        .catch(error=>window.flash(error.message||'Git refresh failed.',true));
      return;
    }
    const sourceDetach=event.target.closest('[data-hs-source-detach]');
    if(sourceDetach){
      if(!confirm('Detach this source? The currently installed release stays active.')) return;
      post('/api/v1/control/homeserver-apps/'+encodeURIComponent(sourceDetach.dataset.hsSourceDetach)+'/source/detach',{confirmed:true})
        .then(async()=>{await load(true);await detail(sourceDetach.dataset.hsSourceDetach);window.flash('Source detached.');})
        .catch(error=>window.flash(error.message||'Source detach failed.',true));
      return;
    }
    const wsOpen=event.target.closest('[data-hs-workspace-open]');
    if(wsOpen){openWorkspaceFile(wsOpen.dataset.hsWorkspaceOpen,wsOpen.dataset.path).catch(error=>window.flash(error.message,true));return;}
    const wsValidate=event.target.closest('[data-hs-workspace-validate]');
    if(wsValidate){
      post('/api/v1/control/homeserver-apps/'+encodeURIComponent(wsValidate.dataset.hsWorkspaceValidate)+'/workspace/validate')
        .then(async()=>{await detail(wsValidate.dataset.hsWorkspaceValidate);window.flash('Project validation passed.');})
        .catch(error=>window.flash(error.message||'Project validation failed.',true));return;
    }
    const wsSave=event.target.closest('[data-hs-workspace-save]');
    if(wsSave){
      const key=wsSave.dataset.hsWorkspaceSave;
      const panel=event.target.closest('[data-hs-workspace]');
      const path=panel?.querySelector('[data-hs-workspace-current]')?.textContent||'';
      const content=panel?.querySelector('.hs-workspace-textarea')?.value||'';
      window.api('/api/v1/control/homeserver-apps/'+encodeURIComponent(key)+'/workspace/file',{method:'PUT',body:JSON.stringify({path,content})})
        .then(async()=>{await detail(key);await openWorkspaceFile(key,path);window.flash('Source saved and package validated.');})
        .catch(error=>window.flash(error.message||'Source save failed.',true));return;
    }
    const wsNew=event.target.closest('[data-hs-workspace-new]');
    if(wsNew){
      const path=prompt('New project file path (for example pages/about.html)');
      if(!path)return;
      window.api('/api/v1/control/homeserver-apps/'+encodeURIComponent(wsNew.dataset.hsWorkspaceNew)+'/workspace/file',{method:'PUT',body:JSON.stringify({path,content:''})})
        .then(async()=>{await detail(wsNew.dataset.hsWorkspaceNew);await openWorkspaceFile(wsNew.dataset.hsWorkspaceNew,path);window.flash('Project file created.');})
        .catch(error=>window.flash(error.message||'File creation failed.',true));return;
    }
    const wsRename=event.target.closest('[data-hs-workspace-rename]');
    if(wsRename){
      const key=wsRename.dataset.hsWorkspaceRename,panel=event.target.closest('[data-hs-workspace]');
      const path=panel?.querySelector('[data-hs-workspace-current]')?.textContent||'';
      const next=prompt('Rename project file',path);
      if(!next||next===path)return;
      post('/api/v1/control/homeserver-apps/'+encodeURIComponent(key)+'/workspace/rename',{path,new_path:next})
        .then(async()=>{await detail(key);await openWorkspaceFile(key,next);window.flash('Project file renamed.');})
        .catch(error=>window.flash(error.message||'Rename failed.',true));return;
    }
    const wsDelete=event.target.closest('[data-hs-workspace-delete]');
    if(wsDelete){
      const key=wsDelete.dataset.hsWorkspaceDelete,panel=event.target.closest('[data-hs-workspace]');
      const path=panel?.querySelector('[data-hs-workspace-current]')?.textContent||'';
      if(!path||!confirm('Delete '+path+'?'))return;
      window.api('/api/v1/control/homeserver-apps/'+encodeURIComponent(key)+'/workspace/file?path='+encodeURIComponent(path),{method:'DELETE'})
        .then(async()=>{await detail(key);window.flash('Project file deleted.');})
        .catch(error=>window.flash(error.message||'Delete failed.',true));return;
    }
    const action=event.target.closest('[data-hs-app-stop],[data-hs-app-resume],[data-hs-app-archive],[data-hs-app-build],[data-hs-app-rollback],[data-hs-app-recover],[data-hs-app-promote]');
    if(action) act(action);
  });

  document.addEventListener('change',async(event)=>{
    const input=event.target.closest('[data-hs-app-permission]');
    if(!input) return;
    try{
      await window.api(`/api/v1/control/homeserver-apps/${encodeURIComponent(input.dataset.hsAppPermission)}/permissions`,{
        method:'PUT',body:JSON.stringify({permission:input.dataset.permission,allowed:input.checked})
      });
      window.flash('App permission updated.');
    }catch(error){input.checked=!input.checked;window.flash(error.message,true);}
  });

  document.addEventListener('submit',async(event)=>{
    if(event.target.id==='hsAppsZipImportForm'){
      event.preventDefault();
      try{await inspectZip(event.target);window.flash('ZIP validated. Review before installing.');}
      catch(error){window.flash(error.message||'ZIP inspection failed.',true);}
      return;
    }
    if(event.target.id==='hsAppsGitImportForm'){
      event.preventDefault();
      try{await inspectGit();window.flash('Git source validated. Review before installing.');}
      catch(error){window.flash(error.message||'Git inspection failed.',true);}
      return;
    }
    if(event.target.id==='hsAppsDistributionImportForm'){
      event.preventDefault();
      try{await inspectDistribution();window.flash('Private app bundle verified. Review before installing.');}
      catch(error){window.flash(error.message||'Private app inspection failed.',true);}
      return;
    }
    if(event.target.id!=='hsAppsCreateForm') return;
    event.preventDefault();
    try{
      const result=await window.api('/api/v1/control/homeserver-apps',{
        method:'POST',
        body:JSON.stringify({
          name:document.getElementById('hsAppName').value,
          app_key:document.getElementById('hsAppKey').value,
          runtime:document.getElementById('hsAppRuntime').value,
          source_type:document.getElementById('hsAppSource').value,
          permissions:Array.from(document.querySelectorAll('[data-hs-create-permission]:checked')).map(n=>n.value)
        })
      });
      event.target.reset();
      document.getElementById('hsAppsCreate').classList.add('hidden');
      await load(true);
      await detail(result.app.app_key);
      window.flash('App created from the VP3 SDK.');
    }catch(error){window.flash(error.message,true);}
  });
})();
