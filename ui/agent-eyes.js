/* Camera processing is browser-local; only bounded lifecycle summaries leave it. */
(() => {
  'use strict';
  async function boot(){
    const adapter=window.VP3_ORB_ADAPTER;if(!adapter||window.VP3AgentEyes||new URLSearchParams(location.search).get('orb_embed')==='1')return;
    const core=window.VP3EyesCore.create(),session=crypto.randomUUID();
    let generation=0,stream=null,engine=null,timer=0,profiles=[],observations=[],currentRows=[],starting=false,enrolling=false,pending=[],flushing=false,profileVersion=0,profileSignature='';
    const panel=document.createElement('section');panel.className='vp3-eyes-panel';panel.hidden=true;panel.setAttribute('aria-label','Agent Eyes');
    panel.innerHTML='<header><strong>Agent Eyes</strong><button type="button" data-eyes-close aria-label="Close Agent Eyes">×</button></header><div class="vp3-eyes-content"><p data-eyes-status role="status">Camera is off.</p><div class="vp3-eyes-actions"><button type="button" data-eyes-start>Start camera</button><button type="button" data-eyes-stop disabled>Stop camera</button></div><div class="vp3-eyes-camera"><video muted playsinline></video><div class="vp3-eyes-boxes"></div></div><p data-eyes-people>No participants in view.</p><label><input type="checkbox" data-eyes-greetings checked> Greet arrivals automatically</label><details><summary>Introduce / recognize a participant</summary><p>A tracking ID is temporary. A local visual match is provisional and grants no account access.</p><label>Existing participant<select data-eyes-profile aria-label="Existing participant"></select></label><label>Or introduce someone<input type="text" data-eyes-name maxlength="120" placeholder="Participant name"></label><label><input type="checkbox" data-eyes-consent> This participant agrees to save local visual samples for recognition.</label><div class="vp3-eyes-actions"><button type="button" data-eyes-enroll>Save three local samples</button><button type="button" data-eyes-forget>Forget local samples</button></div></details><details><summary>Recent Agent Eyes activity</summary><ol data-eyes-history></ol></details><p>Frames and visual samples stay in this browser. Only participant event summaries enter your private Agent Brain history.</p></div>';
    document.body.append(panel);const q=s=>panel.querySelector(s),video=q('video'),status=q('[data-eyes-status]');
    const say=text=>{status.textContent=String(text);};
    const history=q('[data-eyes-history]');
    const greetingKey=`vp3:eyes:greetings:${adapter.ownerKey}`;
    try{q('[data-eyes-greetings]').checked=localStorage.getItem(greetingKey)!=='off';}catch(_){}
    q('[data-eyes-greetings]').addEventListener('change',()=>{try{localStorage.setItem(greetingKey,q('[data-eyes-greetings]').checked?'on':'off');}catch(_){}});
    for(const selector of ['[data-eyes-profile]','[data-eyes-name]'])q(selector).addEventListener('input',()=>{q('[data-eyes-consent]').checked=false;});
    function cameraIndicator(active){const root=document.querySelector('.vp3-orb-root');if(root)root.dataset.eyesActive=String(active);const button=document.querySelector('[data-orb-eyes]');if(button){button.textContent=active?'Eyes on':'Eyes';button.setAttribute('aria-pressed',String(active));}}

    async function flush(){
      if(flushing||!pending.length)return;flushing=true;
      try{while(pending.length){await adapter.recordEyes({...pending[0],session_id:session});pending.shift();}}
      catch(_){say('Camera status is local. Brain history could not be saved; it will retry.');}
      finally{flushing=false;}
    }
    function log(event){
      if(!event)return;pending.push(event);if(pending.length>100){pending.shift();say('Some earlier events could not be saved.');}
      const item=document.createElement('li');item.textContent=`${new Date().toLocaleTimeString()} · ${event.event_type.replaceAll('_',' ')} · ${event.display_name||'Unidentified participant'}`;history.prepend(item);while(history.children.length>40)history.lastChild.remove();void flush();
    }
    async function loadProfiles(){const token=++profileVersion;const rows=await adapter.loadEyesProfiles();if(token!==profileVersion)return;profiles=rows;const signature=JSON.stringify(rows.map(p=>[String(p.id),p.name]));if(signature===profileSignature)return;profileSignature=signature;const selected=q('[data-eyes-profile]').value;q('[data-eyes-profile]').replaceChildren();const blank=document.createElement('option');blank.value='';blank.textContent='Choose participant';q('[data-eyes-profile]').append(blank);for(const p of profiles){const o=document.createElement('option');o.value=String(p.id);o.textContent=p.name;q('[data-eyes-profile]').append(o);}q('[data-eyes-profile]').value=selected;}
    function draw(rows){
      q('.vp3-eyes-boxes').replaceChildren();for(const row of rows){const b=document.createElement('div');b.className='vp3-eyes-box';const [x,y,w,h]=row.box;b.style.cssText=`left:${x*100}%;top:${y*100}%;width:${w*100}%;height:${h*100}%`;const tag=document.createElement('span');tag.textContent=row.display_name?`${row.display_name} · provisional`:`Participant ${row.track_id.slice(-5)}`;b.append(tag);q('.vp3-eyes-boxes').append(b);}
      q('[data-eyes-people]').textContent=rows.length?rows.map(r=>r.display_name?`${r.display_name} (provisional)`:`Participant ${r.track_id.slice(-5)} · unidentified`).join(' · '):'No participants in view.';
    }
    async function stop(reason='Camera is off.'){
      generation++;starting=false;enrolling=false;cameraIndicator(false);q('[data-eyes-consent]').checked=false;clearTimeout(timer);timer=0;
      if(stream){stream.getTracks().forEach(t=>t.stop());stream=null;}video.srcObject=null;observations=[];currentRows=[];core.clear().forEach(log);draw([]);say(reason);q('[data-eyes-start]').disabled=false;q('[data-eyes-stop]').disabled=true;
    }
    async function scan(token){
      if(token!==generation||!stream)return;
      try{
        if(!await adapter.cameraAllowed())return void stop('Camera stopped by privacy or session settings.');
        await loadProfiles();if(token!==generation||!stream)return;
        if(enrolling){timer=setTimeout(()=>void scan(token),900);return;}
        const result=await engine.detect(video);if(token!==generation||!stream)return;
        // Re-read revocations after inference; stale matches cannot revive a profile.
        await loadProfiles();if(token!==generation||!stream)return;
        if(!await adapter.cameraAllowed())return void stop('Camera stopped by privacy settings.');
        if(token!==generation||!stream)return;
        observations=(result.face||[]).slice(0,8).map(f=>({box:[f.box[0]/video.videoWidth,f.box[1]/video.videoHeight,f.box[2]/video.videoWidth,f.box[3]/video.videoHeight],embedding:f.embedding?Array.from(f.embedding):null}));
        const update=core.update(observations,profiles);currentRows=update.rows;draw(update.rows);update.events.forEach(log);
        const greeting=q('[data-eyes-greetings]').checked&&!enrolling&&!adapter.busy()?core.greeting():null;
        if(greeting){
          // No approval click: show immediately and speak through the existing voice owner.
          window.VP3AgentOrb?.bubble(greeting.text);log(core.acknowledge(greeting.track_id));
          void Promise.resolve(adapter.greet(greeting.text,()=>token===generation&&!!stream&&q('[data-eyes-greetings]').checked)).catch(()=>{});
        }
        say(`Camera active · ${update.rows.length} participant${update.rows.length===1?'':'s'} in view.`);void flush();
      }catch(_){if(token===generation)return void stop('Agent Eyes stopped because camera, model, or privacy status is unavailable.');}
      if(token===generation&&stream)timer=setTimeout(()=>void scan(token),900);
    }
    async function start(){
      if(starting||stream)return;if(document.hidden)return;
      starting=true;const token=++generation;q('[data-eyes-start]').disabled=true;q('[data-eyes-stop]').disabled=false;
      let acquired=null;
      try{
        if(!await adapter.cameraAllowed())throw Error('Camera is unavailable while privacy is enabled or another camera activity is running.');
        say('Loading local camera models…');
        if(!engine){
          const module=await import('https://cdn.jsdelivr.net/npm/@vladmandic/human@3.3.6/dist/human.esm.js');if(token!==generation)return;
          const Human=module.Human||module.default;
          engine=new Human({backend:'webgl',debug:false,modelBasePath:'https://cdn.jsdelivr.net/npm/@vladmandic/human@3.3.6/models/',face:{enabled:true,detector:{enabled:true,maxDetected:8,minConfidence:.65},mesh:{enabled:true},description:{enabled:true},iris:{enabled:false},emotion:{enabled:false},antispoof:{enabled:false},liveness:{enabled:false}},body:{enabled:false},hand:{enabled:false},object:{enabled:false},gesture:{enabled:false},segmentation:{enabled:false}});
          await engine.load();
        }
        if(token!==generation)return;
        if(!await adapter.cameraAllowed())throw Error('Camera privacy or session changed.');
        acquired=await navigator.mediaDevices.getUserMedia({video:{width:{ideal:640},height:{ideal:480},facingMode:{ideal:'user'}},audio:false});
        if(token!==generation){acquired.getTracks().forEach(t=>t.stop());return;}
        stream=acquired;acquired=null;cameraIndicator(true);for(const track of stream.getTracks())track.addEventListener('ended',()=>void stop('Camera disconnected.'),{once:true});video.srcObject=stream;await video.play();if(token!==generation)return;starting=false;
        await scan(token);
      }catch(error){if(acquired)acquired.getTracks().forEach(t=>t.stop());if(token===generation)await stop(String(error.message||'Camera could not start.'));}
    }
    async function enroll(){
      if(enrolling)return;if(!stream||currentRows.length!==1||observations.length!==1)return say('Start the camera with exactly one participant in view.');
      if(!q('[data-eyes-consent]').checked)return say('The participant must agree to local recognition samples.');
      const id=q('[data-eyes-profile]').value,name=q('[data-eyes-name]').value.trim();if(!id&&!name)return say('Choose a participant or enter their name.');
      enrolling=true;const token=generation,track=currentRows[0].track_id,samples=[];
      try{
        for(let i=0;i<3;i++){
          if(i)await new Promise(resolve=>setTimeout(resolve,700));
          if(token!==generation||!stream||!q('[data-eyes-consent]').checked||!await adapter.cameraAllowed())throw Error('Enrollment stopped.');
          const result=await engine.detect(video);
          if(token!==generation||!stream||!q('[data-eyes-consent]').checked||result.face?.length!==1||currentRows[0]?.track_id!==track||currentRows.length!==1)throw Error('Keep the same participant alone in view.');
          const embedding=Array.from(result.face[0].embedding||[]);if(window.VP3EyesCore.similarity(embedding,embedding)<.99)throw Error('Visual sample unavailable.');if(samples.length&&window.VP3EyesCore.similarity(embedding,samples[0])<.8)throw Error('Keep the same participant alone in view.');samples.push(embedding);
        }
        if(token!==generation||!q('[data-eyes-consent]').checked)throw Error('Enrollment stopped.');
        await adapter.enrollEyes({id,name,embeddings:samples,isCurrent:()=>token===generation&&!!stream&&q('[data-eyes-consent]').checked});await loadProfiles();q('[data-eyes-consent]').checked=false;q('[data-eyes-name]').value='';say('Local samples saved. Future visual matches remain provisional.');
      }catch(error){say(String(error.message||'Enrollment could not be saved.'));}finally{enrolling=false;}
    }
    q('[data-eyes-start]').addEventListener('click',()=>void start());q('[data-eyes-stop]').addEventListener('click',()=>void stop());q('[data-eyes-close]').addEventListener('click',()=>{panel.hidden=true;});q('[data-eyes-enroll]').addEventListener('click',()=>void enroll());
    q('[data-eyes-forget]').addEventListener('click',async()=>{const id=q('[data-eyes-profile]').value;if(!id)return say('Choose a participant.');try{await adapter.forgetEyes(id);await loadProfiles();core.clear().forEach(log);draw([]);say('Local visual samples removed.');}catch(_){say('Local samples could not be removed.');}});
    const hide=()=>{if(document.hidden)void stop('Camera stopped while this page is hidden.');};document.addEventListener('visibilitychange',hide);
    const retry=setInterval(()=>void flush(),5000);
    window.addEventListener('pagehide',()=>{void stop();clearInterval(retry);document.removeEventListener('visibilitychange',hide);},{once:true});
    window.VP3AgentEyes={toggle:()=>{panel.hidden=!panel.hidden;if(!panel.hidden)void loadProfiles().catch(()=>say('Participant list unavailable.'));},stop,isActive:()=>!!stream};
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>void boot(),{once:true});else void boot();
})();
