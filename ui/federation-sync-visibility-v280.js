// Tracky V2.80 Section 5 — Reconciliation & Sync Visibility.
(() => {
  const $=id=>document.getElementById(id);
  const esc=(v='')=>String(v).replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const pretty=v=>String(v||'unknown').replaceAll('_',' ');
  const age=ms=>{ms=Number(ms||0);if(!ms)return '—';if(ms<60000)return Math.round(ms/1000)+'s';if(ms<3600000)return Math.round(ms/60000)+'m';return (ms/3600000).toFixed(1)+'h';};
  const when=v=>{if(!v)return'—';const n=Number(v);const d=new Date(n>100000000000?n:n*1000);return Number.isNaN(d.getTime())?String(v):d.toLocaleString();};
  const badge=s=>'<span class="syncv-state '+esc(s||'unknown')+'">'+esc(pretty(s))+'</span>';
  function site(row){
    return '<article class="syncv-site">'+
      '<div class="syncv-site-head"><div><strong>'+esc(row.label||row.site_id)+'</strong><span>'+esc(row.site_id||'')+'</span></div>'+badge(row.status)+'</div>'+
      '<div class="syncv-grid"><div><span>Local rev</span><b>'+Number(row.local_cursor?.revision||0)+'</b></div><div><span>Remote rev</span><b>'+Number(row.remote_cursor?.revision||0)+'</b></div><div><span>Gap</span><b>'+Number(row.revision_gap||0)+'</b></div><div><span>Epoch</span><b>'+Number(row.local_cursor?.authority_epoch||0)+' / '+Number(row.remote_cursor?.authority_epoch||0)+'</b></div></div>'+
      '<div class="syncv-grid"><div><span>Stale</span><b>'+esc(age(row.stale_age_ms))+'</b></div><div><span>Retry</span><b>'+Number(row.retry_count||0)+'</b></div><div><span>Next retry</span><b>'+esc(row.next_retry_at?when(row.next_retry_at):'—')+'</b></div><div><span>Last contact</span><b>'+esc(row.last_contact_at?when(row.last_contact_at):'—')+'</b></div></div>'+
      (row.reconciliation_required?'<div class="syncv-progress"><span style="width:'+Math.round(Number(row.catch_up?.progress||0)*100)+'%"></span></div>':'')+
      '<p>'+esc(row.message||'')+'</p>'+
      (row.conflict_code?'<div class="syncv-warning">'+esc(row.conflict_code)+'</div>':'')+
    '</article>';
  }
  function run(row){
    return '<div class="syncv-run"><div><strong>'+esc(row.site_label||row.site_id)+'</strong><span>'+esc(pretty(row.status))+' · '+esc(pretty(row.request_mode))+'</span></div><div><span>'+Number(row.local_revision||0)+' → '+Number(row.remote_revision||0)+'</span><small>'+esc(row.started_at?when(row.started_at):'')+'</small></div></div>';
  }
  function render(report){
    $('syncVisibilityState').innerHTML=badge(report.overall_state||'unknown');
    $('syncVisibilityCurrent').textContent=Number(report.counts?.current||0);
    $('syncVisibilityReconciling').textContent=Number(report.counts?.reconciling||0);
    $('syncVisibilityPartitioned').textContent=Number(report.counts?.partitioned||0);
    $('syncVisibilityFailed').textContent=Number(report.counts?.failed||0);
    $('syncVisibilitySites').innerHTML=(report.sites||[]).length?report.sites.map(site).join(''):'<div class="empty-state">No federation sync state available.</div>';
    $('syncVisibilityRuns').innerHTML=(report.reconciliation_runs||[]).length?report.reconciliation_runs.slice(0,30).map(run).join(''):'<div class="empty-state">No reconciliation history yet.</div>';
    $('syncVisibilityAgent').textContent=report.agent_context?.summary||'No sync summary.';
  }
  async function load(){
    const status=$('syncVisibilityLoading');if(status)status.textContent='Refreshing…';
    const response=await fetch('/api/v1/control/federation-sync-visibility',{headers:{'Accept':'application/json'}});
    let data={};try{data=await response.json();}catch(_){}
    if(!response.ok)throw new Error(data.detail||'Sync visibility unavailable.');
    render(data.visibility||{});
    if(status)status.textContent='Live local reconciliation view';
  }
  window.loadFederationSyncVisibility=load;
  document.addEventListener('click',e=>{if(e.target?.id==='refreshFederationSyncVisibility')load().catch(err=>{const s=$('syncVisibilityLoading');if(s)s.textContent=err.message;});});
})();