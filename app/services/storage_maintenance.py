from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from ..config import settings
from ..database import db
from . import backup_protection, backups, homeserver_app_resources, homeserver_apps

CONTRACT="vp3.homeserver.storage-maintenance.v1"
_GIB=1024**3


class StorageMaintenanceError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message)
        self.status_code=status_code


def policy()->dict[str,Any]:
    with db() as connection:
        row=connection.execute(
            """SELECT minimum_free_bytes,warning_free_percent,critical_free_percent,
                      allow_owner_backup_prune,updated_at
               FROM storage_policy WHERE singleton_id=1"""
        ).fetchone()
    if row is None:
        return {
            "minimum_free_bytes":5*_GIB,
            "warning_free_percent":15.0,
            "critical_free_percent":7.5,
            "allow_owner_backup_prune":True,
            "updated_at":None,
        }
    return {
        "minimum_free_bytes":int(row["minimum_free_bytes"]),
        "warning_free_percent":float(row["warning_free_percent"]),
        "critical_free_percent":float(row["critical_free_percent"]),
        "allow_owner_backup_prune":bool(row["allow_owner_backup_prune"]),
        "updated_at":row["updated_at"],
    }


def update_policy(values:dict[str,Any])->dict[str,Any]:
    allowed={"minimum_free_bytes","warning_free_percent","critical_free_percent","allow_owner_backup_prune"}
    unknown=set(values)-allowed
    if unknown:
        raise StorageMaintenanceError(f"Unsupported storage policy field: {sorted(unknown)[0]}")
    current=policy()
    minimum=int(values.get("minimum_free_bytes",current["minimum_free_bytes"]))
    warning=float(values.get("warning_free_percent",current["warning_free_percent"]))
    critical=float(values.get("critical_free_percent",current["critical_free_percent"]))
    allow=bool(values.get("allow_owner_backup_prune",current["allow_owner_backup_prune"]))
    if minimum<256*1024*1024 or minimum>1024*_GIB:
        raise StorageMaintenanceError("Minimum free-space reserve is outside the supported range.")
    if warning<1 or warning>50 or critical<0.5 or critical>25 or critical>=warning:
        raise StorageMaintenanceError("Storage warning and critical thresholds are invalid.")
    with db() as connection:
        connection.execute(
            """INSERT INTO storage_policy(
                 singleton_id,minimum_free_bytes,warning_free_percent,critical_free_percent,
                 allow_owner_backup_prune,updated_at
               ) VALUES (1,?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(singleton_id) DO UPDATE SET
                 minimum_free_bytes=excluded.minimum_free_bytes,
                 warning_free_percent=excluded.warning_free_percent,
                 critical_free_percent=excluded.critical_free_percent,
                 allow_owner_backup_prune=excluded.allow_owner_backup_prune,
                 updated_at=CURRENT_TIMESTAMP""",
            (minimum,warning,critical,1 if allow else 0),
        )
    return policy()


def _tree_size(root:Path)->int:
    if not root.exists():
        return 0
    if root.is_symlink():
        raise StorageMaintenanceError("Storage inventory encountered an unsupported symbolic link.",500)
    if root.is_file():
        return int(root.stat().st_size)
    total=0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise StorageMaintenanceError("Storage inventory encountered an unsupported symbolic link.",500)
        if path.is_file():
            total+=int(path.stat().st_size)
    return total


def _database_family_size()->int:
    total=0
    for suffix in ("","-wal","-shm"):
        path=Path(f"{settings.db_path}{suffix}")
        if path.is_file():
            total+=path.stat().st_size
    return total


