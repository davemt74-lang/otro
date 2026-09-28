// Tracky V2.80 Section 6 — Federation Permissions & Consent Operations.
(() => {
  const $=id=>document.getElementById(id);
  const esc=(v='')=>String(v).replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const pretty=v=>String(v||'unknown').replaceAll('_',' ');
  let report=null;

  const stateBadge=s=>'<span class="access-state '+esc(s||'denied')+'">'+esc(pretty(s))+'</span>';

  function scopeButton(peer,scope,row){
    const granted=!!row?.effective_allowed;
    return '<div class="access-scope-row"><div><strong>'+esc(pretty(scope))+'</strong><span>r'+Number(row?.revision||0)+(row?.revocation_epoch?' · revoke epoch '+Number(row.revocation_epoch):'')+'</span></div><div>'+stateBadge(granted?'allowed':(row?.status||'denied'))+'<button class="button secondary access-scope-action" data-peer="'+esc(peer.site_id)+'" data-scope="'+esc(scope)+'" data-action="'+(granted?'revoke':'grant')+'" type="button">'+(granted?'Revoke':'Grant')+'</button></div></div>';
  }

  function category(peer,name,category){
    return '<div class="access-category"><div class="access-category-head"><strong>'+esc(pretty(name))+'</strong>'+stateBadge(category.state)+'</div>'+(category.scopes||[]).map(row=>scopeButton(peer,row.scope,row)).join('')+'</div>';
  }

  function peerCard(peer){
    return '<article class="access-peer-card"><div class="access-peer-head"><div><strong>'+esc(peer.label||peer.site_id)+'</strong><span>'+esc(peer.site_id)+'</span></div><div>'+stateBadge(peer.sync?.status||'unknown')+'</div></div><div class="access-peer-policy"><span>Allowed peer <b>'+(peer.policy_peer_allowed?'Yes':'No')+'</b></span><span>Federation <b>'+(peer.federation_enabled?'On':'Off')+'</b></span><span>Remote observation <b>'+(peer.remote_observation_enabled?'On':'Off')+'</b></span><span>Revocation wins <b>Yes</b></span></div>'+(peer.sync&&!peer.sync.fresh?'<div class="access-warning">Remote state is '+esc(pretty(peer.sync.status))+'. Local revocations remain authoritative; stale grants cannot restore access.</div>':'')+'<div class="access-category-grid">'+Object.entries(peer.categories||{}).map(([name,value])=>category(peer,name,value)).join('')+'</div></article>';
  }

  function consentRow(item){
    return '<div class="access-consent-row"><div><strong>'+esc(item.canonical_identity_id)+'</strong><span>'+esc(pretty(item.scope))+' · r'+Number(item.revision||0)+'</span></div><div>'+stateBadge(item.status)+'<span class="muted">'+esc(item.reason||'')+'</span></div></div>';
  }

  function historyRow(item){
    const detail=item.detail||{};
    return '<div class="access-history-row"><div><strong>'+esc(pretty(item.event_type))+'</strong><span>rev '+Number(item.revision||0)+' · epoch '+Number(item.revocation_epoch||0)+'</span></div><div><span>'+esc(detail.scope||detail.destination_site_id||detail.canonical_identity_id||'')+'</span><small>'+esc(item.occurred_at||'')+'</small></div></div>';
  }

  function renderPolicyForm(access){
    const policy=access.local_policy||{};
    $('accessPolicyMode').value=policy.mode||'private';
    $('accessAllowFederation').checked=!!policy.allow_federation;
    $('accessAllowRemoteObservation').checked=!!policy.allow_remote_observation;
    $('accessIdentityVisibility').value=policy.default_identity_visibility||'none';
    $('accessPeerChoices').innerHTML=(access.peers||[]).map(peer=>'<label><input type="checkbox" value="'+esc(peer.site_id)+'" '+(peer.policy_peer_allowed?'checked':'')+'> '+esc(peer.label||peer.site_id)+'</label>').join('')||'<span class="muted">No remote sites are registered.</span>';
  }

  function render(access){
    report=access;
    $('accessPolicyRevision').textContent=Number(access.policy_revision||0);
    $('accessRevocationEpoch').textContent=Number(access.revocation_epoch||0);
    $('accessAllowedCount').textContent=Number(access.counts?.grants_allowed||0);
    $('accessRevokedCount').textContent=Number(access.counts?.grants_revoked||0)+Number(access.counts?.consents_revoked||0);
    $('accessSuppressedCount').textContent=Number(access.counts?.stale_grants_suppressed||0);
    $('accessAgentSummary').textContent=access.agent_context?.summary||'No access-policy summary.';
    renderPolicyForm(access);
    $('accessPeers').innerHTML=(access.peers||[]).length?(access.peers||[]).map(peerCard).join(''):'<div class="empty-state">No federation peers are registered.</div>';
    $('accessConsents').innerHTML=(access.consents||[]).length?(access.consents||[]).map(consentRow).join(''):'<div class="empty-state">No recognition consent records yet.</div>';
    $('accessHistory').innerHTML=(access.history||[]).length?(access.history||[]).slice(0,50).map(historyRow).join(''):'<div class="empty-state">No federation policy history yet.</div>';
  }

  async function request(url,options={}){
    const response=await fetch(url,{...options,headers:{'Accept':'application/json','Content-Type':'application/json',...(options.headers||{})}});
    let data={};try{data=await response.json();}catch(_){}
    if(!response.ok)throw new Error(data.detail||data.error||'Federation access operation failed.');
    return data;
  }

  async function load(){
    const status=$('accessLoading');if(status)status.textContent='Refreshing…';
    const data=await request('/api/v1/control/federation-access');
    render(data.access||{});
    if(status)status.textContent='Local policy authority';
  }

  async function savePolicy(){
    const allowed=[...document.querySelectorAll('#accessPeerChoices input[type="checkbox"]:checked')].map(node=>node.value);
    const data=await request('/api/v1/control/federation-access/site-policy',{
      method:'PUT',
      body:JSON.stringify({
        mode:$('accessPolicyMode').value,
        allow_federation:$('accessAllowFederation').checked,
        allow_remote_observation:$('accessAllowRemoteObservation').checked,
        default_identity_visibility:$('accessIdentityVisibility').value,
        allowed_peer_sites:allowed
      })
    });
    render(data.access||{});
  }

  async function permission(peer,scope,action){
    const data=await request('/api/v1/control/federation-access/permission',{
      method:'POST',body:JSON.stringify({action,destination_site_id:peer,scope,reason:'control_center_'+action})
    });
    render(data.access||{});
  }

  async function consent(){
    const identity=$('accessConsentIdentity').value.trim();
    if(!identity)throw new Error('Canonical identity UUID is required.');
    const data=await request('/api/v1/control/federation-access/consent',{
      method:'POST',body:JSON.stringify({
        canonical_identity_id:identity,
        scope:$('accessConsentScope').value,
        status:$('accessConsentStatus').value,
        reason:$('accessConsentReason').value.trim()
      })
    });
    render(data.access||{});
    $('accessConsentReason').value='';
  }

  window.loadFederationAccessOperations=load;
  document.addEventListener('click',event=>{
    if(event.target?.id==='refreshFederationAccess'){
      load().catch(err=>$('accessLoading').textContent=err.message);
    }
    if(event.target?.id==='saveFederationSitePolicy'){
      savePolicy().catch(err=>$('accessLoading').textContent=err.message);
    }
    if(event.target?.matches('.access-scope-action')){
      const node=event.target;
      permission(node.dataset.peer||'',node.dataset.scope||'',node.dataset.action||'').catch(err=>$('accessLoading').textContent=err.message);
    }
    if(event.target?.id==='saveFederationConsent'){
      consent().catch(err=>$('accessLoading').textContent=err.message);
    }
  });
})();