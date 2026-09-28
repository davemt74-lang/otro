// Tracky V2.80 Section 7 — Agent Brain Federation Health.
(() => {
  const $=id=>document.getElementById(id);
  const esc=(v='')=>String(v).replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const pretty=v=>String(v||'unknown').replaceAll('_',' ');
  const when=v=>{if(!v)return'—';const n=Number(v);const d=new Date(n>100000000000?n:n*1000);return Number.isNaN(d.getTime())?String(v):d.toLocaleString();};
  const badge=s=>'<span class="fah-state '+esc(s||'unknown')+'">'+esc(pretty(s))+'</span>';
  function site(row){
    const trust=row.trust||{};
    return '<article class="fah-site"><div class="fah-head"><div><strong>'+esc(row.label||row.site_id)+'</strong><span>'+esc(row.site_id||'')+'</span></div>'+badge(row.state)+'</div><div class="fah-meta"><span>Severity <b>'+esc(row.severity||'info')+'</b></span><span>Cause <b>'+esc(pretty(row.cause||'none'))+'</b></span><span>Fresh <b>'+(row.fresh?'Yes':'No')+'</b></span><span>Recovery <b>'+(row.recovery_complete?'Complete':'Pending')+'</b></span></div><div class="fah-trust"><span>Semantic '+esc(pretty(trust.semantic_state||'unknown'))+'</span><span>Agent '+esc(pretty(trust.agent_use||'unknown'))+'</span><span>Physical claims '+esc(pretty(trust.physical_claims||'unknown'))+'</span></div><p>'+esc(row.message||'')+'</p></article>';
  }
  function history(row){
    const payload=row.payload||{};
    return '<div class="fah-history-row"><div><strong>'+esc(pretty(row.event_type||''))+'</strong><span>'+esc(payload.label||payload.site_id||payload.component||'')+'</span></div><div><span>'+esc(payload.severity||'')+'</span><small>'+esc(row.occurred_at||'')+'</small></div></div>';
  }
  function render(report,historyRows){
    $('fahOverall').innerHTML=badge(report.overall_state||'unknown');
    $('fahCurrent').textContent=Number(report.counts?.current||0);
    $('fahDegraded').textContent=Number(report.counts?.degraded||0);
    $('fahCritical').textContent=Number(report.counts?.critical||0);
    $('fahRecovering').textContent=Number(report.counts?.recovering||0);
    $('fahRelay').innerHTML=badge(report.relay_health?.state||'unknown');
    $('fahAgentSummary').textContent=report.agent_context?.summary||'No federation health summary.';
    $('fahSites').innerHTML=(report.sites||[]).length?(report.sites||[]).map(site).join(''):'<div class="empty-state">No federation sites are registered.</div>';
    $('fahHistory').innerHTML=(historyRows||[]).length?(historyRows||[]).slice(0,40).map(history).join(''):'<div class="empty-state">No federation health events yet.</div>';
  }
  async function load(){
    const status=$('fahLoading');if(status)status.textContent='Refreshing…';
    const response=await fetch('/api/v1/control/federation-agent-health',{headers:{'Accept':'application/json'}});
    let data={};try{data=await response.json();}catch(_){}
    if(!response.ok)throw new Error(data.detail||'Federation Agent health unavailable.');
    render(data.health||{},data.history||[]);
    if(status)status.textContent='Agent Brain live health';
  }
  window.loadFederationAgentHealth=load;
  document.addEventListener('click',event=>{if(event.target?.id==='refreshFederationAgentHealth')load().catch(err=>{$('fahLoading').textContent=err.message;});});
})();