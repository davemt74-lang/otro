from __future__ import annotations

import hashlib
import io
import json
import zipfile
from typing import Any

from ..database import db
from . import homeserver_app_data_lifecycle, homeserver_app_packages, homeserver_app_releases, homeserver_apps

CONTRACT = "vp3.app.prebuilt-catalog.v1"
CATALOG_VERSION = "2026.09.30.5"

APP_CSS = """*{box-sizing:border-box}body{margin:0;background:#f5f6f8;color:#181b1f;font:14px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.shell{max-width:980px;margin:0 auto;padding:28px}.top{display:flex;justify-content:space-between;gap:16px;margin-bottom:18px}.top h1{margin:3px 0}.eyebrow{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:#727980}.muted{color:#6b7278}.panel{background:#fff;border:1px solid #e2e6e9;border-radius:15px;padding:18px}.toolbar{display:flex;gap:8px;margin-bottom:14px}.toolbar input{flex:1;min-width:0;border:1px solid #d5d9dd;border-radius:9px;padding:10px 11px;font:inherit}.button{border:0;border-radius:9px;padding:10px 14px;font-weight:700;cursor:pointer;background:#17191c;color:#fff}.secondary{background:#eef0f2;color:#202428}.danger{background:#fff1f1;color:#a43c3c}.list{display:grid;gap:10px}.row{border:1px solid #e7eaed;border-radius:12px;padding:13px;display:flex;justify-content:space-between;gap:14px}.row h3{margin:0 0 4px;font-size:15px}.row p{margin:0;color:#697075}.actions{display:flex;gap:7px}.empty{padding:28px;text-align:center;color:#777f86}.pill{display:inline-flex;padding:3px 8px;border-radius:999px;background:#eef1f3;font-size:11px}@media(max-width:700px){.shell{padding:18px}.toolbar,.row{display:block}.toolbar>*{width:100%;margin-bottom:7px}.actions{margin-top:10px}}"""

MEDIA_SERVER_CSS = """*{box-sizing:border-box}body{margin:0;background:#0f1115;color:#f5f7fa;font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.shell{max-width:1280px;margin:0 auto;padding:24px}.top{display:flex;justify-content:space-between;gap:18px;align-items:flex-start}.top h1{font-size:34px;margin:4px 0}.eyebrow{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:#8e98a4}.muted{color:#9aa3ad}.pill,.chip{display:inline-flex;padding:5px 9px;border-radius:999px;background:#20252c;color:#d8dee6;font-size:11px}.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:20px 0}.toolbar input,.toolbar select{background:#171b20;color:#fff;border:1px solid #303741;border-radius:9px;padding:10px 12px}.toolbar input{flex:1;min-width:240px}.button{border:0;border-radius:9px;padding:10px 14px;font-weight:700;cursor:pointer;background:#f5f7fa;color:#11151a}.secondary{background:#232931;color:#eef2f6}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:18px 0}.stat,.panel{background:#171b20;border:1px solid #292f37;border-radius:14px;padding:15px}.stat strong{display:block;font-size:24px;margin-top:4px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}.card{background:#171b20;border:1px solid #292f37;border-radius:14px;overflow:hidden}.thumb{aspect-ratio:16/10;background:#242a32;display:grid;place-items:center;font-size:40px}.card-body{padding:12px}.card h3{font-size:14px;margin:0 0 5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.card p{font-size:12px;color:#9aa3ad;margin:0}.manage{margin-top:20px}.manage-grid{display:grid;grid-template-columns:1fr auto auto;gap:8px}.manage-grid input{background:#11151a;color:#fff;border:1px solid #303741;border-radius:9px;padding:10px}.roots{display:grid;gap:7px;margin-top:10px}.root{display:flex;justify-content:space-between;gap:10px;padding:9px 0;border-top:1px solid #292f37}.player{position:fixed;inset:0;background:rgba(0,0,0,.82);display:grid;place-items:center;padding:30px;z-index:30}.player.hidden{display:none}.player-box{width:min(1000px,95vw);background:#0b0d10;border-radius:15px;padding:14px}.player video,.player audio,.player img{width:100%;max-height:75vh;object-fit:contain;background:#000}.player-head{display:flex;justify-content:space-between;gap:10px;align-items:center;margin-bottom:10px}.empty{padding:50px;text-align:center;color:#89939e}@media(max-width:700px){.stats{grid-template-columns:repeat(2,1fr)}.manage-grid{grid-template-columns:1fr}.shell{padding:16px}}"""

