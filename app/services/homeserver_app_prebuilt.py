from __future__ import annotations

import hashlib
import io
import json
import zipfile
from typing import Any

from ..database import db
from . import homeserver_app_data_lifecycle, homeserver_app_packages, homeserver_app_releases, homeserver_apps

CONTRACT = "vp3.app.prebuilt-catalog.v1"
CATALOG_VERSION = "2026.09.30.16"

APP_CSS = """*{box-sizing:border-box}body{margin:0;background:#f5f6f8;color:#181b1f;font:14px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.shell{max-width:980px;margin:0 auto;padding:28px}.top{display:flex;justify-content:space-between;gap:16px;margin-bottom:18px}.top h1{margin:3px 0}.eyebrow{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:#727980}.muted{color:#6b7278}.panel{background:#fff;border:1px solid #e2e6e9;border-radius:15px;padding:18px}.toolbar{display:flex;gap:8px;margin-bottom:14px}.toolbar input{flex:1;min-width:0;border:1px solid #d5d9dd;border-radius:9px;padding:10px 11px;font:inherit}.button{border:0;border-radius:9px;padding:10px 14px;font-weight:700;cursor:pointer;background:#17191c;color:#fff}.secondary{background:#eef0f2;color:#202428}.danger{background:#fff1f1;color:#a43c3c}.list{display:grid;gap:10px}.row{border:1px solid #e7eaed;border-radius:12px;padding:13px;display:flex;justify-content:space-between;gap:14px}.row h3{margin:0 0 4px;font-size:15px}.row p{margin:0;color:#697075}.actions{display:flex;gap:7px}.empty{padding:28px;text-align:center;color:#777f86}.pill{display:inline-flex;padding:3px 8px;border-radius:999px;background:#eef1f3;font-size:11px}@media(max-width:700px){.shell{padding:18px}.toolbar,.row{display:block}.toolbar>*{width:100%;margin-bottom:7px}.actions{margin-top:10px}}"""

MEDIA_SERVER_CSS = """*{box-sizing:border-box}body{margin:0;background:#0f1115;color:#f5f7fa;font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.shell{max-width:1280px;margin:0 auto;padding:24px}.top{display:flex;justify-content:space-between;gap:18px;align-items:flex-start}.top h1{font-size:34px;margin:4px 0}.eyebrow{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:#8e98a4}.muted{color:#9aa3ad}.pill,.chip{display:inline-flex;padding:5px 9px;border-radius:999px;background:#20252c;color:#d8dee6;font-size:11px}.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:20px 0}.toolbar input,.toolbar select{background:#171b20;color:#fff;border:1px solid #303741;border-radius:9px;padding:10px 12px}.toolbar input{flex:1;min-width:240px}.button{border:0;border-radius:9px;padding:10px 14px;font-weight:700;cursor:pointer;background:#f5f7fa;color:#11151a}.secondary{background:#232931;color:#eef2f6}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:18px 0}.stat,.panel{background:#171b20;border:1px solid #292f37;border-radius:14px;padding:15px}.stat strong{display:block;font-size:24px;margin-top:4px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}.card{background:#171b20;border:1px solid #292f37;border-radius:14px;overflow:hidden}.thumb{aspect-ratio:16/10;background:#242a32;display:grid;place-items:center;font-size:40px}.card-body{padding:12px}.card h3{font-size:14px;margin:0 0 5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.card p{font-size:12px;color:#9aa3ad;margin:0}.manage{margin-top:20px}.manage-grid{display:grid;grid-template-columns:1fr auto auto;gap:8px}.manage-grid input{background:#11151a;color:#fff;border:1px solid #303741;border-radius:9px;padding:10px}.roots{display:grid;gap:7px;margin-top:10px}.root{display:flex;justify-content:space-between;gap:10px;padding:9px 0;border-top:1px solid #292f37}.player{position:fixed;inset:0;background:rgba(0,0,0,.82);display:grid;place-items:center;padding:30px;z-index:30}.player.hidden{display:none}.player-box{width:min(1000px,95vw);background:#0b0d10;border-radius:15px;padding:14px}.player video,.player audio,.player img{width:100%;max-height:75vh;object-fit:contain;background:#000}.player-head{display:flex;justify-content:space-between;gap:10px;align-items:center;margin-bottom:10px}.empty{padding:50px;text-align:center;color:#89939e}@media(max-width:700px){.stats{grid-template-columns:repeat(2,1fr)}.manage-grid{grid-template-columns:1fr}.shell{padding:16px}}"""

VIDEO_EDITOR_CSS = """*{box-sizing:border-box}body{margin:0;background:#0d0f12;color:#f4f6f8;font:14px/1.4 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.shell{min-height:100vh;display:grid;grid-template-rows:auto 1fr auto}.top{display:flex;justify-content:space-between;align-items:center;padding:14px 18px;border-bottom:1px solid #292e35;background:#14171b}.top h1{font-size:18px;margin:0}.eyebrow{font-size:10px;letter-spacing:.12em;color:#8e98a4}.button{border:0;border-radius:8px;padding:9px 12px;font-weight:700;cursor:pointer;background:#f3f5f7;color:#111}.secondary{background:#232830;color:#eef2f6}.workspace{display:grid;grid-template-columns:250px 1fr 290px;min-height:0}.panel{padding:14px;border-right:1px solid #292e35;background:#121519;overflow:auto}.panel.right{border-right:0;border-left:1px solid #292e35}.viewer{display:grid;grid-template-rows:minmax(260px,1fr) auto;background:#090b0e}.stage{display:grid;place-items:center;padding:22px}.canvas{width:min(900px,92%);aspect-ratio:16/9;background:#000;border:1px solid #303640;display:grid;place-items:center;color:#65707b}.transport{padding:12px;display:flex;gap:8px;justify-content:center;border-top:1px solid #292e35}.timeline{grid-column:1/-1;border-top:1px solid #292e35;background:#101317;padding:12px;min-height:220px}.track{display:grid;grid-template-columns:120px 1fr;gap:8px;margin:7px 0}.track-name{padding:10px;background:#1a1f25;border-radius:8px}.lane{min-height:48px;background:#171b20;border-radius:8px;padding:5px;display:flex;gap:5px;align-items:center}.clip{background:#2c333c;border:1px solid #424b57;border-radius:7px;padding:8px 10px;white-space:nowrap}.list{display:grid;gap:7px}.item{padding:9px;border:1px solid #2b3139;border-radius:8px;background:#191d22;cursor:pointer}.muted{color:#8e98a4}.field{width:100%;background:#0e1115;color:#fff;border:1px solid #303741;border-radius:8px;padding:9px;margin:5px 0}.row{display:flex;gap:7px;align-items:center}.status{font-size:11px;padding:4px 7px;border-radius:999px;background:#232830}@media(max-width:900px){.workspace{grid-template-columns:1fr}.panel.right{border-left:0;border-top:1px solid #292e35}.timeline{grid-column:1}.panel{border-right:0;border-bottom:1px solid #292e35}}"""

VIDEO_EDITOR_JS = """const api='/api/v1/control/homeserver-apps/video-editor';const media='/api/v1/control/homeserver-apps/media-server';let active=null,library=[];const esc=s=>String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));async function req(url,opt={}){const r=await fetch(url,{...opt,headers:{'Content-Type':'application/json',...(opt.headers||{})}});if(!r.ok){let m='Request failed';try{m=(await r.json()).detail||m}catch{}throw new Error(m)}return r.json()}async function loadProjects(){const j=await req(api+'/projects');document.getElementById('projects').innerHTML=j.projects.map(p=>'<button class=\"item\" data-project=\"'+esc(p.project_id)+'\">'+esc(p.name)+'</button>').join('')||'<p class=\"muted\">No projects yet.</p>';if(!active&&j.projects[0])openProject(j.projects[0].project_id)}async function loadMedia(){try{const j=await req(media+'/library?limit=100&media_type=video');library=j.items||[]}catch{library=[]}document.getElementById('media').innerHTML=library.map(x=>'<button class=\"item\" data-media=\"'+esc(x.media_id)+'\">'+esc(x.title)+'</button>').join('')||'<p class=\"muted\">Install and scan Media Server to add source clips.</p>'}async function openProject(id){active=await req(api+'/projects/'+encodeURIComponent(id));document.getElementById('projectTitle').textContent=active.project.name;document.getElementById('timeline').innerHTML=active.tracks.map(t=>'<div class=\"track\"><div class=\"track-name\">'+esc(t.name)+'</div><div class=\"lane\" data-track=\"'+esc(t.track_id)+'\">'+active.clips.filter(c=>c.track_id===t.track_id).map(c=>'<button class=\"clip\" data-clip=\"'+esc(c.clip_id)+'\">'+esc((c.metadata||{}).title||c.media_id)+'</button>').join('')+'</div></div>').join('');document.getElementById('renders').innerHTML=active.renders.map(r=>'<div class=\"item\">'+esc(r.output_name)+' · '+esc(r.status)+'</div>').join('')||'<p class=\"muted\">No renders yet.</p>'}document.getElementById('newProject').onclick=async()=>{const name=prompt('Project name');if(!name)return;const j=await req(api+'/projects',{method:'POST',body:JSON.stringify({name})});await loadProjects();await openProject(j.project.project_id)};document.getElementById('render').onclick=async()=>{if(!active)return;await req(api+'/projects/'+encodeURIComponent(active.project.project_id)+'/render',{method:'POST',body:JSON.stringify({preset:'1080p',format:'mp4'})});await openProject(active.project.project_id)};document.getElementById('projects').onclick=e=>{const b=e.target.closest('[data-project]');if(b)openProject(b.dataset.project)};document.getElementById('media').onclick=async e=>{const b=e.target.closest('[data-media]');if(!b||!active)return;const track=active.tracks.find(t=>t.kind==='video');if(!track)return;await req(api+'/projects/'+encodeURIComponent(active.project.project_id)+'/clips',{method:'POST',body:JSON.stringify({track_id:track.track_id,media_id:b.dataset.media,start_seconds:0})});await openProject(active.project.project_id)};Promise.all([loadProjects(),loadMedia()]).catch(e=>document.getElementById('projectTitle').textContent=e.message);"""

COMMON_JS = """const cfg=window.VP3_PREBUILT;const dataUrl='/api/v1/control/homeserver-apps/'+encodeURIComponent(cfg.key)+'/data/file?path='+encodeURIComponent('data.json');const sampleUrl='/api/v1/control/homeserver-apps/'+encodeURIComponent(cfg.key)+'/sample-data';const esc=(s)=>String(s??'').replace(/[&<>"']/g,(c)=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));async function readData(){try{const r=await fetch(dataUrl,{cache:'no-store'});if(r.status===404)return [];if(!r.ok)return [];return await r.json()}catch(e){return []}}async function writeData(value){const blob=new Blob([JSON.stringify(value,null,2)],{type:'application/json'});const f=new FormData();f.append('file',blob,'data.json');const r=await fetch(dataUrl,{method:'PUT',body:f});if(!r.ok)throw new Error('Unable to save app data')}async function samples(){try{const r=await fetch(sampleUrl,{cache:'no-store'});if(!r.ok)return [];const j=await r.json();return j.sample_data?.items||[]}catch(e){return []}}let items=[];function markup(x,i){if(cfg.kind==='notes')return '<article class="row"><div><h3>'+esc(x.title)+'</h3><p>'+esc(x.body)+'</p></div><div class="actions"><button class="button secondary danger" data-delete="'+i+'">Delete</button></div></article>';if(cfg.kind==='inventory')return '<article class="row"><div><h3>'+esc(x.name)+'</h3><p>Quantity: <strong>'+Number(x.qty||0)+'</strong></p></div><div class="actions"><button class="button secondary" data-minus="'+i+'">−</button><button class="button secondary" data-plus="'+i+'">+</button><button class="button secondary danger" data-delete="'+i+'">Delete</button></div></article>';return '<article class="row"><div><h3>'+(x.done?'✓ ':'')+esc(x.task)+'</h3><p>'+(x.done?'Complete':'Open')+'</p></div><div class="actions"><button class="button secondary" data-toggle="'+i+'">'+(x.done?'Reopen':'Complete')+'</button><button class="button secondary danger" data-delete="'+i+'">Delete</button></div></article>'}function render(){const n=document.getElementById('list');n.innerHTML=items.length?items.map(markup).join(''):'<div class="empty">No items yet.</div>'}async function save(){await writeData(items);render()}async function init(){items=await readData();if(!items.length){const s=await samples();if(s.length)items=s}render()}document.getElementById('form').addEventListener('submit',async(e)=>{e.preventDefault();if(cfg.kind==='notes')items.unshift({title:document.getElementById('field1').value.trim(),body:document.getElementById('field2').value.trim()});else if(cfg.kind==='inventory')items.unshift({name:document.getElementById('field1').value.trim(),qty:Number(document.getElementById('field2').value||0)});else items.unshift({task:document.getElementById('field1').value.trim(),done:false});await save();e.target.reset();if(cfg.kind==='inventory')document.getElementById('field2').value='1'});document.addEventListener('click',async(e)=>{const b=e.target.closest('[data-delete],[data-plus],[data-minus],[data-toggle]');if(!b)return;const raw=b.dataset.delete??b.dataset.plus??b.dataset.minus??b.dataset.toggle;const i=Number(raw);if(b.dataset.delete!==undefined)items.splice(i,1);else if(b.dataset.plus!==undefined)items[i].qty=Number(items[i].qty||0)+1;else if(b.dataset.minus!==undefined)items[i].qty=Math.max(0,Number(items[i].qty||0)-1);else items[i].done=!items[i].done;await save()});init();"""

