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
    showLogin('');
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

  loadWorkspace().catch(()=>showLogin(''));
})();