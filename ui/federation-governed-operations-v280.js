(()=>{
  const root=document.getElementById('governedFederationOperationsV280'); if(!root)return;
  const status=root.querySelector('[data-fgo-status]');
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const badge=s=>'<span class="fgo-state '+esc(s)+'">'+esc(String(s||'unknown').replaceAll('_',' '))+'</span>';
  const action=(id,label,kind)=>'<button type="button" class="button secondary fgo-action" data-fgo-action="'+kind+'" data-fgo-id="'+esc(id)+'">'+esc(label)+'</button>';
  function actions(x){
    const a=[];
    if(x.state==='awaiting_approval'){a.push(action(x.request_id,'Approve','approve'),action(x.request_id,'Reject','reject'));}
    if(x.state==='approved'||x.state==='queued'){a.push(action(x.request_id,'Execute','execute'));}
    if(x.state==='reconciling'){a.push(action(x.request_id,'Check reconciliation','refresh'));}
    if(!['completed','failed','rejected','cancelled','expired'].includes(x.state)){a.push(action(x.request_id,'Cancel','cancel'));}
    return a.length?'<div class="fgo-actions">'+a.join('')+'</div>':'';
  }
  function render(r){
    const items=r.operations||[];
    root.querySelector('[data-fgo-active]').textContent=Number(r.counts?.active||0);
    root.querySelector('[data-fgo-approval]').textContent=Number(r.counts?.awaiting_approval||0);
    root.querySelector('[data-fgo-reconciling]').textContent=Number(r.counts?.reconciling||0);
    root.querySelector('[data-fgo-list]').innerHTML=items.length?items.slice(0,30).map(x=>'<div class="fgo-row"><div><strong>'+esc(x.operation_type.replaceAll('_',' '))+'</strong><div class="muted">'+esc(x.target_site_id)+(x.device_id?' · '+esc(x.device_id):'')+'</div><div class="muted">Request '+esc(x.request_id)+(x.expires_at_ms?' · expires '+new Date(x.expires_at_ms).toLocaleString():'')+'</div>'+actions(x)+'</div>'+badge(x.state)+'</div>').join(''):'<div class="empty-state">No governed federation operations yet.</div>';
  }
  async function json(url,options={}){
    const q=await fetch(url,{credentials:'same-origin',headers:{Accept:'application/json','Content-Type':'application/json',...(options.headers||{})},...options});
    const d=await q.json(); if(!q.ok)throw new Error(d.detail||d.error||'Operation failed.'); return d;
  }
  async function load(){
    status.textContent='Refreshing…';
    const d=await json('/api/v1/control/federation-operations');
    render(d.operations||{}); status.textContent='HomeServer authoritative ledger';
  }
  root.querySelector('[data-fgo-create]')?.addEventListener('submit',async e=>{
    e.preventDefault(); const form=e.currentTarget; const fd=new FormData(form);
    const payload=Object.fromEntries(fd.entries());
    payload.require_approval=true; payload.parameters={};
    if(fd.get('explicit_confirmation'))payload.confirmation_token='local_owner_explicit_confirmation';
    delete payload.explicit_confirmation;
    status.textContent='Creating local governed request…';
    await json('/api/v1/control/federation-operations/propose',{method:'POST',body:JSON.stringify(payload)});
    form.reset(); await load();
  });
  root.addEventListener('click',async e=>{
    const b=e.target.closest('[data-fgo-action]'); if(!b)return;
    b.disabled=true; status.textContent='Applying local governed decision…';
    try{
      const id=encodeURIComponent(b.dataset.fgoId),kind=b.dataset.fgoAction;
      if(kind==='approve'||kind==='reject')await json('/api/v1/control/federation-operations/'+id+'/decision',{method:'POST',body:JSON.stringify({approved:kind==='approve'})});
      else await json('/api/v1/control/federation-operations/'+id+'/'+kind,{method:'POST',body:'{}'});
      await load();
    }catch(err){status.textContent=err.message;b.disabled=false;}
  });
  root.querySelector('[data-fgo-refresh]')?.addEventListener('click',()=>load().catch(e=>status.textContent=e.message));
  load().catch(e=>status.textContent=e.message);
})();