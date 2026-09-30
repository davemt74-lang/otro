from __future__ import annotations

import hashlib
import io
import json
import zipfile
from typing import Any

from ..database import db
from . import homeserver_app_data_lifecycle, homeserver_app_packages, homeserver_app_releases, homeserver_apps

CONTRACT = "vp3.app.prebuilt-catalog.v1"
CATALOG_VERSION = "2026.09.30.4"

APP_CSS = """*{box-sizing:border-box}body{margin:0;background:#f5f6f8;color:#181b1f;font:14px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.shell{max-width:980px;margin:0 auto;padding:28px}.top{display:flex;justify-content:space-between;gap:16px;margin-bottom:18px}.top h1{margin:3px 0}.eyebrow{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:#727980}.muted{color:#6b7278}.panel{background:#fff;border:1px solid #e2e6e9;border-radius:15px;padding:18px}.toolbar{display:flex;gap:8px;margin-bottom:14px}.toolbar input{flex:1;min-width:0;border:1px solid #d5d9dd;border-radius:9px;padding:10px 11px;font:inherit}.button{border:0;border-radius:9px;padding:10px 14px;font-weight:700;cursor:pointer;background:#17191c;color:#fff}.secondary{background:#eef0f2;color:#202428}.danger{background:#fff1f1;color:#a43c3c}.list{display:grid;gap:10px}.row{border:1px solid #e7eaed;border-radius:12px;padding:13px;display:flex;justify-content:space-between;gap:14px}.row h3{margin:0 0 4px;font-size:15px}.row p{margin:0;color:#697075}.actions{display:flex;gap:7px}.empty{padding:28px;text-align:center;color:#777f86}.pill{display:inline-flex;padding:3px 8px;border-radius:999px;background:#eef1f3;font-size:11px}@media(max-width:700px){.shell{padding:18px}.toolbar,.row{display:block}.toolbar>*{width:100%;margin-bottom:7px}.actions{margin-top:10px}}"""

