from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path

from ..config import settings

CONTRACT="vp3.app.sdk.v1"
SDK_VERSION="1.2"
_KEY_RE=re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")

INDEX_HTML="""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{APP_NAME}}</title>
<link rel="stylesheet" href="assets/app.css">
</head>
<body>
<main class="vp3-app-shell">
  <header><span class="vp3-kicker">VP3 HomeServer App</span><h1>{{APP_NAME}}</h1></header>
  <section class="vp3-card"><h2>Ready to build.</h2><p>This app starts with the VP3 HomeServer App SDK.</p></section>
</main>
<script src="assets/vp3-sdk.js"></script>
<script src="assets/app.js"></script>
</body>
</html>
"""
APP_CSS="""*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f5f6f8;color:#15171a}.vp3-app-shell{max-width:1100px;margin:0 auto;padding:32px}.vp3-kicker{font-size:12px;text-transform:uppercase;letter-spacing:.12em;color:#667085}.vp3-card{background:#fff;border:1px solid #e4e7ec;border-radius:16px;padding:24px;box-shadow:0 8px 30px rgba(16,24,40,.05)}"""
SDK_JS="""window.VP3App={
version:"1.2",
health:()=>({ok:true,sdk:"1.2"}),
ready(cb){if(document.readyState==="loading"){document.addEventListener("DOMContentLoaded",cb,{once:true});}else{cb();}},
base(){const parts=location.pathname.split("/");const i=parts.indexOf("homeserver-apps");return i>=0?parts.slice(0,i+2).join("/"):"";},
async emit(topic,payload={}){const r=await fetch(this.base()+"/runtime/events",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({topic,payload})});if(!r.ok)throw new Error(await r.text());return r.json();},
async events(){const r=await fetch(this.base()+"/runtime/events");if(!r.ok)throw new Error(await r.text());return r.json();},
async runJob(jobId){const r=await fetch(this.base()+"/runtime/jobs/"+encodeURIComponent(jobId)+"/run",{method:"POST"});if(!r.ok)throw new Error(await r.text());return r.json();},
async writeFile(path,blob){const f=new FormData();f.append("file",blob instanceof Blob?blob:new Blob([blob]));const r=await fetch(this.base()+"/data/file?path="+encodeURIComponent(path),{method:"PUT",body:f});if(!r.ok)throw new Error(await r.text());return r.json();},
async readFile(path){const r=await fetch(this.base()+"/data/file?path="+encodeURIComponent(path));if(!r.ok)throw new Error(await r.text());return r.arrayBuffer();},
async deleteFile(path){const r=await fetch(this.base()+"/data/file?path="+encodeURIComponent(path),{method:"DELETE"});if(!r.ok)throw new Error(await r.text());return r.json();},
async permissions(){const r=await fetch(this.base()+"/permissions");if(!r.ok)throw new Error(await r.text());return r.json();},
async resources(){const r=await fetch(this.base()+"/resources");if(!r.ok)throw new Error(await r.text());return r.json();},
async runtime(){const r=await fetch(this.base()+"/runtime/services");if(!r.ok)throw new Error(await r.text());return r.json();}
};"""
APP_JS="""VP3App.ready(()=>{console.log("VP3 app ready",VP3App.health());});"""


class AppSdkError(RuntimeError):
    pass


def apps_root()->Path:
    root=settings.data_dir/"apps"
    root.mkdir(parents=True,exist_ok=True)
    (root/".staging").mkdir(parents=True,exist_ok=True)
    return root


def app_root(app_key:str)->Path:
    key=str(app_key or "").strip().lower()
    if not _KEY_RE.fullmatch(key):
        raise AppSdkError("Invalid app key.")
    root=apps_root().resolve()
    target=(root/key).resolve()
    if root not in target.parents:
        raise AppSdkError("Unsafe app path.")
    return target


