/* Owner-run hardware tests extend existing diagnostics; never run from page load. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  let busy=false;
  const labels={
    ollama_generation:'Ollama generation',
    ollama_tool_call:'Local model tool calling',
    speech_loopback:'Piper → Whisper transcription',
    speaker_playback:'Speaker playback',
    microphone_capture:'Two-second microphone test',
    synthetic_video:'FFmpeg synthetic video',
    agent_eyes:'Tracky / Agent Eyes observation',
  };
  const sensitive=new Set(['microphone_capture','agent_eyes']);
  const post=async body=>{
    const response=await fetch('/api/v1/control/runtime-certification/run',{
      method:'POST',credentials:'same-origin',cache:'no-store',
      headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},
      body:JSON.stringify(body),
    });
    const data=await response.json();
    if(!response.ok)throw new Error(
      typeof data.detail==='string' ? data.detail : 'Certification did not finish.'
    );
    return data;
  };
  function ensure(){
    const parent=$('runtimeDiagnostics'),health=$('view-health');
    if(!health||$('liveCertification'))return;
    const section=document.createElement('section');
    section.className='runtime-diagnostics live-certification';
    section.id='liveCertification';
    section.setAttribute('aria-label','Live hardware certification');
    section.innerHTML=
      '<div class="runtime-diagnostics-head"><div><p class="eyebrow">OWNER-INITIATED</p>'+
      '<h3>Live hardware certification</h3><p>Run one test at a time on this installed HomeServer. '+
      'Tests do not start automatically.</p></div></div>'+
      '<p class="runtime-diagnostics-summary" id="certificationStatus" role="status" aria-live="polite">'+
      'No test running.</p><div id="certificationTests" class="runtime-diagnostics-grid"></div>'+
      '<h4>Recent device test results</h4><div id="certificationHistory" aria-live="polite"></div>'+
      '<p class="runtime-diagnostics-boundary">Microphone and camera tests request separate confirmation. '+
      'No recordings, camera frames, transcripts, model replies or provider keys are saved. '+
      'Successful synthetic video encoding does not certify saved camera recording. '+
      'Speaker playback requires human confirmation that it was audible.</p>';
    if(parent)parent.insertAdjacentElement('afterend',section);
    else health.appendChild(section);
    load();
  }
  function status(message){const el=$('certificationStatus');if(el)el.textContent=message;}
  function renderHistory(data){
    const el=$('certificationHistory');if(!el)return;
    el.replaceChildren();
    const items=data.history?.records||[];
    if(!items.length){el.textContent='No completed tests recorded.';return;}
    const list=document.createElement('ul');list.className='certification-history';
    items.slice(0,15).forEach(item=>{
      const line=document.createElement('li');
      const date=String(item.created_at||'').slice(0,19);
      line.textContent=(labels[item.test_key]||item.test_key)+' — '+item.status+
        ' ('+Math.round((item.duration_ms||0)/1000)+'s) · '+date;
      list.appendChild(line);
    });
    el.appendChild(list);
  }
  function renderTests(catalog){
    const host=$('certificationTests');if(!host)return;
    host.replaceChildren();
    (catalog.tests||[]).forEach(item=>{
      const card=document.createElement('article');card.className='runtime-diagnostic-item';
      const heading=document.createElement('h4');heading.textContent=labels[item.key]||item.key;
      const desc=document.createElement('p');desc.textContent=item.description||'';
      const button=document.createElement('button');button.className='button secondary';
      button.type='button';button.textContent='Run this test';
      if(sensitive.has(item.key))button.textContent='Consent and run';
      button.addEventListener('click',()=>run(item,button));
      card.append(heading,desc,button);host.appendChild(card);
    });
    const unsupported=document.createElement('p');
    unsupported.className='runtime-diagnostics-boundary';
    unsupported.textContent='Not yet certified: saved camera video recording and production audio recording/retention.';
    host.appendChild(unsupported);
  }
  async function load(){
    try{
      const response=await fetch('/api/v1/control/runtime-certification',{
        credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'},
      });
      if(!response.ok)throw new Error('Certification catalog unavailable');
      const data=await response.json();renderTests(data.catalog);renderHistory(data);
    }catch(error){status(String(error.message));}
  }
  async function run(item,button){
    if(busy)return;
    const key=item.key;
    const capture=sensitive.has(key);
    const prompt=capture
      ?key==='agent_eyes'
        ?'Allow one governed local Tracky camera observation? This may update the scene graph. No raw frames are retained by certification.'
        :'Allow two seconds of microphone recording? Raw audio will be discarded immediately.'
      :'Run '+(labels[key]||key)+' on this installed HomeServer?';
    if(!window.confirm(prompt))return;
    if(capture&&!window.confirm('Confirm you are the device owner and everyone affected has given any required recording or camera consent.'))return;
    busy=true;button.disabled=true;
    status('Running '+(labels[key]||key)+' locally; no other certification can start.');
    try{
      const result=await post({test_key:key,consent:true,physical_capture_ack:capture});
      status((labels[key]||key)+': '+result.status+
        '. Device execution '+Math.round(result.duration_ms/1000)+'s. '+
        (key==='speaker_playback'?'Please confirm manually whether the sound was audible.':''));
      await load();
    }catch(error){status('Test could not complete: '+error.message);}
    finally{busy=false;button.disabled=false;}
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',ensure,{once:true});
  else ensure();
})();
