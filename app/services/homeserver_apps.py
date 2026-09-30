from __future__ import annotations

import json
import re
import uuid
from typing import Any

from ..database import db
from . import homeserver_app_sdk

CONTRACT = "vp3.homeserver-apps.registry.v1"
APP_CLASSES = {"system", "user"}
SOURCE_TYPES = {"vp3_system", "user_created", "zip", "git", "agent_builder", "app_store"}
LIFECYCLE_STATES = {
    "draft", "installing", "installed", "starting", "running", "degraded",
    "stopped", "updating", "recovering", "failed", "uninstalling", "archived",
}
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")

_TRANSITIONS = {
    "draft": {"installing", "archived"},
    "installing": {"installed", "failed"},
    "installed": {"starting", "running", "stopped", "updating", "uninstalling", "failed"},
    "starting": {"running", "failed"},
    "running": {"degraded", "stopped", "updating", "recovering", "uninstalling", "failed"},
    "degraded": {"running", "stopped", "updating", "recovering", "uninstalling", "failed"},
    "stopped": {"starting", "running", "updating", "uninstalling", "archived"},
    "updating": {"installed", "running", "degraded", "failed", "recovering"},
    "recovering": {"running", "degraded", "failed", "stopped"},
    "failed": {"installing", "updating", "recovering", "stopped", "uninstalling", "archived"},
    "uninstalling": {"archived", "failed"},
    "archived": {"draft", "installing"},
}