MEDIA_LIBRARY_JS = """const api='/api/v1/control/homeserver-apps/media-library';let active=null,lastHistory=null;const esc=s=>String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));async function req(path,opt={}){const r=await fetch(api+path,{...opt,headers:{'Content-Type':'application/json',...(opt.headers||{})}});if(!r.ok){let m='Request failed';try{m=(await r.json()).detail||m}catch{}throw new Error(m)}return r.json()}async function loadCollections(){const j=await req('/collections?limit=200');document.getElementById('collections').innerHTML=j.collections.map(x=>'<button class=\"row\" data-collection=\"'+esc(x.collection_id)+'\"><span><strong>'+esc(x.name)+'</strong><br><small class=\"muted\">'+esc(x.collection_type)+' · '+x.count+' items</small></span></button>').join('')||'<p class=\"muted\">No collections yet.</p>'}async function loadCleanup(){const s=await req('/cleanup/status'),j=await req('/duplicates?limit=100');document.getElementById('cleanupStats').textContent=s.scan_required?'Run a duplicate scan to build the cleanup snapshot.':s.duplicate_groups+' groups · '+s.exact_groups+' exact · '+s.candidate_groups+' candidates · '+s.needs_review+' need review';document.getElementById('duplicates').innerHTML=j.groups.map(x=>'<div class=\"row\"><span><strong>'+esc(x.kind==='exact'?'Verified duplicate':'Review candidate')+'</strong><br><small class=\"muted\">'+x.copies+' items · '+esc((x.metadata_conflicts||[]).length?('metadata conflicts: '+x.metadata_conflicts.join(', ')):(x.confidence||''))+'</small></span><div class=\"actions\"><button class=\"button secondary\" data-keep=\"'+esc(x.group_key)+'\">Keep both</button><button class=\"button secondary\" data-ignore=\"'+esc(x.group_key)+'\">Ignore</button></div></div>').join('')||'<p class=\"muted\">No duplicate groups in the current snapshot.</p>'}async function load(){const s=await req('/status');document.getElementById('stats').textContent=s.metadata_items+' enriched items · '+s.tags+' tags · '+s.collections+' collections · '+s.smart_collections+' smart · '+s.duplicate_groups+' duplicate groups';await Promise.all([search(),loadCollections(),loadCleanup()])}async function search(){const q=encodeURIComponent(document.getElementById('q').value||''),t=encodeURIComponent(document.getElementById('type').value||'');const j=await req('/search?q='+q+'&media_type='+t+'&limit=200');document.getElementById('items').innerHTML=j.items.map(x=>'<button class=\"row\" data-id=\"'+esc(x.media_id)+'\"><span><strong>'+esc(x.metadata.title||x.canonical.title)+'</strong><br><small class=\"muted\">'+esc(x.canonical.media_type)+' · '+esc((x.metadata.tags||[]).join(', '))+'</small></span></button>').join('')||'<p class=\"muted\">No matching media.</p>'}async function openItem(id){active=(await req('/items/'+encodeURIComponent(id))).item;document.getElementById('editor').hidden=false;document.getElementById('editorTitle').textContent=active.metadata.title||active.canonical.title;document.getElementById('metaTitle').value=active.metadata.title||'';document.getElementById('tags').value=(active.metadata.tags||[]).join(', ');document.getElementById('rating').value=active.metadata.rating??'';document.getElementById('description').value=active.metadata.description||'';const h=await req('/items/'+encodeURIComponent(id)+'/history?limit=1');lastHistory=h.history[0]?.history_id||null}document.getElementById('items').onclick=e=>{const b=e.target.closest('[data-id]');if(b)openItem(b.dataset.id)};document.getElementById('save').onclick=async()=>{if(!active)return;const patch={title:document.getElementById('metaTitle').value.trim(),description:document.getElementById('description').value,tags:document.getElementById('tags').value.split(',').map(x=>x.trim()).filter(Boolean),rating:document.getElementById('rating').value===''?null:Number(document.getElementById('rating').value)};const j=await req('/items/'+encodeURIComponent(active.media_id),{method:'PUT',body:JSON.stringify({patch,reason:'Media Library UI update'})});lastHistory=j.history_id;await openItem(active.media_id);await search()};document.getElementById('undo').onclick=async()=>{if(!lastHistory)return;await req('/history/'+encodeURIComponent(lastHistory)+'/undo',{method:'POST'});await openItem(active.media_id);await search()};document.getElementById('newCollection').onclick=async()=>{const name=prompt('Collection name');if(!name)return;await req('/collections',{method:'POST',body:JSON.stringify({name,collection_type:'manual'})});await loadCollections()};document.getElementById('newSmart').onclick=async()=>{const name=prompt('Smart collection name');if(!name)return;const tag=prompt('Tag rule (optional)')||'';const type=prompt('Media type: video, audio, image, or blank')||'';const rules={};if(tag.trim())rules.tag=tag.trim();if(type.trim())rules.media_type=type.trim();if(!Object.keys(rules).length){alert('Add at least one smart rule.');return}await req('/collections',{method:'POST',body:JSON.stringify({name,collection_type:'smart',rules})});await loadCollections()};document.getElementById('scanDuplicates').onclick=async()=>{document.getElementById('cleanupStats').textContent='Scanning…';await req('/duplicates/scan',{method:'POST'});await loadCleanup()};document.getElementById('duplicates').onclick=async e=>{const keep=e.target.closest('[data-keep]'),ignore=e.target.closest('[data-ignore]');const b=keep||ignore;if(!b)return;const key=keep?keep.dataset.keep:ignore.dataset.ignore,decision=keep?'keep_both':'ignore';await req('/duplicates/'+encodeURIComponent(key)+'/review',{method:'PUT',body:JSON.stringify({decision})});await loadCleanup()};document.getElementById('search').onclick=search;document.getElementById('refresh').onclick=load;load().catch(e=>document.getElementById('stats').textContent=e.message);"""

MEDIA_PROCESSOR_JS = """const local=location.pathname.includes('/api/v1/control/homeserver-apps/');const control='/api/v1/control/homeserver-apps/media-processor';let access=sessionStorage.getItem('vp3_processor_access')||'';const auth=()=>access?{'Authorization':'Bearer '+access}:{};const esc=s=>String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));async function req(path,opt={}){const base=local?control:'/__vp3_processor__';const r=await fetch(base+path,{...opt,headers:{'Content-Type':'application/json',...(opt.headers||{}),...(!local?auth():{})}});if(r.status===401&&!local){access=prompt('Media Processor access key')||'';sessionStorage.setItem('vp3_processor_access',access);return req(path,opt)}if(!r.ok){let m='Request failed';try{m=(await r.json()).detail||m}catch{}throw new Error(m)}return r.json()}async function load(){const [s,j,d]=await Promise.all([req('/status'),req('/jobs?limit=100'),req('/derivatives?limit=100')]);document.getElementById('stats').textContent=s.active+' active · '+s.queued+' queued · '+s.failed+' failed · '+s.completed+' completed · '+s.derivatives+' derivatives';document.getElementById('tools').textContent=s.ffmpeg_available?'Managed FFmpeg: '+(s.ffmpeg_version||'available'):'Managed FFmpeg unavailable';document.getElementById('jobs').innerHTML=j.jobs.map(x=>'<div class=\"item\"><strong>'+esc(x.operation)+'</strong> · '+esc(x.status)+' · '+Math.round((x.progress||0)*100)+'%'+(x.eta_seconds!=null?' · ETA '+x.eta_seconds+'s':'')+'<br><small class=\"muted\">'+esc(x.media_id)+'</small></div>').join('')||'<p class=\"muted\">No processing jobs.</p>';document.getElementById('derivatives').innerHTML=d.derivatives.map(x=>'<div class=\"item\">'+esc(x.kind)+' · '+esc(x.format)+' · '+Math.round((x.size_bytes||0)/1024)+' KB</div>').join('')||'<p class=\"muted\">No derivatives yet.</p>'}document.getElementById('enqueue').onclick=async()=>{const media_id=document.getElementById('mediaId').value.trim(),operation=document.getElementById('operation').value;if(!media_id)return;await req('/jobs',{method:'POST',body:JSON.stringify({media_id,operation})});await load()};document.getElementById('refresh').onclick=load;load().catch(e=>document.getElementById('stats').textContent=e.message);setInterval(()=>load().catch(()=>{}),3000);"""

DOWNLOAD_MANAGER_JS = """const local=location.pathname.includes('/api/v1/control/homeserver-apps/');const control='/api/v1/control/homeserver-apps/download-manager';let access=sessionStorage.getItem('vp3_download_access')||'';const auth=()=>access?{'Authorization':'Bearer '+access}:{};const esc=s=>String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));async function req(path,opt={}){const base=local?control:'/__vp3_downloads__';const r=await fetch(base+path,{...opt,headers:{'Content-Type':'application/json',...(opt.headers||{}),...(!local?auth():{})}});if(r.status===401&&!local){access=prompt('Download Manager access key')||'';sessionStorage.setItem('vp3_download_access',access);return req(path,opt)}if(!r.ok){let m='Request failed';try{m=(await r.json()).detail||m}catch{}throw new Error(m)}return r.json()}async function load(){const [s,dst,list]=await Promise.all([req('/status'),req('/destinations'),req('/downloads?limit=200')]);document.getElementById('stats').textContent=s.active+' active · '+s.queued+' queued · '+s.paused+' paused · '+s.failed+' failed · '+s.completed+' completed';document.getElementById('destinations').innerHTML=dst.destinations.map(x=>'<div class=\"item\">'+esc(x.label)+' · '+esc(x.destination_kind)+'</div>').join('');document.getElementById('downloads').innerHTML=list.downloads.map(x=>'<div class=\"item\"><strong>'+esc(x.final_filename||x.display_url)+'</strong><br><span class=\"muted\">'+esc(x.status)+' · '+Math.round((x.progress||0)*100)+'%</span><div><button data-pause=\"'+esc(x.download_id)+'\">Pause</button> <button data-resume=\"'+esc(x.download_id)+'\">Resume</button> <button data-cancel=\"'+esc(x.download_id)+'\">Cancel</button></div></div>').join('')||'<p class=\"muted\">No downloads.</p>'}document.getElementById('add').onclick=async()=>{const url=document.getElementById('url').value.trim();if(!url)return;await req('/downloads',{method:'POST',body:JSON.stringify({url})});document.getElementById('url').value='';await load()};document.getElementById('refresh').onclick=load;document.getElementById('downloads').onclick=async e=>{const p=e.target.closest('[data-pause]'),r=e.target.closest('[data-resume]'),c=e.target.closest('[data-cancel]');if(p)await req('/downloads/'+encodeURIComponent(p.dataset.pause)+'/pause',{method:'POST'});if(r)await req('/downloads/'+encodeURIComponent(r.dataset.resume)+'/resume',{method:'POST'});if(c&&confirm('Cancel this download?'))await req('/downloads/'+encodeURIComponent(c.dataset.cancel),{method:'DELETE'});await load()};document.getElementById('remote').onclick=async()=>{if(!local)return;const x=await req('/remote/enable',{method:'POST'});prompt('Copy this access key now',x.access_key)};load().catch(e=>document.getElementById('stats').textContent=e.message);setInterval(()=>load().catch(()=>{}),3000);"""

