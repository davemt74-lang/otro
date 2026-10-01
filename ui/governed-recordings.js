/* Private saved recordings in canonical HomeServer Health; no background capture. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  let busy=false;
  const base='/api/v1/control/governed-recordings';
  const errors={
    microphone_unavailable:'Microphone unavailable on this machine.',
    ffmpeg_unavailable:'Install or repair the managed FFmpeg package.',
    camera_not_configured:'Configure a trusted local camera device on HomeServer.',
    camera_not_ready:'The hardware runtime does not report a ready camera.',
    capture_failed:'Recording failed; verify devices and local permissions.',
    capture_timeout:'The camera exceeded the bounded capture time.',
    storage_full:'HomeServer recording storage needs free space.',
    recording_exceeds_limit:'Clip exceeded the safe recording size.',
    privacy_switch_engaged:'Turn off the physical privacy switch to record.',
    meeting_in_progress:'End the active physical meeting before recording.',
    capture_in_use:'Another recording is already in progress.',
    transcription_unavailable:'Install or repair local Whisper, or use an audio recording.',
  };
  const status=text=>{const n=$('savedRecordingStatus');if(n)n.textContent=text;};
  async function jsonRequest(url,method='GET',body){
    const response=await fetch(url,{
      method,credentials:'same-origin',cache:'no-store',
      headers:{
        Accept:'application/json',
        ...(method==='GET'?{}:{'X-Requested-With':'XMLHttpRequest','Content-Type':'application/json'}),
      },
      ...(body?{body:JSON.stringify(body)}:{}),
    });
    const data=await response.json();
    if(!response.ok)throw new Error(errors[data.detail]||'Recording request failed ('+response.status+').');
    return data;
  }
  function addText(tag,text,className){
    const el=document.createElement(tag);el.textContent=text;
    if(className)el.className=className;
    return el;
  }
  async function refresh(){
    try{
      const data=await jsonRequest(base);
      const state=data.status||{};
      $('savedRecordingAudio').disabled=busy||!state.audio;
      $('savedRecordingVideo').disabled=busy||!state.video;
      $('savedRecordingAudio').title=state.audio?'':'Install/enable a local microphone.';
      $('savedRecordingVideo').title=state.video?'':'Configure a local camera and FFmpeg.';
      const list=$('savedRecordingList');list.replaceChildren();
      const items=data.recordings?.items||[];
      if(!items.length){list.append(addText('p','No saved recordings. Nothing records automatically.'));return;}
      items.forEach(item=>{
        const row=document.createElement('div');row.className='saved-recording-item';
        const heading=addText('span',(item.kind==='audio'?'Audio':'Video')+' · '+item.seconds+'s · '+item.created_at.slice(0,19));
        const actions=document.createElement('div');actions.className='runtime-diagnostics-actions';
        const download=document.createElement('a');
        download.textContent='Download';download.className='button secondary';
        download.href=base+'/'+encodeURIComponent(item.id)+'/download';
        download.setAttribute('download','');
        const remove=document.createElement('button');
        remove.type='button';remove.className='button secondary';remove.textContent='Delete';
        remove.addEventListener('click',async()=>{
          if(busy||!window.confirm('Permanently delete this private recording?'))return;
          busy=true;remove.disabled=true;
          try{
            await jsonRequest(base+'/'+encodeURIComponent(item.id),'DELETE');
            status('Recording deleted.');await refresh();
          }catch(err){status(err.message);}
          finally{busy=false;remove.disabled=false;}
        });
        if(item.kind==='audio'){
          const transcriptButton=document.createElement('button');
          transcriptButton.type='button';transcriptButton.className='button secondary';
          transcriptButton.textContent=item.has_transcript?'View transcript':'Transcribe';
          transcriptButton.addEventListener('click',async()=>{
            if(busy)return;
            busy=true;transcriptButton.disabled=true;
            try{
              const result=await jsonRequest(base+'/'+encodeURIComponent(item.id)+
                (item.has_transcript?'/transcript':'/transcribe'),item.has_transcript?'GET':'POST');
              showTranscript(result.transcript||'');
              status('Transcript is private. Use Copy or Draft in Agent Chat to share it explicitly.');
              await refresh();
            }catch(err){status(err.message);}
            finally{busy=false;transcriptButton.disabled=false;}
          });
          actions.append(transcriptButton);
        }
        actions.append(download,remove);row.append(heading,actions);list.append(row);
      });
    }catch(err){status(err.message);}
  }
  function showTranscript(text){
    const area=$('savedRecordingTranscript');if(!area)return;
    area.value=String(text||'').slice(0,12000);
    $('savedRecordingTranscriptActions').hidden=false;
    area.hidden=false;area.focus();
  }
  async function capture(kind){
    if(busy)return;
    const seconds=Number($('savedRecordingDuration').value);
    if(!Number.isInteger(seconds)||seconds<2||seconds>30){
      status('Choose a recording length from 2 to 30 seconds.');return;
    }
    const label=kind==='audio'?'microphone':'camera';
    if(!window.confirm('Record '+seconds+' seconds using this HomeServer\'s '+label+'? The clip is saved privately for up to seven days.'))return;
    if(!window.confirm('Confirm you own this HomeServer, the physical privacy controls permit recording, and everyone affected has given any required consent.'))return;
    busy=true;
    $('savedRecordingAudio').disabled=true;$('savedRecordingVideo').disabled=true;
    status('Recording '+label+' locally for '+seconds+' seconds. No Cloud upload.');
    try{
      const result=await jsonRequest(base+'/capture','POST',{
        kind,seconds,consent:true,physical_capture_ack:true,
      });
      status('Private '+label+' recording saved ('+result.recording.seconds+'s).');
      await refresh();
    }catch(err){status(err.message);}
    finally{busy=false;await refresh();}
  }
  function ensure(){
    const home=$('view-health');if(!home||$('savedRecordings'))return;
    const container=document.createElement('section');
    container.className='runtime-diagnostics';
    container.id='savedRecordings';
    container.append(
      addText('h3','Saved audio and video recordings'),
      addText('p','Private, owner-initiated recordings on this HomeServer only. Never activated automatically.','runtime-diagnostics-boundary'),
    );
    const controls=document.createElement('div');controls.className='runtime-diagnostics-actions';
    const duration=document.createElement('label');duration.textContent='Clip length ';
    const picker=document.createElement('select');picker.id='savedRecordingDuration';
    for(const seconds of [3,5,10,15,30]){
      const option=document.createElement('option');option.value=String(seconds);
      option.textContent=String(seconds)+' seconds';picker.append(option);
    }
    duration.append(picker);
    const audio=addText('button','Record microphone');audio.id='savedRecordingAudio';audio.type='button';
    const video=addText('button','Record camera');video.id='savedRecordingVideo';video.type='button';
    audio.addEventListener('click',()=>capture('audio'));
    video.addEventListener('click',()=>capture('video'));
    controls.append(duration,audio,video);
    const message=addText('p','Loading private recording status…','runtime-diagnostics-summary');
    message.id='savedRecordingStatus';message.setAttribute('role','status');message.setAttribute('aria-live','polite');
    const list=document.createElement('div');list.id='savedRecordingList';
    const textarea=document.createElement('textarea');
    textarea.id='savedRecordingTranscript';textarea.rows=6;textarea.hidden=true;
    textarea.readOnly=true;textarea.setAttribute('aria-label','Private local transcription');
    const transcriptActions=document.createElement('div');transcriptActions.id='savedRecordingTranscriptActions';
    transcriptActions.className='runtime-diagnostics-actions';transcriptActions.hidden=true;
    const copy=addText('button','Copy transcription');copy.type='button';
    copy.addEventListener('click',async()=>{
      try{await navigator.clipboard.writeText(textarea.value);status('Transcript copied locally.');}
      catch(_){textarea.select();status('Copy the selected transcript manually.');}
    });
    const draft=addText('button','Draft in Agent Chat');draft.type='button';
    draft.addEventListener('click',()=>{
      const input=$('chatInput');if(!input){status('Open Agent Chat to draft this transcript.');return;}
      const max=Number(input.maxLength||32000);
      const next=[input.value,textarea.value].filter(Boolean).join(String.fromCharCode(10,10));
      if(next.length>max){status('Transcript exceeds Agent Chat draft limit; select a shorter excerpt.');return;}
      input.value=next;
      input.dispatchEvent(new Event('input',{bubbles:true}));
      document.querySelector('[data-view="chat"]')?.click();
      input.focus();
      status('Transcript drafted in Agent Chat; nothing sent until you submit it.');
    });
    transcriptActions.append(copy,draft);
    container.append(controls,message,
      addText('p','Talk and Dictation remain in the existing Agent Chat canvas. Saved recordings use separate private transcription and are never sent to Cloud automatically.','runtime-diagnostics-boundary'),
      list,textarea,transcriptActions);
    const after=$('liveCertification')||$('runtimeDiagnostics');
    if(after)after.insertAdjacentElement('afterend',container);
    else home.append(container);
    refresh();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',ensure,{once:true});
  else ensure();
})();