(() => {
  const $ = id => document.getElementById(id);
  const esc = (value='') => String(value).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const fmt = value => value ? new Date(value).toLocaleString() : '';

  async function api(path, options={}) {
    const headers={...(options.headers||{})};
    if(options.body && !headers['Content-Type']) headers['Content-Type']='application/json';
    const response=await fetch(path,{...options,headers,credentials:'same-origin'});
    let data={}; try{data=await response.json();}catch(_){}
    if(!response.ok) throw new Error(data.detail||('Request failed ('+response.status+')'));
    return data;
  }

  function showLogin(message='') {
    $('memberWorkspace').classList.add('hidden');
    $('memberLogin').classList.remove('hidden');
    $('loginError').textContent=message;
  }

  async function loadWorkspace() {
    const me=await api('/api/v1/member/me');
    const [apps,context,activity,brain]=await Promise.all([
      api('/api/v1/member/apps'),
      api('/api/v1/member/context'),
      api('/api/v1/member/activity?limit=100'),
      api('/api/v1/member/agent-context')
    ]);
    $('memberLogin').classList.add('hidden');
    $('memberWorkspace').classList.remove('hidden');
    $('memberName').textContent=me.member.display_name;
    $('memberIdentity').textContent='@'+me.member.username+' · '+me.member.role;
    const contextForm=$('memberContextForm');
    if(contextForm) contextForm.classList.toggle('hidden',me.member.role==='guest');
    $('stonefellowPanel').hidden=!apps.items.some(app=>app.app_key==='vp3.stonefellow'&&app.allowed!==false);
    if(!$('stonefellowPanel').hidden)refreshStonefellow();else clearMusic();
    $('memberApps').innerHTML=apps.items.length ? apps.items.map(app =>
      '<article class="member-item"><div class="member-item-head"><h3>'+esc(app.name)+'</h3><span class="tag">'+esc(app.lifecycle_state)+'</span></div><p class="muted">'+esc(app.app_key)+'</p></article>'
    ).join('') : '<div class="empty-state">No apps have been assigned to your account.</div>';
    $('memberContext').innerHTML=context.items.length ? context.items.map(item =>
      '<article class="member-item"><div class="member-item-head"><h3>'+esc(item.context_key)+'</h3><button class="text-button danger" data-context-delete="'+esc(item.context_key)+'">Delete</button></div><pre>'+esc(JSON.stringify(item.value,null,2))+'</pre></article>'
    ).join('') : '<div class="empty-state">'+(me.member.role==='guest'?'Guest accounts are read-only and do not persist Agent context.':'No private Agent context saved yet.')+'</div>';
    $('memberActivity').innerHTML=activity.items.length ? activity.items.map(item =>
      '<article class="member-item"><div class="member-item-head"><h3>'+esc(item.action)+'</h3><span class="muted">'+esc(fmt(item.created_at))+'</span></div><p class="muted">'+esc([item.resource_type,item.resource_key].filter(Boolean).join(' · '))+'</p></article>'
    ).join('') : '<div class="empty-state">No user activity yet.</div>';
    if(brain.isolation.owner_memory_included || brain.isolation.other_member_context_included || brain.isolation.unassigned_apps_included) {
      throw new Error('Member privacy boundary is unavailable.');
    }
  }

  $('memberLoginForm').addEventListener('submit', async event => {
    event.preventDefault();
    $('loginError').textContent='';
    try {
      await api('/api/v1/member/session',{
        method:'POST',
        body:JSON.stringify({username:$('loginUsername').value,password:$('loginPassword').value})
      });
      event.target.reset();
      await loadWorkspace();
    } catch(error) {
      showLogin(error.message);
    }
  });

  $('memberSignOut').addEventListener('click', async () => {
    try { await api('/api/v1/member/session',{method:'DELETE'}); } catch(_) {}
    clearMusic();showLogin('');
  });

  $('memberContextForm').addEventListener('submit', async event => {
    event.preventDefault();
    const key=$('contextKey').value.trim();
    let value;
    try { value=JSON.parse($('contextValue').value); }
    catch(_) { value=$('contextValue').value; }
    try {
      await api('/api/v1/member/context/'+encodeURIComponent(key),{
        method:'PUT',body:JSON.stringify({value})
      });
      event.target.reset();
      await loadWorkspace();
    } catch(error) { alert(error.message); }
  });

  document.addEventListener('click', async event => {
    const button=event.target.closest('[data-context-delete]');
    if(!button) return;
    try {
      await api('/api/v1/member/context/'+encodeURIComponent(button.dataset.contextDelete),{method:'DELETE'});
      await loadWorkspace();
    } catch(error) { alert(error.message); }
  });


  let musicSongs=[];
  let musicBlobUrl='';
  function clearMusic() {
    const player=$('stonefellowPlayer');
    player.pause();player.removeAttribute('src');player.load();
    if(musicBlobUrl)URL.revokeObjectURL(musicBlobUrl);
    musicBlobUrl='';player.hidden=true;musicSongs=[];
  }
  function musicNotice(message) {$('stonefellowMessage').textContent=message;}
  function showSongs(tracks) {
    musicSongs=tracks;
    $('stonefellowSongs').innerHTML=tracks.length?tracks.map(t=>{
      const r=t.vinyl_print_rights||{};
      const packs=Array.isArray(r.packages)?r.packages:[];
      return '<article class="member-item"><div class="member-item-head"><h3>'+esc(t.title)+'</h3>'
        +'<button class="button secondary" data-stonefellow-play="'+esc(t.id)+'" '+(t.stream_available_on_stonefellow?'':'disabled')+'>Play</button></div>'
        +'<p class="muted">Stonefellow · '+esc(t.duration_seconds||0)+' seconds · Vinyl prints available: '+esc(r.available||0)+'</p>'
        +(packs.length?'<p class="muted">Extra print-license packages: '+packs.map(p=>esc(p.licenses)+' for USD '+(Number(p.price_cents||0)/100).toFixed(2)).join(', ')+' — purchase on Stonefellow.</p>':'')
        +'<p class="muted">Owned downloads remain available through your Stonefellow account.</p></article>';
    }).join(''):'<div class="empty-state">No digitally owned tracks are available for this Stonefellow account.</div>';
  }
  async function refreshStonefellow() {
    try{
      const data=await api('/api/v1/member/stonefellow/library');
      const connected=data.status==='connected';
      $('stonefellowConnect').hidden=connected;
      $('stonefellowComplete').hidden=connected;
      $('stonefellowDisconnect').hidden=!connected;
      showSongs(connected?(data.tracks||[]):[]);
      musicNotice(connected?'Connected to your authorized Stonefellow music library.':'Connect and approve your own Stonefellow account to view owned music.');
    }catch(error){clearMusic();showSongs([]);musicNotice(error.message)}
  }
  async function musicAction(path,method='POST'){
    try{
      const data=await api(path,{method});
      if(data.authorization_url){
        const url=new URL(data.authorization_url);
        if(url.protocol!=='https:'||!url.searchParams.has('request_id'))throw Error('Unsafe approval URL');
        musicNotice('Sign in to Stonefellow and explicitly authorize HomeServer, then select Finish authorization.');
        $('stonefellowComplete').hidden=false;
        window.open(url.href,'_blank','noopener,noreferrer');
      }else if(data.status==='awaiting_stonefellow_login_and_approval'){
        musicNotice('Still awaiting your explicit Stonefellow approval.');
      }else await refreshStonefellow();
    }catch(error){musicNotice(error.message)}
  }
  $('stonefellowConnect').addEventListener('click',()=>musicAction('/api/v1/member/stonefellow/authorize'));
  $('stonefellowComplete').addEventListener('click',()=>musicAction('/api/v1/member/stonefellow/complete'));
  $('stonefellowRefresh').addEventListener('click',refreshStonefellow);
  $('stonefellowDisconnect').addEventListener('click',async()=>{
    clearMusic();await musicAction('/api/v1/member/stonefellow','DELETE');
    musicNotice('Disconnected locally. Revoke the grant in your Stonefellow account to disable it immediately at the source.');
  });
  document.addEventListener('click',async event=>{
    const btn=event.target.closest('[data-stonefellow-play]');
    if(!btn)return;
    const id=btn.dataset.stonefellowPlay;
    if(!musicSongs.some(t=>t.id===id&&t.stream_available_on_stonefellow))return;
    clearMusic();
    // Browser media requests use this account's own HttpOnly HomeServer cookie;
    // the server injects the fan-specific Stonefellow credential privately.
    const player=$('stonefellowPlayer');
    player.src='/api/v1/member/stonefellow/audio/'+encodeURIComponent(id);
    player.hidden=false;
    try{await player.play();}catch(error){musicNotice('Playback needs a user gesture or the audio format is unavailable.')}
  });

  loadWorkspace().catch(()=>showLogin(''));
})();