COMMON_JS = """const cfg=window.VP3_PREBUILT;const dataUrl='/api/v1/control/homeserver-apps/'+encodeURIComponent(cfg.key)+'/data/file?path='+encodeURIComponent('data.json');const sampleUrl='/api/v1/control/homeserver-apps/'+encodeURIComponent(cfg.key)+'/sample-data';const esc=(s)=>String(s??'').replace(/[&<>"']/g,(c)=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));async function readData(){try{const r=await fetch(dataUrl,{cache:'no-store'});if(r.status===404)return [];if(!r.ok)return [];return await r.json()}catch(e){return []}}async function writeData(value){const blob=new Blob([JSON.stringify(value,null,2)],{type:'application/json'});const f=new FormData();f.append('file',blob,'data.json');const r=await fetch(dataUrl,{method:'PUT',body:f});if(!r.ok)throw new Error('Unable to save app data')}async function samples(){try{const r=await fetch(sampleUrl,{cache:'no-store'});if(!r.ok)return [];const j=await r.json();return j.sample_data?.items||[]}catch(e){return []}}let items=[];function summary(x){return (cfg.fields||[]).map(f=>'<span><strong>'+esc(f.label)+':</strong> '+esc(x[f.key]??'')+'</span>').join(' · ')}function markup(x,i){if(cfg.kind==='notes')return '<article class="row"><div><h3>'+esc(x.title)+'</h3><p>'+esc(x.body)+'</p></div><div class="actions"><button class="button secondary danger" data-delete="'+i+'">Delete</button></div></article>';if(cfg.kind==='inventory')return '<article class="row"><div><h3>'+esc(x.name)+'</h3><p>Quantity: <strong>'+Number(x.qty||0)+'</strong></p></div><div class="actions"><button class="button secondary" data-minus="'+i+'">−</button><button class="button secondary" data-plus="'+i+'">+</button><button class="button secondary danger" data-delete="'+i+'">Delete</button></div></article>';if(cfg.kind==='checklist')return '<article class="row"><div><h3>'+(x.done?'✓ ':'')+esc(x.task)+'</h3><p>'+(x.done?'Complete':'Open')+'</p></div><div class="actions"><button class="button secondary" data-toggle="'+i+'">'+(x.done?'Reopen':'Complete')+'</button><button class="button secondary danger" data-delete="'+i+'">Delete</button></div></article>';const titleField=(cfg.fields||[])[0];return '<article class="row"><div><h3>'+esc(titleField?x[titleField.key]:'Record')+'</h3><p>'+summary(x)+'</p></div><div class="actions"><button class="button secondary danger" data-delete="'+i+'">Delete</button></div></article>'}function render(){const n=document.getElementById('list');n.innerHTML=items.length?items.map(markup).join(''):'<div class="empty">No items yet.</div>'}async function save(){await writeData(items);render()}async function init(){items=await readData();if(!items.length){const s=await samples();if(s.length)items=s}render()}document.getElementById('form').addEventListener('submit',async(e)=>{e.preventDefault();if(cfg.kind==='notes')items.unshift({title:document.getElementById('field1').value.trim(),body:document.getElementById('field2').value.trim()});else if(cfg.kind==='inventory')items.unshift({name:document.getElementById('field1').value.trim(),qty:Number(document.getElementById('field2').value||0)});else if(cfg.kind==='checklist')items.unshift({task:document.getElementById('field1').value.trim(),done:false});else{const row={};(cfg.fields||[]).forEach((f,i)=>{const el=document.getElementById('field'+(i+1));row[f.key]=el?el.value.trim():''});items.unshift(row)}await save();e.target.reset();if(cfg.kind==='inventory')document.getElementById('field2').value='1'});document.addEventListener('click',async(e)=>{const b=e.target.closest('[data-delete],[data-plus],[data-minus],[data-toggle]');if(!b)return;const raw=b.dataset.delete??b.dataset.plus??b.dataset.minus??b.dataset.toggle;const i=Number(raw);if(b.dataset.delete!==undefined)items.splice(i,1);else if(b.dataset.plus!==undefined)items[i].qty=Number(items[i].qty||0)+1;else if(b.dataset.minus!==undefined)items[i].qty=Math.max(0,Number(items[i].qty||0)-1);else items[i].done=!items[i].done;await save()});init();"""

