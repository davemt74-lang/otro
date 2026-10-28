/* Text-only bridge from existing local participants to the paired workspace queue. */
export function createParticipantMemorySync(store) {
  const endpoint='/api/v1/control/workspace-sync/participants';
  let snapshot=null,active=null,stopped=false,lastError='Connecting participant memory…';
  const projection=p=>({name:p?.name||'',note:p?.agentConversationNote||'',deleted:!p});
  const same=(a,b)=>!!a&&!!b&&a.deleted===b.deleted&&(a.deleted||(a.name===b.name&&a.note===b.note));
  const remoteValue=r=>({name:r.name,note:r.note,deleted:r.deleted});
  const normalized=n=>String(n).normalize('NFKC').trim().toLocaleLowerCase();
  const notify=()=>window.dispatchEvent(new Event('vp3:participant-sync-status'));
  async function api(path='',body) {
    const response=await fetch(endpoint+path,{method:body?'POST':'GET',credentials:'same-origin',cache:'no-store',headers:body?{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'}:{},body:body?JSON.stringify(body):undefined});
    const data=await response.json();if(!response.ok){const error=Error(data.detail||'Participant sync unavailable. Local changes are kept.');error.status=response.status;throw error;}return data;
  }
  const all=()=>store.participantMemoryAction(async({participants,links,request})=>({people:await request(participants.getAll()),links:await request(links.getAll())}));
  async function run() {
    if(stopped)return;
    if(active){await active;return run();}
    active=cycle();try{await active;}finally{active=null;}
  }
  async function cycle() {
    try {
      const previousPeer=snapshot?.peer_id;
      let after='',pages=[],seen=new Set(),pending=[];
      do {
        const page=await api(after?'?after='+encodeURIComponent(after):'');
        if(snapshot&&pages.length&&page.peer_id!==snapshot.peer_id)throw Error('Participant pairing changed. Retrying with the current account.');
        if(!page.peer_id||!Array.isArray(page.participants)||!Array.isArray(page.pending))throw Error('Invalid participant memory response.');
        snapshot={...page,participants:[]};pages.push(...page.participants);pending.push(...page.pending);
        after=page.next_after||'';if(after&&seen.has(after))throw Error('Participant sync page repeated.');seen.add(after);
      }while(after);
      snapshot.participants=pages;snapshot.pending=pending;
      if(previousPeer!==snapshot.peer_id){window.VP3ParticipantContinuity?.clearSelection?.();window.VP3ParticipantContinuity?.invalidate?.();window.VP3VoiceGuard?.clear?.();}
      const peer=snapshot.peer_id,remotes=new Map(pages.map(r=>[r.key,r])),queued=new Map(pending.map(p=>[p.key,p]));
      let changed=false;
      // Import only names that do not collide with an existing local record.
      // A same-name record is a candidate for an explicit link, never identity proof.
      await store.participantMemoryAction(async({participants,links,request})=>{
        const people=await request(participants.getAll()),bindings=await request(links.getAll());
        if(snapshot.initial_complete&&snapshot.enabled!==false){
          for(const person of people){
            if(bindings.some(l=>l.id===person.id)||pages.some(r=>!r.deleted&&normalized(r.name)===normalized(person.name))||!person.name?.trim())continue;
            const link={id:person.id,peer,key:crypto.randomUUID().replaceAll('-',''),base:null,deleted:false};
            await request(links.put(link));bindings.push(link);
          }
        }
        for(const remote of pages){
          if(bindings.some(l=>l.peer===peer&&l.key===remote.key)||remote.deleted||people.some(p=>normalized(p.name)===normalized(remote.name)))continue;
          const id='cloud-'+crypto.randomUUID();
          const person=store.participantRecord({id,name:remote.name,agentConversationNote:remote.note,recognitionEnabled:false,voiceRecognitionEnabled:false});
          await request(participants.put(person));const link={id,peer,key:remote.key,base:remote,deleted:false};await request(links.put(link));people.push(person);bindings.push(link);changed=true;
        }
        for(const link of bindings){
          if(link.peer!==peer)continue;
          const remote=remotes.get(link.key);if(!remote)continue;
          const person=people.find(p=>p.id===link.id),local=projection(person),q=queued.get(link.key);
          if(same(local,remoteValue(remote))){await request(links.put({...link,base:remote,deleted:remote.deleted}));continue;}
          if(link.review||!same(local,link.base)||q)continue;
          if(remote.deleted){
            // Commit the tombstone before invoking the existing deletion cleanup.
            await request(links.put({...link,base:remote,remoteDeleted:true,deleted:true,deletionExpected:local}));
          }else if(person){
            await request(participants.put({...person,name:remote.name,agentConversationNote:remote.note,updatedAt:new Date().toISOString()}));
            await request(links.put({...link,base:remote,deleted:false}));changed=true;
          }
        }
      });
      let data=await all();
      for(const l of data.links){
        if(l.peer===peer&&l.remoteDeleted&&data.people.some(p=>p.id===l.id)){
          const removed=await store.deleteParticipant(l.id,{remote:true,guard:p=>same(projection(p),l.deletionExpected)});
          if(!removed)await store.participantMemoryAction(async({links,request})=>{const latest=await request(links.get(l.id));if(latest?.key===l.key)await request(links.put({...latest,remoteDeleted:false}));});
          changed=true;
        }
      }
      data=await all();const changes=[];
      for(const link of data.links){
        if(link.peer!==peer)continue;
        const p=data.people.find(p=>p.id===link.id),local=projection(p),q=queued.get(link.key);
        if(link.review||same(local,link.base)||q?.status==='conflict'||q?.status==='protected')continue;
        changes.push({key:link.key,local_id:link.id,expected_revision:link.base?.revision||'',...local,resolve:false});
      }
      for(let i=0;i<changes.length;i+=25)await api('',{peer_id:peer,changes:changes.slice(i,i+25)});
      lastError='';
      if(changed||previousPeer!==peer){window.VP3ParticipantContinuity?.clearSelection?.();window.VP3ParticipantContinuity?.invalidate?.();window.VP3VoiceGuard?.clear?.();await window.VP3AgentEyes?.refreshProfiles?.();window.dispatchEvent(new Event('vp3:participant-memory-changed'));}
    }catch(error){if([401,403,409].includes(error.status)){snapshot=null;window.VP3ParticipantContinuity?.clearSelection?.();window.VP3ParticipantContinuity?.invalidate?.();}lastError=String(error.message||'Participant sync unavailable. Local changes are kept.');}
    finally{notify();}
  }
  async function status(id) {
    const data=await all(),link=data.links.find(l=>l.id===id),p=data.people.find(p=>p.id===id);
    if(!snapshot)return {text:lastError||'Participant sync is waiting for Cloud pairing.',options:[]};
    if(link&&link.peer!==snapshot.peer_id)return {text:'This participant is linked to a different Cloud account. Its details remain separate.',options:[]};
    const remote=link?snapshot.participants.find(r=>r.key===link.key):null,q=link?snapshot.pending.find(r=>r.key===link.key):null;
    if(link?.review||q&&q.status!=='queued')return {text:'Both versions are kept. Choose which participant details to use.',conflict:true,local:projection(p),remote:remote||null,protected:q?.status==='protected'};
    if(link){
      const dirty=!same(projection(p),link.base);
      return {text:lastError||snapshot.error||(snapshot.enabled===false?'Sync paused. Local changes are kept.':dirty||q?'Participant changes queued for Cloud.':snapshot.last_success_at?'Participant memory is synchronized.':'Waiting for the first participant sync.'),linked:true};
    }
    const used=new Set(data.links.filter(l=>l.peer===snapshot.peer_id).map(l=>l.key));
    return {text:lastError||'Local participant. Choose a Cloud record to link, or create a separate record.',options:snapshot.participants.filter(r=>!r.deleted&&!used.has(r.key)).map(r=>({key:r.key,label:r.name+' · Cloud #'+r.id})),canLink:!!p};
  }
  async function link(id,key) {
    if(!snapshot)throw Error(lastError||'Pair Cloud before linking participants.');
    const peer=snapshot.peer_id,remote=key?snapshot.participants.find(p=>p.key===key&&!p.deleted):null;
    if(key&&!remote)throw Error('Cloud participant unavailable. Refresh and retry.');
    await store.participantMemoryAction(async({participants,links,request})=>{
      const p=await request(participants.get(id)),bindings=await request(links.getAll());
      if(!p||bindings.some(l=>l.id===id||key&&l.peer===peer&&l.key===key))throw Error('Participant already linked or unavailable.');
      await request(links.put({id,peer,key:key||crypto.randomUUID().replaceAll('-',''),base:remote||null,deleted:false,review:!!remote&&!same(projection(p),remoteValue(remote))}));
      // Linking preserves the local details. A difference becomes an explicit conflict.
    });
    await run();notify();
  }
  async function resolve(id,choice) {
    const data=await all(),link=data.links.find(l=>l.id===id&&l.peer===snapshot?.peer_id),p=data.people.find(p=>p.id===id);
    if(!link)throw Error('Participant link unavailable.');
    const remote=snapshot.participants.find(r=>r.key===link.key),q=snapshot.pending.find(r=>r.key===link.key);
    if(!remote||(!link.review&&(!q||q.status==='queued')))throw Error('Refresh participant sync before resolving this change.');
    const before=projection(p);
    if(choice==='cloud'){
      if(q)await api('/use-cloud',{peer_id:link.peer,key:link.key,change_id:q.change.change_id});
      await store.participantMemoryAction(async({participants,links,request})=>{
        const current=await request(participants.get(id));if(!same(projection(current),before))throw Error('Participant changed while resolving. Review both versions again.');
        if(!remote.deleted&&current)await request(participants.put({...current,name:remote.name,agentConversationNote:remote.note}));
        // Deleted local records can be restored as text-only records by explicit choice.
        if(!remote.deleted&&!current)await request(participants.put(store.participantRecord({id,name:remote.name,agentConversationNote:remote.note,recognitionEnabled:false,voiceRecognitionEnabled:false})));
        await request(links.put({...link,base:remote,deleted:remote.deleted,remoteDeleted:remote.deleted,deletionExpected:before,review:false}));
      });
      if(remote.deleted&&!await store.deleteParticipant(id,{remote:true,guard:p=>same(projection(p),before)}))throw Error('Participant changed while resolving. Your newer local details were kept.');
    }else{
      if(q?.status==='protected')throw Error('Your Cloud account participant cannot be removed. Use Cloud details to restore it.');
      if(remote.deleted&&!before.deleted){
        if(q)await api('/use-cloud',{peer_id:link.peer,key:link.key,change_id:q.change.change_id});
        await store.participantMemoryAction(async({participants,links,request})=>{
          if(!same(projection(await request(participants.get(id))),before))throw Error('Participant changed. Review the details again.');
          await request(links.put({...link,key:crypto.randomUUID().replaceAll('-',''),base:null,deleted:false,remoteDeleted:false}));
        });
      }else{
        await api('',{peer_id:link.peer,changes:[{key:link.key,local_id:id,expected_revision:remote.revision,...before,resolve:true}]});
        await store.participantMemoryAction(async({links,request})=>{const latest=await request(links.get(id));if(latest?.key===link.key)await request(links.put({...latest,base:remote,review:false}));});
      }
    }
    window.VP3ParticipantContinuity?.clearSelection?.();window.VP3ParticipantContinuity?.invalidate?.();await window.VP3AgentEyes?.refreshProfiles?.();await run();notify();
  }
  const timer=setInterval(()=>void run(),15000),online=()=>void run();window.addEventListener('online',online);
  window.addEventListener('pagehide',()=>{stopped=true;clearInterval(timer);window.removeEventListener('online',online);},{once:true});
  void run();
  async function people(){const data=await all();return data.people.filter(p=>!data.links.some(l=>l.id===p.id&&l.peer!==snapshot?.peer_id));}
  async function details(id){const data=await all(),l=data.links.find(l=>l.id===id),p=data.people.find(p=>p.id===id);if(l&&l.peer!==snapshot?.peer_id||!p&&!l?.deleted)throw Error('Participant unavailable for the current account.');return {id,name:p?.name||l?.base?.name||'Removed participant',note:p?.agentConversationNote||(!p?l?.base?.note:'')||''};}
  async function list(){const data=await all();return [...await people(),...data.links.filter(l=>l.peer===snapshot?.peer_id&&l.deleted&&!data.people.some(p=>p.id===l.id)&&snapshot?.pending.some(q=>q.key===l.key)).map(l=>({id:l.id,name:(l.base?.name||'Participant')+' (removed locally)'}))];}
  return {run,status,link,resolve,list,people,details,remove:async id=>{await store.deleteParticipant(id);await run();}};
}
