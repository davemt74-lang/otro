/* Camera processing is browser-local; only bounded lifecycle summaries leave it. */
(() => {
  'use strict';
  async function boot(){
    const adapter=window.VP3_ORB_ADAPTER;if(!adapter||window.VP3AgentEyes||new URLSearchParams(location.search).get('orb_embed')==='1')return;
    const core=window.VP3EyesCore.create(),session=crypto.randomUUID();
    let generation=0,stream=null,engine=null,timer=0,profiles=[],observations=[],currentRows=[],starting=false,enrolling=false,pending=[],flushing=false,profileVersion=0,profileSignature='',introduction=null,savingIntroduction=false,recognitionOffer=null;
    const panel=document.createElement('section');panel.className='vp3-eyes-panel';panel.hidden=true;panel.setAttribute('aria-label','Agent Eyes');
    panel.innerHTML='<header><strong>Agent Eyes</strong><button type="button" data-eyes-close aria-label="Close Agent Eyes">×</button></header><div class="vp3-eyes-content"><p data-eyes-status role="status">Camera is off.</p><div class="vp3-eyes-actions"><button type="button" data-eyes-start>Start camera</button><button type="button" data-eyes-stop disabled>Stop camera</button></div><div class="vp3-eyes-camera"><video muted playsinline></video><div class="vp3-eyes-boxes"></div></div><p data-eyes-people>No participants in view.</p><label><input type="checkbox" data-eyes-greetings checked> Greet arrivals automatically</label><details><summary>Introduce / recognize a participant</summary><p>A tracking ID is temporary. A local visual match is provisional and grants no account access.</p><label>Existing participant<select data-eyes-profile aria-label="Existing participant"></select></label><label>Or introduce someone<input type="text" data-eyes-name maxlength="120" placeholder="Participant name"></label><label><input type="checkbox" data-eyes-consent> This participant agrees to save local visual samples for recognition.</label><div class="vp3-eyes-actions"><button type="button" data-eyes-enroll>Save three local samples</button><button type="button" data-eyes-forget>Forget local samples</button></div></details><details><summary>Recent Agent Eyes activity</summary><ol data-eyes-history></ol></details><p>Frames and visual samples stay in this browser. Only participant event summaries enter your private Agent Brain history.</p></div>';
    document.body.append(panel);const q=s=>panel.querySelector(s),video=q('video'),status=q('[data-eyes-status]');
    const speakerStatus=document.createElement('p');speakerStatus.dataset.eyesSpeaker='';speakerStatus.setAttribute('role','status');q('.vp3-eyes-camera').after(speakerStatus);
    const legend=document.createElement('p');legend.textContent='Blue: tracked participant. Green: recent voice match (up to 3 seconds).';speakerStatus.after(legend);
    let speakerTimer=0;const boxNodes=new Map();
    function paintSpeaker(){
      clearTimeout(speakerTimer);const speaker=window.VP3ParticipantContinuity?.activeSpeaker?.(),track=window.VP3EyesCore.speakerTrack(currentRows,speaker);
      for(const row of currentRows){const node=boxNodes.get(row.track_id);if(!node)continue;const active=row.track_id===track;node.classList.toggle('vp3-eyes-speaker',active);node.dataset.speaker=String(active);node.querySelector('span').textContent=(row.display_name?`${row.display_name} · provisional`:`Participant ${row.track_id.slice(-5)}`)+(active?' · voice match':'');}
      const row=currentRows.find(r=>r.track_id===track);speakerStatus.textContent=row?`Recent speaker: ${row.display_name} · provisional.`:'No current speaker match.';
      if(track)speakerTimer=setTimeout(paintSpeaker,Math.max(1,speaker.expires_at-Date.now()));
    }
    window.addEventListener('vp3:active-speaker',paintSpeaker);
    const movement=document.createElement('div');movement.innerHTML='<label><input type="checkbox" data-eyes-follow checked> Follow participant movement</label><label><input type="checkbox" data-eyes-mirror checked> Mirror camera movement</label>';q('[data-eyes-greetings]').closest('label').after(movement);
    for(const key of ['follow','mirror']){const input=q(`[data-eyes-${key}]`),pref=`vp3:eyes:${key}:${adapter.ownerKey}`;try{input.checked=localStorage.getItem(pref)!=='off';}catch(_){}input.addEventListener('change',()=>{try{localStorage.setItem(pref,input.checked?'on':'off');}catch(_){}draw(currentRows);});}
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
      window.dispatchEvent(new CustomEvent('vp3:participant-presence',{detail:{active:!!stream,rows:rows.map(({track_id,participant_id,display_name,identity_source})=>({track_id,participant_id,display_name,identity_source}))}}));
      const mirrored=q('[data-eyes-mirror]').checked;video.style.transform=mirrored?'scaleX(-1)':'';
      window.VP3AgentOrb?.follow?.(q('[data-eyes-follow]').checked?rows:[],{mirrored});
      for(const [id,node] of boxNodes)if(!rows.some(r=>r.track_id===id)){node.remove();boxNodes.delete(id);}
      for(const row of rows){let b=boxNodes.get(row.track_id);if(!b){b=document.createElement('div');b.className='vp3-eyes-box';b.dataset.trackId=row.track_id;b.append(document.createElement('span'));boxNodes.set(row.track_id,b);q('.vp3-eyes-boxes').append(b);}const [x,y,w,h]=row.box;b.style.cssText=`left:${(mirrored?1-x-w:x)*100}%;top:${y*100}%;width:${w*100}%;height:${h*100}%`;b.dataset.tracking=row.tracking_state||'tracked';}
      paintSpeaker();
      q('[data-eyes-people]').textContent=rows.length?rows.map(r=>r.display_name?`${r.display_name} (provisional)`:`Participant ${r.track_id.slice(-5)} · unidentified`).join(' · '):'No participants in view.';
    }
    async function hear(text){
      if(window.VP3ParticipantContinuity?.hear(text))return true;
      if(recognitionOffer&&stream&&!enrolling&&!savingIntroduction&&currentRows.length===1&&currentRows[0].track_id===recognitionOffer.track&&Date.now()<recognitionOffer.expires){
        const answer=String(text||'').trim().replace(/[.!]$/,'');
        if(/^(?:no(?: thanks| thank you)?|do not remember me|don't remember me)$/i.test(answer)){recognitionOffer=null;window.VP3AgentOrb?.setIntroductionMode?.('');say('Your name is saved. No visual samples were saved.');return true;}
        if(recognitionOffer.prompted&&/^(?:please )?(?:remember me visually|save my local visual samples|recognize me next time)$/i.test(answer)){q('[data-eyes-profile]').value=recognitionOffer.id;q('[data-eyes-consent]').checked=true;recognitionOffer=null;window.VP3AgentOrb?.setIntroductionMode?.('');await enroll();return true;}
      }
      if(!stream||enrolling||savingIntroduction||!introduction||Date.now()>introduction.expires||currentRows.length!==1||currentRows[0].track_id!==introduction.track)return false;
      const name=window.VP3EyesCore.introductionName(text,true);if(!name)return false;
      const token=generation,track=introduction.track;const current=()=>token===generation&&!!stream&&currentRows.length===1&&currentRows[0].track_id===track;
      savingIntroduction=true;
      try{
        const profile=await adapter.introduceEyes({name,isCurrent:current});if(!current())return false;
        await loadProfiles();if(!current())return false;
        const event=core.introduce(track,profile);if(!event)return false;
        introduction=null;currentRows=currentRows.map(row=>({...row,participant_id:profile.id,display_name:profile.name,identity_source:'self_introduction'}));draw(currentRows);log(event);
        q('[data-eyes-profile]').value=String(profile.id);q('[data-eyes-name]').value='';q('[data-eyes-consent]').checked=false;
        recognitionOffer={id:String(profile.id),track,expires:Date.now()+90000,prompted:false};
        const message=`Thanks, ${profile.name}. Your name is saved. Say “remember me visually” to allow local visual samples so I can recognize you next time on this device.`;
        recognitionOffer.message=message;
        window.VP3AgentOrb?.bubble(message);say(message);return true;
      }catch(error){if(current())say(String(error.message||'Your name could not be saved. Please try again.'));return false;}
      finally{savingIntroduction=false;}
    }
    async function stop(reason='Camera is off.'){
      introduction=null;recognitionOffer=null;window.VP3AgentOrb?.setIntroductionMode?.('');
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
        if(introduction&&(currentRows.length!==1||currentRows[0].track_id!==introduction.track||currentRows[0].participant_id)){introduction=null;window.VP3AgentOrb?.setIntroductionMode?.('');}
        if(recognitionOffer&&(Date.now()>recognitionOffer.expires||currentRows.length!==1||currentRows[0].track_id!==recognitionOffer.track)){recognitionOffer=null;window.VP3AgentOrb?.setIntroductionMode?.('');}
        if(recognitionOffer&&!recognitionOffer.prompted&&!adapter.busy()){
          const offer=recognitionOffer;offer.prompted=true;window.VP3AgentOrb?.setIntroductionMode?.('visual-consent');window.VP3AgentOrb?.bubble(offer.message);
          void Promise.resolve(adapter.greet(offer.message,()=>recognitionOffer===offer&&token===generation&&!!stream)).catch(()=>{});
        }
        const greeting=q('[data-eyes-greetings]').checked&&!enrolling&&!adapter.busy()?core.greeting():null;
        if(greeting){
          if(!greeting.participant_id){introduction={track:greeting.track_id,expires:Date.now()+90000};window.VP3AgentOrb?.setIntroductionMode?.('name');}
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
      enrolling=true;const token=generation,track=currentRows[0].track_id,reference=observations[0].embedding,samples=[];
      try{
        for(let i=0;i<3;i++){
          if(i)await new Promise(resolve=>setTimeout(resolve,700));
          if(token!==generation||!stream||!q('[data-eyes-consent]').checked||!await adapter.cameraAllowed())throw Error('Enrollment stopped.');
          const result=await engine.detect(video);
          if(token!==generation||!stream||!q('[data-eyes-consent]').checked||result.face?.length!==1||currentRows[0]?.track_id!==track||currentRows.length!==1)throw Error('Keep the same participant alone in view.');
          const embedding=Array.from(result.face[0].embedding||[]);if(window.VP3EyesCore.similarity(embedding,reference)<.8)throw Error('Keep the same participant alone in view.');if(window.VP3EyesCore.similarity(embedding,embedding)<.99)throw Error('Visual sample unavailable.');if(samples.length&&window.VP3EyesCore.similarity(embedding,samples[0])<.8)throw Error('Keep the same participant alone in view.');samples.push(embedding);
        }
        if(token!==generation||!q('[data-eyes-consent]').checked)throw Error('Enrollment stopped.');
        await adapter.enrollEyes({id,name,embeddings:samples,isCurrent:()=>token===generation&&!!stream&&q('[data-eyes-consent]').checked});await loadProfiles();q('[data-eyes-consent]').checked=false;q('[data-eyes-name]').value='';say('Local samples saved. Future visual matches remain provisional.');
      }catch(error){say(String(error.message||'Enrollment could not be saved.'));}finally{enrolling=false;}
    }
    q('[data-eyes-start]').addEventListener('click',()=>void start());q('[data-eyes-stop]').addEventListener('click',()=>void stop());q('[data-eyes-close]').addEventListener('click',()=>{panel.hidden=true;});q('[data-eyes-enroll]').addEventListener('click',()=>void enroll());
    q('[data-eyes-forget]').addEventListener('click',async()=>{const id=q('[data-eyes-profile]').value;if(!id)return say('Choose a participant.');try{await adapter.forgetEyes(id);window.dispatchEvent(new CustomEvent('vp3:participant-forgotten',{detail:{id,name:profiles.find(p=>String(p.id)===id)?.name||''}}));await loadProfiles();core.clear().forEach(log);currentRows=[];observations=[];introduction=null;recognitionOffer=null;window.VP3AgentOrb?.setIntroductionMode?.('');draw([]);window.VP3VoiceGuard?.clear();say('Local visual samples removed.');}catch(_){say('Local samples could not be removed.');}});
    const hide=()=>{if(document.hidden)void stop('Camera stopped while this page is hidden.');};document.addEventListener('visibilitychange',hide);
    const retry=setInterval(()=>void flush(),5000);
    window.addEventListener('pagehide',()=>{void stop();clearInterval(retry);clearTimeout(speakerTimer);window.removeEventListener('vp3:active-speaker',paintSpeaker);document.removeEventListener('visibilitychange',hide);},{once:true});
    function rejectCurrent(){introduction=null;recognitionOffer=null;window.VP3AgentOrb?.setIntroductionMode?.('');if(!stream||currentRows.length!==1)return;const track=currentRows[0].track_id;core.reject(track);currentRows=currentRows.map(row=>({...row,participant_id:'',display_name:'',identity_source:'unidentified'}));draw(currentRows);introduction={track,expires:Date.now()+90000};window.VP3AgentOrb?.setIntroductionMode?.('name');window.VP3AgentOrb?.bubble('Thanks for correcting me. What is your name?');}
    async function refreshProfiles(){await loadProfiles();currentRows=currentRows.map(row=>{const p=profiles.find(p=>String(p.id)===String(row.participant_id));return p?{...row,display_name:p.name}:{...row,participant_id:'',display_name:'',identity_source:'unidentified'};});draw(currentRows);}
    window.VP3AgentEyes={rejectCurrent,refreshProfiles,toggle:()=>{panel.hidden=!panel.hidden;if(!panel.hidden)void loadProfiles().catch(()=>say('Participant list unavailable.'));},stop,hear,expectingIntroduction:()=>!!stream&&!!introduction&&Date.now()<introduction.expires&&currentRows.length===1&&currentRows[0].track_id===introduction.track,isActive:()=>!!stream};
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>void boot(),{once:true});else void boot();
})();