class HomeServerAppError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _loads(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _public(row) -> dict[str, Any] | None:  # noqa: ANN001
    if row is None:
        return None
    item = dict(row)
    item["protected_system_app"] = bool(item.get("protected_system_app"))
    item["metadata"] = _loads(item.pop("metadata_json", "{}"))
    return item


def sync_legacy_local_apps() -> int:
    with db() as connection:
        rows = connection.execute(
            "SELECT app_key,name,installed_version,status,source_label,last_error FROM local_apps"
        ).fetchall()
        changed = 0
        for row in rows:
            existing = connection.execute(
                "SELECT app_id FROM homeserver_apps WHERE app_key=?",
                (row["app_key"],),
            ).fetchone()
            state = {"installing":"installing","installed":"running","updating":"updating","failed":"failed"}.get(str(row["status"] or ""), "installed")
            metadata = {"legacy_local_app": True, "legacy_source_label": str(row["source_label"] or "")}
            if row["last_error"]:
                metadata["last_error"] = str(row["last_error"])[:1200]
            if existing is None:
                app_id = "app_" + uuid.uuid4().hex
                connection.execute(
                    """
                    INSERT INTO homeserver_apps(
                        app_id,app_key,name,app_class,source_type,lifecycle_state,
                        installed_version,desired_version,source_ref,owner_key,
                        protected_system_app,metadata_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        app_id,row["app_key"],row["name"],"system","vp3_system",state,
                        row["installed_version"],row["installed_version"],str(row["source_label"] or ""),
                        "local_owner",1,json.dumps(metadata,separators=(",",":"),sort_keys=True),
                    ),
                )
                connection.execute(
                    """INSERT INTO homeserver_app_events(app_id,event_type,to_state,actor_type,actor_key,metadata_json)
                       VALUES (?,?,?,'system','local_apps_v040',?)""",
                    (app_id,"app.migrated",state,json.dumps(metadata,separators=(",",":"),sort_keys=True)),
                )
                changed += 1
            else:
                connection.execute(
                    """UPDATE homeserver_apps SET name=?,installed_version=?,desired_version=?,
                       lifecycle_state=?,source_type='vp3_system',app_class='system',
                       protected_system_app=1,updated_at=CURRENT_TIMESTAMP WHERE app_key=?""",
                    (row["name"],row["installed_version"],row["installed_version"],state,row["app_key"]),
                )
        return changed



def ensure_system_app(
    app_key:str,
    name:str,
    *,
    source_ref:str="vp3-prebuilt",
    metadata:dict[str,Any]|None=None,
)->dict[str,Any]:
    key=str(app_key or "").strip().lower()
    label=str(name or "").strip()
    if not _KEY_RE.fullmatch(key):
        raise HomeServerAppError("System app key is invalid.")
    if not label or len(label)>160:
        raise HomeServerAppError("System app name is invalid.")
    merged=dict(metadata or {})
    merged.update({"vp3_managed":True,"prebuilt_app":True})
    with db() as connection:
        row=connection.execute("SELECT * FROM homeserver_apps WHERE app_key=?",(key,)).fetchone()
        if row is not None:
            if str(row["app_class"])!="system":
                raise HomeServerAppError("A user app already uses this VP3 system app key.",409)
            existing=_loads(row["metadata_json"])
            existing.update(merged)
            connection.execute(
                """UPDATE homeserver_apps SET name=?,source_type='vp3_system',source_ref=?,
                   protected_system_app=1,metadata_json=?,updated_at=CURRENT_TIMESTAMP WHERE app_id=?""",
                (label,str(source_ref or "")[:500],json.dumps(existing,separators=(",",":"),sort_keys=True),row["app_id"]),
            )
            return get(key)
        app_id="app_"+uuid.uuid4().hex
        connection.execute(
            """INSERT INTO homeserver_apps(
                app_id,app_key,name,app_class,source_type,lifecycle_state,
                source_ref,owner_key,protected_system_app,metadata_json
            ) VALUES (?,?,?,'system','vp3_system','draft',?,'vp3_system',1,?)""",
            (app_id,key,label,str(source_ref or "")[:500],json.dumps(merged,separators=(",",":"),sort_keys=True)),
        )
        connection.execute(
            """INSERT INTO homeserver_app_events(app_id,event_type,to_state,actor_type,actor_key,metadata_json)
               VALUES (?, 'app.system.registered','draft','system','vp3_prebuilt',?)""",
            (app_id,json.dumps({"source_ref":source_ref},separators=(",",":"),sort_keys=True)),
        )
    return get(key)


def register_user_app(
    app_key: str,
    name: str,
    *,
    source_type: str = "user_created",
    source_ref: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    key = str(app_key or "").strip().lower()
    label = str(name or "").strip()
    source = str(source_type or "user_created").strip()
    if not _KEY_RE.fullmatch(key):
        raise HomeServerAppError("app_key must be 2-80 characters using lowercase letters, numbers, dots, underscores, or hyphens.")
    if not label or len(label) > 160:
        raise HomeServerAppError("name is required and must be at most 160 characters.")
    if source not in {"user_created", "zip", "git", "agent_builder"}:
        raise HomeServerAppError("User apps must use a supported local source type.")
    app_id = "app_" + uuid.uuid4().hex
    with db() as connection:
        exists = connection.execute("SELECT 1 FROM homeserver_apps WHERE app_key=?", (key,)).fetchone()
        if exists is not None:
            raise HomeServerAppError("An app with this key already exists.", 409)
        connection.execute(
            """INSERT INTO homeserver_apps(
                app_id,app_key,name,app_class,source_type,lifecycle_state,
                source_ref,owner_key,protected_system_app,metadata_json
            ) VALUES (?,?,?,?,?,'draft',?,'local_owner',0,?)""",
            (app_id,key,label,"user",source,str(source_ref or "")[:500],json.dumps(metadata or {},separators=(",",":"),sort_keys=True)),
        )
        connection.execute(
            """INSERT INTO homeserver_app_events(app_id,event_type,to_state,metadata_json)
               VALUES (?, 'app.registered', 'draft', ?)""",
            (app_id,json.dumps({"source_type":source},separators=(",",":"),sort_keys=True)),
        )
    return get(key)



def create_user_app(app_key: str, name: str, *, runtime: str="static", source_type: str="user_created", metadata: dict[str,Any]|None=None, permissions:list[str]|None=None) -> dict[str,Any]:
    source=str(source_type or "user_created").strip()
    if source not in {"user_created","agent_builder"}:
        raise HomeServerAppError("Create App uses the VP3 SDK and supports user_created or agent_builder sources.")
    try:
        scaffold=homeserver_app_sdk.scaffold(app_key,name,runtime=runtime,permissions=permissions)
    except homeserver_app_sdk.AppSdkError as exc:
        raise HomeServerAppError(str(exc),409 if "already exists" in str(exc).lower() else 400) from exc
    combined=dict(metadata or {})
    combined.update({"sdk_version":scaffold["sdk_version"],"runtime":runtime,"sdk_scaffolded":True})
    try:
        app=register_user_app(app_key,name,source_type=source,metadata=combined)
    except Exception:
        homeserver_app_sdk.remove_project(app_key)
        raise
    return {"app":app,"sdk":scaffold}

def get(app_key: str) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute("SELECT * FROM homeserver_apps WHERE app_key=?", (str(app_key or "").strip().lower(),)).fetchone()
    if row is None:
        raise HomeServerAppError("App not found.",404)
    return _public(row) or {}


def list_apps() -> dict[str, Any]:
    sync_legacy_local_apps()
    with db() as connection:
        rows = connection.execute(
            "SELECT * FROM homeserver_apps ORDER BY app_class,name,app_key"
        ).fetchall()
    apps=[_public(row) for row in rows]
    return {
        "contract":CONTRACT,
        "apps":apps,
        "counts":{
            "system":sum(1 for item in apps if item and item["app_class"]=="system"),
            "user":sum(1 for item in apps if item and item["app_class"]=="user"),
        },
        "future_sources":["app_store"],
    }


def transition(app_key: str, target_state: str, *, actor_type: str="owner", actor_key: str="local_owner", metadata: dict[str,Any]|None=None) -> dict[str,Any]:
    target=str(target_state or "").strip()
    if target not in LIFECYCLE_STATES:
        raise HomeServerAppError("Unsupported app lifecycle state.")
    with db() as connection:
        row=connection.execute("SELECT * FROM homeserver_apps WHERE app_key=?",(str(app_key or "").strip().lower(),)).fetchone()
        if row is None:
            raise HomeServerAppError("App not found.",404)
        current=str(row["lifecycle_state"])
        if target==current:
            return _public(row) or {}
        if target not in _TRANSITIONS.get(current,set()):
            raise HomeServerAppError(f"Invalid app lifecycle transition: {current} -> {target}.",409)
        connection.execute(
            "UPDATE homeserver_apps SET lifecycle_state=?,updated_at=CURRENT_TIMESTAMP WHERE app_id=?",
            (target,row["app_id"]),
        )
        connection.execute(
            """INSERT INTO homeserver_app_events(app_id,event_type,from_state,to_state,actor_type,actor_key,metadata_json)
               VALUES (?,?,?,?,?,?,?)""",
            (
                row["app_id"],"app.lifecycle.changed",current,target,actor_type[:40],actor_key[:120],
                json.dumps(metadata or {},separators=(",",":"),sort_keys=True),
            ),
        )
    return get(str(app_key))


def archive_user_app(app_key:str)->dict[str,Any]:
    app=get(app_key)
    if app["app_class"]!="user" or app["protected_system_app"]:
        raise HomeServerAppError("System apps cannot be archived from the user app manager.",409)
    current=str(app["lifecycle_state"])
    if current=="archived":
        return app
    if current not in {"draft","installed","running","degraded","stopped","failed"}:
        raise HomeServerAppError("App is busy with another lifecycle operation.",409)
    if current in {"running","degraded","installed"}:
        app=transition(app_key,"stopped",metadata={"reason":"owner_archive"})
        current="stopped"
    if current=="draft":
        return transition(app_key,"archived",metadata={"reason":"owner_archive"})
    if current in {"stopped","failed"}:
        return transition(app_key,"archived",metadata={"reason":"owner_archive"})
    raise HomeServerAppError("App could not be archived from its current state.",409)


def resume_user_app(app_key:str)->dict[str,Any]:
    app=get(app_key)
    if app["app_class"]!="user":
        raise HomeServerAppError("System apps are managed by VP3.",409)
    current=str(app["lifecycle_state"])
    if current=="running":
        return app
    if current=="archived":
        app=transition(app_key,"draft",metadata={"reason":"owner_restore"})
        return app
    if current=="stopped":
        return transition(app_key,"running",metadata={"reason":"owner_start"})
    if current=="installed":
        return transition(app_key,"running",metadata={"reason":"owner_start"})
    raise HomeServerAppError("App cannot be started from its current state.",409)

def history(app_key: str, limit: int=100) -> list[dict[str,Any]]:
    app=get(app_key)
    with db() as connection:
        rows=connection.execute(
            """SELECT id,event_type,from_state,to_state,actor_type,actor_key,metadata_json,created_at
               FROM homeserver_app_events WHERE app_id=? ORDER BY id DESC LIMIT ?""",
            (app["app_id"],max(1,min(500,int(limit)))),
        ).fetchall()
    out=[]
    for row in rows:
        item=dict(row)
        item["metadata"]=_loads(item.pop("metadata_json","{}"))
        out.append(item)
    return out


def public_capability() -> dict[str,Any]:
    return {
        "contract":CONTRACT,
        "canonical_registry":True,
        "system_apps":True,
        "user_apps":True,
        "legacy_local_apps_migrate_in_place":True,
        "user_app_sources":["user_created","zip","git","agent_builder"],
        "user_app_default":"vp3_sdk_1.2",
        "starter_runtime_integration":True,
        "starter_permissions_section8":True,
        "starter_data_recovery_section7":True,
        "app_store":"future",
        "lifecycle_states":sorted(LIFECYCLE_STATES),
    }