PHOTO_LIBRARY_JS = """const local=location.pathname.includes('/api/v1/control/homeserver-apps/');const control='/api/v1/control/homeserver-apps/photo-library';let access=sessionStorage.getItem('vp3_photo_access')||sessionStorage.getItem('vp3_media_access')||'';const auth=()=>access?{'Authorization':'Bearer '+access}:{};const esc=s=>String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));async function req(path,opt={}){const base=local?control:'/__vp3_photos__';const r=await fetch(base+path,{...opt,headers:{'Content-Type':'application/json',...(opt.headers||{}),...(!local?auth():{})}});if(r.status===401&&!local){access=prompt('Photo Library access key')||'';sessionStorage.setItem('vp3_photo_access',access);return req(path,opt)}if(!r.ok){let m='Request failed';try{m=(await r.json()).detail||m}catch{}throw new Error(m)}return r.json()}async function imageUrl(mediaId){if(local)return '/api/v1/control/homeserver-apps/media-server/stream/'+encodeURIComponent(mediaId);const t=await req('/stream-ticket/'+encodeURIComponent(mediaId));return t.stream_url}async function renderPhotos(rows){const el=document.getElementById('photos');el.innerHTML=rows.map(x=>'<button class="photo-card" data-photo="'+esc(x.media_id)+'" data-title="'+esc(x.title)+'"><div class="photo-thumb" data-thumb="'+esc(x.media_id)+'"></div><strong>'+esc(x.title)+'</strong><br><small class="muted">'+esc(x.folder_album)+'</small></button>').join('')||'<p class="muted">No photos yet. Map a folder in Media Server, scan it, then sync.</p>';const observer=new IntersectionObserver(entries=>{for(const entry of entries){if(!entry.isIntersecting)continue;const n=entry.target;observer.unobserve(n);imageUrl(n.dataset.thumb).then(u=>{n.style.backgroundImage='url("'+u.replaceAll('"','')+'")'}).catch(()=>{})}},{rootMargin:'300px'});for(const n of el.querySelectorAll('[data-thumb]'))observer.observe(n)}async function load(){const [s,p,a,smart]=await Promise.all([req('/status'),req('/photos?limit=200'),req('/albums'),req('/smart-albums')]);document.getElementById('stats').textContent=s.photos+' photos · '+s.albums+' albums · '+s.tags+' tags · '+s.mapped_sources+' mapped sources';await renderPhotos(p.photos);document.getElementById('albums').innerHTML=a.albums.map(x=>'<div class=\"item\">'+esc(x.name)+' · '+x.photos+' photos</div>').join('')||'<p class=\"muted\">No albums.</p>';document.getElementById('smart').innerHTML=smart.smart_albums.map(x=>'<div class=\"item\">'+esc(x.name)+' · '+x.photos+' photos</div>').join('')}document.getElementById('sync').onclick=async()=>{await req('/sync',{method:'POST'});await load()};document.getElementById('search').onclick=async()=>{const q=encodeURIComponent(document.getElementById('q').value);const p=await req('/photos?q='+q+'&limit=200');await renderPhotos(p.photos)};document.getElementById('newAlbum').onclick=async()=>{const name=prompt('Album name');if(!name)return;await req('/albums',{method:'POST',body:JSON.stringify({name})});await load()};document.getElementById('photos').onclick=async e=>{const b=e.target.closest('[data-photo]');if(!b)return;const dlg=document.getElementById('viewer');document.getElementById('viewerImage').src=await imageUrl(b.dataset.photo);document.getElementById('viewerTitle').textContent=b.dataset.title;dlg.showModal()};document.getElementById('closeViewer').onclick=()=>document.getElementById('viewer').close();document.getElementById('slideshow').onclick=async()=>{const s=await req('/slideshow?limit=200');if(!s.slides.length)return;let i=0;const dlg=document.getElementById('viewer'),img=document.getElementById('viewerImage'),title=document.getElementById('viewerTitle');dlg.showModal();const next=async()=>{const row=s.slides[i++%s.slides.length];img.src=await imageUrl(row.media_id);title.textContent=row.title};await next();const timer=setInterval(next,3000);dlg.addEventListener('close',()=>clearInterval(timer),{once:true})};load().catch(e=>document.getElementById('stats').textContent=e.message);"""

MUSIC_SERVER_JS = """const local=location.pathname.includes('/api/v1/control/homeserver-apps/');const control='/api/v1/control/homeserver-apps/music-server';let access=sessionStorage.getItem('vp3_music_access')||sessionStorage.getItem('vp3_media_access')||'';const auth=()=>access?{'Authorization':'Bearer '+access}:{};const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));async function req(path,opt={}){const base=local?control:'/__vp3_music__';const r=await fetch(base+path,{...opt,headers:{'Content-Type':'application/json',...(opt.headers||{}),...(!local?auth():{})}});if(r.status===401&&!local){access=prompt('Music Server access key')||'';sessionStorage.setItem('vp3_music_access',access);return req(path,opt)}if(!r.ok){let m='Request failed';try{m=(await r.json()).detail||m}catch{}throw new Error(m)}return r.json()}async function playTrack(mediaId){let stream=local?'/api/v1/control/homeserver-apps/media-server/stream/'+encodeURIComponent(mediaId):'';if(!local){const ticket=await req('/stream-ticket/'+encodeURIComponent(mediaId));stream=ticket.stream_url}const player=document.getElementById('player');player.src=stream;await req('/playback',{method:'POST',body:JSON.stringify({command:'play',media_id:mediaId,position_seconds:0})});try{await player.play()}catch{}await load()}async function load(){const [s,t,p,q]=await Promise.all([req('/status'),req('/tracks?limit=200'),req('/playlists'),req('/queue')]);document.getElementById('stats').textContent=s.tracks+' tracks · '+s.artists+' artists · '+s.albums+' albums · '+s.mapped_sources+' mapped sources';document.getElementById('tracks').innerHTML=t.tracks.map(x=>'<button class="item" data-play="'+esc(x.media_id)+'"><strong>'+esc(x.title)+'</strong><br><span class="muted">'+esc(x.artist)+' · '+esc(x.album)+'</span></button>').join('')||'<p class="muted">No tracks yet. Map a folder in Media Server, scan it, then sync.</p>';document.getElementById('playlists').innerHTML=p.playlists.map(x=>'<div class="item">'+esc(x.name)+' · '+x.tracks+' tracks</div>').join('')||'<p class="muted">No playlists.</p>';document.getElementById('queue').innerHTML=q.queue.map(x=>'<div class="item">'+esc(x.title)+' · '+esc(x.artist)+'</div>').join('')||'<p class="muted">Queue is empty.</p>'}document.getElementById('sync').onclick=async()=>{await req('/sync',{method:'POST'});await load()};document.getElementById('search').onclick=async()=>{const q=encodeURIComponent(document.getElementById('q').value);const t=await req('/tracks?q='+q+'&limit=200');document.getElementById('tracks').innerHTML=t.tracks.map(x=>'<button class="item" data-play="'+esc(x.media_id)+'"><strong>'+esc(x.title)+'</strong><br><span class="muted">'+esc(x.artist)+' · '+esc(x.album)+'</span></button>').join('')};document.getElementById('tracks').onclick=async e=>{const b=e.target.closest('[data-play]');if(!b)return;await req('/queue',{method:'POST',body:JSON.stringify({media_id:b.dataset.play})});await playTrack(b.dataset.play)};document.getElementById('newPlaylist').onclick=async()=>{const name=prompt('Playlist name');if(!name)return;await req('/playlists',{method:'POST',body:JSON.stringify({name})});await load()};const player=document.getElementById('player');player.addEventListener('pause',()=>{if(!player.ended)req('/playback',{method:'POST',body:JSON.stringify({command:'pause',position_seconds:player.currentTime||0})}).catch(()=>{})});player.addEventListener('ended',()=>req('/playback',{method:'POST',body:JSON.stringify({command:'stop'})}).catch(()=>{}));load().catch(e=>document.getElementById('stats').textContent=e.message);"""

MEDIA_SERVER_JS = """const local=location.pathname.includes('/api/v1/control/homeserver-apps/');const control='/api/v1/control/homeserver-apps/media-server';let access=sessionStorage.getItem('vp3_media_access')||'';const auth=()=>access?{'Authorization':'Bearer '+access}:{};async function api(path,opt={}){const base=local?control:'/__vp3_media__';const r=await fetch(base+path,{...opt,headers:{...(opt.headers||{}),...(!local?auth():{})}});if(r.status===401&&!local){access=prompt('Media Server access key')||'';sessionStorage.setItem('vp3_media_access',access);return api(path,opt)}if(!r.ok){let m='Request failed';try{m=(await r.json()).detail||m}catch{}throw new Error(m)}return r.json()}const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const fmt=n=>{n=Number(n||0);if(n<1024)return n+' B';if(n<1048576)return (n/1024).toFixed(1)+' KB';if(n<1073741824)return (n/1048576).toFixed(1)+' MB';return (n/1073741824).toFixed(1)+' GB'};let current=null;async function load(){const q=document.getElementById('q').value.trim(),type=document.getElementById('type').value;const p=new URLSearchParams({limit:'300'});if(q)p.set('q',q);if(type)p.set('media_type',type);const [lib,status]=await Promise.all([api('/library?'+p),api('/status')]);document.getElementById('count').textContent=status.library_count||0;document.getElementById('videos').textContent=status.types.video||0;document.getElementById('audio').textContent=status.types.audio||0;document.getElementById('images').textContent=status.types.image||0;const grid=document.getElementById('grid');grid.innerHTML=lib.items.length?lib.items.map(x=>'<button class="card" data-media="'+esc(x.media_id)+'"><div class="thumb">'+(x.media_type==='video'?'▶':x.media_type==='audio'?'♫':'▧')+'</div><div class="card-body"><h3>'+esc(x.title)+'</h3><p>'+esc(x.media_type)+' · '+fmt(x.size_bytes)+'</p></div></button>').join(''):'<div class="empty">No media indexed yet.</div>';if(local)loadRoots()}async function loadRoots(){const r=await api('/roots');document.getElementById('roots').innerHTML=r.roots.map(x=>'<div class="root"><span><strong>'+esc(x.label)+'</strong><br><small class="muted">'+esc(x.computer_name||'This HomeServer')+' · '+esc(x.source_kind||'local_folder')+' · '+(x.connected?'Connected':'Unavailable')+'</small></span><span><button class="button secondary" data-check="'+esc(x.root_id)+'">Check</button> <button class="button secondary" data-remove="'+esc(x.root_id)+'">Remove</button></span></div>').join('')||'<p class="muted">No folders mapped.</p>'}async function openMedia(id){current=(await api('/item/'+encodeURIComponent(id))).item;const p=document.getElementById('playerContent');let stream=(local?control:'/__vp3_media__')+'/stream/'+encodeURIComponent(id);if(!local){const ticket=await api('/stream-ticket/'+encodeURIComponent(id));stream=ticket.stream_url}if(current.media_type==='video')p.innerHTML='<video controls autoplay src="'+stream+'"></video>';else if(current.media_type==='audio')p.innerHTML='<audio controls autoplay src="'+stream+'"></audio>';else p.innerHTML='<img src="'+stream+'" alt="">';document.getElementById('playerTitle').textContent=current.title;document.getElementById('player').classList.remove('hidden');const el=p.querySelector('video,audio');if(el&&current.playback?.position_seconds)el.currentTime=current.playback.position_seconds;if(el){el.addEventListener('timeupdate',()=>{if(Math.floor(el.currentTime)%10===0)api('/playback/'+encodeURIComponent(id),{method:local?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({position_seconds:el.currentTime,duration_seconds:el.duration||0,completed:false})}).catch(()=>{})})}}document.getElementById('q').addEventListener('input',()=>load());document.getElementById('type').addEventListener('change',load);document.getElementById('grid').addEventListener('click',e=>{const b=e.target.closest('[data-media]');if(b)openMedia(b.dataset.media)});document.getElementById('closePlayer').addEventListener('click',()=>{document.getElementById('player').classList.add('hidden');document.getElementById('playerContent').innerHTML=''});if(local){document.getElementById('manage').hidden=false;document.getElementById('addRoot').addEventListener('click',async()=>{const path=document.getElementById('rootPath').value.trim();if(!path)return;const computer_name=document.getElementById('computerName').value.trim(),source_hint=document.getElementById('sourceHint').value.trim();await api('/mapped-roots',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path,label:'',computer_name,source_hint,source_kind:computer_name?'computer_folder':'local_folder'})});document.getElementById('rootPath').value='';document.getElementById('computerName').value='';document.getElementById('sourceHint').value='';await api('/scan',{method:'POST'});load()});document.getElementById('scan').addEventListener('click',async()=>{await api('/scan',{method:'POST'});load()});document.getElementById('remote').addEventListener('click',async()=>{const r=await api('/remote/enable',{method:'POST'});prompt('Copy this access key now. It will not be shown again.',r.access_key)});document.getElementById('roots').addEventListener('click',async e=>{const check=e.target.closest('[data-check]');if(check){await api('/roots/'+encodeURIComponent(check.dataset.check)+'/check',{method:'POST'});load();return}const b=e.target.closest('[data-remove]');if(!b)return;await api('/roots/'+encodeURIComponent(b.dataset.remove),{method:'DELETE'});load()})}load().catch(e=>document.getElementById('grid').innerHTML='<div class="empty">'+esc(e.message)+'</div>');"""