COMMON_JS = """const cfg=window.VP3_PREBUILT;const dataUrl='/api/v1/control/homeserver-apps/'+encodeURIComponent(cfg.key)+'/data/file?path='+encodeURIComponent('data.json');const sampleUrl='/api/v1/control/homeserver-apps/'+encodeURIComponent(cfg.key)+'/sample-data';const esc=(s)=>String(s??'').replace(/[&<>"']/g,(c)=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));async function readData(){try{const r=await fetch(dataUrl,{cache:'no-store'});if(r.status===404)return [];if(!r.ok)return [];return await r.json()}catch(e){return []}}async function writeData(value){const blob=new Blob([JSON.stringify(value,null,2)],{type:'application/json'});const f=new FormData();f.append('file',blob,'data.json');const r=await fetch(dataUrl,{method:'PUT',body:f});if(!r.ok)throw new Error('Unable to save app data')}async function samples(){try{const r=await fetch(sampleUrl,{cache:'no-store'});if(!r.ok)return [];const j=await r.json();return j.sample_data?.items||[]}catch(e){return []}}let items=[];function markup(x,i){if(cfg.kind==='notes')return '<article class="row"><div><h3>'+esc(x.title)+'</h3><p>'+esc(x.body)+'</p></div><div class="actions"><button class="button secondary danger" data-delete="'+i+'">Delete</button></div></article>';if(cfg.kind==='inventory')return '<article class="row"><div><h3>'+esc(x.name)+'</h3><p>Quantity: <strong>'+Number(x.qty||0)+'</strong></p></div><div class="actions"><button class="button secondary" data-minus="'+i+'">−</button><button class="button secondary" data-plus="'+i+'">+</button><button class="button secondary danger" data-delete="'+i+'">Delete</button></div></article>';return '<article class="row"><div><h3>'+(x.done?'✓ ':'')+esc(x.task)+'</h3><p>'+(x.done?'Complete':'Open')+'</p></div><div class="actions"><button class="button secondary" data-toggle="'+i+'">'+(x.done?'Reopen':'Complete')+'</button><button class="button secondary danger" data-delete="'+i+'">Delete</button></div></article>'}function render(){const n=document.getElementById('list');n.innerHTML=items.length?items.map(markup).join(''):'<div class="empty">No items yet.</div>'}async function save(){await writeData(items);render()}async function init(){items=await readData();if(!items.length){const s=await samples();if(s.length)items=s}render()}document.getElementById('form').addEventListener('submit',async(e)=>{e.preventDefault();if(cfg.kind==='notes')items.unshift({title:document.getElementById('field1').value.trim(),body:document.getElementById('field2').value.trim()});else if(cfg.kind==='inventory')items.unshift({name:document.getElementById('field1').value.trim(),qty:Number(document.getElementById('field2').value||0)});else items.unshift({task:document.getElementById('field1').value.trim(),done:false});await save();e.target.reset();if(cfg.kind==='inventory')document.getElementById('field2').value='1'});document.addEventListener('click',async(e)=>{const b=e.target.closest('[data-delete],[data-plus],[data-minus],[data-toggle]');if(!b)return;const raw=b.dataset.delete??b.dataset.plus??b.dataset.minus??b.dataset.toggle;const i=Number(raw);if(b.dataset.delete!==undefined)items.splice(i,1);else if(b.dataset.plus!==undefined)items[i].qty=Number(items[i].qty||0)+1;else if(b.dataset.minus!==undefined)items[i].qty=Math.max(0,Number(items[i].qty||0)-1);else items[i].done=!items[i].done;await save()});init();"""