CATALOG = {
    "vp3.notes": {"key":"vp3.notes","name":"VP3 Notes","version":"1.3.0","release_channel":"stable","min_homeserver_version":"2.4","data_schema_version":"2","data_migration_reversible":True,"release_notes":["Unified App Manager metadata.","Governed app-data recovery."],"category":"Productivity","kind":"notes","description":"Private lightweight notes stored in isolated HomeServer app data.","sample":[{"title":"Welcome to VP3 Notes","body":"Your notes stay in isolated HomeServer app data."}],"permissions":[]},
    "vp3.inventory": {"key":"vp3.inventory","name":"VP3 Inventory","version":"1.2.0","release_channel":"stable","min_homeserver_version":"2.4","release_notes":["Unified App Manager metadata."],"category":"Operations","kind":"inventory","description":"Track local inventory counts and supplies.","sample":[{"name":"Sample item","qty":12},{"name":"Low-stock example","qty":2}],"permissions":[]},
    "vp3.checklists": {"key":"vp3.checklists","name":"VP3 Checklists","version":"1.2.0","release_channel":"stable","min_homeserver_version":"2.4","release_notes":["Unified App Manager metadata."],"category":"Productivity","kind":"checklist","description":"Create local operational and personal checklists.","sample":[{"task":"Review HomeServer Apps","done":False}],"permissions":[]},
    "vp3.contacts": {"key":"vp3.contacts","name":"VP3 Contacts","version":"1.0.0","release_channel":"stable","min_homeserver_version":"2.4","category":"People","kind":"records","description":"A private HomeServer contact book for people and organizations.","fields":[{"key":"name","label":"Name","placeholder":"Name"},{"key":"contact","label":"Contact","placeholder":"Email or phone"}],"sample":[{"name":"Sample Contact","contact":"contact@example.com"}],"permissions":[],"release_notes":["Initial HomeServer system-app release."]},
    "vp3.tasks": {"key":"vp3.tasks","name":"VP3 Tasks","version":"1.0.0","release_channel":"stable","min_homeserver_version":"2.4","category":"Productivity","kind":"records","description":"Track tasks, owners, and due dates locally.","fields":[{"key":"task","label":"Task","placeholder":"Task"},{"key":"due","label":"Due","placeholder":"Due date"}],"sample":[{"task":"Review HomeServer status","due":"Today"}],"permissions":["notifications.write"],"release_notes":["Initial HomeServer system-app release."]},
    "vp3.calendar": {"key":"vp3.calendar","name":"VP3 Calendar","version":"1.0.0","release_channel":"stable","min_homeserver_version":"2.4","category":"Productivity","kind":"records","description":"Local events and schedules with optional notification capability.","fields":[{"key":"event","label":"Event","placeholder":"Event"},{"key":"when","label":"When","placeholder":"Date / time"}],"sample":[{"event":"HomeServer review","when":"Friday 10:00"}],"permissions":["notifications.write"],"release_notes":["Initial HomeServer system-app release."]},
    "vp3.files": {"key":"vp3.files","name":"VP3 Files","version":"1.0.0","release_channel":"stable","min_homeserver_version":"2.4","category":"Storage","kind":"records","description":"A governed index for HomeServer files and collections.","fields":[{"key":"name","label":"Name","placeholder":"File or collection"},{"key":"location","label":"Location","placeholder":"Local location"}],"sample":[{"name":"Documents","location":"HomeServer"}],"permissions":[],"release_notes":["Initial HomeServer system-app release."]},
    "vp3.media": {"key":"vp3.media","name":"VP3 Media","version":"1.0.0","release_channel":"stable","min_homeserver_version":"2.4","category":"Media","kind":"records","description":"Organize local media libraries and playback collections.","fields":[{"key":"title","label":"Title","placeholder":"Media title"},{"key":"type","label":"Type","placeholder":"Audio / Video / Photo"}],"sample":[{"title":"Sample Library","type":"Media"}],"permissions":[],"release_notes":["Initial HomeServer system-app release."]},
    "vp3.home": {"key":"vp3.home","name":"VP3 Home","version":"1.0.0","release_channel":"stable","min_homeserver_version":"2.4","category":"Automation","kind":"records","description":"Home automation dashboard shell for rooms, devices, and routines.","fields":[{"key":"device","label":"Device","placeholder":"Device or room"},{"key":"state","label":"State","placeholder":"State"}],"sample":[{"device":"Kitchen","state":"Ready"}],"permissions":["agent.context"],"release_notes":["Initial HomeServer automation system-app shell."]},
    "vp3.tracky": {"key":"vp3.tracky","name":"VP3 Tracky","version":"1.0.0","release_channel":"stable","min_homeserver_version":"2.4","category":"Vision","kind":"records","description":"Camera and physical-context system-app shell for Tracky runtime integrations.","fields":[{"key":"camera","label":"Camera","placeholder":"Camera"},{"key":"status","label":"Status","placeholder":"Status"}],"sample":[{"camera":"Primary camera","status":"Not configured"}],"permissions":["hardware.camera","hardware.microphone","agent.context"],"release_notes":["Initial governed Tracky HomeServer shell."]},
    "vp3.crm": {"key":"vp3.crm","name":"VP3 CRM","version":"1.0.0","release_channel":"stable","min_homeserver_version":"2.4","category":"Business","kind":"records","description":"Local customer and relationship management workspace.","fields":[{"key":"account","label":"Account","placeholder":"Customer or account"},{"key":"stage","label":"Stage","placeholder":"Stage"}],"sample":[{"account":"Sample Account","stage":"New"}],"permissions":["notifications.write"],"release_notes":["Initial HomeServer CRM system app."]},
    "vp3.campaigns": {"key":"vp3.campaigns","name":"VP3 Campaigns","version":"1.0.0","release_channel":"stable","min_homeserver_version":"2.4","category":"Business","kind":"records","description":"Campaign planning and local audience workflow shell.","fields":[{"key":"campaign","label":"Campaign","placeholder":"Campaign"},{"key":"status","label":"Status","placeholder":"Draft / Active"}],"sample":[{"campaign":"Welcome Campaign","status":"Draft"}],"permissions":["notifications.write"],"release_notes":["Initial HomeServer Campaigns system app."]},
    "vp3.rewards": {"key":"vp3.rewards","name":"VP3 Rewards","version":"1.0.0","release_channel":"stable","min_homeserver_version":"2.4","category":"Business","kind":"records","description":"Rewards and offer ledger shell for local customer experiences.","fields":[{"key":"reward","label":"Reward","placeholder":"Reward"},{"key":"state","label":"State","placeholder":"Available / Claimed"}],"sample":[{"reward":"Welcome Reward","state":"Available"}],"permissions":[],"release_notes":["Initial HomeServer Rewards system app."]},
    "vp3.website": {"key":"vp3.website","name":"VP3 Website","version":"1.0.0","release_channel":"stable","min_homeserver_version":"2.4","category":"Hosting","kind":"records","description":"Website deployment workspace shell tied to canonical Hosting and subdomain services.","fields":[{"key":"site","label":"Site","placeholder":"Site name"},{"key":"visibility","label":"Visibility","placeholder":"Local / Private / Public"}],"sample":[{"site":"My Site","visibility":"Local"}],"permissions":["network.external"],"release_notes":["Initial Hosting-integrated HomeServer system app shell."]},
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
        "routes": {"local": True, "private_remote": False, "public": False},
    }


