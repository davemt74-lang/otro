(() => {
  'use strict';

  const BUILD='homeserver-universal-shell-v220-20260924';
  const byId=id=>document.getElementById(id);
  const esc=(value='')=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const page=()=>{
    const path=location.pathname.replace(/\/+$/,'')||'/';
    if(path==='/tasks')return 'tasks';
    if(path==='/system')return 'system';
    if(path==='/remote')return 'remote';
    return 'home';
  };
  const pageTitle=()=>({
    tasks:['Tasks & Notifications','Local Agent workflow'],
    system:['Setup & Diagnostics','HomeServer system'],
    remote:['VP3 Cloud','Pairing & connection'],
    home:['Agent Chat','HomeServer'],
  }[page()]||['HomeServer','Private Agent runtime']);

  async function json(path){
    const response=await fetch(path,{credentials:'same-origin',cache:'no-store'});
    let data={};try{data=await response.json();}catch(_error){}
    if(!response.ok)throw new Error(data?.detail||data?.error||`Request failed (${response.status})`);
    return data;
  }

  function navLink(href,label,icon,key){
    const active=page()===key?' active':'';
    return `<a class="${active.trim()}" href="${href}"><span class="hs-v220-nav-icon" aria-hidden="true">${icon}</span><span>${label}</span></a>`;
  }

  function sidebarMarkup(){
    return `
      <aside class="hs-v220-sidebar" aria-label="HomeServer navigation">
        <div class="hs-v220-brand-row">
          <a class="hs-v220-brand" href="/#chat" aria-label="VP3 HomeServer Agent Chat">
            <span class="hs-v220-brand-mark">VP3</span>
            <span class="hs-v220-brand-copy"><strong>VP3</strong><small>HomeServer</small></span>
          </a>
          <a class="hs-v220-cloud-pill" id="hsV220CloudPill" href="/remote" data-state="checking">Cloud</a>
        </div>
        <nav class="hs-v220-nav">
          <div class="hs-v220-nav-label">Agent</div>
          ${navLink('/#chat','Agent Chat','✦','home')}
          ${navLink('/#agent','Agent Brain','◈','agent')}
          <div class="hs-v220-nav-label">Workspace</div>
          ${navLink('/#approvals','Approvals','✓','approvals')}
          ${navLink('/#knowledge','Knowledge','◇','knowledge')}
          ${navLink('/#memory','Memory','◌','memory')}
          ${navLink('/#contacts','Contacts','○','contacts')}
          ${navLink('/tasks','Tasks & Notifications','□','tasks')}
          ${navLink('/#apps','Connected Apps','↔','apps')}
          ${navLink('/#automation','Rooms & Devices','⌂','automation')}
          <div class="hs-v220-nav-label">System</div>
          ${navLink('/remote','VP3 Cloud','●','remote')}
          ${navLink('/system','Setup & Diagnostics','⚙','system')}
          <a href="/#activity"><span class="hs-v220-nav-icon">≡</span><span>Activity</span></a>
        </nav>
        <div class="hs-v220-sidebar-spacer"></div>
        <div class="hs-v220-runtime">
          <strong>HomeServer <span id="hsV220Version">v2.2</span></strong>
          <span id="hsV220Runtime">Local runtime · checking Cloud…</span>
        </div>
      </aside>`;
  }

  function wrapStandalone(){
    if(document.querySelector('.shell')||document.querySelector('.hs-v220-app'))return false;
    const main=document.querySelector('body > main');
    if(!main)return false;
    document.body.classList.add('hs-universal-shell-v220');
    const title=pageTitle();
    const app=document.createElement('div');
    app.className='hs-v220-app';
    app.dataset.shellBuild=BUILD;
    app.innerHTML=sidebarMarkup()+`
      <section class="hs-v220-main">
        <header class="hs-v220-topbar">
          <button class="hs-v220-menu-button" id="hsV220Menu" type="button" aria-label="Open navigation">☰</button>
          <div class="hs-v220-title"><strong>${esc(title[0])}</strong><span>${esc(title[1])}</span></div>
          <div class="hs-v220-topbar-actions"><a href="/#chat">Agent Chat</a></div>
        </header>
        <div class="hs-v220-content"></div>
      </section>
      <button class="hs-v220-backdrop" id="hsV220Backdrop" type="button" aria-label="Close navigation"></button>`;
    main.parentNode.insertBefore(app,main);
    app.querySelector('.hs-v220-content')?.appendChild(main);
    return true;
  }

  function enhanceIndexShell(){
    if(!document.querySelector('.shell'))return;
    document.body.dataset.universalShell='v2.2';
    const topbar=document.querySelector('.topbar');
    if(topbar&&!byId('hsV220IndexMenu')){
      const button=document.createElement('button');
      button.id='hsV220IndexMenu';
      button.type='button';
      button.className='hs-v220-menu-button';
      button.setAttribute('aria-label','Open navigation');
      button.textContent='☰';
      topbar.insertBefore(button,topbar.firstChild);
    }
    if(!byId('hsV220IndexBackdrop')){
      const backdrop=document.createElement('button');
      backdrop.id='hsV220IndexBackdrop';
      backdrop.className='hs-v220-backdrop';
      backdrop.type='button';
      backdrop.setAttribute('aria-label','Close navigation');
      document.body.appendChild(backdrop);
    }
  }

  function setNav(open){
    document.body.classList.toggle('hs-v220-nav-open',Boolean(open));
    document.body.classList.toggle('hs-nav-open',Boolean(open));
  }

  async function refreshCloud(){
    const pill=byId('hsV220CloudPill')||byId('homeServerConnectionButton');
    const runtime=byId('hsV220Runtime');
    try{
      const data=await json('/api/v1/control/cloud-connection');
      const cloud=data.cloud||{};
      const service=data.service||{};
      const state=cloud.connected?'connected':(cloud.state||cloud.connection_state||'offline');
      if(pill){
        pill.dataset.state=state;
        pill.classList?.toggle('online',state==='connected');
        pill.title=`VP3 Cloud: ${state.replaceAll('_',' ')}`;
      }
      if(byId('hsV220Version')&&service.version)byId('hsV220Version').textContent=`v${service.version}`;
      if(runtime)runtime.textContent=state==='connected'?'Cloud connected · shared Agent online':`Cloud ${state.replaceAll('_',' ')} · local Agent available`;
      window.dispatchEvent(new CustomEvent('homeserver:cloud-presence',{detail:{state,cloud,service,build:BUILD}}));
    }catch(_error){
      if(pill){pill.dataset.state='offline';pill.classList?.remove('online');}
      if(runtime)runtime.textContent='Cloud status unavailable · local Agent available';
    }
  }

  function boot(){
    wrapStandalone();
    enhanceIndexShell();
    document.addEventListener('click',event=>{
      if(event.target.closest('#hsV220Menu,#hsV220IndexMenu')){setNav(true);return;}
      if(event.target.closest('#hsV220Backdrop,#hsV220IndexBackdrop')){setNav(false);return;}
      if(event.target.closest('.hs-v220-nav a,.primary-sidebar-nav button,.sidebar-user-wrap a'))setNav(false);
    });
    document.addEventListener('keydown',event=>{if(event.key==='Escape')setNav(false);});
    void refreshCloud();
    const timer=window.setInterval(()=>void refreshCloud(),5000);
    window.addEventListener('pagehide',()=>window.clearInterval(timer),{once:true});
    window.HOMESERVER_UNIVERSAL_SHELL_V220={build:BUILD,refreshCloud,setNav};
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});
  else boot();
})();