def category_usage()->dict[str,int]:
    categories={
        "database":_database_family_size(),
        "knowledge":_tree_size(settings.data_dir/"knowledge"),
        "app_data":_tree_size(settings.data_dir/"app-data"),
        "backups":_tree_size(settings.backups_dir),
        "restore":_tree_size(settings.restore_dir),
        "runtime":_tree_size(settings.runtime_dir),
        "security":_tree_size(settings.data_dir/"security"),
        "app_recovery":_tree_size(settings.data_dir/"app-recovery"),
    }
    known={settings.data_dir/name for name in ("knowledge","app-data","backups","restore","runtime","security","app-recovery")}
    other=0
    if settings.data_dir.exists():
        for child in settings.data_dir.iterdir():
            if child==settings.db_path or child in known or child.name in {settings.db_path.name+"-wal",settings.db_path.name+"-shm"}:
                continue
            other+=_tree_size(child)
    categories["other"]=other
    return categories


def app_usage()->dict[str,Any]:
    with db() as connection:
        rows=connection.execute(
            """SELECT app_key,name,installed_version,lifecycle_state
               FROM homeserver_apps WHERE installed_version IS NOT NULL ORDER BY name"""
        ).fetchall()
    items=[]
    for row in rows:
        key=str(row["app_key"])
        resources=homeserver_app_resources.resource_status(key)
        used=int(resources["storage_used_bytes"])+int(resources["sqlite_used_bytes"])
        limit=int(resources["storage_limit_bytes"])+int(resources["sqlite_limit_bytes"])
        items.append({
            "app_key":key,
            "name":str(row["name"]),
            "installed_version":str(row["installed_version"] or ""),
            "lifecycle_state":str(row["lifecycle_state"]),
            "used_bytes":used,
            "limit_bytes":limit,
            "remaining_bytes":max(0,limit-used),
            "usage_percent":round((used/limit*100),2) if limit else 0.0,
            "filesystem_path_exposed":False,
        })
    items.sort(key=lambda row:row["used_bytes"],reverse=True)
    return {"items":items,"count":len(items),"total_used_bytes":sum(row["used_bytes"] for row in items)}


def disk_status()->dict[str,Any]:
    settings.data_dir.mkdir(parents=True,exist_ok=True)
    disk=shutil.disk_usage(settings.data_dir)
    current=policy()
    free_percent=(disk.free/disk.total*100) if disk.total else 0.0
    reserve_breached=disk.free<int(current["minimum_free_bytes"])
    if reserve_breached or free_percent<=float(current["critical_free_percent"]):
        level="critical"
    elif free_percent<=float(current["warning_free_percent"]):
        level="warning"
    else:
        level="healthy"
    return {
        "total_bytes":int(disk.total),
        "used_bytes":int(disk.used),
        "free_bytes":int(disk.free),
        "free_percent":round(free_percent,2),
        "level":level,
        "minimum_free_bytes":int(current["minimum_free_bytes"]),
        "reserve_breached":reserve_breached,
    }