CATALOG = {
    "vp3.notes": {
        "key": "vp3.notes",
        "name": "VP3 Notes",
        "version": "1.2.0",
        "release_channel": "stable",
        "min_homeserver_version": "2.4",
        "data_schema_version": "2",
        "data_migration_reversible": True,
        "release_notes": ["Adds governed app-data migration and recovery.", "Creates a verified pre-migration recovery snapshot."],
        "category": "Productivity",
        "kind": "notes",
        "description": "Private lightweight notes stored in isolated HomeServer app data.",
        "sample": [{"title": "Welcome to VP3 Notes", "body": "Sample data is visible only when Apps sample data is enabled."}],
    },
    "vp3.inventory": {
        "key": "vp3.inventory",
        "name": "VP3 Inventory",
        "version": "1.1.0",
        "release_channel": "stable",
        "min_homeserver_version": "2.4",
        "release_notes": ["Adds release lifecycle metadata.", "Supports verified updates and rollback."],
        "category": "Operations",
        "kind": "inventory",
        "description": "Track local inventory counts and supplies without a separate server.",
        "sample": [{"name": "Sample item", "qty": 12}, {"name": "Low-stock example", "qty": 2}],
    },
    "vp3.checklists": {
        "key": "vp3.checklists",
        "name": "VP3 Checklists",
        "version": "1.1.0",
        "release_channel": "stable",
        "min_homeserver_version": "2.4",
        "release_notes": ["Adds release lifecycle metadata.", "Supports verified updates and rollback."],
        "category": "Productivity",
        "kind": "checklist",
        "description": "Create local operational and personal checklists.",
        "sample": [{"task": "Review HomeServer Apps", "done": False}, {"task": "Create first user app", "done": False}],
    },
    "vp3.media-server": {
        "key": "vp3.media-server",
        "name": "VP3 Media Server",
        "version": "1.2.0",
        "sdk_version": "1.2",
        "release_channel": "stable",
        "min_homeserver_version": "2.4",
        "release_notes": [
            "Adds governed Media Processor handoff for conversion, thumbnails, proxies, and derived media.",
            "Retains mapped-source privacy, health checks, and in-place source ownership.",
            "Ships on SDK 1.2 / Agent Actions v2.",
        ],
        "category": "Media",
        "kind": "media_server",
        "description": "Index, browse, and stream your HomeServer media library locally or through governed VP3 Hosting.",
        "sample": [],
        "permissions": ["files.read"],
        "routes": {"local": True, "private_remote": True, "public": False},
        "agent_actions": [
            {
                "key":"media.roots.list","risk":"read","requires_confirmation":False,
                "input_schema":{"type":"object","properties":{},"additionalProperties":False},
                "executor":{"type":"builtin","provider":"media_server"}
            },
            {
                "key":"media.root.check","risk":"read","requires_confirmation":False,
                "input_schema":{"type":"object","properties":{"root_id":{"type":"string","minLength":1,"maxLength":64}},"required":["root_id"],"additionalProperties":False},
                "executor":{"type":"builtin","provider":"media_server"}
            },
            {
                "key":"media.scan","risk":"background","requires_confirmation":False,
                "input_schema":{"type":"object","properties":{"root_id":{"type":"string","maxLength":64}},"additionalProperties":False},
                "executor":{"type":"builtin","provider":"media_server"}
            },
            {
                "key":"media.process","risk":"consequential","requires_confirmation":True,
                "input_schema":{"type":"object","properties":{
                    "media_id":{"type":"string","minLength":1,"maxLength":100},
                    "operation":{"type":"string","enum":["thumbnail","proxy","video.convert","audio.convert","image.convert"]},
                    "preset":{"type":"string","maxLength":80},
                    "output_format":{"type":"string","enum":["","jpg","jpeg","png","webp","mp4","mp3"]},
                    "priority":{"type":"integer","minimum":-100,"maximum":100}
                },"required":["media_id","operation"],"additionalProperties":False},
                "executor":{"type":"builtin","provider":"media_server"}
            },
            {
                "key":"media.root.map","risk":"admin","requires_confirmation":True,
                "input_schema":{"type":"object","properties":{
                    "path":{"type":"string","minLength":1,"maxLength":2000},
                    "label":{"type":"string","maxLength":120},
                    "computer_name":{"type":"string","maxLength":120},
                    "source_hint":{"type":"string","maxLength":240},
                    "source_kind":{"type":"string","enum":["computer_folder","network_share","local_folder"]}
                },"required":["path"],"additionalProperties":False},
                "executor":{"type":"builtin","provider":"media_server"}
            },
            {
                "key":"media.root.remove","risk":"destructive","requires_confirmation":True,
                "input_schema":{"type":"object","properties":{"root_id":{"type":"string","minLength":1,"maxLength":64}},"required":["root_id"],"additionalProperties":False},
                "executor":{"type":"builtin","provider":"media_server"}
            }
        ],
    },
    "vp3.music-server": {
        "key": "vp3.music-server",
        "name": "VP3 Music Server",
        "version": "1.0.1",
        "sdk_version": "1.2",
        "release_channel": "stable",
        "min_homeserver_version": "2.4",
        "release_notes": [
            "Adds real local and private-hosted audio playback backed by Media Server stream tickets.",
            "Hardens large-library sync with generation reconciliation and stale-playback cleanup.",
            "Ships on SDK 1.2 / Agent Actions v2 while preserving canonical Media Server ownership."
        ],
        "category": "Media",
        "kind": "music_server",
        "description": "Browse and play your music library from mapped computer folders without copying your source files.",
        "sample": [],
        "permissions": [],
        "routes": {"local": True, "private_remote": True, "public": False},
        "agent_actions": [
            {"key":"music.status","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"builtin","provider":"music_server"}},
            {"key":"music.sync","risk":"background","requires_confirmation":False,"input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"builtin","provider":"music_server"}},
            {"key":"music.search","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{"query":{"type":"string","maxLength":200},"limit":{"type":"integer","minimum":1,"maximum":1000}},"additionalProperties":False},"executor":{"type":"builtin","provider":"music_server"}},
            {"key":"music.artists","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{"limit":{"type":"integer","minimum":1,"maximum":1000}},"additionalProperties":False},"executor":{"type":"builtin","provider":"music_server"}},
            {"key":"music.albums","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{"artist":{"type":"string","maxLength":240},"limit":{"type":"integer","minimum":1,"maximum":1000}},"additionalProperties":False},"executor":{"type":"builtin","provider":"music_server"}},
            {"key":"music.favorites","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"builtin","provider":"music_server"}},
            {"key":"music.favorite.set","risk":"write","requires_confirmation":False,"input_schema":{"type":"object","properties":{"media_id":{"type":"string","minLength":1,"maxLength":100},"enabled":{"type":"boolean"}},"required":["media_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"music_server"}},
            {"key":"music.playlists","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"builtin","provider":"music_server"}},
            {"key":"music.playlist.create","risk":"write","requires_confirmation":False,"input_schema":{"type":"object","properties":{"name":{"type":"string","minLength":1,"maxLength":160}},"required":["name"],"additionalProperties":False},"executor":{"type":"builtin","provider":"music_server"}},
            {"key":"music.playlist.add","risk":"write","requires_confirmation":False,"input_schema":{"type":"object","properties":{"playlist_id":{"type":"string","minLength":1,"maxLength":80},"media_id":{"type":"string","minLength":1,"maxLength":100}},"required":["playlist_id","media_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"music_server"}},
            {"key":"music.playlist.remove","risk":"write","requires_confirmation":False,"input_schema":{"type":"object","properties":{"playlist_id":{"type":"string","minLength":1,"maxLength":80},"media_id":{"type":"string","minLength":1,"maxLength":100}},"required":["playlist_id","media_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"music_server"}},
            {"key":"music.playlist.delete","risk":"destructive","requires_confirmation":True,"input_schema":{"type":"object","properties":{"playlist_id":{"type":"string","minLength":1,"maxLength":80}},"required":["playlist_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"music_server"}},
            {"key":"music.queue","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"builtin","provider":"music_server"}},
            {"key":"music.queue.add","risk":"write","requires_confirmation":False,"input_schema":{"type":"object","properties":{"media_id":{"type":"string","minLength":1,"maxLength":100}},"required":["media_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"music_server"}},
            {"key":"music.queue.clear","risk":"destructive","requires_confirmation":True,"input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"builtin","provider":"music_server"}},
            {"key":"music.playback","risk":"write","requires_confirmation":False,"input_schema":{"type":"object","properties":{"command":{"type":"string","enum":["play","pause","stop"]},"media_id":{"type":"string","maxLength":100},"position_seconds":{"type":"number","minimum":0}},"required":["command"],"additionalProperties":False},"executor":{"type":"builtin","provider":"music_server"}}
        ],
    },
    "vp3.photo-library": {
        "key": "vp3.photo-library",
        "name": "VP3 Photo Library",
        "version": "1.0.0",
        "sdk_version": "1.2",
        "release_channel": "stable",
        "min_homeserver_version": "2.4",
        "release_notes": [
            "Adds local and private-hosted photo browsing sourced from VP3 Media Server.",
            "Adds albums, folder albums, favorites, tags, timeline, smart albums, duplicate candidates and people placeholders.",
            "Ships with full SDK 1.2 / Agent Actions v2 control while preserving canonical Media Server ownership."
        ],
        "category": "Media",
        "kind": "photo_library",
        "description": "Browse, organize, tag, favorite, and present photos from your mapped Media Server folders.",
        "sample": [],
        "permissions": [],
        "routes": {"local": True, "private_remote": True, "public": False},
        "agent_actions": [
            {"key":"photos.status","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"builtin","provider":"photo_library"}},
            {"key":"photos.sync","risk":"background","requires_confirmation":False,"input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"builtin","provider":"photo_library"}},
            {"key":"photos.search","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{"query":{"type":"string","maxLength":200},"folder_album":{"type":"string","maxLength":240},"tag":{"type":"string","maxLength":80},"favorites_only":{"type":"boolean"},"limit":{"type":"integer","minimum":1,"maximum":500}},"additionalProperties":False},"executor":{"type":"builtin","provider":"photo_library"}},
            {"key":"photos.folders","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"builtin","provider":"photo_library"}},
            {"key":"photos.timeline","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{"limit":{"type":"integer","minimum":1,"maximum":2000}},"additionalProperties":False},"executor":{"type":"builtin","provider":"photo_library"}},
            {"key":"photos.favorite.set","risk":"write","requires_confirmation":False,"input_schema":{"type":"object","properties":{"media_id":{"type":"string","minLength":1,"maxLength":100},"enabled":{"type":"boolean"}},"required":["media_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"photo_library"}},
            {"key":"photos.tags","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"builtin","provider":"photo_library"}},
            {"key":"photos.tags.set","risk":"write","requires_confirmation":False,"input_schema":{"type":"object","properties":{"media_id":{"type":"string","minLength":1,"maxLength":100},"tags":{"type":"array"}},"required":["media_id","tags"],"additionalProperties":False},"executor":{"type":"builtin","provider":"photo_library"}},
            {"key":"photos.albums","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"builtin","provider":"photo_library"}},
            {"key":"photos.album.create","risk":"write","requires_confirmation":False,"input_schema":{"type":"object","properties":{"name":{"type":"string","minLength":1,"maxLength":160}},"required":["name"],"additionalProperties":False},"executor":{"type":"builtin","provider":"photo_library"}},
            {"key":"photos.album.add","risk":"write","requires_confirmation":False,"input_schema":{"type":"object","properties":{"album_id":{"type":"string","minLength":1,"maxLength":90},"media_id":{"type":"string","minLength":1,"maxLength":100}},"required":["album_id","media_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"photo_library"}},
            {"key":"photos.album.remove","risk":"write","requires_confirmation":False,"input_schema":{"type":"object","properties":{"album_id":{"type":"string","minLength":1,"maxLength":90},"media_id":{"type":"string","minLength":1,"maxLength":100}},"required":["album_id","media_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"photo_library"}},
            {"key":"photos.album.delete","risk":"destructive","requires_confirmation":True,"input_schema":{"type":"object","properties":{"album_id":{"type":"string","minLength":1,"maxLength":90}},"required":["album_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"photo_library"}},
            {"key":"photos.people","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"builtin","provider":"photo_library"}},
            {"key":"photos.person.create","risk":"write","requires_confirmation":False,"input_schema":{"type":"object","properties":{"name":{"type":"string","minLength":1,"maxLength":160}},"required":["name"],"additionalProperties":False},"executor":{"type":"builtin","provider":"photo_library"}},
            {"key":"photos.person.assign","risk":"write","requires_confirmation":False,"input_schema":{"type":"object","properties":{"person_id":{"type":"string","minLength":1,"maxLength":90},"media_id":{"type":"string","minLength":1,"maxLength":100},"enabled":{"type":"boolean"}},"required":["person_id","media_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"photo_library"}},
            {"key":"photos.duplicates","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{"limit":{"type":"integer","minimum":1,"maximum":500}},"additionalProperties":False},"executor":{"type":"builtin","provider":"photo_library"}},
            {"key":"photos.smart-albums","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"builtin","provider":"photo_library"}},
            {"key":"photos.slideshow","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{"query":{"type":"string","maxLength":200},"folder_album":{"type":"string","maxLength":240},"limit":{"type":"integer","minimum":1,"maximum":500}},"additionalProperties":False},"executor":{"type":"builtin","provider":"photo_library"}}
        ],
    },
    "vp3.download-manager": {
        "key": "vp3.download-manager",
        "name": "VP3 Download Manager",
        "version": "1.1.0",
        "sdk_version": "1.2",
        "release_channel": "stable",
        "min_homeserver_version": "2.4",
        "release_notes": [
            "Adds governed HTTP/HTTPS downloads with pause, resume, cancel, retry, scheduling, priority, checksums and atomic finalization.",
            "Adds owner-approved external destinations, bandwidth/concurrency limits, private hosted access, and Agent Brain context.",
            "Ships with full SDK 1.2 / Agent Actions v2 control while HomeServer remains execution authority."
        ],
        "category": "Files",
        "kind": "download_manager",
        "description": "Queue and manage safe HomeServer downloads into app storage or approved local/mapped folders.",
        "sample": [],
        "permissions": ["network.external","files.write"],
        "routes": {"local": True, "private_remote": True, "public": False},
        "settings_fields": [
            {"key":"authorization_host","type":"string","label":"Authenticated host","description":"Exact hostname allowed to receive the stored Authorization header.","required":False,"secret":False,"default":""},
            {"key":"authorization_header","type":"string","label":"Authorization header","description":"Stored securely and sent only to the configured authenticated host.","required":False,"secret":True}
        ],
        "agent_actions": [
            {"key":"downloads.status","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"builtin","provider":"download_manager"}},
            {"key":"downloads.list","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{"status":{"type":"string","maxLength":40},"limit":{"type":"integer","minimum":1,"maximum":1000}},"additionalProperties":False},"executor":{"type":"builtin","provider":"download_manager"}},
            {"key":"downloads.get","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{"download_id":{"type":"string","minLength":1,"maxLength":80}},"required":["download_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"download_manager"}},
            {"key":"downloads.enqueue","risk":"consequential","requires_confirmation":True,"input_schema":{"type":"object","properties":{"url":{"type":"string","minLength":1,"maxLength":4096},"destination_id":{"type":"string","maxLength":80},"filename":{"type":"string","maxLength":240},"priority":{"type":"integer","minimum":-100,"maximum":100},"checksum_algorithm":{"type":"string","enum":["","sha256","sha512"]},"checksum_expected":{"type":"string","maxLength":128},"max_retries":{"type":"integer","minimum":0,"maximum":10},"scheduled_at":{"type":["integer","null"]}},"required":["url"],"additionalProperties":False},"executor":{"type":"builtin","provider":"download_manager"}},
            {"key":"downloads.pause","risk":"write","requires_confirmation":False,"input_schema":{"type":"object","properties":{"download_id":{"type":"string","minLength":1,"maxLength":80}},"required":["download_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"download_manager"}},
            {"key":"downloads.resume","risk":"write","requires_confirmation":False,"input_schema":{"type":"object","properties":{"download_id":{"type":"string","minLength":1,"maxLength":80}},"required":["download_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"download_manager"}},
            {"key":"downloads.cancel","risk":"destructive","requires_confirmation":True,"input_schema":{"type":"object","properties":{"download_id":{"type":"string","minLength":1,"maxLength":80}},"required":["download_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"download_manager"}},
            {"key":"downloads.retry","risk":"write","requires_confirmation":False,"input_schema":{"type":"object","properties":{"download_id":{"type":"string","minLength":1,"maxLength":80}},"required":["download_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"download_manager"}},
            {"key":"downloads.priority.set","risk":"write","requires_confirmation":False,"input_schema":{"type":"object","properties":{"download_id":{"type":"string","minLength":1,"maxLength":80},"priority":{"type":"integer","minimum":-100,"maximum":100}},"required":["download_id","priority"],"additionalProperties":False},"executor":{"type":"builtin","provider":"download_manager"}},
            {"key":"downloads.history.clear","risk":"destructive","requires_confirmation":True,"input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"builtin","provider":"download_manager"}},
            {"key":"downloads.destinations","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"builtin","provider":"download_manager"}},
            {"key":"downloads.destination.add","risk":"admin","requires_confirmation":True,"input_schema":{"type":"object","properties":{"path":{"type":"string","minLength":1,"maxLength":2000},"label":{"type":"string","maxLength":120},"destination_kind":{"type":"string","enum":["mapped_folder","network_share","local_folder"]}},"required":["path"],"additionalProperties":False},"executor":{"type":"builtin","provider":"download_manager"}},
            {"key":"downloads.destination.remove","risk":"destructive","requires_confirmation":True,"input_schema":{"type":"object","properties":{"destination_id":{"type":"string","minLength":1,"maxLength":80}},"required":["destination_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"download_manager"}},
            {"key":"downloads.settings","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"builtin","provider":"download_manager"}},
            {"key":"downloads.settings.update","risk":"admin","requires_confirmation":True,"input_schema":{"type":"object","properties":{"values":{"type":"object"}},"required":["values"],"additionalProperties":False},"executor":{"type":"builtin","provider":"download_manager"}},
            {"key":"downloads.brain-context","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{"limit":{"type":"integer","minimum":1,"maximum":20}},"additionalProperties":False},"executor":{"type":"builtin","provider":"download_manager"}},
            {"key":"downloads.processor.handoff","risk":"consequential","requires_confirmation":True,"input_schema":{"type":"object","properties":{"download_id":{"type":"string","minLength":1,"maxLength":80},"operation":{"type":"string","enum":["thumbnail","proxy","video.convert","audio.convert","image.convert"]},"preset":{"type":"string","maxLength":80},"output_format":{"type":"string","enum":["","jpg","jpeg","png","webp","mp4","mp3"]},"priority":{"type":"integer","minimum":-100,"maximum":100}},"required":["download_id","operation"],"additionalProperties":False},"executor":{"type":"builtin","provider":"download_manager"}}
        ],
    },
    "vp3.media-processor": {
        "key": "vp3.media-processor",
        "name": "VP3 Media Processor",
        "version": "1.0.0",
        "sdk_version": "1.2",
        "release_channel": "stable",
        "min_homeserver_version": "2.4",
        "release_notes": [
            "Adds HomeServer-managed FFmpeg processing for video, audio, images, thumbnails, and editor proxies.",
            "Adds processor-owned derivative registry, resource limits, restart recovery, and Agent Brain context.",
            "Integrates with Media Server, Download Manager, Video Editor, Hosting, Agent Chat, and Agent Brain."
        ],
        "category": "Media",
        "kind": "media_processor",
        "description": "Convert, compress, proxy, thumbnail, and derive media locally on your HomeServer.",
        "sample": [],
        "permissions": ["files.write"],
        "routes": {"local": True, "private_remote": True, "public": False},
        "agent_actions": [
            {"key":"processor.status","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"builtin","provider":"media_processor"}},
            {"key":"processor.jobs","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{"limit":{"type":"integer","minimum":1,"maximum":500}},"additionalProperties":False},"executor":{"type":"builtin","provider":"media_processor"}},
            {"key":"processor.job.get","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{"job_id":{"type":"string","minLength":1,"maxLength":80}},"required":["job_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"media_processor"}},
            {"key":"processor.enqueue","risk":"consequential","requires_confirmation":True,"input_schema":{"type":"object","properties":{"media_id":{"type":"string","minLength":1,"maxLength":100},"operation":{"type":"string","enum":["thumbnail","proxy","video.convert","audio.convert","image.convert"]},"preset":{"type":"string","maxLength":80},"output_format":{"type":"string","enum":["","jpg","jpeg","png","webp","mp4","mp3"]},"priority":{"type":"integer","minimum":-100,"maximum":100},"destination_id":{"type":"string","maxLength":80}},"required":["media_id","operation"],"additionalProperties":False},"executor":{"type":"builtin","provider":"media_processor"}},
            {"key":"processor.cancel","risk":"destructive","requires_confirmation":True,"input_schema":{"type":"object","properties":{"job_id":{"type":"string","minLength":1,"maxLength":80}},"required":["job_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"media_processor"}},
            {"key":"processor.retry","risk":"write","requires_confirmation":False,"input_schema":{"type":"object","properties":{"job_id":{"type":"string","minLength":1,"maxLength":80}},"required":["job_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"media_processor"}},
            {"key":"processor.derivatives","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{"media_id":{"type":"string","maxLength":100},"limit":{"type":"integer","minimum":1,"maximum":500}},"additionalProperties":False},"executor":{"type":"builtin","provider":"media_processor"}},
            {"key":"processor.brain-context","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{"limit":{"type":"integer","minimum":1,"maximum":20}},"additionalProperties":False},"executor":{"type":"builtin","provider":"media_processor"}},
            {"key":"processor.destinations","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"builtin","provider":"media_processor"}},
            {"key":"processor.destination.add","risk":"admin","requires_confirmation":True,"input_schema":{"type":"object","properties":{"path":{"type":"string","minLength":1,"maxLength":2000},"label":{"type":"string","maxLength":120},"destination_kind":{"type":"string","enum":["mapped_folder","network_share","local_folder"]}},"required":["path"],"additionalProperties":False},"executor":{"type":"builtin","provider":"media_processor"}},
            {"key":"processor.destination.remove","risk":"destructive","requires_confirmation":True,"input_schema":{"type":"object","properties":{"destination_id":{"type":"string","minLength":1,"maxLength":80}},"required":["destination_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"media_processor"}},
            {"key":"processor.settings","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"builtin","provider":"media_processor"}},
            {"key":"processor.settings.update","risk":"admin","requires_confirmation":True,"input_schema":{"type":"object","properties":{"values":{"type":"object"}},"required":["values"],"additionalProperties":False},"executor":{"type":"builtin","provider":"media_processor"}}
        ],
    },
    "vp3.media-library": {
        "key": "vp3.media-library",
        "name": "VP3 Media Library",
        "version": "1.3.0",
        "sdk_version": "1.2",
        "release_channel": "stable",
        "min_homeserver_version": "2.4",
        "release_notes": [
            "Adds Media Processor-backed thumbnails, posters, album art, covers, and multi-source collection contact sheets.",
            "Media Library owns semantic artwork assignments only; derivatives remain owned by Media Processor and originals remain untouched.",
            "Retains metadata, collections, cleanup review, SDK 1.2 / Agent Actions v2, and Agent Brain awareness."
        ],
        "category": "Media",
        "kind": "media_library",
        "description": "Organize and enrich Media Server items with shared metadata, history, and undo.",
        "sample": [],
        "permissions": [],
        "routes": {"local": True, "private_remote": True, "public": False},
        "agent_actions": [
            {"key":"library.status","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"builtin","provider":"media_library"}},
            {"key":"library.item.get","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{"media_id":{"type":"string","minLength":1,"maxLength":100}},"required":["media_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"media_library"}},
            {"key":"library.item.update","risk":"write","requires_confirmation":False,"input_schema":{"type":"object","properties":{"media_id":{"type":"string","minLength":1,"maxLength":100},"patch":{"type":"object"},"actor":{"type":"string","maxLength":120},"source":{"type":"string","maxLength":120},"reason":{"type":"string","maxLength":500}},"required":["media_id","patch"],"additionalProperties":False},"executor":{"type":"builtin","provider":"media_library"}},
            {"key":"library.history","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{"media_id":{"type":"string","minLength":1,"maxLength":100},"limit":{"type":"integer","minimum":1,"maximum":500}},"required":["media_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"media_library"}},
            {"key":"library.undo","risk":"write","requires_confirmation":True,"input_schema":{"type":"object","properties":{"history_id":{"type":"string","minLength":1,"maxLength":100},"actor":{"type":"string","maxLength":120}},"required":["history_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"media_library"}},
            {"key":"library.search","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{"query":{"type":"string","maxLength":200},"media_type":{"type":"string","enum":["","video","audio","image"]},"tag":{"type":"string","maxLength":80},"favorite":{"type":["boolean","null"]},"limit":{"type":"integer","minimum":1,"maximum":500}},"additionalProperties":False},"executor":{"type":"builtin","provider":"media_library"}},
            {"key":"library.collections","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{"limit":{"type":"integer","minimum":1,"maximum":500}},"additionalProperties":False},"executor":{"type":"builtin","provider":"media_library"}},
            {"key":"library.collection.get","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{"collection_id":{"type":"string","minLength":1,"maxLength":100},"limit":{"type":"integer","minimum":1,"maximum":500}},"required":["collection_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"media_library"}},
            {"key":"library.collection.create","risk":"write","requires_confirmation":False,"input_schema":{"type":"object","properties":{"name":{"type":"string","minLength":1,"maxLength":160},"description":{"type":"string","maxLength":2000},"collection_type":{"type":"string","enum":["manual","smart"]},"rules":{"type":"object"}},"required":["name"],"additionalProperties":False},"executor":{"type":"builtin","provider":"media_library"}},
            {"key":"library.collection.add","risk":"write","requires_confirmation":False,"input_schema":{"type":"object","properties":{"collection_id":{"type":"string","minLength":1,"maxLength":100},"media_id":{"type":"string","minLength":1,"maxLength":100},"position":{"type":"integer","minimum":-1000000,"maximum":1000000}},"required":["collection_id","media_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"media_library"}},
            {"key":"library.collection.remove","risk":"write","requires_confirmation":False,"input_schema":{"type":"object","properties":{"collection_id":{"type":"string","minLength":1,"maxLength":100},"media_id":{"type":"string","minLength":1,"maxLength":100}},"required":["collection_id","media_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"media_library"}},
            {"key":"library.collection.delete","risk":"destructive","requires_confirmation":True,"input_schema":{"type":"object","properties":{"collection_id":{"type":"string","minLength":1,"maxLength":100}},"required":["collection_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"media_library"}},
            {"key":"library.smart-collections","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"builtin","provider":"media_library"}},
            {"key":"library.duplicates.scan","risk":"background","requires_confirmation":False,"input_schema":{"type":"object","properties":{"limit":{"type":"integer","minimum":1,"maximum":500}},"additionalProperties":False},"executor":{"type":"builtin","provider":"media_library"}},
            {"key":"library.duplicates","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{"kind":{"type":"string","enum":["","exact","same_size_candidate","near_name_candidate"]},"limit":{"type":"integer","minimum":1,"maximum":500}},"additionalProperties":False},"executor":{"type":"builtin","provider":"media_library"}},
            {"key":"library.duplicate.review","risk":"write","requires_confirmation":False,"input_schema":{"type":"object","properties":{"group_key":{"type":"string","minLength":1,"maxLength":200},"decision":{"type":"string","enum":["needs_review","keep_both","ignore","resolved"]},"primary_media_id":{"type":"string","maxLength":100},"note":{"type":"string","maxLength":1000}},"required":["group_key","decision"],"additionalProperties":False},"executor":{"type":"builtin","provider":"media_library"}},
            {"key":"library.cleanup.status","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{},"additionalProperties":False},"executor":{"type":"builtin","provider":"media_library"}},
            {"key":"library.artwork.generate","risk":"consequential","requires_confirmation":True,"input_schema":{"type":"object","properties":{"target_type":{"type":"string","enum":["media","collection"]},"target_id":{"type":"string","minLength":1,"maxLength":100},"role":{"type":"string","enum":["thumbnail","poster","album_art","cover","contact_sheet"]},"source_media_ids":{"type":"array","items":{"type":"string","minLength":1,"maxLength":100},"maxItems":16},"preset":{"type":"string","maxLength":40}},"required":["target_type","target_id","role"],"additionalProperties":False},"executor":{"type":"builtin","provider":"media_library"}},
            {"key":"library.artwork","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{"target_type":{"type":"string","enum":["media","collection"]},"target_id":{"type":"string","minLength":1,"maxLength":100}},"required":["target_type","target_id"],"additionalProperties":False},"executor":{"type":"builtin","provider":"media_library"}},
            {"key":"library.artwork.remove","risk":"destructive","requires_confirmation":True,"input_schema":{"type":"object","properties":{"target_type":{"type":"string","enum":["media","collection"]},"target_id":{"type":"string","minLength":1,"maxLength":100},"role":{"type":"string","enum":["thumbnail","poster","album_art","cover","contact_sheet"]}},"required":["target_type","target_id","role"],"additionalProperties":False},"executor":{"type":"builtin","provider":"media_library"}},
            {"key":"library.brain-context","risk":"read","requires_confirmation":False,"input_schema":{"type":"object","properties":{"limit":{"type":"integer","minimum":1,"maximum":20}},"additionalProperties":False},"executor":{"type":"builtin","provider":"media_library"}}
        ],
    },
    "vp3.video-editor": {
        "key": "vp3.video-editor",
        "name": "VP3 Video Editor",
        "version": "1.1.0",
        "sdk_version": "1.2",
        "release_channel": "stable",
        "min_homeserver_version": "2.4",
        "release_notes": [
            "Adds governed Media Processor proxy generation for timeline clips.",
            "Retains local non-destructive multi-track editing backed by Media Server source IDs.",
            "Ships on SDK 1.2 / Agent Actions v2.",
        ],
        "category": "Media",
        "kind": "video_editor",
        "description": "Create local video projects, edit multi-track timelines, and render on your HomeServer.",
        "sample": [],
        "permissions": [],
        "routes": {"local": True, "private_remote": True, "public": False},
        "agent_actions": [
            {
                "key":"video.projects.list","risk":"read","requires_confirmation":False,
                "input_schema":{"type":"object","properties":{"limit":{"type":"integer","minimum":1,"maximum":200}},"additionalProperties":False},
                "executor":{"type":"builtin","provider":"video_editor"}
            },
            {
                "key":"video.project.get","risk":"read","requires_confirmation":False,
                "input_schema":{"type":"object","properties":{"project_id":{"type":"string","minLength":1,"maxLength":80}},"required":["project_id"],"additionalProperties":False},
                "executor":{"type":"builtin","provider":"video_editor"}
            },
            {
                "key":"video.project.create","risk":"write","requires_confirmation":False,
                "input_schema":{"type":"object","properties":{
                    "name":{"type":"string","minLength":1,"maxLength":160},
                    "width":{"type":"integer","minimum":320,"maximum":7680},
                    "height":{"type":"integer","minimum":240,"maximum":4320},
                    "fps":{"type":"number","minimum":1,"maximum":240}
                },"required":["name"],"additionalProperties":False},
                "executor":{"type":"builtin","provider":"video_editor"}
            },
            {
                "key":"video.track.add","risk":"write","requires_confirmation":False,
                "input_schema":{"type":"object","properties":{
                    "project_id":{"type":"string","minLength":1,"maxLength":80},
                    "kind":{"type":"string","enum":["video","audio"]},
                    "name":{"type":"string","maxLength":120}
                },"required":["project_id","kind"],"additionalProperties":False},
                "executor":{"type":"builtin","provider":"video_editor"}
            },
            {
                "key":"video.clip.add","risk":"write","requires_confirmation":False,
                "input_schema":{"type":"object","properties":{
                    "project_id":{"type":"string","minLength":1,"maxLength":80},
                    "track_id":{"type":"string","minLength":1,"maxLength":80},
                    "media_id":{"type":"string","minLength":1,"maxLength":100},
                    "start_seconds":{"type":"number","minimum":0}
                },"required":["project_id","track_id","media_id"],"additionalProperties":False},
                "executor":{"type":"builtin","provider":"video_editor"}
            },
            {
                "key":"video.clip.update","risk":"write","requires_confirmation":False,
                "input_schema":{"type":"object","properties":{
                    "project_id":{"type":"string","minLength":1,"maxLength":80},
                    "clip_id":{"type":"string","minLength":1,"maxLength":80},
                    "start_seconds":{"type":"number","minimum":0},
                    "in_seconds":{"type":"number","minimum":0},
                    "out_seconds":{"type":"number","minimum":0},
                    "volume":{"type":"number","minimum":0,"maximum":4}
                },"required":["project_id","clip_id"],"additionalProperties":False},
                "executor":{"type":"builtin","provider":"video_editor"}
            },
            {
                "key":"video.clip.remove","risk":"destructive","requires_confirmation":True,
                "input_schema":{"type":"object","properties":{
                    "project_id":{"type":"string","minLength":1,"maxLength":80},
                    "clip_id":{"type":"string","minLength":1,"maxLength":80}
                },"required":["project_id","clip_id"],"additionalProperties":False},
                "executor":{"type":"builtin","provider":"video_editor"}
            },
            {
                "key":"video.render.queue","risk":"consequential","requires_confirmation":True,
                "input_schema":{"type":"object","properties":{
                    "project_id":{"type":"string","minLength":1,"maxLength":80},
                    "preset":{"type":"string","enum":["720p","1080p","4k","source"]},
                    "format":{"type":"string","enum":["mp4","webm"]}
                },"required":["project_id"],"additionalProperties":False},
                "executor":{"type":"builtin","provider":"video_editor"}
            },
            {
                "key":"video.clip.proxy","risk":"consequential","requires_confirmation":True,
                "input_schema":{"type":"object","properties":{
                    "project_id":{"type":"string","minLength":1,"maxLength":80},
                    "clip_id":{"type":"string","minLength":1,"maxLength":80}
                },"required":["project_id","clip_id"],"additionalProperties":False},
                "executor":{"type":"builtin","provider":"video_editor"}
            },
        ],
    },
}


def _manifest(definition: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract": "vp3.app.package.v1",
        "app_key": definition["key"],
        "name": definition["name"],
        "version": definition["version"],
        "runtime": "static",
        "entrypoint": "index.html",
        "sdk_version": str(definition.get("sdk_version") or "1.0"),
        "release_channel": definition.get("release_channel", "stable"),
        "min_homeserver_version": definition.get("min_homeserver_version", ""),
        "max_homeserver_version": definition.get("max_homeserver_version", ""),
        "release_notes": list(definition.get("release_notes") or []),
        "data_schema_version": str(definition.get("data_schema_version") or "1"),
        "data_migration_reversible": bool(definition.get("data_migration_reversible", True)),
        "permissions": list(definition.get("permissions") or []),
        "settings_schema": "settings.schema.json",
        "database_migrations": "database/migrations",
        "agent_actions": "agent/actions.json",
        "jobs": "runtime/jobs.json",
        "events": "runtime/events.json",
        "sample_data": "sample-data.json",
        "routes": dict(definition.get("routes") or {"local": True, "private_remote": False, "public": False}),
    }


def _html(definition: dict[str, Any]) -> str:
    if definition["kind"] == "media_library":
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>VP3 Media Library</title><link rel="stylesheet" href="assets/app.css"></head><body><main class="shell">'
            '<header class="top"><div><span class="eyebrow">VP3 APP · MEDIA LIBRARY</span><h1>Media Library</h1><p class="muted">Shared metadata for Media Server items.</p></div><button class="button secondary" id="refresh">Refresh</button></header>'
            '<section class="panel"><div class="toolbar"><input id="q" placeholder="Search titles, descriptions, tags, people"><select id="type"><option value="">All media</option><option value="video">Video</option><option value="audio">Audio</option><option value="image">Images</option></select><button class="button" id="search">Search</button></div><div id="stats" class="muted"></div><div id="items" class="list"></div></section>'
            '<section class="panel" id="editor" hidden><strong id="editorTitle">Metadata</strong><div class="toolbar"><input id="metaTitle" placeholder="Title"><input id="tags" placeholder="tags, comma separated"><input id="rating" type="number" min="0" max="5" placeholder="Rating 0-5"><button class="button" id="save">Save</button><button class="button secondary" id="undo">Undo last change</button></div><textarea id="description" style="width:100%;min-height:120px" placeholder="Description"></textarea></section>'
            '<section class="panel"><div class="row"><strong>Collections</strong><div><button class="button secondary" id="newCollection">New Collection</button> <button class="button secondary" id="newSmart">New Smart Collection</button></div></div><div id="collections" class="list"></div></section>'
            '<section class="panel"><div class="row"><strong>Duplicate & Cleanup Center</strong><button class="button secondary" id="scanDuplicates">Scan</button></div><div id="cleanup" class="muted"></div><div id="duplicates" class="list"></div></section>'
            '<script src="assets/app.js"></script></main></body></html>'
        )
    if definition["kind"] == "media_processor":
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>VP3 Media Processor</title><link rel="stylesheet" href="assets/app.css"></head><body><main class="shell">'
            '<header class="top"><div><span class="eyebrow">VP3 APP · MEDIA PROCESSOR</span><h1>Media Processor</h1><p class="muted">HomeServer-managed FFmpeg processing.</p></div><button class="button secondary" id="refresh">Refresh</button></header>'
            '<section class="panel"><div id="stats" class="muted"></div><div id="tools" class="muted"></div></section>'
            '<section class="panel"><div class="toolbar"><input id="mediaId" placeholder="Media Server media ID"><select id="operation"><option value="thumbnail">Thumbnail</option><option value="proxy">Editing Proxy</option><option value="video.convert">Video Convert</option><option value="audio.convert">Audio Convert</option><option value="image.convert">Image Convert</option></select><button class="button" id="enqueue">Process</button></div></section>'
            '<section class="panel"><strong>Processing Queue</strong><div id="jobs" class="list"></div></section>'
            '<section class="panel"><strong>Recent Derivatives</strong><div id="derivatives" class="list"></div></section>'
            '<script src="assets/app.js"></script></main></body></html>'
        )
    if definition["kind"] == "download_manager":
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>VP3 Download Manager</title><link rel="stylesheet" href="assets/app.css"></head><body><main class="shell">'
            '<header class="top"><div><span class="eyebrow">VP3 APP · DOWNLOAD MANAGER</span><h1>Download Manager</h1><p class="muted">HomeServer-authoritative HTTP/HTTPS download queue.</p></div><button class="button secondary" id="remote">Remote Access</button></header>'
            '<section class="panel"><div class="toolbar"><input id="url" placeholder="https://example.com/file.zip"><button class="button" id="add">Add Download</button></div><div id="stats" class="muted"></div></section>'
            '<section class="panel"><div class="row"><strong>Queue</strong><button class="button secondary" id="refresh">Refresh</button></div><div id="downloads" class="list"></div></section>'
            '<section class="panel"><strong>Destinations</strong><div id="destinations" class="list"></div></section>'
            '<script src="assets/app.js"></script></main></body></html>'
        )
    if definition["kind"] == "photo_library":
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>VP3 Photo Library</title><link rel="stylesheet" href="assets/app.css"></head><body><main class="shell">'
            '<header class="top"><div><span class="eyebrow">VP3 APP · PHOTO LIBRARY</span><h1>Photo Library</h1><p class="muted">Photos from your mapped Media Server folders.</p></div><button class="button" id="sync">Sync Library</button></header>'
            '<section class="panel"><div class="toolbar"><input id="q" placeholder="Search photos or folders"><button class="button secondary" id="search">Search</button><button class="button secondary" id="slideshow">Slideshow</button></div><div id="stats" class="muted"></div><div id="photos" class="photo-grid"></div></section>'
            '<section class="panel"><div class="row"><strong>Albums</strong><button class="button secondary" id="newAlbum">+</button></div><div id="albums" class="list"></div></section>'
            '<section class="panel"><strong>Smart Albums</strong><div id="smart" class="list"></div></section>'
            '<dialog id="viewer"><button id="closeViewer" class="button secondary">Close</button><img id="viewerImage" alt=""><p id="viewerTitle"></p></dialog>'
            '<script src="assets/app.js"></script></main></body></html>'
        )
    if definition["kind"] == "music_server":
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>VP3 Music Server</title><link rel="stylesheet" href="assets/app.css"></head><body><main class="shell">'
            '<header class="top"><div><span class="eyebrow">VP3 APP · MUSIC SERVER</span><h1>Music Server</h1><p class="muted">Music from your mapped Media Server folders.</p></div><button class="button" id="sync">Sync Library</button></header>'
            '<section class="panel"><div class="toolbar"><input id="q" placeholder="Search artists, albums, tracks"><button class="button secondary" id="search">Search</button></div><div id="stats" class="muted"></div><div id="tracks" class="list"></div></section>'
            '<section class="panel"><div class="row"><strong>Playlists</strong><button class="button secondary" id="newPlaylist">+</button></div><div id="playlists" class="list"></div></section>'
            '<section class="panel"><strong>Now Playing</strong><audio id="player" controls preload="metadata" style="width:100%"></audio></section><section class="panel"><strong>Queue</strong><div id="queue" class="list"></div></section>'
            '<script src="assets/app.js"></script></main></body></html>'
        )
    if definition["kind"] == "video_editor":
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>VP3 Video Editor</title><link rel="stylesheet" href="assets/app.css"></head><body><main class="shell">'
            '<header class="top"><div><span class="eyebrow">VP3 APP · VIDEO EDITOR</span><h1 id="projectTitle">Video Editor</h1></div><div class="row"><span class="status">HomeServer Render</span><button class="button secondary" id="render">Render</button></div></header>'
            '<section class="workspace"><aside class="panel"><div class="row"><strong>Projects</strong><button class="button secondary" id="newProject">+</button></div><div id="projects" class="list"></div><hr><strong>Media Server</strong><div id="media" class="list"></div></aside>'
            '<section class="viewer"><div class="stage"><div class="canvas">Preview Monitor</div></div><div class="transport"><button class="button secondary">◀</button><button class="button">▶</button><button class="button secondary">▶|</button></div></section>'
            '<aside class="panel right"><strong>Inspector</strong><p class="muted">Select clips to adjust timing, trims, volume, and effects.</p><hr><strong>Render Queue</strong><div id="renders" class="list"></div></aside>'
            '<section class="timeline"><strong>Timeline</strong><div id="timeline"></div></section></section><script src="assets/app.js"></script></main></body></html>'
        )
    if definition["kind"] == "media_server":
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>VP3 Media Server</title><link rel="stylesheet" href="assets/app.css"></head><body><main class="shell">'
            '<header class="top"><div><span class="eyebrow">VP3 APP · MEDIA SERVER</span><h1>Media Server</h1><p class="muted">Your media, indexed and streamed directly from this HomeServer.</p></div><span class="pill">Direct Play</span></header>'
            '<div class="stats"><div class="stat"><span>Total</span><strong id="count">—</strong></div><div class="stat"><span>Video</span><strong id="videos">—</strong></div><div class="stat"><span>Audio</span><strong id="audio">—</strong></div><div class="stat"><span>Images</span><strong id="images">—</strong></div></div>'
            '<div class="toolbar"><input id="q" placeholder="Search library"><select id="type"><option value="">All media</option><option value="video">Video</option><option value="audio">Audio</option><option value="image">Images</option></select></div><section id="grid" class="grid"></section>'
            '<section class="panel manage" id="manage" hidden><h2>Library folders</h2><p class="muted">Map a folder from this HomeServer or a connected computer/share. Source files stay where they are and are never copied or deleted.</p><div class="manage-grid"><input id="rootPath" placeholder="Mounted folder path"><input id="computerName" placeholder="Computer name (optional)"><input id="sourceHint" placeholder="Original folder, e.g. D:\\Music or \\\\PC\\Music"><button class="button" id="addRoot" type="button">Map & Scan</button><button class="button secondary" id="scan" type="button">Rescan</button></div><div id="roots" class="roots"></div><hr><button class="button secondary" id="remote" type="button">Generate Remote Access Key</button></section>'
            '<div class="player hidden" id="player"><div class="player-box"><div class="player-head"><strong id="playerTitle"></strong><button class="button secondary" id="closePlayer" type="button">Close</button></div><div id="playerContent"></div></div></div>'
            '<script src="assets/app.js"></script></main></body></html>'
        )
    second = ""
    if definition["kind"] == "notes":
        second = '<input id="field2" maxlength="2000" placeholder="Write a note…" required>'
    elif definition["kind"] == "inventory":
        second = '<input id="field2" type="number" min="0" max="999999" value="1" required>'
    placeholder = {"notes": "Note title", "inventory": "Item", "checklist": "Checklist item"}[definition["kind"]]
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{definition["name"]}</title><link rel="stylesheet" href="assets/app.css"></head><body><main class="shell">'
        f'<header class="top"><div><span class="eyebrow">VP3 PREBUILT APP</span><h1>{definition["name"]}</h1>'
        f'<p class="muted">{definition["description"]}</p></div><span class="pill">HomeServer</span></header>'
        f'<section class="panel"><form id="form" class="toolbar"><input id="field1" maxlength="180" placeholder="{placeholder}" required>{second}'
        '<button class="button" type="submit">Add</button></form><div id="list" class="list"></div></section></main>'
        f'<script>window.VP3_PREBUILT={json.dumps({"key":definition["key"],"kind":definition["kind"]},separators=(",",":"))};</script>'
        '<script src="assets/app.js"></script></body></html>'
    )


def _package(definition: dict[str, Any]) -> bytes:
    files = {
        "vp3-app.json": json.dumps(_manifest(definition), indent=2, sort_keys=True) + "\n",
        "index.html": _html(definition),
        "assets/app.css": (APP_CSS + ".photo-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px;margin-top:14px}.photo-card{display:block;text-align:left;border:1px solid #2b3139;border-radius:10px;background:#171b20;color:#f4f6f8;padding:8px;cursor:pointer}.photo-thumb{aspect-ratio:1/1;background:#090b0e center/cover no-repeat;border-radius:7px;margin-bottom:8px}dialog{max-width:min(92vw,1100px);background:#11151a;color:#fff;border:1px solid #303640;border-radius:12px;padding:14px}dialog::backdrop{background:rgba(0,0,0,.8)}#viewerImage{display:block;max-width:86vw;max-height:78vh;margin:12px auto;object-fit:contain}") if definition["kind"]=="photo_library" else (VIDEO_EDITOR_CSS if definition["kind"]=="video_editor" else (MEDIA_SERVER_CSS if definition["kind"]=="media_server" else APP_CSS)),
        "assets/app.js": MEDIA_LIBRARY_JS if definition["kind"]=="media_library" else (MEDIA_PROCESSOR_JS if definition["kind"]=="media_processor" else (DOWNLOAD_MANAGER_JS if definition["kind"]=="download_manager" else (PHOTO_LIBRARY_JS if definition["kind"]=="photo_library" else (MUSIC_SERVER_JS if definition["kind"]=="music_server" else (VIDEO_EDITOR_JS if definition["kind"]=="video_editor" else (MEDIA_SERVER_JS if definition["kind"]=="media_server" else COMMON_JS)))))),
        "settings.schema.json": json.dumps({"contract": "vp3.app.settings-schema.v1", "fields": list(definition.get("settings_fields") or [])}, indent=2) + "\n",
        "agent/actions.json": json.dumps({
            "contract": "vp3.app.agent-actions.v2" if str(definition.get("sdk_version") or "1.0")=="1.2" else "vp3.app.agent-actions.v1",
            "actions": list(definition.get("agent_actions") or [])
        }, indent=2) + "\n",
        "runtime/jobs.json": json.dumps({"contract": "vp3.app.jobs.v1", "jobs": []}, indent=2) + "\n",
        "runtime/events.json": json.dumps({"contract": "vp3.app.events.v1", "subscriptions": []}, indent=2) + "\n",
        "sample-data.json": json.dumps({"contract": "vp3.app.sample-data.v1", "items": definition["sample"]}, indent=2) + "\n",
    }
    if str(definition.get("data_schema_version") or "1") == "2":
        files["database/migrations/002_section7.sql"] = "CREATE TABLE IF NOT EXISTS vp3_section7_migration_marker (id INTEGER PRIMARY KEY, applied_at TEXT);\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, files[name].encode("utf-8"))
    return buffer.getvalue()


def _public(definition: dict[str, Any]) -> dict[str, Any]:
    package = _package(definition)
    digest = hashlib.sha256(package).hexdigest()
    try:
        app = homeserver_apps.get(definition["key"])
    except homeserver_apps.HomeServerAppError:
        app = None
    installed = bool(app and app["app_class"] == "system" and app.get("installed_version"))
    current = bool(
        installed
        and app["installed_version"] == definition["version"]
        and (app.get("metadata") or {}).get("package_sha256") == digest
        and app["lifecycle_state"] == "running"
    )
    return {
        "key": definition["key"],
        "name": definition["name"],
        "version": definition["version"],
        "category": definition["category"],
        "description": definition["description"],
        "release_channel": definition.get("release_channel", "stable"),
        "release_notes": list(definition.get("release_notes") or []),
        "data_migration": {
            "target_schema_version": str(definition.get("data_schema_version") or "1"),
            "reversible": bool(definition.get("data_migration_reversible", True)),
        },
        "compatibility": {
            "min_homeserver_version": definition.get("min_homeserver_version") or None,
            "max_homeserver_version": definition.get("max_homeserver_version") or None,
        },
        "integrity": {"algorithm": "sha256", "package_sha256": digest, "trust": "embedded_vp3"},
        "package_sha256": digest,
        "package_bytes": len(package),
        "installed": installed,
        "current": current,
        "update_available": bool(installed and not current),
        "state": app["lifecycle_state"] if app else "available",
        "product_type": "vp3_optional_app",
        "deployment_modes": ["local","private_remote","hosted_subdomain","custom_domain"],
        "core_homeserver_feature": False,
    }


def catalog() -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "catalog_version": CATALOG_VERSION,
        "source": "embedded_vp3",
        "app_store": False,
        "packages": [_public(CATALOG[key]) for key in sorted(CATALOG)],
    }


def install(
    catalog_key: str,
    *,
    expected_version: str | None = None,
    expected_sha256: str | None = None,
    release_channel: str | None = None,
) -> dict[str, Any]:
    key = str(catalog_key or "").strip().lower()
    definition = CATALOG.get(key)
    if definition is None:
        raise homeserver_apps.HomeServerAppError("VP3 prebuilt app not found.", 404)
    package = _package(definition)
    digest = hashlib.sha256(package).hexdigest()
    if expected_version and str(expected_version) != str(definition["version"]):
        raise homeserver_apps.HomeServerAppError("Requested System App version is no longer current.", 409)
    if expected_sha256 and str(expected_sha256).lower() != digest.lower():
        raise homeserver_apps.HomeServerAppError("Requested System App package hash does not match the HomeServer catalog.", 409)
    if release_channel and str(release_channel).strip().lower() != str(definition.get("release_channel") or "stable"):
        raise homeserver_apps.HomeServerAppError("Requested System App release channel does not match the HomeServer catalog.", 409)
    app = homeserver_apps.ensure_system_app(
        key,
        definition["name"],
        source_ref=f"vp3-prebuilt:{CATALOG_VERSION}",
        metadata={
            "prebuilt_catalog_key": key,
            "prebuilt_catalog_version": CATALOG_VERSION,
            "prebuilt_category": definition["category"],
            "prebuilt_description": definition["description"],
        },
    )
    if (
        app.get("installed_version") == definition["version"]
        and (app.get("metadata") or {}).get("package_sha256") == digest
        and app["lifecycle_state"] == "running"
    ):
        return {"changed": False, "reason": "already_current", "package": _public(definition), "app": app}
    prior_status = homeserver_app_packages.runtime_status(key)
    release = homeserver_app_packages.install_system_package(key, package)
    try:
        verification = homeserver_app_packages.verify_active_release(
            key,
            expected_release_id=str(release.get("release_id") or ""),
            expected_version=str(definition["version"]),
            expected_sha256=digest,
        )
    except Exception as exc:
        previous_release_id = str(prior_status.get("active_release_id") or "")
        if previous_release_id:
            rollback = homeserver_app_releases.promote(
                key,
                previous_release_id,
                reason="post_update_verification_failed",
                system_managed=True,
            )
            return {
                "changed": False,
                "rolled_back": True,
                "reason": "verification_failed_rolled_back",
                "error": str(exc)[:500],
                "rollback": rollback,
                "package": _public(definition),
                "app": homeserver_apps.get(key),
            }
        raise
    with db() as connection:
        row = connection.execute("SELECT app_id,metadata_json FROM homeserver_apps WHERE app_key=?", (key,)).fetchone()
        metadata = json.loads(row["metadata_json"] or "{}")
        metadata.update(
            {
                "prebuilt_app": True,
                "vp3_managed": True,
                "prebuilt_catalog_key": key,
                "prebuilt_catalog_version": CATALOG_VERSION,
                "prebuilt_category": definition["category"],
                "prebuilt_description": definition["description"],
            }
        )
        connection.execute(
            "UPDATE homeserver_apps SET metadata_json=?,updated_at=CURRENT_TIMESTAMP WHERE app_key=?",
            (json.dumps(metadata, separators=(",", ":"), sort_keys=True), key),
        )
        connection.execute(
            """INSERT INTO homeserver_app_events(app_id,event_type,actor_type,actor_key,metadata_json)
               VALUES (?, 'app.prebuilt.installed','system','vp3_prebuilt',?)""",
            (
                row["app_id"],
                json.dumps(
                    {
                        "catalog_version": CATALOG_VERSION,
                        "version": definition["version"],
                        "package_sha256": digest,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ),
        )
    return {
        "changed": True,
        "release": release,
        "verification": verification,
        "package": _public(definition),
        "app": homeserver_apps.get(key),
    }





def release_status(catalog_key: str) -> dict[str, Any]:
    key = str(catalog_key or "").strip().lower()
    definition = CATALOG.get(key)
    if definition is None:
        raise homeserver_apps.HomeServerAppError("VP3 prebuilt app not found.", 404)
    package = _public(definition)
    try:
        runtime = homeserver_app_packages.runtime_status(key)
    except homeserver_apps.HomeServerAppError:
        runtime = {
            "contract": homeserver_app_packages.RUNTIME_CONTRACT,
            "app_key": key,
            "lifecycle_state": "available",
            "installed_version": None,
            "active_release_id": None,
            "previous_release_id": None,
            "active_release": None,
        }
    return {
        "contract": "vp3.system-app-release-status.v1",
        "catalog_version": CATALOG_VERSION,
        "app_key": key,
        "release_channel": definition.get("release_channel", "stable"),
        "available_version": definition["version"],
        "release_notes": list(definition.get("release_notes") or []),
        "compatibility": package["compatibility"],
        "package_sha256": package["package_sha256"],
        "integrity": package["integrity"],
        "runtime": runtime,
        "data": homeserver_app_data_lifecycle.status(key) if runtime.get("active_release_id") else {"contract":"vp3.app.data-lifecycle.v1","app_key":key,"schema_version":"1"},
        "rollback_available": bool(runtime.get("previous_release_id")),
        "rollback_safe": bool(runtime.get("previous_release_id")) and bool(((runtime.get("active_release") or {}).get("data_migration") or {}).get("rollback_safe", True)),
    }


def rollback(catalog_key: str, *, expected_active_release_id: str | None = None, reason: str = "owner_requested") -> dict[str, Any]:
    key = str(catalog_key or "").strip().lower()
    if key not in CATALOG:
        raise homeserver_apps.HomeServerAppError("VP3 prebuilt app not found.", 404)
    releases = homeserver_app_releases.list_releases(key)
    active = str(releases.get("active_release_id") or "")
    previous = str(releases.get("previous_release_id") or "")
    if expected_active_release_id and active != expected_active_release_id:
        raise homeserver_apps.HomeServerAppError("Active System App release changed before rollback.",409)
    if not previous:
        raise homeserver_apps.HomeServerAppError("No previous System App release is available for rollback.",409)
    active_release = next((row for row in releases.get("releases", []) if row.get("release_id") == active), None) or {}
    migration = dict(active_release.get("data_migration") or {})
    if migration.get("migration_required") and not migration.get("migration_reversible"):
        raise homeserver_apps.HomeServerAppError(
            "Rollback is blocked because the active release contains an irreversible app data migration.",409
        )
    result = homeserver_app_releases.promote(
        key,
        previous,
        reason=reason,
        system_managed=True,
    )
    data_restore = {"restored": False, "reason": "no_data_migration"}
    try:
        data_restore = homeserver_app_data_lifecycle.rollback_data_for_active_release(key, active_release)
    except Exception:
        homeserver_app_releases.promote(
            key,
            active,
            reason="data_restore_failed_reactivate_current",
            system_managed=True,
        )
        raise
    return {"changed": bool(result.get("changed")), "rollback": result, "data_restore": data_restore, "status": release_status(key)}


def public_capability() -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "catalog_version": CATALOG_VERSION,
        "embedded": True,
        "first_party_only": True,
        "optional_vp3_apps": True,
        "core_homeserver_features_in_catalog": False,
        "deployment_modes": ["local","private_remote","hosted_subdomain","custom_domain"],
        "external_downloads": False,
        "app_store": False,
        "protected_system_apps": True,
        "canonical_package_runtime": True,
        "release_channel": "stable",
        "release_metadata": True,
        "compatibility_gates": True,
        "sha256_integrity": True,
        "embedded_trust": True,
        "post_update_verification": True,
        "rollback": True,
        "data_lifecycle": homeserver_app_data_lifecycle.public_capability(),
        "package_count": len(CATALOG),
    }
