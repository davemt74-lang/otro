/* Native workspace views share the account-bound replica and original source editors. */
(() => {
  'use strict';
  const labels={calendar:'Calendar',crm:'CRM',products:'Products',transcriptions:'Transcriptions',meetings:'Meetings',schedules:'Schedules'};
  const $=id=>document.getElementById(id);
  const base='/api/v1/control/workspace-sync';
  const state=new Map();
  let detailEpoch=0;
  const stamp=value=>value?new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)?value:String(value).replace(' ','T')+'Z').toLocaleString():'Not synchronized yet';
  async function request(path,options={}){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    try{
      const response=await fetch(base+path,{cache:'no-store',credentials:'same-origin',...options,signal:controller.signal});
      const data=await response.json();if(!response.ok){const error=Error(data.detail||'Workspace unavailable');error.status=response.status;throw error;}return data;
    }finally{clearTimeout(timer);}
  }
  function element(tag,text,className=''){
    const node=document.createElement(tag);node.textContent=text;if(className)node.className=className;return node;
  }
  function sourceLink(dataset,key){
    const link=element('a','Manage in Cloud','button secondary');
    link.href=base+'/source/'+encodeURIComponent(dataset)+'?key='+encodeURIComponent(key);
    link.target='_blank';link.rel='noopener noreferrer';return link;
  }
  async function edit(dataset,key){
    const epoch=++detailEpoch,dialog=$('nativeWorkspaceDetail');
    dialog.replaceChildren(element('p','Loading editor…'));if(!dialog.open)dialog.showModal();
    try{
      const data=await request('/edit/'+encodeURIComponent(dataset)+'?key='+encodeURIComponent(key));
      if(epoch!==detailEpoch||!dialog.open)return;
      const row=data.record,form=document.createElement('form'),inputs=new Map();
      let retryPayload=null;
      dialog.replaceChildren(element('h2','Edit '+(row.data.title||row.data.name||key)),element('p','Changes save to Cloud. Offline changes wait here and are checked against the original revision.','muted'));
      const feedback=element('p','','muted');feedback.setAttribute('role','status');dialog.append(feedback);
      for(const action of data.actions||[])feedback.textContent=action.state+' · '+(action.error||'Updated '+stamp(action.updated_at));
      for(const [field,limit] of Object.entries(data.editable_fields)){
        const label=element('label',field.replaceAll('_',' '),'native-edit-field');
        const input=document.createElement(limit>500?'textarea':'input');input.name=field;
        if(field==='all_day'){input.type='checkbox';input.checked=Boolean(data.editable_values[field]);}
        else{
          if(field==='date'||field==='end_date')input.type='date';
          else if(field==='start_time'||field==='end_time')input.type='time';
          else if(field==='email')input.type='email';
          input.value=String(data.editable_values[field]??'');input.maxLength=limit;
          // A title-only edit must never truncate a larger original document.
          if(input.value.length>limit){input.disabled=true;label.append(element('small','Use the Cloud editor to change this larger field.','muted'));}
          if(field==='title'||field==='display_name')input.required=true;
        }
        label.append(input);form.append(label);inputs.set(field,input);
      }
      const save=element('button','Save to Cloud','button primary');save.type='submit';
      const close=element('button','Close','button secondary');close.type='button';close.onclick=()=>dialog.close();
      form.append(save,close);dialog.append(form);
      form.onsubmit=async event=>{
        event.preventDefault();
        if(!retryPayload){
          const fields={};
          for(const [field,input] of inputs){if(input.disabled)continue;const value=field==='all_day'?input.checked:input.value;if(value!==data.editable_values[field])fields[field]=value;}
          if(!Object.keys(fields).length){feedback.textContent='No fields changed.';return;}
          const bytes=new Uint8Array(16);crypto.getRandomValues(bytes);
          const mutation=Array.from(bytes,value=>value.toString(16).padStart(2,'0')).join('');
          retryPayload={dataset,key,expected_revision:row.record_revision,mutation_id:mutation,fields};
        }
        save.disabled=true;for(const input of inputs.values())input.disabled=true;
        feedback.textContent='Queuing your change…';
        try{
          const result=await request('/edits',{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},body:JSON.stringify(retryPayload)});
          if(epoch!==detailEpoch||!dialog.open)return;
          form.remove();feedback.textContent=result.action.state==='synced'?'Saved and synchronized.':'Change '+result.action.state+'. Agent Brain shows delivery and any conflicts. The original copy stays visible until synchronization confirms it.';
          const inspect=element('button','Refresh record & status','button secondary');inspect.onclick=()=>details(dataset,key);dialog.append(inspect,close);
          window.dispatchEvent(new Event('homeserver:workspace-edits'));
        }catch(error){
          if(epoch!==detailEpoch||!dialog.open)return;
          feedback.textContent=error.message;
          if(error.status){retryPayload=null;for(const [field,input] of inputs)input.disabled=typeof data.editable_values[field]==='string'&&data.editable_values[field].length>data.editable_fields[field];save.textContent='Save to Cloud';}
          else{save.textContent='Retry same change';feedback.textContent='Delivery status is uncertain. Retry this same change; your draft is preserved.';}
          save.disabled=false;
        }
      };
    }catch(error){if(epoch===detailEpoch&&dialog.open){dialog.replaceChildren(element('p',error.message));const back=element('button','Back to record','button secondary');back.onclick=()=>details(dataset,key);dialog.append(back);}}
  }
  async function details(dataset,key){
    const epoch=++detailEpoch,dialog=$('nativeWorkspaceDetail');
    dialog.replaceChildren(element('p','Loading record…'));if(!dialog.open)dialog.showModal();
    try{
      const data=await request('/records/'+encodeURIComponent(dataset)+'?key='+encodeURIComponent(key));
      if(epoch!==detailEpoch||!dialog.open)return;
      const record=data.items?.[0];if(!record)throw Error('This record is no longer available. Refresh the workspace.');
      dialog.replaceChildren();
      const close=element('button','Close','button secondary');close.type='button';close.onclick=()=>dialog.close();dialog.append(close);
      const title=record.data.title||record.data.name||record.data.display_name||key;
      dialog.append(element('h2',String(title)),element('p','VP3 Cloud · last checked '+stamp(data.synced_at),'muted'));
      if(window.HomeServerNativeSourceTables.has(record.table))dialog.append(sourceLink(dataset,key));
      if(record.record_revision&&['crm_contacts','knowledge_items','user_calendar_events'].includes(record.table)){
        const button=element('button','Edit here','button primary');button.type='button';button.onclick=()=>edit(dataset,key);dialog.append(button);
        const actions=await request('/edits');
        if(epoch!==detailEpoch||!dialog.open)return;
        for(const action of actions.items||[]){if(action.dataset===dataset&&action.record_key===key)dialog.append(element('p',action.state+' · '+(action.error||stamp(action.updated_at)),'muted'));}
      }
      for(const file of data.attachments||[]){
        if(!/^[a-f0-9]{64}$/.test(file.sha256))continue;
        const link=element('a','Download '+file.name,'button secondary');link.href=base+'/assets/'+file.sha256;link.download=file.name;dialog.append(link);
      }
      const list=document.createElement('dl');
      for(const [field,value] of Object.entries(record.data)){
        if(value==null||value===''||field==='id'||field.endsWith('_id')||field.endsWith('_path'))continue;
        list.append(element('dt',field.replaceAll('_',' ')),element('dd',typeof value==='object'?JSON.stringify(value,null,2):String(value)));
      }
      dialog.append(list);
    }catch(error){if(epoch===detailEpoch&&dialog.open){dialog.replaceChildren(element('p',error.message));const close=element('button','Close');close.onclick=()=>dialog.close();dialog.append(close);}}
  }
  async function load(dataset){
    const view=$('view-native-'+dataset),s=state.get(dataset);
    if(!view||!view.classList.contains('active'))return;
    const epoch=++s.epoch;s.status.textContent='Loading…';
    try{
      const [data,status]=await Promise.all([request('/native/'+dataset+'?offset='+s.offset+'&q='+encodeURIComponent(s.search.value)),request('')]);
      if(epoch!==s.epoch||!view.classList.contains('active'))return;
      s.status.textContent=status.last_error||(!status.enabled?'Automatic sync paused. ': '')+'Last checked '+stamp(data.synced_at)+' · '+data.count+' Cloud records';
      s.host.replaceChildren();
      for(const row of data.items){
        const card=element('article','','panel');card.append(element('h3',row.title),element('p',row.snippet||row.location||row.sku||row.table));
        if(row.start_at_utc)card.append(element('p',stamp(row.start_at_utc)+' – '+stamp(row.end_at_utc)));
        const button=element('button','Details & original files','button secondary');button.type='button';button.onclick=()=>details(dataset,row.record_key);card.append(button);
        if(row.source_route)card.append(sourceLink(dataset,row.record_key));
        s.host.append(card);
      }
      if(!data.items.length)s.host.append(element('p',data.state==='not_paired'?'Connect HomeServer to Cloud to load this workspace.':'No matching records.'));
      s.previous.disabled=s.offset===0;s.next.disabled=s.offset+50>=data.count;
      s.page.textContent=data.count?`${s.offset+1}–${Math.min(s.offset+50,data.count)} of ${data.count}`:'0 records';
    }catch(error){if(epoch===s.epoch)s.status.textContent=error.message;}
  }
  function init(){
    const nav=document.querySelector('.nav'),container=$('view-cloud-data')?.parentElement;
    if(!nav||!container)return;
    const dialog=document.createElement('dialog');dialog.id='nativeWorkspaceDetail';dialog.className='native-workspace-detail';dialog.addEventListener('close',()=>detailEpoch++);document.body.append(dialog);
    window.HomeServerNativeSourceTables=new Set(['crm_contacts','knowledge_items','artist_transcript_folders_v177','user_calendar_events','artist_transcript_sessions_v172','agent_commerce_products_v800','agent_scheduling_schedules','agent_scheduling_bookings','agent_scheduling_event_types','video_meetings']);
    for(const [dataset,label] of Object.entries(labels)){
      const name='native-'+dataset,button=element('button',label,'nav-item');button.dataset.view=name;button.type='button';nav.append(button);
      const view=element('section','','view');view.id='view-'+name;
      view.append(element('h2',label),element('p','Cloud records are available here from the last complete sync. Supported records can be edited here; saved changes synchronize automatically.','muted'));
      if(dataset==='schedules')view.append(element('p','Schedules and reminders execute only on their owning system.','muted'));
      const status=element('p','','muted');status.setAttribute('role','status');view.append(status);
      const toolbar=element('div','','toolbar'),search=document.createElement('input');search.type='search';search.placeholder='Search '+label.toLowerCase();search.maxLength=240;search.setAttribute('aria-label',search.placeholder);toolbar.append(search);
      const refresh=element('button','Sync now','button secondary');refresh.type='button';toolbar.append(refresh);view.append(toolbar);
      const host=element('div','','cards-list');view.append(host);
      const pages=element('div','','toolbar'),previous=element('button','Previous','button secondary'),page=element('span',''),next=element('button','Next','button secondary');pages.append(previous,page,next);view.append(pages);container.append(view);
      const s={epoch:0,offset:0,status,host,search,previous,next,page};state.set(dataset,s);
      search.oninput=()=>{s.epoch++;clearTimeout(s.debounce);s.offset=0;s.debounce=setTimeout(()=>load(dataset),250);};
      previous.onclick=()=>{s.offset=Math.max(0,s.offset-50);load(dataset);};next.onclick=()=>{s.offset+=50;load(dataset);};
      refresh.onclick=async()=>{refresh.disabled=true;try{await request('/refresh',{method:'POST',headers:{'X-Requested-With':'XMLHttpRequest'}});status.textContent='Sync queued. The previous complete copy remains available.';}catch(error){status.textContent=error.message;}finally{refresh.disabled=false;}};
    }
    document.addEventListener('click',event=>{const button=event.target.closest('[data-workspace-detail]');if(button)details(button.dataset.workspaceDetail,button.dataset.recordKey);});
    window.addEventListener('focus',()=>{
      const current=document.querySelector('.view.active')?.id||'';
      if(current.startsWith('view-native-'))load(current.slice(12));
      if(current==='view-contacts')window.loadHomeServerContacts?.();
    });
    window.loadHomeServerNativeWorkspace=load;
    const initial=location.hash.slice(1);
    if(initial.startsWith('native-')&&labels[initial.slice(7)])window.openHomeServerView(initial);
    const timer=setInterval(()=>{if(document.visibilityState==='visible'){const current=document.querySelector('.view.active')?.id||'';if(current.startsWith('view-native-'))load(current.slice(12));}},15000);
    window.addEventListener('pagehide',()=>clearInterval(timer),{once:true});
  }
  document.readyState==='loading'?document.addEventListener('DOMContentLoaded',init,{once:true}):init();
})();
