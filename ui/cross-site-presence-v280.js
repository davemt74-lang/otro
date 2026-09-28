// Tracky V2.80 Section 4 — Mobile Transition & Cross-Site Presence.
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (value='') => String(value).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const pct = (value) => Math.round(Math.max(0,Math.min(1,Number(value||0)))*100)+'%';
  const pretty = (value) => String(value||'unknown').replaceAll('_',' ');
  const when = (value) => {
    if(!value) return 'Unknown';
    const n=Number(value);
    const d=new Date(n>100000000000?n:n*1000);
    return Number.isNaN(d.getTime())?String(value):d.toLocaleString();
  };

  function transitionCard(t){
    const source=t.source_site||{};
    const dest=t.destination_site||null;
    const last=t.last_confirmed_site||null;
    const presence=t.current_presence||null;
    const authority=t.authority||{};
    let statusLine='';
    if(t.state==='arrived' && dest){
      statusLine='<strong>Present at '+esc(dest.label||dest.site_id)+'</strong>';
    }else if(t.state==='arriving' && dest){
      statusLine='<strong>Arriving at '+esc(dest.label||dest.site_id)+'</strong><small>Destination presence is not confirmed yet.</small>';
    }else if(t.state==='in_transit'){
      statusLine='<strong>In transit'+(dest?' to '+esc(dest.label||dest.site_id):'')+'</strong><small>Last confirmed at '+esc(last?.label||last?.site_id||'unknown')+'.</small>';
    }else if(t.state==='departing'){
      statusLine='<strong>Departing '+esc(source.label||source.site_id)+'</strong>';
    }else if(t.state==='offline'){
      statusLine='<strong>Offline during transition</strong><small>Last confirmed at '+esc(last?.label||last?.site_id||'unknown')+'.</small>';
    }else if(t.state==='temporary_context'){
      statusLine='<strong>'+esc(presence?.label||'Temporary context')+'</strong><small>Temporary context only — not a durable site.</small>';
    }else{
      statusLine='<strong>'+esc(pretty(t.state))+'</strong>';
    }
    return '<article class="presence-card '+esc(t.state||'unknown')+'">'+
      '<div class="presence-card-head"><div><span class="presence-kind">'+esc(t.subject_type||t.subject_kind||'subject')+'</span><h4>'+esc(t.subject_label||t.subject_id)+'</h4></div><span class="presence-state">'+esc(pretty(t.state))+'</span></div>'+
      '<div class="presence-route"><span>'+esc(source.label||source.site_id||'Unknown source')+'</span><b>→</b><span>'+esc(dest?.label||dest?.site_id||'Unknown destination')+'</span></div>'+
      '<div class="presence-status">'+statusLine+'</div>'+
      '<div class="presence-meta"><span>Confidence <b>'+pct(t.confidence)+'</b></span><span>Destination <b>'+pct(t.destination_confidence)+'</b></span><span>Updated <b>'+esc(when(t.state_changed_at||t.updated_at))+'</b></span></div>'+
      (authority.subject_is_source_authority?'<div class="presence-warning">This subject is the source-site authority device. Movement does not transfer authority.</div>':'')+
    '</article>';
  }

  function timelineRow(row){
    return '<div class="presence-timeline-row">'+
      '<div><span class="presence-dot '+esc(row.state||'unknown')+'"></span></div>'+
      '<div><strong>'+esc(row.subject_label||row.subject_id)+'</strong><span>'+esc(pretty(row.state))+' · '+esc(row.source_site?.label||row.source_site?.site_id||'')+(row.destination_site?' → '+esc(row.destination_site.label||row.destination_site.site_id):'')+'</span></div>'+
      '<div><span>r'+Number(row.revision||0)+'</span><small>'+esc(when(row.occurred_at))+'</small></div>'+
    '</div>';
  }

  function sitePresenceCard(site){
    return '<div class="presence-site-row"><div><strong>'+esc(site.label||site.site_id)+'</strong><span>'+esc(site.federation_status||'unknown')+'</span></div><div>'+
      '<span>Departing <b>'+Number(site.departing||0)+'</b></span><span>Arriving <b>'+Number(site.arriving||0)+'</b></span><span>Transit <b>'+Number((site.in_transit_from||0)+(site.in_transit_to||0))+'</b></span><span>Offline <b>'+Number(site.offline||0)+'</b></span>'+
    '</div></div>';
  }

  function render(report){
    $('crossSiteActiveCount').textContent=Number(report.active_count||0);
    $('crossSiteTransitCount').textContent=Number(report.state_counts?.in_transit||0);
    $('crossSiteArrivingCount').textContent=Number(report.state_counts?.arriving||0);
    $('crossSiteOfflineCount').textContent=Number(report.state_counts?.offline||0);

    $('crossSiteActive').innerHTML=(report.active_transitions||[]).length
      ?report.active_transitions.map(transitionCard).join('')
      :'<div class="empty-state">No active cross-site transitions.</div>';

    $('crossSiteSites').innerHTML=(report.site_presence||[]).length
      ?report.site_presence.map(sitePresenceCard).join('')
      :'<div class="empty-state">No site presence summary available.</div>';

    $('crossSiteTimeline').innerHTML=(report.timeline||[]).length
      ?report.timeline.slice(0,40).map(timelineRow).join('')
      :'<div class="empty-state">No transition history yet.</div>';

    $('crossSiteHistorySource').textContent=report.history_source==='immutable_transition_revision_history'
      ?'Immutable revision history'
      :'Current snapshots only';

    const ctx=report.agent_context||{};
    $('crossSiteAgentRule').textContent=ctx.destination_claim_rule==='present_at_destination_only_after_arrived'
      ?'Agent confirms destination presence only after ARRIVED.'
      :'Destination claim rule unavailable.';
  }

  async function load(){
    const status=$('crossSiteLoading');
    if(status)status.textContent='Refreshing…';
    const response=await fetch('/api/v1/control/cross-site-presence',{headers:{'Accept':'application/json'}});
    let data={};try{data=await response.json();}catch(_){}
    if(!response.ok)throw new Error(data.detail||'Cross-site presence is unavailable.');
    render(data.presence||{});
    if(status)status.textContent='Live governed transition view';
  }

  window.loadCrossSitePresence=load;
  document.addEventListener('click',event=>{
    if(event.target?.id==='refreshCrossSitePresence'){
      load().catch(err=>{const status=$('crossSiteLoading');if(status)status.textContent=err.message;});
    }
  });
})();