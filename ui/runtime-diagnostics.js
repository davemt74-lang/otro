/* Section 32: installed-machine inventory in canonical Health view, no duplicate assistant. */
(() => {
  'use strict';
  const get = id => document.getElementById(id);
  let generation=0,loaded=false;
  const friendly={
    transcription:'Whisper transcription',voice_output:'Piper speech',microphone_capture:'Microphone',
    speaker_device:'Speakers',video_processing:'FFmpeg processing',
    recording_retention:'Saved recordings',camera_hardware:'Camera hardware',
    agent_eyes:'Agent Eyes / Tracky',local_llm:'Local Ollama',
    provider_routing:'Provider routing',hardware_acceleration:'CPU / GPU',
  };
  function ensure(){
    if(get('runtimeDiagnostics'))return;
    const health=get('view-health');
    if(!health)return;
    const section=document.createElement('section');
    section.id='runtimeDiagnostics';
    section.className='runtime-diagnostics';
    section.setAttribute('aria-label','Installed HomeServer runtime diagnostics');
    const header=document.createElement('div');
    header.className='runtime-diagnostics-head';
    header.innerHTML='<div><p class="eyebrow">INSTALLED DEVICE</p><h3>Runtime diagnostics</h3><p>Verify AI, voice, recording, video and Agent Eyes on this HomeServer.</p></div>';
    const actions=document.createElement('div');actions.className='runtime-diagnostics-actions';
    const refresh=document.createElement('button');refresh.className='button secondary';
    refresh.type='button';refresh.textContent='Check installed software';
    const probe=document.createElement('button');probe.className='button secondary';
    probe.type='button';probe.textContent='Test Ollama connection';
    actions.append(refresh,probe);header.appendChild(actions);
    const status=document.createElement('p');status.className='runtime-diagnostics-summary';
    status.id='runtimeDiagnosticsSummary';status.setAttribute('aria-live','polite');
    status.textContent='Inventory not yet checked.';
    const grid=document.createElement('div');grid.id='runtimeDiagnosticsGrid';grid.className='runtime-diagnostics-grid';
    const boundary=document.createElement('p');boundary.className='runtime-diagnostics-boundary';
    boundary.textContent='No microphones, camera frames, saved recordings, model generation, API keys or repairs are accessed by these checks. Status marked not verified requires an explicit on-device acceptance test.';
    section.append(header,status,grid,boundary);
    const maintenance=get('maintenanceWorkspace');
    if(maintenance)maintenance.insertAdjacentElement('afterend',section);
    else health.appendChild(section);
    refresh.addEventListener('click',()=>load(false));
    probe.addEventListener('click',()=>load(true));
    document.querySelectorAll('[data-view="health"],[data-go="health"]').forEach(node=>node.addEventListener('click',()=>{
      if(!loaded)load(false);
    }));
    if(health.classList.contains('active'))load(false);
  }
  function render(data){
    const summary=get('runtimeDiagnosticsSummary'),host=get('runtimeDiagnosticsGrid');
    if(!host||!summary)return;
    host.replaceChildren();
    const checks=Array.isArray(data.checks)?data.checks:[];
    const counts=data.summary||{};
    summary.textContent=checks.length+' checks · '+(counts.missing||0)+' missing · '+
      (counts.degraded||0)+' degraded · '+(counts.not_verified||0)+' need end-to-end verification';
    checks.forEach(check=>{
      const card=document.createElement('article');card.className='runtime-diagnostic-item';
      card.dataset.state=String(check.status||'not_verified');
      const state=document.createElement('span');state.className='runtime-diagnostic-state';
      state.textContent=String(check.status||'not_verified').replaceAll('_',' ');
      const h=document.createElement('h4');h.textContent=friendly[check.name]||String(check.name);
      const description=document.createElement('p');description.textContent=String(check.evidence||'');
      const next=document.createElement('small');next.textContent=String(check.next_step||'');
      card.append(state,h,description,next);host.append(card);
    });
    loaded=true;
  }
  async function load(probe){
    const gen=++generation;
    const summary=get('runtimeDiagnosticsSummary');
    if(summary)summary.textContent=probe?'Checking Ollama connectivity…':'Reading installed runtime status…';
    try{
      const response=await fetch(
        probe?'/api/v1/control/runtime-diagnostics/safe-probe':'/api/v1/control/runtime-diagnostics',
        {method:probe?'POST':'GET',credentials:'same-origin',cache:'no-store',headers:{'Accept':'application/json'}},
      );
      if(!response.ok)throw new Error('Diagnostics unavailable ('+response.status+')');
      const data=await response.json();
      if(gen!==generation)return;
      render(data);
    }catch(error){if(gen===generation&&summary)summary.textContent='Unable to read HomeServer diagnostics. '+String(error.message);}
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',ensure,{once:true});
  else ensure();
})();
