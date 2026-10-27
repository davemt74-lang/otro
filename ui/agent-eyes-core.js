/* Shared VP3/HomeServer presence lifecycle. Track IDs never confer identity. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;if(root)root.VP3EyesCore=api;})(typeof window==='object'?window:null,()=>{
  'use strict';
  const vector=v=>Array.isArray(v)&&v.length>=16&&v.length<=2048&&v.every(Number.isFinite);
  function similarity(a,b){
    if(!vector(a)||!vector(b)||a.length!==b.length)return -1;
    const ma=Math.max(...a.map(Math.abs)),mb=Math.max(...b.map(Math.abs));if(!ma||!mb)return -1;
    let dot=0,aa=0,bb=0;for(let i=0;i<a.length;i++){const x=a[i]/ma,y=b[i]/mb;dot+=x*y;aa+=x*x;bb+=y*y;}return dot/Math.sqrt(aa*bb);
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
      const at=now(),events=[],used=new Set(),rows=[];
      for(const [key,t] of tracks)if(at-t.seen>=8000){if(t.arrived)events.push(event(t,'departure'));tracks.delete(key);}
      for(const observation of (observations||[]).slice(0,8)){
        const bounds=box(observation.box);if(!bounds)continue;
        const candidates=[...tracks.values()].filter(t=>!used.has(t.id)&&at-t.seen<2500).map(t=>({t,d:separation(bounds,t.box),appearance:similarity(observation.embedding,t.embedding)})).filter(x=>x.d<.16&&(x.appearance<0||x.appearance>=.65)).sort((a,b)=>a.d-b.d);
        // Ambiguous geometry starts a fresh track rather than transferring a name.
        const previous=candidates[0]&&(!candidates[1]||candidates[1].d-candidates[0].d>.025)?candidates[0].t:null;
        const t=previous||{id:id(),first:at,seen:at,box:bounds,arrived:false,person:null,candidate:'',streak:0,greeted:false};
        const person=match(observation.embedding,profiles);
        if(t.candidate===(person?.id||''))t.streak++;else{t.candidate=person?.id||'';t.streak=1;}
        const resolved=person&&t.streak>=3?person:null;
        const changed=t.person?.id!==(resolved?.id);
        t.seen=at;t.box=bounds;t.embedding=vector(observation.embedding)?observation.embedding.slice():null;t.person=resolved;used.add(t.id);tracks.set(t.id,t);rows.push(t);
        if(!t.arrived&&at-t.first>=3000){t.arrived=true;events.push(event(t,'arrival'));}
        else if(t.arrived&&changed)events.push(event(t,'identity_changed'));
      }
      // Two tracks claiming one profile are both unidentified.
      const counts=new Map();for(const t of rows)if(t.person)counts.set(t.person.id,(counts.get(t.person.id)||0)+1);
      for(const t of rows)if(t.person&&counts.get(t.person.id)>1){t.person=null;t.streak=0;}
      // Rewrite pre-ambiguity event labels from the final frame decision.
      for(const e of events){const t=tracks.get(e.track_id);if(t)Object.assign(e,identity(t));}
      return {rows:rows.map(t=>({track_id:t.id,box:t.box,...identity(t)})),events};
    }
    function identity(t){return {participant_id:t.person?.id||'',display_name:t.person?.name||'',identity_source:t.person?'local_visual_similarity':'unidentified',authentication_authority:false};}
    function event(t,type){return {event_id:id(),event_type:type,track_id:t.id,...identity(t)};}
    function greeting(){
      const at=now();if(at-lastGreeting<30000)return null;
      const active=[...tracks.values()].filter(t=>t.arrived&&at-t.seen<2000);
      const t=active.find(t=>!t.greeted&&(!t.person?active.length===1:at-(greeted.get(t.person.id)??-Infinity)>=cooldown));if(!t)return null;
      return {track_id:t.id,text:t.person?`Welcome back, ${t.person.name}.`:'Hello! I’m your VP3 agent. What’s your name?',...identity(t)};
    }
    function acknowledge(trackId){const t=tracks.get(trackId);if(!t)return null;t.greeted=true;lastGreeting=now();if(t.person)greeted.set(t.person.id,now());return event(t,'greeting');}
    function clear(){const events=[...tracks.values()].filter(t=>t.arrived).map(t=>event(t,'departure'));tracks.clear();return events;}
    return {update,greeting,acknowledge,clear};
  }
  function visualState({state='idle',recording=false}={}){return {state:({processing:'thinking',transcribing:'thinking',starting:'connecting'})[state]||(['listening','thinking','speaking','error','connecting'].includes(state)?state:'idle'),recording:recording===true};}
  return {create,match,similarity,box,visualState};
});
