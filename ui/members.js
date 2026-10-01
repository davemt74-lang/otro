(() => {
  let selectedMemberId = null;
  let membersCache = [];

  const escapeHtml = (value='') => String(value).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));

  async function loadMembers() {
    const data = await api('/api/v1/control/members');
    membersCache = data.items || [];
    const list = document.getElementById('membersList');
    if (!list) return;
    if (membersCache.length) {
      list.innerHTML = membersCache.map(function(member) {
        return '<article class="item-card member-row ' + (selectedMemberId===member.member_id?'selected':'') + '" data-member-select="' + escapeHtml(member.member_id) + '">' +
          '<div><h3>' + escapeHtml(member.display_name) + '</h3><div class="item-meta">' +
          '<span>@' + escapeHtml(member.username) + '</span><span class="tag">' + escapeHtml(member.role) + '</span><span class="tag">' + escapeHtml(member.status) + '</span>' +
          '<span>' + Number(member.app_count||0) + ' apps</span><span>' + Number(member.active_sessions||0) + ' sessions</span>' +
          '</div></div></article>';
      }).join('');
    } else {
      list.innerHTML = '<div class="panel empty-state">No member accounts yet.</div>';
    }
    if (selectedMemberId && membersCache.some(row => row.member_id===selectedMemberId)) {
      await loadMemberDetail(selectedMemberId);
    } else if (membersCache.length) {
      selectedMemberId = membersCache[0].member_id;
      await loadMemberDetail(selectedMemberId);
    } else {
      selectedMemberId = null;
      const detail=document.getElementById('memberDetail');
      if(detail) detail.innerHTML='<div class="empty-state">Select a user to manage role, status, password, and app access.</div>';
    }
  }

  async function loadMemberDetail(memberId) {
    const member = membersCache.find(row => row.member_id===memberId);
    if (!member) return;
    const apps = await api('/api/v1/control/members/'+encodeURIComponent(memberId)+'/apps');
    const detail=document.getElementById('memberDetail');
    if(!detail) return;
    const appRows = apps.items.length ? apps.items.map(function(app) {
      return '<label class="permission"><input type="checkbox" data-member-app="' + escapeHtml(memberId) + '" data-app-key="' + escapeHtml(app.app_key) + '" ' + (app.allowed?'checked':'') + '>' +
        '<span>' + escapeHtml(app.name) + ' <small>' + escapeHtml(app.app_key) + '</small></span></label>';
    }).join('') : '<p class="muted">No installed apps are available.</p>';
    detail.innerHTML =
      '<div class="panel-head"><div><p class="eyebrow">USER</p><h3>' + escapeHtml(member.display_name) + '</h3><p>@' + escapeHtml(member.username) + '</p></div></div>' +
      '<label>Role<select data-member-role="' + escapeHtml(memberId) + '">' +
      '<option value="admin" ' + (member.role==='admin'?'selected':'') + '>Admin</option>' +
      '<option value="member" ' + (member.role==='member'?'selected':'') + '>Member</option>' +
      '<option value="guest" ' + (member.role==='guest'?'selected':'') + '>Guest</option></select></label>' +
      '<label>Status<select data-member-status="' + escapeHtml(memberId) + '">' +
      '<option value="active" ' + (member.status==='active'?'selected':'') + '>Active</option>' +
      '<option value="disabled" ' + (member.status==='disabled'?'selected':'') + '>Disabled</option></select></label>' +
      '<form data-member-password="' + escapeHtml(memberId) + '" class="member-password-form">' +
      '<label>Reset password<input name="password" type="password" minlength="10" maxlength="256" autocomplete="new-password" required></label>' +
      '<button class="button secondary" type="submit">Reset & revoke sessions</button></form>' +
      '<div class="member-apps"><h4>App access</h4>' + appRows + '</div>' +
      '<div class="member-security-note"><strong>Isolation:</strong> This user receives only their own private context and explicitly assigned apps. Owner control remains unavailable.</div>';
    document.querySelectorAll('[data-member-select]').forEach(node => node.classList.toggle('selected',node.dataset.memberSelect===memberId));
  }

  window.loadHomeServerMembers = loadMembers;

  document.addEventListener('click', event => {
    if (event.target.id==='showMemberForm') document.getElementById('memberForm')?.classList.remove('hidden');
    if (event.target.id==='cancelMemberForm') document.getElementById('memberForm')?.classList.add('hidden');
    const card=event.target.closest('[data-member-select]');
    if(card){
      selectedMemberId=card.dataset.memberSelect;
      loadMemberDetail(selectedMemberId).catch(error=>flash(error.message,true));
    }
  });

  document.addEventListener('change', event => {
    const role=event.target.closest('[data-member-role]');
    if(role){
      api('/api/v1/control/members/'+encodeURIComponent(role.dataset.memberRole),{
        method:'PATCH',body:JSON.stringify({role:role.value})
      }).then(()=>loadMembers()).catch(error=>flash(error.message,true));
      return;
    }
    const status=event.target.closest('[data-member-status]');
    if(status){
      api('/api/v1/control/members/'+encodeURIComponent(status.dataset.memberStatus),{
        method:'PATCH',body:JSON.stringify({status:status.value})
      }).then(()=>loadMembers()).catch(error=>flash(error.message,true));
      return;
    }
    const app=event.target.closest('[data-member-app]');
    if(app){
      api('/api/v1/control/members/'+encodeURIComponent(app.dataset.memberApp)+'/apps/'+encodeURIComponent(app.dataset.appKey),{
        method:'PUT',body:JSON.stringify({allowed:Boolean(app.checked)})
      }).then(()=>flash('User app access updated.')).catch(error=>{app.checked=!app.checked;flash(error.message,true);});
    }
  });

  document.addEventListener('submit', event => {
    if(event.target.id==='memberForm'){
      event.preventDefault();
      api('/api/v1/control/members',{
        method:'POST',
        body:JSON.stringify({
          username:document.getElementById('memberUsername').value,
          display_name:document.getElementById('memberDisplayName').value,
          password:document.getElementById('memberPassword').value,
          role:document.getElementById('memberRole').value
        })
      }).then(async result=>{
        event.target.reset();
        event.target.classList.add('hidden');
        selectedMemberId=result.member.member_id;
        await loadMembers();
        flash('HomeServer user created.');
      }).catch(error=>flash(error.message,true));
      return;
    }
    const reset=event.target.closest('[data-member-password]');
    if(reset){
      event.preventDefault();
      const password=new FormData(reset).get('password');
      api('/api/v1/control/members/'+encodeURIComponent(reset.dataset.memberPassword)+'/password',{
        method:'PUT',body:JSON.stringify({password})
      }).then(()=>{reset.reset();flash('Password updated and active sessions revoked.');loadMembers();}).catch(error=>flash(error.message,true));
    }
  });
})();