def _html(definition: dict[str, Any]) -> str:
    fields = list(definition.get("fields") or [])
    if definition["kind"] == "notes":
        controls = '<input id="field1" maxlength="180" placeholder="Note title" required><input id="field2" maxlength="2000" placeholder="Write a note…" required>'
    elif definition["kind"] == "inventory":
        controls = '<input id="field1" maxlength="180" placeholder="Item" required><input id="field2" type="number" min="0" max="999999" value="1" required>'
    elif definition["kind"] == "checklist":
        controls = '<input id="field1" maxlength="180" placeholder="Checklist item" required>'
    else:
        controls = ''.join(
            f'<input id="field{i+1}" maxlength="300" placeholder="{field.get("placeholder") or field.get("label") or "Value"}" required>'
            for i, field in enumerate(fields[:4])
        )
    config = {"key":definition["key"],"kind":definition["kind"],"fields":fields[:4]}
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{definition["name"]}</title><link rel="stylesheet" href="assets/app.css"></head><body><main class="shell">'
        f'<header class="top"><div><span class="eyebrow">VP3 SYSTEM APP</span><h1>{definition["name"]}</h1>'
        f'<p class="muted">{definition["description"]}</p></div><span class="pill">HomeServer</span></header>'
        f'<section class="panel"><form id="form" class="toolbar">{controls}'
        '<button class="button" type="submit">Add</button></form><div id="list" class="list"></div></section></main>'
        f'<script>window.VP3_PREBUILT={json.dumps(config,separators=(",",":"))};</script>'
        '<script src="assets/app.js"></script></body></html>'
    )


def _package(definition: dict[str, Any]) -> bytes:
    files = {
        "vp3-app.json": json.dumps(_manifest(definition), indent=2, sort_keys=True) + "\n",
        "index.html": _html(definition),
        "assets/app.css": APP_CSS,
        "assets/app.js": COMMON_JS,
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
