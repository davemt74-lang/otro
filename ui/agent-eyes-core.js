/* Shared VP3/HomeServer presence lifecycle. Track IDs never confer identity. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;if(root)root.VP3EyesCore=api;})(typeof window==='object'?window:null,()=>{
  'use strict';
  const vector=v=>Array.isArray(v)&&v.length>=16&&v.length<=2048&&v.every(Number.isFinite);
  function similarity(a,b){
    if(!vector(a)||!vector(b)||a.length!==b.length)return -1;
    const ma=Math.max(...a.map(Math.abs)),mb=Math.max(...b.map(Math.abs));if(!ma||!mb)return -1;
    let dot=0,aa=0,bb=0;for(let i=0;i<a.length;i++){const x=a[i]/ma,y=b[i]/mb;dot+=x*y;aa+=x*x;bb+=y*y;}return Math.max(-1,Math.min(1,dot/Math.sqrt(aa*bb)));
  }
  function match(embedding,profiles){
    const ranked=(profiles||[]).filter(p=>p.recognitionEnabled!==false&&p.embeddings?.filter(vector).length>=3).map(p=>{
      const scores=p.embeddings.map(v=>similarity(embedding,v)).sort((a,b)=>b-a).slice(0,3);
      return {id:String(p.id),name:String(p.name||'Participant').slice(0,120),score:scores.reduce((a,b)=>a+b,0)/scores.length};
    }).sort((a,b)=>b.score-a.score);
    return ranked[0]?.score>=.65&&ranked[0].score-(ranked[1]?.score||0)>=.06?ranked[0]:null;
  }
  function box(value){if(!Array.isArray(value)||value.length!==4||value.some(v=>!Number.isFinite(v)||v<0||v>1)||value[2]<=0||value[3]<=0||value[0]+value[2]>1.001||value[1]+value[3]>1.001)return null;return value;}
  const separation=(a,b)=>Math.hypot(a[0]+a[2]/2-b[0]-b[2]/2,a[1]+a[3]/2-b[1]-b[3]/2);
  function create({now=()=>Date.now(),id=()=>crypto.randomUUID(),cooldown=600000}={}){
    const tracks=new Map(),greeted=new Map();let lastGreeting=-Infinity;
    function update(observations,profiles=[]){
      const at=now(),events=[],rows=[];
      for(const [key,t] of tracks)if(at-t.seen>=8000){if(t.arrived)events.push(event(t,'departure'));tracks.delete(key);}
      const inputs=(Array.isArray(observations)?observations:[]).slice(0,8).filter(o=>o&&box(o.box));
      const edges=[];
      for(const observation of inputs)for(const t of tracks.values()){
        const gap=at-t.seen;if(gap<0||gap>=4500)continue;
        const bounds=observation.box,appearance=similarity(observation.embedding,t.embedding);
        if(appearance>=0&&appearance<.65)continue;
        const ratio=bounds[2]*bounds[3]/(t.box[2]*t.box[3]);if(ratio<.4||ratio>2.5)continue;
        const predicted=t.box.slice();for(let axis=0;axis<2;axis++)predicted[axis]+=Math.max(-.18,Math.min(.18,(t.velocity?.[axis]||0)*Math.min(gap,2000)));
        const d=separation(bounds,predicted),limit=.16+Math.min(.06,Math.max(0,gap-1000)*.00002);
        if(d>=limit||separation(bounds,t.box)>=.38)continue;
        edges.push({observation,t,cost:d+(appearance<0?0:(1-appearance)*.12)});
      }
      // Decide the entire frame before mutating tracks. Both ends must have a
      // unique best candidate, so detector ordering cannot steal a track/name.
      const uniqueBest=list=>{list.sort((a,b)=>a.cost-b.cost);return list[0]&&(!list[1]||list[1].cost-list[0].cost>.025)?list[0]:null;};
      const assignments=new Map();
      for(const observation of inputs){const best=uniqueBest(edges.filter(e=>e.observation===observation));if(best&&uniqueBest(edges.filter(e=>e.t===best.t))===best)assignments.set(observation,best.t);}
      for(const observation of inputs){
        const bounds=observation.box,previous=assignments.get(observation);
        const t=previous||{id:id(),first:at,seen:at,box:bounds,arrived:false,person:null,candidate:'',streak:0,greeted:false,rejected:new Set()};
        const gap=at-t.seen;if(previous&&!t.arrived&&gap>2000)t.first=at;t.tracking=previous?(gap>2000?'reacquired':'tracked'):'new';
        if(previous&&gap>0){t.velocity=[0,1].map(axis=>{const displacement=bounds[axis]+bounds[axis+2]/2-t.box[axis]-t.box[axis+2]/2;const measured=Math.max(-.00035,Math.min(.00035,displacement/gap));return .5*measured+.5*(t.velocity?.[axis]||0);});}
        const matched=match(observation.embedding,profiles),person=matched&&!t.rejected?.has(matched.id)?matched:null;
        if(t.candidate===(person?.id||''))t.streak++;else{t.candidate=person?.id||'';t.streak=1;}
        const introduced=t.introducedId&&profiles.find(p=>String(p.id)===t.introducedId);
        const resolved=person&&t.streak>=3?person:introduced?{id:String(introduced.id),name:String(introduced.name).slice(0,120),introduced:true}:null;
        const changed=t.person?.id!==(resolved?.id);
        t.seen=at;t.box=bounds.slice();t.embedding=vector(observation.embedding)?observation.embedding.slice():null;t.person=resolved;tracks.set(t.id,t);rows.push(t);
        if(!t.arrived&&at-t.first>=3000){t.arrived=true;events.push(event(t,'arrival'));}
        else if(t.arrived&&changed)events.push(event(t,'identity_changed'));
      }
      // Two tracks claiming one profile are both unidentified.
      const counts=new Map();for(const t of rows)if(t.person)counts.set(t.person.id,(counts.get(t.person.id)||0)+1);
      for(const t of rows)if(t.person&&counts.get(t.person.id)>1){t.person=null;t.streak=0;}
      // Rewrite pre-ambiguity event labels from the final frame decision.
      for(const e of events){const t=tracks.get(e.track_id);if(t)Object.assign(e,identity(t));}
      return {rows:rows.map(t=>({track_id:t.id,box:t.box.slice(),tracking_state:t.tracking,...identity(t)})),events};
    }
    function identity(t){return {participant_id:t.person?.id||'',display_name:t.person?.name||'',identity_source:t.person?(t.person.introduced?'self_introduction':'local_visual_similarity'):'unidentified',authentication_authority:false};}
    function event(t,type){return {event_id:id(),event_type:type,track_id:t.id,...identity(t)};}
    function greeting(){
      const at=now();if(at-lastGreeting<30000)return null;
      const active=[...tracks.values()].filter(t=>t.arrived&&at-t.seen<2000);
      const t=active.find(t=>!t.greeted&&(!t.person?active.length===1:at-(greeted.get(t.person.id)??-Infinity)>=cooldown));if(!t)return null;
      return {track_id:t.id,text:t.person?`Welcome back, ${t.person.name}.`:'Hello! I’m your VP3 agent. What’s your name?',...identity(t)};
    }
    function acknowledge(trackId){const t=tracks.get(trackId);if(!t)return null;t.greeted=true;lastGreeting=now();if(t.person)greeted.set(t.person.id,now());return event(t,'greeting');}
    function clear(){const events=[...tracks.values()].filter(t=>t.arrived).map(t=>event(t,'departure'));tracks.clear();return events;}
    function introduce(trackId,profile){const t=tracks.get(trackId),active=[...tracks.values()].filter(p=>now()-p.seen<2000);if(!t||active.length!==1||active[0]!==t||!profile?.id||!profile.name)return null;t.introducedId=String(profile.id);t.person={id:String(profile.id),name:String(profile.name).slice(0,120),introduced:true};return event(t,'identity_changed');}
    function reject(trackId){const t=tracks.get(trackId);if(!t)return null;const previous=t.person?.id;if(previous)(t.rejected||(t.rejected=new Set())).add(previous);t.introducedId='';t.person=null;t.candidate='';t.streak=0;t.greeted=true;return event(t,'identity_changed');}
    return {update,greeting,acknowledge,clear,introduce,reject};
  }
  function introductionName(text,awaiting=false){
    const value=String(text||'').normalize('NFKC').trim();
    const explicit=/^(?:(?:hi|hello|hey)[,!.]?\s+)?(?:my name is|i am|i['’]m|call me|it['’]s|it is)\s+(.+?)[.!]?$/iu.exec(value);
    const name=(explicit?explicit[1]:awaiting?value:'').replace(/[.!]$/u,'').trim();
    if(!name||name.length>80||!/^\p{L}[\p{L}\p{M}'’.-]*(?:\s+\p{L}[\p{L}\p{M}'’.-]*){0,3}$/u.test(name))return '';
    if(/\b(?:yes|no|okay|ok|fine|good|great|thanks|thank|you|hello|hey|stop|listening|recording|help|what|who|where|when|why|how|can|could|would|please|tell|show|doing|looking|going|ready|happy|sorry)\b/iu.test(name))return '';
    return name.replace(/\s+/g,' ');
  }
  function followPoint(rows,mirrored=true){if(!Array.isArray(rows)||rows.length!==1)return null;const b=box(rows[0].box);if(!b)return null;const x=b[0]+b[2]/2;return {x:mirrored?1-x:x,y:b[1]+b[3]/2};}
  function speakerTrack(rows,speaker,at=Date.now()){
    if(!speaker?.participant_id||!Number.isFinite(speaker.at)||!Number.isFinite(speaker.expires_at)||speaker.at>at||speaker.expires_at<=at||speaker.expires_at-speaker.at>3000)return '';
    const same=(Array.isArray(rows)?rows:[]).filter(r=>r?.participant_id===speaker.participant_id);
    return same.length===1&&same[0].track_id===speaker.track_id?same[0].track_id:'';
  }
  function visualState({state='idle',recording=false}={}){return {state:({processing:'thinking',transcribing:'thinking',starting:'connecting'})[state]||(['listening','thinking','speaking','error','connecting'].includes(state)?state:'idle'),recording:recording===true};}
  return {create,match,similarity,box,visualState,introductionName,followPoint,speakerTrack};
});
