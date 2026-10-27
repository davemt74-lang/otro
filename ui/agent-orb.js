(function(){
  'use strict';
  const boot=()=>{
    const adapter=window.VP3_ORB_ADAPTER;if(!adapter||window.VP3AgentOrb)return;
    const $=id=>document.getElementById(id),embed=new URLSearchParams(location.search).get('orb_embed')==='1'&&parent!==window;
    const origin=location.origin;let lastState='',disposed=false,timer=0,pendingText='',pendingListen=false,ready=false,frame=null,returnFocus=null,restorePanel=null;
    const state=()=>window.VP3EyesCore.visualState(adapter.state());
    if(embed){
      document.documentElement.dataset.vp3OrbEmbed='1';
      const announce=()=>{if(!disposed)parent.postMessage({channel:'vp3-orb',kind:'state',state:state()},origin);};
      const commands=async event=>{
        if(event.origin!==origin||event.source!==parent||event.data?.channel!=='vp3-orb')return;
        if(event.data.kind==='compose'){
          const input=$('chatInput'),text=String(event.data.text||'').slice(0,6000);
          if(!input||input.value.trim()||adapter.busy())return parent.postMessage({channel:'vp3-orb',kind:'draft-blocked'},origin);
          input.value=text;input.dispatchEvent(new Event('input',{bubbles:true}));
          parent.postMessage({channel:'vp3-orb',kind:'draft-accepted'},origin);
          $('chatForm')?.requestSubmit();
        }
        if(event.data.kind==='stop')adapter.stop();
        if(event.data.kind==='listen')adapter.listen();
      };
      window.addEventListener('message',commands);timer=setInterval(announce,250);
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
    let bar=null;
    function command(kind){if(frame?.contentWindow)frame.contentWindow.postMessage({channel:'vp3-orb',kind},origin);else adapter[kind]?.();}
    function close(){panel.hidden=true;root.dataset.popupOpen='false';restorePanel?.();restorePanel=null;document.querySelectorAll('[data-orb-chat]').forEach(b=>b.setAttribute('aria-expanded','false'));returnFocus?.focus?.();}
    function open(){
      returnFocus=document.activeElement;
      if(adapter.isMainChat?.()){adapter.focusChat?.();return;}
      if(adapter.attachChat)restorePanel=restorePanel||adapter.attachChat(host);
      else if(!frame){
        frame=document.createElement('iframe');frame.title='VP3 Agent Chat';frame.allow='microphone; camera';const url=new URL(adapter.chatUrl,location.href);url.searchParams.set('orb_embed','1');frame.src=url.href;host.append(frame);
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
    if(composer){composer.insertBefore(button(),composer.firstChild);}
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
      const data=event.data;if(data.kind==='ready'){ready=true;if(pendingListen){pendingListen=false;command('listen');}if(pendingText)frame.contentWindow.postMessage({channel:'vp3-orb',kind:'compose',text:pendingText},origin);}
      if(data.kind==='state')render(data.state);
      if(data.kind==='draft-accepted'){const input=bar?.querySelector('input');if(input&&input.value===pendingText){input.value='';try{sessionStorage.removeItem(draftKey);}catch(_){}}pendingText='';}
      if(data.kind==='draft-blocked'){pendingText='';bubble('Your existing draft is in the chat popup. Your chatbar message is still here.');}
    }
    function render(raw){const s=window.VP3EyesCore.visualState(raw);root.dataset.state=s.state;root.dataset.recording=String(s.recording);label.textContent=(s.recording?'Recording · ':'')+({idle:'Ready',listening:'Listening',thinking:'Thinking',speaking:'Speaking',connecting:'Connecting',error:'Needs attention'}[s.state]||'Ready');ball.setAttribute('aria-label',`Agent: ${label.textContent}. Open chat`);}
    function bubble(text){const node=root.querySelector('.vp3-orb-bubble');node.textContent=String(text).slice(0,600);node.hidden=false;clearTimeout(bubbleTimer);bubbleTimer=setTimeout(()=>{node.hidden=true;},14000);}
    let bubbleTimer=0,drag=null,dragged=false;
    function save(){const r=root.getBoundingClientRect();try{localStorage.setItem(key,JSON.stringify({x:r.left,y:r.top,small:root.dataset.small==='true'}));}catch(_){}}
    ball.addEventListener('pointerdown',event=>{if(event.button!==0)return;dragged=false;const r=root.getBoundingClientRect();drag={id:event.pointerId,x:event.clientX,y:event.clientY,left:r.left,top:r.top};ball.setPointerCapture(event.pointerId);});
    ball.addEventListener('pointermove',event=>{if(drag?.id!==event.pointerId)return;const dx=event.clientX-drag.x,dy=event.clientY-drag.y;if(Math.hypot(dx,dy)>5)dragged=true;if(!dragged)return;root.style.left=`${Math.max(0,Math.min(innerWidth-root.offsetWidth,drag.left+dx))}px`;root.style.top=`${Math.max(0,Math.min(innerHeight-root.offsetHeight-80,drag.top+dy))}px`;root.style.right='auto';root.style.bottom='auto';});
    ball.addEventListener('pointerup',()=>{drag=null;if(dragged)save();});ball.addEventListener('pointercancel',()=>{drag=null;});
    const resize=()=>{const r=root.getBoundingClientRect();if(r.left>innerWidth-80||r.top>innerHeight-140){root.style.left='auto';root.style.top='auto';root.style.right='12px';root.style.bottom='94px';}};
    const escape=event=>{if(event.key==='Escape'&&!panel.hidden)close();};
    window.addEventListener('message',message);window.addEventListener('resize',resize);document.addEventListener('keydown',escape);
    timer=setInterval(()=>{if(!frame){const next=state(),json=JSON.stringify(next);if(json!==lastState){render(next);lastState=json;}}},200);
    window.VP3AgentOrb={open,close,toggle,bubble,state,stop:()=>command('stop')};
    window.addEventListener('pagehide',()=>{disposed=true;clearInterval(timer);clearTimeout(bubbleTimer);window.removeEventListener('message',message);window.removeEventListener('resize',resize);document.removeEventListener('keydown',escape);adapter.stop?.();},{once:true});
  };
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
