/* First-launch product introduction only: every setup operation runs inside Agent Chat. */
(() => {
  'use strict';
  const byId=id=>document.getElementById(id);
  const first=byId('startSetup'),next=byId('enterAgent');
  first.disabled=true;next.disabled=true;
  const launch=()=>window.location.assign('/#chat');
  first.addEventListener('click',launch);
  next.addEventListener('click',launch);
  (async()=>{
    try{
      const [system,catalog]=await Promise.all([
        fetch('/api/v1/control/system',{credentials:'same-origin',cache:'no-store'}),
        fetch('/api/v1/control/local-apps',{credentials:'same-origin',cache:'no-store'})
      ]);
      if(!system.ok||!catalog.ok)throw new Error('Not authorized');
      const installed=await catalog.json();
      const packages=(installed.packages||[]).filter(p=>['whisper-stt','piper-tts'].includes(p.key));
      const ready=packages.filter(p=>p.installed?.status==='installed'&&p.installed?.healthy===true);
      byId('voiceState').textContent=ready.length===2?'Voice essentials ready':'Setup in Agent Chat';
      byId('packageDetail').textContent=packages.map(p=>p.name+': '+(p.installed?.healthy===true?'ready':'can be prepared in Chat')).join(' · ');
      byId('overallState').textContent='Ready to meet';
      byId('setupIntroduction').textContent='Your Agent guides the entire setup from its conversation canvas.';
      first.textContent='Meet my Agent →';next.textContent='Open Agent Chat →';
      first.disabled=false;next.disabled=false;
    }catch(_){
      byId('overallState').textContent='Open from HomeServer tray';
      byId('setupIntroduction').textContent='Launch HomeServer from the tray to authorize this session.';
      first.disabled=true;next.disabled=true;
    }
  })();
})();