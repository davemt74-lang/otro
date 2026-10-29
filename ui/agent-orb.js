(function(){
  'use strict';
  const boot=()=>{
    const adapter=window.VP3_ORB_ADAPTER;if(!adapter||window.VP3AgentOrb)return;
    const $=id=>document.getElementById(id),embed=new URLSearchParams(location.search).get('orb_embed')==='1'&&parent!==window;
    const origin=location.origin;let lastState='',disposed=false,timer=0,pendingText='',pendingListen=false,pendingVoiceEnrollment='',introductionMode='',ready=false,frame=null,returnFocus=null,restorePanel=null;
    const state=()=>window.VP3EyesCore.visualState(adapter.state());
    const userTurn=event=>{if(event.target?.id!=='chatForm')return;const text=String($('chatInput')?.value||'').slice(0,240);const context={voice:window.VP3VoiceGuard?.wasVoiceTurn?.(text)===true,known_voice:window.VP3VoiceGuard?.hasRecognizedTurn?.(text)===true};if(embed)parent.postMessage({channel:'vp3-orb',kind:'user-turn',text,...context},origin);else void window.VP3AgentEyes?.hear?.(text,context);};
    document.addEventListener('submit',userTurn,true);window.addEventListener('pagehide',()=>document.removeEventListener('submit',userTurn,true),{once:true});
    if(embed){
      let queuedEnrollment='',queuedIntroduction='';window.addEventListener('vp3:voice-guard-ready',()=>{if(queuedEnrollment){void window.VP3VoiceGuard.beginEnrollment(queuedEnrollment);queuedEnrollment='';}window.VP3VoiceGuard.inviteIntroduction(queuedIntroduction);});
      document.documentElement.dataset.vp3OrbEmbed='1';
      const announce=()=>{if(!disposed)parent.postMessage({channel:'vp3-orb',kind:'state',state:state(),conversation:adapter.conversationRef?.()},origin);};
      const commands=async event=>{
        if(event.origin!==origin||event.source!==parent||event.data?.channel!=='vp3-orb')return;
        if(event.data.kind==='compose'){
          const input=$('chatInput'),text=String(event.data.text||'').slice(0,6000);
          if(!input||input.value.trim()||adapter.busy())return parent.postMessage({channel:'vp3-orb',kind:'draft-blocked'},origin);
          input.value=text;input.dispatchEvent(new Event('input',{bubbles:true}));
          parent.postMessage({channel:'vp3-orb',kind:'draft-accepted'},origin);
          $('chatForm')?.requestSubmit();
        }
        if(event.data.kind==='participant-selection')window.VP3ParticipantContinuity?.setSelection(event.data.value,true);
        if(event.data.kind==='participant-presence')window.VP3ParticipantContinuity?.updatePresence(event.data.value,true);
        if(event.data.kind==='participant-reject')window.VP3ParticipantContinuity?.rejectCurrent(true,String(event.data.value?.participant_id||''));
        if(event.data.kind==='participant-invalidate')window.VP3ParticipantContinuity?.invalidate();
        if(event.data.kind==='stop')adapter.stop();
        if(event.data.kind==='listen')adapter.listen();
        if(event.data.kind==='voice-enroll'){if(window.VP3VoiceGuard)void window.VP3VoiceGuard.beginEnrollment(String(event.data.id||''));else queuedEnrollment=String(event.data.id||'');}
        if(event.data.kind==='voice-enroll-cancel'){queuedEnrollment='';window.VP3VoiceGuard?.cancelEnrollment();}
        if(event.data.kind==='introduction-window'){queuedIntroduction=['name','visual-consent'].includes(event.data.mode)?event.data.mode:'';window.VP3VoiceGuard?.inviteIntroduction(queuedIntroduction);}
      };
      window.addEventListener('message',commands);timer=setInterval(announce,250);
      window.addEventListener('vp3:voice-filter-status',event=>parent.postMessage({channel:'vp3-orb',kind:'voice-filter-status',message:String(event.detail?.message||'').slice(0,300)},origin));
      window.VP3AgentOrb={participantSignal:(kind,value)=>{if(['speaker','reject','selection'].includes(kind))parent.postMessage({channel:'vp3-orb',kind:'participant-'+kind,value},origin);}};
      parent.postMessage({channel:'vp3-orb',kind:'ready'},origin);
      window.addEventListener('pagehide',()=>{disposed=true;clearInterval(timer);window.removeEventListener('message',commands);},{once:true});
      return;
    }
    const root=document.createElement('aside');root.className='vp3-orb-root';root.dataset.state='idle';
    root.innerHTML='<div class="vp3-orb-bubble" role="status" hidden></div><button type="button" class="vp3-orb-ball" aria-label="Open Agent Chat"></button><span class="vp3-orb-state">Ready</span><div class="vp3-orb-controls"><button type="button" data-orb-stop>Stop</button><button type="button" data-orb-eyes>Eyes</button><button type="button" data-orb-size aria-label="Minimize agent">−</button></div>';
    document.body.append(root);
    const panel=document.createElement('section');panel.className='vp3-orb-panel';panel.id='vp3AgentChatPopup';panel.hidden=true;panel.setAttribute('role','dialog');panel.setAttribute('aria-label','Agent Chat');
    panel.innerHTML='<header><strong>Agent Chat</strong><a>Full chat ↗</a><button type="button" aria-label="Close Agent Chat">×</button></header><div class="vp3-orb-chat-host"></div>';panel.querySelector('a').href=adapter.chatUrl;document.body.append(panel);
    const host=panel.querySelector('.vp3-orb-chat-host'),ball=root.querySelector('.vp3-orb-ball'),label=root.querySelector('.vp3-orb-state');
    const key=`vp3:orb:${adapter.ownerKey}`,draftKey=`${key}:draft:${location.pathname}`;
    try{const p=JSON.parse(localStorage.getItem(key)||'null');if(p){root.style.left=`${Math.max(0,Math.min(innerWidth-100,Number(p.x)||0))}px`;root.style.top=`${Math.max(0,Math.min(innerHeight-150,Number(p.y)||0))}px`;root.style.right='auto';root.style.bottom='auto';root.dataset.small=p.small?'true':'false';}}catch(_){}
    let bar=null,conversationRef=null;const fullLink=panel.querySelector('a'),handoffKey=`${key}:conversation`;
    function rememberRef(ref){if(!adapter.conversationUrl||!ref||typeof ref!=='object')return;const url=adapter.conversationUrl(ref);if(!url)return;if(conversationRef&&(String(conversationRef.id)!==String(ref.id)||String(conversationRef.agent)!==String(ref.agent)))window.VP3ParticipantContinuity?.clearSelection();conversationRef={id:ref.id,agent:ref.agent};fullLink.href=url;try{sessionStorage.setItem(handoffKey,JSON.stringify({ref:conversationRef,at:Date.now()}));}catch(_){}}
    try{const saved=JSON.parse(sessionStorage.getItem(handoffKey)||'null');if(saved&&Number.isFinite(saved.at)&&saved.at<=Date.now()&&Date.now()-saved.at<1800000)rememberRef(saved.ref);}catch(_){}
    fullLink.addEventListener('click',event=>{if(adapter.attachChat){event.preventDefault();close();adapter.focusChat?.();}});
    function command(kind){if(frame?.contentWindow)frame.contentWindow.postMessage({channel:'vp3-orb',kind},origin);else adapter[kind]?.();}
    function close(){panel.hidden=true;root.dataset.popupOpen='false';restorePanel?.();restorePanel=null;document.querySelectorAll('[data-orb-chat]').forEach(b=>b.setAttribute('aria-expanded','false'));returnFocus?.focus?.();}
    function open(){
      returnFocus=document.activeElement;
      if(adapter.isMainChat?.()){adapter.focusChat?.();return;}
      if(adapter.attachChat)restorePanel=restorePanel||adapter.attachChat(host);
      else if(!frame){
        frame=document.createElement('iframe');frame.title='VP3 Agent Chat';frame.allow='microphone; camera';const url=new URL(adapter.conversationUrl&&conversationRef?adapter.conversationUrl(conversationRef):adapter.chatUrl,location.href);url.searchParams.set('orb_embed','1');frame.src=url.href;host.append(frame);
      }
      panel.hidden=false;root.dataset.popupOpen='true';document.querySelectorAll('[data-orb-chat]').forEach(b=>b.setAttribute('aria-expanded','true'));panel.querySelector('button').focus();
    }
    function toggle(){panel.hidden?open():close();}
    panel.querySelector('button').addEventListener('click',close);ball.addEventListener('click',event=>{if(!dragged)toggle();});
    root.querySelector('[data-orb-stop]').addEventListener('click',()=>command('stop'));
    root.querySelector('[data-orb-eyes]').addEventListener('click',()=>window.VP3AgentEyes?.toggle());
    const sizeButton=root.querySelector('[data-orb-size]');
    function sizeLabel(){sizeButton.textContent=root.dataset.small==='true'?'+':'−';sizeButton.setAttribute('aria-label',root.dataset.small==='true'?'Expand agent':'Minimize agent');}
    sizeButton.addEventListener('click',()=>{root.dataset.small=root.dataset.small==='true'?'false':'true';sizeLabel();save();});
    sizeLabel();
    function button(){const b=document.createElement('button');b.type='button';b.dataset.orbChat='1';b.textContent='Chat';b.setAttribute('aria-controls',panel.id);b.setAttribute('aria-expanded','false');b.addEventListener('click',toggle);return b;}
    const composer=$('chatForm');
    if(composer){const actions=composer.querySelector('.chat-composer-actions')||composer;actions.insertBefore(button(),actions.firstChild);}
    if(!composer||adapter.alwaysBar){
      bar=document.createElement('form');bar.className='vp3-orb-bar';bar.setAttribute('aria-label','Agent chatbar');bar.append(button());
      const input=document.createElement('input');input.type='text';input.maxLength=6000;input.placeholder='Message your agent…';input.setAttribute('aria-label','Message your agent');try{input.value=sessionStorage.getItem(draftKey)||'';}catch(_){}bar.append(input);
      const listen=document.createElement('button');listen.type='button';listen.textContent='Listen';listen.addEventListener('click',()=>{open();if(!frame||ready)command('listen');else pendingListen=true;});bar.append(listen);
      const send=document.createElement('button');send.type='submit';send.textContent='Send';bar.append(send);
      input.addEventListener('input',()=>{try{sessionStorage.setItem(draftKey,input.value);}catch(_){}});
      bar.addEventListener('submit',event=>{event.preventDefault();if(pendingText||!input.value.trim())return;pendingText=input.value;open();if(adapter.submitDraft){if(adapter.submitDraft(pendingText)){input.value='';try{sessionStorage.removeItem(draftKey);}catch(_){}}else bubble('Your message is kept in the chatbar. Finish the existing draft in Chat first.');pendingText='';return;}if(ready)frame.contentWindow.postMessage({channel:'vp3-orb',kind:'compose',text:pendingText},origin);});
      document.body.append(bar);document.body.classList.add('vp3-orb-has-bar');
    }
    function message(event){
      if(event.origin!==origin||event.source!==frame?.contentWindow||event.data?.channel!=='vp3-orb')return;
      const data=event.data;if(data.kind==='ready'){ready=true;if(pendingListen){pendingListen=false;command('listen');}if(pendingText)frame.contentWindow.postMessage({channel:'vp3-orb',kind:'compose',text:pendingText},origin);if(pendingVoiceEnrollment){frame.contentWindow.postMessage({channel:'vp3-orb',kind:'voice-enroll',id:pendingVoiceEnrollment},origin);pendingVoiceEnrollment='';}}
      if(data.kind==='voice-filter-status')window.dispatchEvent(new CustomEvent('vp3:voice-filter-remote-status',{detail:{message:String(data.message||'').slice(0,300)}}));
      if(data.kind==='user-turn')void window.VP3AgentEyes?.hear?.(String(data.text||'').slice(0,240),{voice:data.voice===true,known_voice:data.known_voice===true});
      if(data.kind==='ready'){frame.contentWindow.postMessage({channel:'vp3-orb',kind:'introduction-window',mode:introductionMode},origin);frame.contentWindow.postMessage({channel:'vp3-orb',kind:'participant-presence',value:window.VP3ParticipantContinuity?.snapshot()},origin);frame.contentWindow.postMessage({channel:'vp3-orb',kind:'participant-selection',value:window.VP3ParticipantContinuity?.selectedContext()},origin);}
      if(data.kind==='participant-selection')window.VP3ParticipantContinuity?.setSelection(data.value,true);
      if(data.kind==='participant-speaker')window.VP3ParticipantContinuity?.receiveSpeaker(data.value);
      if(data.kind==='participant-reject')window.VP3ParticipantContinuity?.rejectCurrent(false,String(data.value?.participant_id||''));
      if(data.kind==='state'&&data.conversation)rememberRef(data.conversation);
      if(data.kind==='state')render(data.state);
      if(data.kind==='draft-accepted'){const input=bar?.querySelector('input');if(input&&input.value===pendingText){input.value='';try{sessionStorage.removeItem(draftKey);}catch(_){}}pendingText='';}
      if(data.kind==='draft-blocked'){pendingText='';bubble('Your existing draft is in the chat popup. Your chatbar message is still here.');}
    }
    function render(raw){const s=window.VP3EyesCore.visualState(raw);root.dataset.state=s.state;root.dataset.recording=String(s.recording);label.textContent=(s.recording?'Recording · ':'')+({idle:'Ready',listening:'Listening',thinking:'Thinking',speaking:'Speaking',connecting:'Connecting',error:'Needs attention'}[s.state]||'Ready');ball.setAttribute('aria-label',`Agent: ${label.textContent}. Open chat`);}
    function bubble(text){const node=root.querySelector('.vp3-orb-bubble');node.textContent=String(text).slice(0,600);node.hidden=false;clearTimeout(bubbleTimer);bubbleTimer=setTimeout(()=>{node.hidden=true;},14000);}
    let bubbleTimer=0,drag=null,dragged=false,motionFrame=0,followTarget=null,manualUntil=0;
    const reduced=matchMedia('(prefers-reduced-motion: reduce)');
    function move(at){
      motionFrame=0;if(disposed||!followTarget)return;
      if(!reduced.matches&&!drag&&at>manualUntil&&panel.hidden&&!root.matches(':hover,:focus-within')){
        const r=root.getBoundingClientRect(),margin=12,maxX=Math.max(margin,innerWidth-r.width-margin);
        let maxY=innerHeight-r.height-90;const composer=$('chatForm')?.getBoundingClientRect();
        if(composer&&composer.top>0&&composer.top<innerHeight)maxY=Math.min(maxY,composer.top-r.height-16);
        maxY=Math.max(70,maxY);let x=margin+followTarget.x*(maxX-margin),y=70+followTarget.y*(maxY-70);
        x=Math.max(margin,Math.min(maxX,x+Math.sin(at/2100)*5));y=Math.max(70,Math.min(maxY,y+Math.sin(at/1500)*5));
        const eyes=document.querySelector('.vp3-eyes-panel:not([hidden])')?.getBoundingClientRect();
        if(eyes&&eyes.left<r.width+margin+16){motionFrame=requestAnimationFrame(move);return;}
        if(eyes&&x+r.width>eyes.left-12&&x<eyes.right+12&&y+r.height>eyes.top&&y<eyes.bottom)x=Math.max(margin,eyes.left-r.width-16);
        const step=(from,to)=>from+Math.max(-18,Math.min(18,(to-from)*.065));
        root.style.left=`${step(r.left,x)}px`;root.style.top=`${step(r.top,y)}px`;root.style.right='auto';root.style.bottom='auto';
      }
      motionFrame=requestAnimationFrame(move);
    }
    function follow(rows,{mirrored=true}={}){followTarget=window.VP3EyesCore.followPoint(rows,mirrored);root.dataset.following=String(!!followTarget);if(followTarget&&!motionFrame)motionFrame=requestAnimationFrame(move);if(!followTarget&&motionFrame){cancelAnimationFrame(motionFrame);motionFrame=0;}}
    function save(){const r=root.getBoundingClientRect();try{localStorage.setItem(key,JSON.stringify({x:r.left,y:r.top,small:root.dataset.small==='true'}));}catch(_){}}
    ball.addEventListener('pointerdown',event=>{if(event.button!==0)return;manualUntil=performance.now()+15000;dragged=false;const r=root.getBoundingClientRect();drag={id:event.pointerId,x:event.clientX,y:event.clientY,left:r.left,top:r.top};ball.setPointerCapture(event.pointerId);});
    ball.addEventListener('pointermove',event=>{if(drag?.id!==event.pointerId)return;const dx=event.clientX-drag.x,dy=event.clientY-drag.y;if(Math.hypot(dx,dy)>5)dragged=true;if(!dragged)return;root.style.left=`${Math.max(0,Math.min(innerWidth-root.offsetWidth,drag.left+dx))}px`;root.style.top=`${Math.max(0,Math.min(innerHeight-root.offsetHeight-80,drag.top+dy))}px`;root.style.right='auto';root.style.bottom='auto';});
    ball.addEventListener('pointerup',()=>{drag=null;if(dragged)save();});ball.addEventListener('pointercancel',()=>{drag=null;});
    const resize=()=>{const r=root.getBoundingClientRect();if(r.left>innerWidth-80||r.top>innerHeight-140){root.style.left='auto';root.style.top='auto';root.style.right='12px';root.style.bottom='94px';}};
    const escape=event=>{if(event.key==='Escape'&&!panel.hidden)close();};
    window.addEventListener('message',message);window.addEventListener('resize',resize);document.addEventListener('keydown',escape);
    timer=setInterval(()=>{if(!frame){const ref=adapter.conversationRef?.();if(ref)rememberRef(ref);const next=state(),json=JSON.stringify(next);if(json!==lastState){render(next);lastState=json;}}},200);
    window.VP3AgentOrb={open,close,toggle,bubble,state,follow,participantSignal:(kind,value)=>{if(['presence','reject','invalidate','selection'].includes(kind))frame?.contentWindow?.postMessage({channel:'vp3-orb',kind:'participant-'+kind,value},origin);},setIntroductionMode:mode=>{introductionMode=mode;window.VP3VoiceGuard?.inviteIntroduction(mode);frame?.contentWindow?.postMessage({channel:'vp3-orb',kind:'introduction-window',mode},origin);},startVoiceEnrollment:id=>{if(adapter.attachChat||adapter.isMainChat?.())return false;open();if(ready)frame.contentWindow.postMessage({channel:'vp3-orb',kind:'voice-enroll',id},origin);else pendingVoiceEnrollment=id;return true;},cancelVoiceEnrollment:()=>{pendingVoiceEnrollment='';frame?.contentWindow?.postMessage({channel:'vp3-orb',kind:'voice-enroll-cancel'},origin);},stop:()=>command('stop')};
    window.addEventListener('pagehide',()=>{disposed=true;clearInterval(timer);clearTimeout(bubbleTimer);cancelAnimationFrame(motionFrame);window.removeEventListener('message',message);window.removeEventListener('resize',resize);document.removeEventListener('keydown',escape);adapter.stop?.();},{once:true});
  };
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