MEDIA_SERVER_JS = """const local=location.pathname.includes('/api/v1/control/homeserver-apps/');const control='/api/v1/control/homeserver-apps/media-server';let access=sessionStorage.getItem('vp3_media_access')||'';const auth=()=>access?{'Authorization':'Bearer '+access}:{};async function api(path,opt={}){const base=local?control:'/__vp3_media__';const r=await fetch(base+path,{...opt,headers:{...(opt.headers||{}),...(!local?auth():{})}});if(r.status===401&&!local){access=prompt('Media Server access key')||'';sessionStorage.setItem('vp3_media_access',access);return api(path,opt)}if(!r.ok){let m='Request failed';try{m=(await r.json()).detail||m}catch{}throw new Error(m)}return r.json()}const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const fmt=n=>{n=Number(n||0);if(n<1024)return n+' B';if(n<1048576)return (n/1024).toFixed(1)+' KB';if(n<1073741824)return (n/1048576).toFixed(1)+' MB';return (n/1073741824).toFixed(1)+' GB'};let current=null;async function load(){const q=document.getElementById('q').value.trim(),type=document.getElementById('type').value;const p=new URLSearchParams({limit:'300'});if(q)p.set('q',q);if(type)p.set('media_type',type);const [lib,status]=await Promise.all([api('/library?'+p),api('/status')]);document.getElementById('count').textContent=status.library_count||0;document.getElementById('videos').textContent=status.types.video||0;document.getElementById('audio').textContent=status.types.audio||0;document.getElementById('images').textContent=status.types.image||0;const grid=document.getElementById('grid');grid.innerHTML=lib.items.length?lib.items.map(x=>'<button class="card" data-media="'+esc(x.media_id)+'"><div class="thumb">'+(x.media_type==='video'?'▶':x.media_type==='audio'?'♫':'▧')+'</div><div class="card-body"><h3>'+esc(x.title)+'</h3><p>'+esc(x.media_type)+' · '+fmt(x.size_bytes)+'</p></div></button>').join(''):'<div class="empty">No media indexed yet.</div>';if(local)loadRoots()}async function loadRoots(){const r=await api('/roots');document.getElementById('roots').innerHTML=r.roots.map(x=>'<div class="root"><span>'+esc(x.label)+'</span><button class="button secondary" data-remove="'+esc(x.root_id)+'">Remove</button></div>').join('')||'<p class="muted">No folders granted.</p>'}async function openMedia(id){current=(await api('/item/'+encodeURIComponent(id))).item;const p=document.getElementById('playerContent');let stream=(local?control:'/__vp3_media__')+'/stream/'+encodeURIComponent(id);if(!local){const ticket=await api('/stream-ticket/'+encodeURIComponent(id));stream=ticket.stream_url}if(current.media_type==='video')p.innerHTML='<video controls autoplay src="'+stream+'"></video>';else if(current.media_type==='audio')p.innerHTML='<audio controls autoplay src="'+stream+'"></audio>';else p.innerHTML='<img src="'+stream+'" alt="">';document.getElementById('playerTitle').textContent=current.title;document.getElementById('player').classList.remove('hidden');const el=p.querySelector('video,audio');if(el&&current.playback?.position_seconds)el.currentTime=current.playback.position_seconds;if(el){el.addEventListener('timeupdate',()=>{if(Math.floor(el.currentTime)%10===0)api('/playback/'+encodeURIComponent(id),{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({position_seconds:el.currentTime,duration_seconds:el.duration||0,completed:false})}).catch(()=>{})})}}document.getElementById('q').addEventListener('input',()=>load());document.getElementById('type').addEventListener('change',load);document.getElementById('grid').addEventListener('click',e=>{const b=e.target.closest('[data-media]');if(b)openMedia(b.dataset.media)});document.getElementById('closePlayer').addEventListener('click',()=>{document.getElementById('player').classList.add('hidden');document.getElementById('playerContent').innerHTML=''});if(local){document.getElementById('manage').hidden=false;document.getElementById('addRoot').addEventListener('click',async()=>{const path=document.getElementById('rootPath').value.trim();if(!path)return;await api('/roots',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path,label:''})});document.getElementById('rootPath').value='';await api('/scan',{method:'POST'});load()});document.getElementById('scan').addEventListener('click',async()=>{await api('/scan',{method:'POST'});load()});document.getElementById('remote').addEventListener('click',async()=>{const r=await api('/remote/enable',{method:'POST'});prompt('Copy this access key now. It will not be shown again.',r.access_key)});document.getElementById('roots').addEventListener('click',async e=>{const b=e.target.closest('[data-remove]');if(!b)return;await api('/roots/'+encodeURIComponent(b.dataset.remove),{method:'DELETE'});load()})}load().catch(e=>document.getElementById('grid').innerHTML='<div class="empty">'+esc(e.message)+'</div>');"""

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
        "version": "1.0.0",
        "release_channel": "stable",
        "min_homeserver_version": "2.4",
        "release_notes": [
            "First full VP3 optional HomeServer app.",
            "Adds governed media-root grants, local indexing, direct playback, resume state, and hosted access-key support.",
        ],
        "category": "Media",
        "kind": "media_server",
        "description": "Index, browse, and stream your HomeServer media library locally or through governed VP3 Hosting.",
        "sample": [],
        "permissions": ["files.read"],
        "routes": {"local": True, "private_remote": True, "public": False},
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
        "sdk_version": "1.0",
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
    if definition["kind"] == "media_server":
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>VP3 Media Server</title><link rel="stylesheet" href="assets/app.css"></head><body><main class="shell">'
            '<header class="top"><div><span class="eyebrow">VP3 APP · MEDIA SERVER</span><h1>Media Server</h1><p class="muted">Your media, indexed and streamed directly from this HomeServer.</p></div><span class="pill">Direct Play</span></header>'
            '<div class="stats"><div class="stat"><span>Total</span><strong id="count">—</strong></div><div class="stat"><span>Video</span><strong id="videos">—</strong></div><div class="stat"><span>Audio</span><strong id="audio">—</strong></div><div class="stat"><span>Images</span><strong id="images">—</strong></div></div>'
            '<div class="toolbar"><input id="q" placeholder="Search library"><select id="type"><option value="">All media</option><option value="video">Video</option><option value="audio">Audio</option><option value="image">Images</option></select></div><section id="grid" class="grid"></section>'
            '<section class="panel manage" id="manage" hidden><h2>Library folders</h2><p class="muted">Folders are owner-granted. Media files are never copied into the app.</p><div class="manage-grid"><input id="rootPath" placeholder="Folder path"><button class="button" id="addRoot" type="button">Add & Scan</button><button class="button secondary" id="scan" type="button">Rescan</button></div><div id="roots" class="roots"></div><hr><button class="button secondary" id="remote" type="button">Generate Remote Access Key</button></section>'
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
        "assets/app.css": MEDIA_SERVER_CSS if definition["kind"]=="media_server" else APP_CSS,
        "assets/app.js": MEDIA_SERVER_JS if definition["kind"]=="media_server" else COMMON_JS,
        "settings.schema.json": json.dumps({"contract": "vp3.app.settings-schema.v1", "fields": []}, indent=2) + "\n",
        "agent/actions.json": json.dumps({"contract": "vp3.app.agent-actions.v1", "actions": []}, indent=2) + "\n",
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
