// Tracky V2.80 Section 7 — Agent Brain Federation Health, Failures & Recovery.
(() => {
  const $=id=>document.getElementById(id);
  const esc=(v='')=>String(v).replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const pretty=v=>String(v||'unknown').replaceAll('_',' ');
  const age=ms=>{ms=Number(ms||0);if(!ms)return'0s';if(ms<60000)return Math.round(ms/1000)+'s';if(ms<3600000)return Math.round(ms/60000)+'m';return(ms/3600000).toFixed(1)+'h';};
  const badge=s=>'<span class="fah-state '+esc(s||'unknown')+'">'+esc(pretty(s))+'</span>';

  function site(row){
    const authority=row.authority_device||{};
    const trust=row.trust||{};
    return '<article class="fah-site">'+
      '<div class="fah-site-head"><div><strong>'+esc(row.label||row.site_id)+'</strong><span>'+esc(row.site_id||'')+'</span></div>'+badge(row.state)+'</div>'+
      '<div class="fah-grid">'+
        '<div><span>Sync</span><b>'+esc(pretty(row.sync_status))+'</b></div>'+
        '<div><span>Age</span><b>'+esc(age(row.state_age_ms))+'</b></div>'+
        '<div><span>Priority</span><b>'+esc(pretty(row.priority))+'</b></div>'+
        '<div><span>Authority</span><b>'+esc(authority.label||row.authority?.device_id||'—')+'</b></div>'+
      '</div>'+
      '<p>'+esc(row.message||'')+'</p>'+
      (row.recovery_pending?'<div class="fah-warning">Recovery pending: reconciliation must return to current before the Agent marks this site recovered.</div>':'')+
      '<div class="fah-trust"><span>Local truth <b>'+(trust.local_physical_truth_current?'current':'not verified')+'</b></span><span>Remote federation <b>'+(trust.remote_federation_truth_current?'current':'stale/unknown')+'</b></span><span>Cloud transport <b>'+(trust.cloud_transport_connected?'connected':'offline')+'</b></span></div>'+
    '</article>';
  }

  function eventRow(row){
    const payload=row.payload||{};
    return '<div class="fah-history-row"><div><strong>'+esc(pretty((payload.event_type||row.event_type||'').replace('tracky.federation_health.','')))+'</strong><span>'+esc(payload.site_label||payload.site_id||row.entity_key||'VP3 Cloud Relay')+'</span></div><div><span>'+esc(pretty(payload.priority||'info'))+'</span><small>'+esc(row.occurred_at||row.created_at||'')+'</small></div></div>';
  }

  function render(data){
    const report=data.health||{};
    $('fahOverall').innerHTML=badge(report.overall_state||'unknown');
    $('fahConnected').textContent=Number(report.counts?.connected||0);
    $('fahRecovering').textContent=Number(report.counts?.recovering||0)+Number(report.counts?.reconciling||0);
    $('fahCritical').textContent=Number(report.counts?.partitioned||0)+Number(report.counts?.offline||0)+Number(report.counts?.failed||0);
    const bridge=report.bridge||{};
    $('fahBridge').innerHTML=badge(bridge.state||'unknown');
    $('fahBridgeDetail').textContent=bridge.connected
      ?'VP3 Cloud transport connected.'
      :'Cloud transport '+pretty(bridge.state||'unknown')+'. Local physical truth may remain available.';
    $('fahSites').innerHTML=(report.sites||[]).length?(report.sites||[]).map(site).join(''):'<div class="empty-state">No federation health state available.</div>';
    $('fahHistory').innerHTML=(data.history||[]).length?(data.history||[]).slice(0,40).map(eventRow).join(''):'<div class="empty-state">No health events recorded yet.</div>';
    $('fahRuntime').textContent=data.runtime?.running?'Health monitor active':'Health monitor inactive';
  }

  async function load(){
    const status=$('fahLoading');if(status)status.textContent='Refreshing…';
    const response=await fetch('/api/v1/control/federation-agent-health',{headers:{'Accept':'application/json'}});
    let data={};try{data=await response.json();}catch(_){}
    if(!response.ok)throw new Error(data.detail||'Federation health unavailable.');
    render(data);
    if(status)status.textContent='Agent Brain live health';
  }

  window.loadFederationAgentHealth=load;
  document.addEventListener('click',event=>{
    if(event.target?.id==='refreshFederationAgentHealth'){
      load().catch(err=>{const s=$('fahLoading');if(s)s.textContent=err.message;});
    }
  });
  setInterval(()=>load().catch(()=>{}),10000);
})();