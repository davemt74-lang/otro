(() => {
  const root=document.getElementById('fleetHealthV280'); if(!root)return;
  const esc=(v='')=>String(v).replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const pretty=v=>String(v||'unknown').replaceAll('_',' ');
  const badge=s=>'<span class="ffh-state '+esc(s||'unknown')+'">'+esc(pretty(s))+'</span>';
  function device(d){
    return '<article class="ffh-device"><div class="ffh-device-head"><div><strong>'+esc(d.label||d.device_id||'Device')+'</strong><span>'+esc(pretty(d.hardware_profile||'custom'))+'</span></div>'+badge(d.state)+'</div>'+
      '<div class="ffh-meta"><span>OS <b>'+esc(d.os_version||'n/a')+'</b></span><span>Runtime <b>'+esc(d.runtime_status||'unknown')+'</b></span><span>Update <b>'+esc(d.upgrade_state||d.update_status||'unknown')+'</b></span><span>Storage <b>'+esc(d.storage_state||'unknown')+'</b></span><span>Backup <b>'+esc(d.backup_state||'unknown')+'</b></span><span>Cameras <b>'+Number(d.camera_count||0)+'</b></span><span>Sensors <b>'+Number(d.sensor_count||0)+'</b></span><span>Models <b>'+Number(d.active_models||0)+' · '+esc(d.model_health||'unknown')+'</b></span><span>Calibration <b>'+Number(d.calibration_profiles||0)+' · '+esc(d.calibration_state||'unknown')+'</b></span><span>Errors <b>'+Number(d.error_count||0)+'</b></span></div>'+
      (d.issues?.length?'<div class="ffh-issues">'+d.issues.map(x=>'<span>'+esc(pretty(x))+'</span>').join('')+'</div>':'')+'</article>';
  }
  function site(s){
    return '<section class="ffh-site"><div class="ffh-site-head"><div><strong>'+esc(s.label||s.site_id)+'</strong><span>'+esc(s.site_id||'')+'</span></div>'+badge(s.state)+'</div>'+
      '<div class="ffh-site-line"><span>Federation '+esc(pretty(s.federation_state))+'</span><span>Diagnostics '+(s.diagnostics_current?'current':'qualified / last-known')+'</span><span>'+Number(s.device_count||0)+' device(s)</span></div>'+
      (s.devices?.length?s.devices.map(device).join(''):'<div class="empty-state">No diagnostics projected for this site.</div>')+'</section>';
  }
  function render(report){
    root.querySelector('[data-ffh-overall]').innerHTML=badge(report.overall_state);
    root.querySelector('[data-ffh-sites]').textContent=Number(report.counts?.sites||0);
    root.querySelector('[data-ffh-devices]').textContent=Number(report.counts?.devices||0);
    root.querySelector('[data-ffh-degraded]').textContent=Number(report.counts?.degraded||0);
    root.querySelector('[data-ffh-critical]').textContent=Number(report.counts?.critical||0);
    root.querySelector('[data-ffh-agent]').textContent=report.agent_context?.summary||'No fleet diagnostic summary.';
    root.querySelector('[data-ffh-list]').innerHTML=(report.sites||[]).length?(report.sites||[]).map(site).join(''):'<div class="empty-state">No fleet diagnostic sites available.</div>';
  }
  async function load(){
    const status=root.querySelector('[data-ffh-status]'); status.textContent='Refreshing…';
    const r=await fetch('/api/v1/control/federation-fleet-health',{credentials:'same-origin',headers:{Accept:'application/json'}});
    const data=await r.json(); if(!r.ok)throw new Error(data.detail||'Fleet diagnostics unavailable.');
    render(data.fleet_health||{}); status.textContent='Authoritative local diagnostics';
  }
  root.querySelector('[data-ffh-refresh]')?.addEventListener('click',()=>load().catch(e=>root.querySelector('[data-ffh-status]').textContent=e.message));
  window.loadFederationFleetHealth=load; load().catch(e=>root.querySelector('[data-ffh-status]').textContent=e.message);
})();