def scaffold(app_key:str,name:str,*,runtime:str="static",permissions:list[str]|None=None)->dict:
    key=str(app_key or "").strip().lower()
    label=str(name or "").strip()
    if not _KEY_RE.fullmatch(key):
        raise AppSdkError("Invalid app key.")
    if not label or len(label)>160:
        raise AppSdkError("Invalid app name.")
    if runtime not in {"static","php"}:
        raise AppSdkError("Unsupported SDK runtime.")
    requested=[]
    for raw in list(permissions or []):
        value=str(raw or "").strip()
        if not value or len(value)>120:
            raise AppSdkError("Invalid app permission.")
        if value not in requested:
            requested.append(value)
    root=apps_root()
    target=app_root(key)
    if target.exists():
        raise AppSdkError("App project already exists.")
    staging=root/".staging"/("sdk_"+uuid.uuid4().hex)
    try:
        (staging/"assets").mkdir(parents=True)
        (staging/"database"/"migrations").mkdir(parents=True)
        (staging/"agent").mkdir(parents=True)
        (staging/"sample").mkdir(parents=True)
        (staging/"runtime").mkdir(parents=True)
        manifest={
            "contract":"vp3.app.package.v1",
            "app_key":key,
            "name":label,
            "version":"0.1.0",
            "runtime":runtime,
            "entrypoint":"index.html" if runtime=="static" else "index.php",
            "sdk_version":SDK_VERSION,
            "permissions":sorted(requested),
            "settings_schema":"settings.schema.json",
            "database_migrations":"database/migrations",
            "agent_actions":"agent/actions.json",
            "jobs":"runtime/jobs.json",
            "events":"runtime/events.json",
            "sample_data":"sample/data.json",
            "release_channel":"stable",
            "release_notes":["Initial VP3 HomeServer App SDK release."],
            "data_schema_version":"1",
            "data_migration_reversible":True,
            "routes":{"local":True,"private_remote":False,"public":False},
        }
        (staging/"vp3-app.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
        page=INDEX_HTML.replace("{{APP_NAME}}",label)
        if runtime=="static":
            (staging/"index.html").write_text(page,encoding="utf-8")
        else:
            (staging/"index.php").write_text("<?php declare(strict_types=1); ?>\n"+page,encoding="utf-8")
        (staging/"assets"/"vp3-sdk.js").write_text(SDK_JS+"\n",encoding="utf-8")
        (staging/"assets"/"app.css").write_text(APP_CSS+"\n",encoding="utf-8")
        (staging/"assets"/"app.js").write_text(APP_JS+"\n",encoding="utf-8")
        (staging/"settings.schema.json").write_text(json.dumps({"contract":"vp3.app.settings-schema.v1","fields":[]},indent=2)+"\n",encoding="utf-8")
        (staging/"agent"/"actions.json").write_text(json.dumps({"contract":"vp3.app.agent-actions.v2","actions":[]},indent=2)+"\n",encoding="utf-8")
        (staging/"runtime"/"jobs.json").write_text(json.dumps({"contract":"vp3.app.jobs.v1","jobs":[]},indent=2)+"\n",encoding="utf-8")
        (staging/"runtime"/"events.json").write_text(json.dumps({"contract":"vp3.app.events.v1","subscriptions":[]},indent=2)+"\n",encoding="utf-8")
        (staging/"sample"/"data.json").write_text(json.dumps({"contract":"vp3.app.sample-data.v1","items":[{"id":"welcome","title":"Sample item","description":"Replace this with app-specific demo data."}]},indent=2)+"\n",encoding="utf-8")
        (staging/"database"/"migrations"/"README.md").write_text(
            "# Data schema changes\n\n"
            "HomeServer snapshots app data before a release changes data_schema_version. "
            "For user-created apps, schema migration execution is app-managed; package SQL is never executed automatically. "
            "Keep migrations idempotent in your app runtime and increment data_schema_version only when the stored schema changes.\n",
            encoding="utf-8",
        )
        (staging/"README.md").write_text(
            f"# {label}\n\nGenerated by VP3 HomeServer App SDK {SDK_VERSION}.\n\n"
            "## Lifecycle\nCreate → edit → Build & Install → run → host → update → rollback/recover.\n\n"
            "## Built-in integration\n"
            "- App-owned files and SQLite storage stay outside release source files.\n"
            "- Permission declarations are reviewed by HomeServer and default to denied.\n"
            "- Data schema changes create recovery snapshots before activation.\n"
            "- User-app schema execution remains app-managed; HomeServer never executes arbitrary package SQL.\n"
            "- Runtime events/jobs, Agent action declarations, release history, rollback, ZIP/Git source, and Hosting use canonical HomeServer services.\n",
            encoding="utf-8",
        )
        os.replace(staging,target)
    except Exception:
        shutil.rmtree(staging,ignore_errors=True)
        raise
    return {
        "contract":CONTRACT,
        "sdk_version":SDK_VERSION,
        "app_key":key,
        "runtime":runtime,
        "project_created":True,
        "manifest":manifest,
    }


def remove_project(app_key:str)->None:
    target=app_root(app_key)
    if target.exists():
        shutil.rmtree(target)
