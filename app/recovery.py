from __future__ import annotations

import html

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

from .config import settings
from .security import owner_token_matches
from .services import backups
from .services.runtime_control import request_runtime_command
from .services.windows_integration import WindowsIntegrationError, open_folder


def build_recovery_app(reason: str) -> FastAPI:
    app = FastAPI(title="HomeServer Recovery", version=settings.version)
    safe_reason = html.escape(reason[:240])

    def require_owner(candidate: str | None) -> None:
        if not owner_token_matches(candidate):
            raise HTTPException(status_code=401, detail="Owner authorization required")

    @app.get("/api/v1/health")
    def health() -> dict:
        return {
            "ok": False,
            "recovery": True,
            "service": settings.app_name,
            "version": settings.version,
            "reason": reason[:120],
        }

    @app.get("/", include_in_schema=False)
    def recovery_page():
        return HTMLResponse(
            f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HomeServer Recovery</title><style>
body{{font-family:Inter,Segoe UI,sans-serif;background:#f7f7f5;color:#151515;margin:0}}main{{max-width:760px;margin:8vh auto;padding:24px}}.card{{background:white;border:1px solid #ddd;border-radius:18px;padding:28px;box-shadow:0 12px 40px #0000000c}}button{{padding:11px 16px;border:1px solid #bbb;border-radius:10px;background:white;cursor:pointer}}button.primary{{background:#111;color:white;border-color:#111}}.row{{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}}.status{{margin-top:18px;padding:12px;border-radius:10px;background:#f1f1ef;white-space:pre-wrap}}small{{color:#666}}input{{display:none}}
</style></head><body><main><div class="card"><p><strong>HOMESERVER RECOVERY MODE</strong></p><h1>Your private data was not modified.</h1><p>HomeServer could not initialize the normal database runtime, so it started a restricted recovery surface instead of continuing with a partially working server.</p><p><small>Startup reason: {safe_reason}</small></p><div class="row"><button class="primary" id="restore">Stage a backup restore</button><button id="folder">Open data folder</button><button id="restart">Restart HomeServer</button><input id="file" type="file" accept=".zip,application/zip"></div><div class="status" id="status">Choose a known-good HomeServer backup to stage it. The restore will be validated before the next restart.</div></div></main>
<script>
let owner=''; const hash=new URLSearchParams(location.hash.slice(1)); owner=hash.get('owner')||''; history.replaceState(null,'',location.pathname);
const status=document.getElementById('status');
async function call(path,options={{}}){{ const headers={{...(options.headers||{{}}),'X-HomeServer-Owner':owner}}; const r=await fetch(path,{{...options,headers}}); let d={{}}; try{{d=await r.json()}}catch(_e){{}} if(!r.ok)throw new Error(d.detail||`Request failed (${{r.status}})`); return d; }}
document.getElementById('restore').onclick=()=>document.getElementById('file').click();
document.getElementById('file').onchange=async e=>{{const file=e.target.files?.[0]; if(!file)return; const form=new FormData(); form.append('file',file,file.name); status.textContent='Validating backup…'; try{{const d=await call('/api/v1/recovery/restore',{{method:'POST',body:form}}); status.textContent=d.message;}}catch(err){{status.textContent=err.message;}} e.target.value='';}};
document.getElementById('folder').onclick=async()=>{{try{{await call('/api/v1/recovery/open-data-folder',{{method:'POST'}});}}catch(err){{status.textContent=err.message;}}}};
document.getElementById('restart').onclick=async()=>{{try{{status.textContent='Restart requested…'; await call('/api/v1/recovery/restart',{{method:'POST'}});}}catch(err){{status.textContent=err.message;}}}};
</script></body></html>""",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/v1/recovery/restore")
    def stage_restore(
        file: UploadFile = File(...),
        x_homeserver_owner: str | None = Header(default=None),
    ) -> dict:
        require_owner(x_homeserver_owner)
        try:
            file.file.seek(0)
            state = backups.stage_restore(file.file, file.filename or "backup.zip")
        except backups.BackupError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        return {
            "staged": True,
            "restore": state,
            "message": "Backup validated and staged. Restart HomeServer to apply it before the normal server starts.",
        }

    @app.post("/api/v1/recovery/open-data-folder")
    def open_data(x_homeserver_owner: str | None = Header(default=None)) -> dict:
        require_owner(x_homeserver_owner)
        try:
            open_folder(settings.data_dir)
        except WindowsIntegrationError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"opened": True}

    @app.post("/api/v1/recovery/restart")
    def restart(x_homeserver_owner: str | None = Header(default=None)) -> dict:
        require_owner(x_homeserver_owner)
        if not request_runtime_command("restart"):
            raise HTTPException(status_code=503, detail="Runtime restart control is unavailable")
        return {"accepted": True}

    return app
