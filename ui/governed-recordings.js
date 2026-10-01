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
        actions.append(download,remove);row.append(heading,actions);list.append(row);
      });
    }catch(err){status(err.message);}
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
    container.append(controls,message,
      addText('p','Recordings expire after seven days. Only you can download or delete them; video requires a camera configured on the installed HomeServer.','runtime-diagnostics-boundary'),
      list);
    const after=$('liveCertification')||$('runtimeDiagnostics');
    if(after)after.insertAdjacentElement('afterend',container);
    else home.append(container);
    refresh();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',ensure,{once:true});
  else ensure();
})();