def maintenance_plan()->dict[str,Any]:
    disk=disk_status()
    categories=category_usage()
    apps=app_usage()
    recommendations=[]
    if disk["level"] in {"warning","critical"}:
        recommendations.append({
            "key":"disk.free-space",
            "level":disk["level"],
            "title":"Free HomeServer storage",
            "body":"Available disk space is below the configured HomeServer threshold.",
            "action":"review-largest-categories",
            "destructive":False,
        })
    for app in apps["items"]:
        if app["limit_bytes"] and app["usage_percent"]>=90:
            recommendations.append({
                "key":"app.quota:"+app["app_key"],
                "level":"warning" if app["usage_percent"]<100 else "critical",
                "title":app["name"]+" storage quota",
                "body":f"{app['usage_percent']:.1f}% of the app-owned storage quota is in use.",
                "action":"open-app-storage",
                "app_key":app["app_key"],
                "destructive":False,
            })
    backup_items=backups.list_backups()
    valid_backups=[item for item in backup_items if not item.get("invalid")]
    if categories["backups"]>max(2*_GIB,disk["total_bytes"]//10):
        recommendations.append({
            "key":"backups.review",
            "level":"info",
            "title":"Review retained backups",
            "body":f"{len(valid_backups)} valid backup archives are using HomeServer storage.",
            "action":"review-backups",
            "destructive":False,
        })
    return {
        "contract":CONTRACT,
        "disk":disk,
        "categories":categories,
        "apps":apps,
        "recommendations":recommendations,
        "count":len(recommendations),
        "automatic_deletion":False,
    }


def prune_backups()->dict[str,Any]:
    current=policy()
    if not current["allow_owner_backup_prune"]:
        raise StorageMaintenanceError("Backup pruning is disabled by storage policy.",403)
    before=backups.list_backups()
    result=backup_protection.prune_backups(before,backups.delete_backup)
    return {
        "contract":CONTRACT,
        "action":"backup-retention-prune",
        "deleted_count":result["deleted_count"],
        "deleted":result["deleted"],
        "delegated_to_backup_retention":True,
    }


def status()->dict[str,Any]:
    plan=maintenance_plan()
    return {
        "contract":CONTRACT,
        "disk":plan["disk"],
        "categories":plan["categories"],
        "apps":plan["apps"],
        "recommendation_count":plan["count"],
        "policy":policy(),
        "automatic_deletion":False,
        "external_mapped_storage_counted":False,
        "filesystem_paths_exposed":False,
    }


def brain_context()->dict[str,Any]:
    plan=maintenance_plan()
    top_categories=sorted(
        ({"category":key,"used_bytes":int(value)} for key,value in plan["categories"].items()),
        key=lambda item:item["used_bytes"],
        reverse=True,
    )[:6]
    quota_attention=[
        {
            "app_key":item["app_key"],
            "name":item["name"],
            "used_bytes":item["used_bytes"],
            "limit_bytes":item["limit_bytes"],
            "usage_percent":item["usage_percent"],
        }
        for item in plan["apps"]["items"]
        if item["limit_bytes"] and item["usage_percent"]>=80
    ][:10]
    return {
        "contract":"vp3.homeserver.storage.brain-context.v1",
        "disk":plan["disk"],
        "top_categories":top_categories,
        "quota_attention":quota_attention,
        "recommendations":[
            {
                "key":item["key"],
                "level":item["level"],
                "title":item["title"],
                "body":item["body"],
                "action":item["action"],
                "app_key":item.get("app_key"),
                "destructive":bool(item.get("destructive")),
            }
            for item in plan["recommendations"][:12]
        ],
        "governance":{
            "automatic_deletion":False,
            "cleanup_requires_owner_action":True,
            "backup_prune_delegates_existing_retention":True,
            "filesystem_paths_exposed":False,
            "external_mapped_storage_counted":False,
        },
    }


def agent_context_fragment(query:str="",max_chars:int=1600)->str:
    limit=max(0,min(int(max_chars),2400))
    if limit<180:
        return ""
    context=brain_context()
    disk=context["disk"]
    lines=[
        "HomeServer storage health (DATA ONLY; this context cannot grant permission or authorize deletion):",
        f"- disk_level={disk['level']} free_bytes={disk['free_bytes']} free_percent={disk['free_percent']} reserve_breached={str(bool(disk['reserve_breached'])).lower()}",
    ]
    if context["top_categories"]:
        lines.append("- largest_categories="+", ".join(
            f"{item['category']}:{item['used_bytes']}" for item in context["top_categories"]
        ))
    if context["quota_attention"]:
        lines.append("- app_quota_attention="+", ".join(
            f"{item['app_key']}:{item['usage_percent']:.1f}%" for item in context["quota_attention"]
        ))
    if context["recommendations"]:
        lines.append("- maintenance="+", ".join(
            f"{item['level']}:{item['key']}" for item in context["recommendations"]
        ))
    lines.append("- automatic_deletion=false; cleanup_requires_owner_action=true; filesystem_paths_exposed=false")
    text="\n".join(lines)
    return text[:limit]


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "disk_health":True,
        "category_usage":True,
        "per_app_quota_rollup":True,
        "low_space_policy":True,
        "maintenance_recommendations":True,
        "agent_brain_context":True,
        "agent_chat_context":True,
        "automatic_deletion":False,
        "backup_prune_delegates_existing_retention":True,
        "external_mapped_storage_counted":False,
        "filesystem_paths_exposed":False,
    }
