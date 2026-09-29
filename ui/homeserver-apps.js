(() => {
  'use strict';

  const esc = (value='') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const bytes = (value=0) => {
    let n=Number(value||0), i=0; const units=['B','KB','MB','GB','TB'];
    while(n>=1024 && i<units.length-1){ n/=1024; i++; }
    return `${n.toFixed(i===0?0:n>=10?1:2)} ${units[i]}`;
  };
  const state={data:null,filter:'all',loading:false};

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
        <div><p class="eyebrow">HOMESERVER APPS</p><h2>Installed Apps</h2><p>Manage VP3 system apps and the apps you build from the VP3 SDK.</p></div>
        <button class="button primary" type="button" data-hs-app-create>Create App</button>
      </div>
      <div class="hs-apps-toolbar">
        <button class="button secondary active" type="button" data-hs-app-filter="all">All</button>
        <button class="button secondary" type="button" data-hs-app-filter="system">VP3 System</button>
        <button class="button secondary" type="button" data-hs-app-filter="user">My Apps</button>
        <span class="muted" id="hsAppsSummary">Loading…</span>
      </div>
      <div id="hsAppsCreate" class="panel hs-app-create hidden">
        <div class="panel-head"><div><p class="eyebrow">VP3 APP SDK</p><h3>Create a user app</h3><p class="muted">Every app starts with the VP3 SDK shell, manifest, settings, Agent actions, data folders and runtime contracts.</p></div></div>
        <form id="hsAppsCreateForm">
          <div class="form-grid"><label>App name<input id="hsAppName" required maxlength="160" placeholder="Garage Inventory"></label><label>App key<input id="hsAppKey" required maxlength="80" pattern="[a-z0-9][a-z0-9._-]{1,79}" placeholder="garage.inventory"></label></div>
          <div class="form-grid"><label>Runtime<select id="hsAppRuntime"><option value="static">Static</option><option value="php">PHP</option></select></label><label>Source<select id="hsAppSource"><option value="user_created">User created</option><option value="agent_builder">Build with Agent</option></select></label></div>
          <div class="form-actions"><button class="button secondary" type="button" data-hs-app-create-cancel>Cancel</button><button class="button primary" type="submit">Create from SDK</button></div>
        </form>
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
    const canOpen=!system && app.lifecycle_state==='running' && meta.active_release_id;
    return `
      <article class="panel hs-app-card" data-hs-app-card="${esc(app.app_key)}">
        <div class="hs-app-card-head">
          <div class="hs-app-icon">${system?'VP3':'APP'}</div>
          <div><div class="hs-app-status-row"><span class="hs-app-status ${esc(app.lifecycle_state)}"></span><span>${esc(statusLabel(app))}</span></div><h3>${esc(app.name)}</h3><p>${esc(app.app_key)}</p></div>
        </div>
        <div class="hs-app-tags"><span>${system?'System App':'User App'}</span><span>${esc(app.source_type)}</span>${meta.runtime?`<span>${esc(meta.runtime)}</span>`:''}</div>
        <dl class="hs-app-meta"><div><dt>Version</dt><dd>${esc(app.installed_version||'—')}</dd></div><div><dt>SDK</dt><dd>${esc(meta.sdk_version|| (system?'VP3':'—'))}</dd></div></dl>
        <div class="hs-app-actions">
          ${canOpen?`<a class="button primary" href="/api/v1/control/homeserver-apps/${encodeURIComponent(app.app_key)}/preview/" target="_blank" rel="noreferrer">Open</a>`:''}
          <button class="button secondary" type="button" data-hs-app-details="${esc(app.app_key)}">Manage</button>
          ${!system && app.lifecycle_state==='running'? `<button class="text-button" data-hs-app-stop="${esc(app.app_key)}">Stop</button>`:''}
          ${!system && ['stopped','installed','archived'].includes(app.lifecycle_state)? `<button class="text-button" data-hs-app-resume="${esc(app.app_key)}">${app.lifecycle_state==='archived'?'Restore':'Start'}</button>`:''}
        </div>
      </article>`;
  }

  function render(){
    const grid=document.getElementById('hsAppsGrid');
    if(!grid||!state.data) return;
    const apps=(state.data.apps||[]).filter(app=>state.filter==='all'||app.app_class===state.filter);
    document.getElementById('hsAppsSummary').textContent=`${state.data.counts?.system||0} system · ${state.data.counts?.user||0} user`;
    grid.innerHTML=apps.length?apps.map(card).join(''):'<div class="panel empty-state">No Apps match this filter.</div>';
  }

  async function load(force=false){
    if(state.loading) return;
    state.loading=true;
    try{
      if(force||!state.data) state.data=await window.api('/api/v1/control/homeserver-apps');
      render();
    }catch(error){
      const grid=document.getElementById('hsAppsGrid');
      if(grid) grid.innerHTML=`<div class="panel empty-state">${esc(error.message||'Unable to load Apps.')}</div>`;
      throw error;
    }finally{state.loading=false;}
  }

  async function detail(key){
    const panel=document.getElementById('hsAppDetail');
    panel.classList.remove('hidden');
    panel.innerHTML='<div class="empty-state">Loading app details…</div>';
    try{
      const detail=await window.api(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}`);
      const app=detail.app, system=app.app_class==='system';
      let permissions=null,resources=null,runtime=null,secrets=null;
      if(!system){
        [permissions,resources,runtime,secrets]=await Promise.all([
          window.api(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/permissions`).catch(()=>null),
          window.api(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/resources`).catch(()=>null),
          window.api(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/runtime/services`).catch(()=>null),
          window.api(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/secrets`).catch(()=>null),
        ]);
      }
      const permRows=permissions?.permissions?.permissions||[];
      const r=resources?.resources;
      const rt=runtime?.runtime;
      panel.innerHTML=`
        <div class="panel-head"><div><p class="eyebrow">${system?'VP3 SYSTEM APP':'USER APP'}</p><h3>${esc(app.name)}</h3><p class="muted">${esc(app.app_key)} · ${esc(statusLabel(app))}</p></div><button class="text-button" type="button" data-hs-app-detail-close>Close</button></div>
        <div class="hs-app-detail-grid">
          <section><h4>Runtime</h4><p>Version <strong>${esc(app.installed_version||'—')}</strong></p><p>Source <strong>${esc(app.source_type)}</strong></p>${rt?`<p>Jobs <strong>${rt.jobs?.length||0}</strong> · Events <strong>${rt.event_count||0}</strong></p>`:''}</section>
          <section><h4>Permissions</h4>${system?'<p class="muted">Managed by VP3 system policy.</p>':permRows.length?permRows.map(p=>`<label class="hs-app-permission"><input type="checkbox" data-hs-app-permission="${esc(key)}" data-permission="${esc(p.permission)}" ${p.allowed?'checked':''}> ${esc(p.permission)}</label>`).join(''):'<p class="muted">No permissions declared.</p>'}</section>
          <section><h4>Resources</h4>${r?`<p>Files ${bytes(r.storage_used_bytes)} / ${bytes(r.storage_limit_bytes)}</p><p>SQLite ${bytes(r.sqlite_used_bytes)} / ${bytes(r.sqlite_limit_bytes)}</p>`:'<p class="muted">Managed by VP3.</p>'}</section>
          <section><h4>Secrets</h4>${secrets?`<p>${secrets.secrets.count} configured · values never displayed</p>`:'<p class="muted">Managed by VP3.</p>'}</section>
        </div>
        ${!system?`<div class="hs-app-detail-actions"><button class="button secondary" data-hs-app-build="${esc(key)}">Build & Install</button><button class="text-button danger" data-hs-app-archive="${esc(key)}">Archive App</button></div>`:''}
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
    const key=target.dataset.hsAppStop||target.dataset.hsAppResume||target.dataset.hsAppArchive||target.dataset.hsAppBuild;
    if(!key) return;
    try{
      if(target.dataset.hsAppStop) await post(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/lifecycle`,{state:'stopped',metadata:{reason:'owner_ui'}});
      if(target.dataset.hsAppResume) await post(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/resume`);
      if(target.dataset.hsAppBuild) await post(`/api/v1/control/homeserver-apps/${encodeURIComponent(key)}/build-install`);
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
    if(event.target.closest('[data-hs-app-create-cancel]')) document.getElementById('hsAppsCreate')?.classList.add('hidden');
    if(event.target.closest('[data-hs-app-detail-close]')) document.getElementById('hsAppDetail')?.classList.add('hidden');
    const manage=event.target.closest('[data-hs-app-details]');
    if(manage) detail(manage.dataset.hsAppDetails);
    const action=event.target.closest('[data-hs-app-stop],[data-hs-app-resume],[data-hs-app-archive],[data-hs-app-build]');
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
    if(event.target.id!=='hsAppsCreateForm') return;
    event.preventDefault();
    try{
      const result=await window.api('/api/v1/control/homeserver-apps',{
        method:'POST',
        body:JSON.stringify({
          name:document.getElementById('hsAppName').value,
          app_key:document.getElementById('hsAppKey').value,
          runtime:document.getElementById('hsAppRuntime').value,
          source_type:document.getElementById('hsAppSource').value
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
