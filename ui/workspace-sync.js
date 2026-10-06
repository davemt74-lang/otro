(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const labels={profile:'Profile',contacts:'Contacts',crm:'CRM',knowledge:'Knowledge & folders',transcriptions:'Transcriptions',calendar:'Calendar & tasks',schedules:'Schedules',meetings:'Meetings',products:'Products',orders:'Orders',agents:'Agents & memory',chats:'Chat history',notifications:'Notifications',music:'Music',workspace_other:'Other account data',artist_workspace:'Artist workspace'};
  const date=value=>{
    if(!value)return 'Not yet synchronized';
    const iso=/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)?value:String(value).replace(' ','T')+'Z';
    const time=new Date(iso);return Number.isFinite(time.getTime())?time.toLocaleString():'Time unavailable';
  };
  let sequence=0,detailSequence=0,offset=0,poll=null,debounce=null,statusPromise=null,visibilityGeneration=0,wasVisible=false;
  function renderFields(host, data) {
    host.replaceChildren();
    for (const [key, value] of Object.entries(data)) {
      if (value === null || value === '' || /^(?:id|.*_id|source_app_key|.*_path)$/.test(key)) continue;
      const label = document.createElement('dt');
      label.textContent = key.replace(/_json$/, '').replaceAll('_', ' ').replace(/^./, char => char.toUpperCase());
      const content = document.createElement('dd');
      let text = typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
      if (/_json$/.test(key)) { try { text = JSON.stringify(JSON.parse(text), null, 2); } catch (_) {} }
      content.textContent = text.slice(0, 20000);
      if (text.length > 20000) {
        const more = document.createElement('button');more.type = 'button';more.textContent = 'Show full text';
        more.addEventListener('click', () => { content.textContent = text; });content.append(more);
      }
      host.append(label, content);
    }
    if (!host.children.length) host.textContent = 'No additional details.';
  }
  async function request(path='',options={}) {
    const controller=new AbortController();
    const timeout=setTimeout(()=>controller.abort(),15000);
    try {
      const response=await fetch('/api/v1/control/workspace-sync'+path,{credentials:'same-origin',cache:'no-store',...options,signal:controller.signal,headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest',...(options.headers||{})}});
      const data=await response.json();
      if(!response.ok)throw Error(data.detail||`Sync unavailable (${response.status})`);
      return data;
    } finally {clearTimeout(timeout);}
  }
  function visible(){return !document.hidden&&$('view-cloud-data')?.classList.contains('active');}
  function statusText(state){
    if(!state.enabled)return 'Automatic sync paused.';
    if(!state.paired)return 'Connect HomeServer to Cloud to begin automatic sync.';
    if(state.last_error)return state.last_error;
    return state.last_success_at?'Last complete sync: '+date(state.last_success_at):'Waiting for the first complete sync…';
  }
  async function loadStatus(){
    if(!visible())return;
    if(statusPromise)return statusPromise;
    const generation=visibilityGeneration;
    statusPromise=(async()=>{
      try {
        const state=await request();
        if(!visible()||generation!==visibilityGeneration)return;
        if(!$('workspaceSyncEnabled').disabled)$('workspaceSyncEnabled').checked=state.enabled;
        $('workspaceSyncStatus').textContent=statusText(state);
      } catch(error){if(visible()&&generation===visibilityGeneration)$('workspaceSyncStatus').textContent=error.message;}
      finally {statusPromise=null;}
    })();
    return statusPromise;
  }
  async function loadRecords(){
    if(!visible())return;
    const seq=++sequence,dataset=$('workspaceSyncDataset').value;
    const data=await request('/records/'+encodeURIComponent(dataset)+'?offset='+offset+'&q='+encodeURIComponent($('workspaceSyncSearch').value));
    if(seq!==sequence||!visible())return;
    const host=$('workspaceSyncRecords');host.replaceChildren();
    for(const item of data.items||[]){
      const button=document.createElement('button');button.type='button';button.className='workspace-sync-record';
      const title=document.createElement('strong');title.textContent=item.title||item.source_id;
      const preview=document.createElement('p');preview.textContent=item.preview||item.table;
      const time=document.createElement('small');time.textContent=date(item.updated_at);
      button.append(title,preview,time);
      button.addEventListener('click',async()=>{
        const detail=++detailSequence;
        button.disabled=true;
        try{
          const details=await request('/records/'+encodeURIComponent(dataset)+'?key='+encodeURIComponent(item.table+':'+item.source_id));
          if(seq!==sequence||detail!==detailSequence||!visible())return;
          $('workspaceSyncDetailTitle').textContent=item.title||item.source_id;
          renderFields($('workspaceSyncDetailText'),details.items?.[0]?.data||{});
          const attachments=$('workspaceSyncAttachments');attachments.replaceChildren();
          for(const file of details.attachments||[]){
            if(!/^[a-f0-9]{64}$/.test(file.sha256))continue;
            const link=document.createElement('a');link.textContent='Download '+file.name;
            link.href='/api/v1/control/workspace-sync/assets/'+file.sha256;link.download=file.name;attachments.append(link);
          }
          $('workspaceSyncDetail').showModal();
        }catch(error){if(seq===sequence&&detail===detailSequence&&visible())$('workspaceSyncStatus').textContent=error.message;}finally{button.disabled=false;}
      });
      host.append(button);
    }
    if(!host.children.length)host.textContent=data.state==='not_paired'?'Waiting for the current pairing to synchronize.':'No synchronized records in this workspace yet.';
    $('workspaceSyncPrevious').disabled=offset===0;
    $('workspaceSyncNext').disabled=offset+50>=data.count;
    $('workspaceSyncPage').textContent=data.count?`${offset+1}–${Math.min(offset+50,data.count)} of ${data.count}`:'0 records';
  }
  function load(){return Promise.all([loadStatus(),loadRecords()]).catch(error=>{if(visible())$('workspaceSyncStatus').textContent=error.message;});}
  function reconcile(){
    const current=visible();
    if(current===wasVisible)return;
    wasVisible=current;visibilityGeneration++;
    if(poll!==null){clearInterval(poll);poll=null;}
    if(current){load();poll=setInterval(()=>loadStatus(),15000);}else{sequence++;detailSequence++;$('workspaceSyncDetail')?.close();}
  }
  function init(){
    if(!$('workspaceSyncDataset'))return;
    for(const [value,label] of Object.entries(labels)){const option=document.createElement('option');option.value=value;option.textContent=label;$('workspaceSyncDataset').append(option);}
    $('workspaceSyncDataset').value='contacts';
    $('workspaceSyncDataset').addEventListener('change',()=>{sequence++;detailSequence++;$('workspaceSyncDetail').close();offset=0;loadRecords().catch(error=>$('workspaceSyncStatus').textContent=error.message);});
    $('workspaceSyncSearch').addEventListener('input',()=>{sequence++;detailSequence++;$('workspaceSyncDetail').close();clearTimeout(debounce);debounce=setTimeout(()=>{offset=0;loadRecords().catch(error=>$('workspaceSyncStatus').textContent=error.message);},250);});
    $('workspaceSyncPrevious').addEventListener('click',()=>{offset=Math.max(0,offset-50);load();});
    $('workspaceSyncNext').addEventListener('click',()=>{offset+=50;load();});
    $('workspaceSyncRefresh').addEventListener('click',async()=>{const button=$('workspaceSyncRefresh');button.disabled=true;try{await request('/refresh',{method:'POST'});$('workspaceSyncStatus').textContent='Synchronization queued. You can keep using HomeServer.';}catch(error){$('workspaceSyncStatus').textContent=error.message;}finally{button.disabled=false;}});
    $('workspaceSyncEnabled').addEventListener('change',async()=>{const checkbox=$('workspaceSyncEnabled');checkbox.disabled=true;try{await request('/settings',{method:'PUT',body:JSON.stringify({enabled:checkbox.checked})});await loadStatus();}catch(error){checkbox.checked=!checkbox.checked;$('workspaceSyncStatus').textContent=error.message;}finally{checkbox.disabled=false;}});
    new MutationObserver(reconcile).observe($('view-cloud-data'),{attributes:true,attributeFilter:['class']});
    document.addEventListener('visibilitychange',reconcile);
    window.addEventListener('pagehide',()=>{if(poll!==null)clearInterval(poll);clearTimeout(debounce);sequence++;});
    reconcile();
  }
  document.readyState==='loading'?document.addEventListener('DOMContentLoaded',init,{once:true}):init();
})();
