from __future__ import annotations

from .homeserver_app_locks import serialized

import json
import hashlib
import math
import re
from pathlib import Path
from typing import Any

from ..database import db
from . import homeserver_app_packages, homeserver_app_runtime, homeserver_app_security, homeserver_apps

CONTRACT="vp3.app.agent-control.v3"
MANIFEST_CONTRACTS={"vp3.app.agent-actions.v1","vp3.app.agent-actions.v2"}
RISK_LEVELS={"read","write","destructive","consequential","background","admin"}
_ACTION_KEY=re.compile(r"^[a-z][a-z0-9_.-]{2,119}$")


class AppControlError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message)
        self.status_code=status_code


def _safe_rel(value:str)->Path:
    raw=str(value or "").replace("\\","/").strip()
    rel=Path(raw)
    if not raw or rel.is_absolute() or ".." in rel.parts:
        raise AppControlError("Agent action manifest path is invalid.")
    return rel


def validate_action_manifest(content_root:Path,app_key:str,manifest_path:str)->dict[str,Any]:
    root=content_root.resolve()
    target=(root/_safe_rel(manifest_path)).resolve()
    if root not in target.parents or not target.is_file():
        raise AppControlError("Agent action manifest is unavailable.")
    try:
        payload=json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AppControlError("Agent action manifest is invalid JSON.") from exc
    if not isinstance(payload,dict) or payload.get("contract") not in MANIFEST_CONTRACTS:
        raise AppControlError("Agent action manifest contract is unsupported.")
    manifest_contract=str(payload.get("contract") or "")
    raw=payload.get("actions",[])
    if not isinstance(raw,list) or len(raw)>128:
        raise AppControlError("Agent action manifest actions are invalid.")
    actions=[]
    seen=set()
    for row in raw:
        if not isinstance(row,dict):
            raise AppControlError("Agent action definition must be an object.")
        key=str(row.get("key") or "").strip()
        if not _ACTION_KEY.fullmatch(key) or key in seen:
            raise AppControlError("Agent action key is invalid or duplicated.")
        seen.add(key)
        risk=str(row.get("risk") or "write").strip().lower()
        if risk not in RISK_LEVELS:
            raise AppControlError(f"Agent action {key} has an unsupported risk class.")
        confirmation=True if risk in {"destructive","consequential","admin"} else bool(row.get("requires_confirmation",False))
        schema=row.get("input_schema",{"type":"object","properties":{},"additionalProperties":False})
        if not isinstance(schema,dict) or schema.get("type")!="object":
            raise AppControlError(f"Agent action {key} input_schema must be an object schema.")
        properties=schema.get("properties",{})
        required=schema.get("required",[])
        if not isinstance(properties,dict) or not isinstance(required,list) or any(not isinstance(x,str) for x in required):
            raise AppControlError(f"Agent action {key} input_schema properties/required are invalid.")
        if any(name not in properties for name in required):
            raise AppControlError(f"Agent action {key} input_schema requires an undefined property.")
        if schema.get("additionalProperties",False) not in {True,False}:
            raise AppControlError(f"Agent action {key} additionalProperties must be boolean.")
        allowed_types={"string","integer","number","boolean","object","array","null"}
        for prop_name,prop in properties.items():
            if not isinstance(prop,dict):
                raise AppControlError(f"Agent action {key} property {prop_name} schema is invalid.")
            declared=prop.get("type")
            declared_types=list(declared) if isinstance(declared,list) else [declared] if declared else []
            if any(t not in allowed_types for t in declared_types):
                raise AppControlError(f"Agent action {key} property {prop_name} type is unsupported.")
            enum=prop.get("enum")
            if enum is not None and not isinstance(enum,list):
                raise AppControlError(f"Agent action {key} property {prop_name} enum is invalid.")
            pattern=str(prop.get("pattern") or "")
            if pattern:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise AppControlError(f"Agent action {key} property {prop_name} pattern is invalid.") from exc
        executor=row.get("executor")
        if executor is None and manifest_contract=="vp3.app.agent-actions.v2":
            raise AppControlError(f"Agent action {key} requires an executor in v2 manifests.")
        if executor is not None:
            if not isinstance(executor,dict):
                raise AppControlError(f"Agent action {key} executor must be an object.")
            kind=str(executor.get("type") or "")
            if kind not in {"runtime.status","settings.read","event.emit","job.run","builtin"}:
                raise AppControlError(f"Agent action {key} executor type is unsupported.")
            if kind=="event.emit":
                topic=str(executor.get("topic") or "").strip()
                if not topic:
                    raise AppControlError(f"Agent action {key} event topic is required.")
            elif kind=="job.run":
                job_id=str(executor.get("job_id") or "").strip()
                if not job_id:
                    raise AppControlError(f"Agent action {key} job_id is required.")
            elif kind=="builtin":
                provider=str(executor.get("provider") or "").strip()
                if not provider:
                    raise AppControlError(f"Agent action {key} builtin provider is required.")
            if risk=="read" and kind in {"event.emit","job.run"}:
                raise AppControlError(f"Read action {key} may not use a mutating executor.")
        actions.append({
            "key":key,
            "name":str(row.get("name") or key)[:160],
            "description":str(row.get("description") or "")[:1000],
            "risk":risk,
            "requires_confirmation":confirmation,
            "input_schema":schema,
            "executor":executor,
            "executable":executor is not None,
        })
    return {
        "contract":CONTRACT,
        "manifest_contract":manifest_contract,
        "app_key":app_key,
        "actions":actions,
        "count":len(actions),
        "compatible":all(bool(row.get("executable")) for row in actions),
    }


def manifest(app_key:str)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    if not app.get("installed_version"):
        raise AppControlError("App is not installed.",409)
    metadata=dict(app.get("metadata") or {})
    manifest_path=str(metadata.get("agent_actions") or "agent/actions.json")
    try:
        root=homeserver_app_packages.active_content_root(app_key)
        return validate_action_manifest(root,app_key,manifest_path)
    except homeserver_app_packages.AppPackageError as exc:
        raise AppControlError(str(exc),exc.status_code) from exc


def action_spec(app_key:str,action_key:str)->dict[str,Any]:
    key=str(action_key or "").strip()
    for row in manifest(app_key)["actions"]:
        if row["key"]==key:
            return row
    raise AppControlError("App action not found.",404)



def _validate_arguments(spec:dict[str,Any],arguments:dict[str,Any])->dict[str,Any]:
    schema=dict(spec.get("input_schema") or {})
    properties=dict(schema.get("properties") or {})
    required=list(schema.get("required") or [])
    for name in required:
        if name not in arguments:
            raise AppControlError(f"App action argument is required: {name}.")
    if not bool(schema.get("additionalProperties",False)):
        unknown=set(arguments)-set(properties)
        if unknown:
            raise AppControlError(f"Unsupported app action argument: {sorted(unknown)[0]}.")
    for name,value in arguments.items():
        definition=properties.get(name)
        if not isinstance(definition,dict):
            continue
        expected=definition.get("type")
        allowed=list(expected) if isinstance(expected,list) else [expected] if expected else []
        valid=False
        for kind in allowed:
            if kind=="null" and value is None: valid=True
            elif kind=="string" and isinstance(value,str): valid=True
            elif kind=="boolean" and isinstance(value,bool): valid=True
            elif kind=="integer" and isinstance(value,int) and not isinstance(value,bool): valid=True
            elif kind=="number" and isinstance(value,(int,float)) and not isinstance(value,bool): valid=True
            elif kind=="object" and isinstance(value,dict): valid=True
            elif kind=="array" and isinstance(value,list): valid=True
        if allowed and not valid:
            raise AppControlError(f"App action argument {name} has the wrong type.")
        if "enum" in definition and value not in list(definition.get("enum") or []):
            raise AppControlError(f"App action argument {name} is not an allowed value.")
        if isinstance(value,str):
            if "minLength" in definition and len(value)<int(definition["minLength"]):
                raise AppControlError(f"App action argument {name} is too short.")
            if "maxLength" in definition and len(value)>int(definition["maxLength"]):
                raise AppControlError(f"App action argument {name} is too long.")
            pattern=str(definition.get("pattern") or "")
            if pattern:
                try:
                    if re.fullmatch(pattern,value) is None:
                        raise AppControlError(f"App action argument {name} does not match its pattern.")
                except re.error as exc:
                    raise AppControlError(f"App action argument {name} has an invalid schema pattern.") from exc
        if isinstance(value,float) and not math.isfinite(value):
            raise AppControlError(f"App action argument {name} must be finite.")
        if isinstance(value,(int,float)) and not isinstance(value,bool):
            if "minimum" in definition and value<float(definition["minimum"]):
                raise AppControlError(f"App action argument {name} is below its minimum.")
            if "maximum" in definition and value>float(definition["maximum"]):
                raise AppControlError(f"App action argument {name} is above its maximum.")
    return dict(arguments)


def _builtin(app_key:str,provider:str,action_key:str,arguments:dict[str,Any])->Any:
    if provider=="video_editor":
        from . import homeserver_video_editor
        if app_key!=homeserver_video_editor.APP_KEY:
            raise AppControlError("Builtin action provider does not match the app.",409)
        return homeserver_video_editor.invoke(action_key,arguments)
    if provider=="music_server":
        from . import homeserver_music_server
        if app_key!=homeserver_music_server.APP_KEY:
            raise AppControlError("Builtin action provider does not match the app.",409)
        return homeserver_music_server.invoke(action_key,arguments)
    if provider=="photo_library":
        from . import homeserver_photo_library
        if app_key!=homeserver_photo_library.APP_KEY:
            raise AppControlError("Builtin action provider does not match the app.",409)
        return homeserver_photo_library.invoke(action_key,arguments)
    if provider=="download_manager":
        from . import homeserver_download_manager
        if app_key!=homeserver_download_manager.APP_KEY:
            raise AppControlError("Builtin action provider does not match the app.",409)
        return homeserver_download_manager.invoke(action_key,arguments)
    if provider=="media_processor":
        from . import homeserver_media_processor
        if app_key!=homeserver_media_processor.APP_KEY:
            raise AppControlError("Builtin action provider does not match the app.",409)
        return homeserver_media_processor.invoke(action_key,arguments)
    if provider=="media_library":
        from . import homeserver_media_library
        if app_key!=homeserver_media_library.APP_KEY:
            raise AppControlError("Builtin action provider does not match the app.",409)
        return homeserver_media_library.invoke(action_key,arguments)
    if provider=="media_player":
        from . import homeserver_media_player
        if app_key!=homeserver_media_player.APP_KEY:
            raise AppControlError("Builtin action provider does not match the app.",409)
        return homeserver_media_player.invoke(action_key,arguments)
    if provider=="media_server":
        from . import homeserver_media_server
        if app_key!=homeserver_media_server.APP_KEY:
            raise AppControlError("Builtin action provider does not match the app.",409)
        action=str(action_key or "")
        if action=="media.roots.list": return homeserver_media_server.roots()
        if action=="media.scan": return homeserver_media_server.scan(str(arguments.get("root_id") or ""))
        if action=="media.root.check": return homeserver_media_server.check_root(str(arguments.get("root_id") or ""))
        if action=="media.root.map":
            return homeserver_media_server.add_mapped_root(
                str(arguments.get("path") or ""),
                str(arguments.get("label") or ""),
                computer_name=str(arguments.get("computer_name") or ""),
                source_hint=str(arguments.get("source_hint") or ""),
                source_kind=str(arguments.get("source_kind") or "computer_folder"),
            )
        if action=="media.root.remove": return homeserver_media_server.remove_root(str(arguments.get("root_id") or ""))
        if action=="media.process":
            return homeserver_media_server.process_media(
                str(arguments.get("media_id") or ""),str(arguments.get("operation") or ""),
                str(arguments.get("preset") or "default"),str(arguments.get("output_format") or ""),
                int(arguments.get("priority",0)),
            )
        raise AppControlError("Unsupported Media Server builtin action.",404)
    raise AppControlError("Builtin app action provider is unavailable.",501)


def action_binding(app_key:str,spec:dict[str,Any])->str:
    state=homeserver_app_packages._read_state(app_key)
    encoded=json.dumps({"release_id":state.get("active_release_id"),"action":spec},sort_keys=True,separators=(",",":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@serialized
def invoke(app_key:str,action_key:str,arguments:dict[str,Any]|None=None,*,confirmed:bool=False,read_only:bool=False,expected_binding:str|None=None)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    if app.get("lifecycle_state")!="running":
        raise AppControlError("App must be running before invoking an app action.",409)
    spec=action_spec(app_key,action_key)
    if read_only and (spec["risk"]!="read" or spec["requires_confirmation"]):
        raise AppControlError("Use the governed Apps action path for non-read actions.",409)
    if spec["requires_confirmation"] and confirmed is not True:
        raise AppControlError("This app action requires owner confirmation.",409)
    if expected_binding is not None and expected_binding!=action_binding(app_key,spec):
        raise AppControlError("App action changed since approval was requested; request a new approval.",409)
    executor=spec.get("executor")
    if not isinstance(executor,dict):
        raise AppControlError("App action has no executable handler.",409)
    args=_validate_arguments(spec,dict(arguments or {}))
    kind=str(executor.get("type") or "")
    if kind=="runtime.status":
        if args:
            raise AppControlError("runtime.status actions do not accept arguments.")
        result=homeserver_app_runtime.runtime_status(app_key)
    elif kind=="settings.read":
        if args:
            raise AppControlError("settings.read actions do not accept arguments.")
        result=settings(app_key)
    elif kind=="event.emit":
        base=dict(executor.get("payload") or {})
        base.update(args)
        result=homeserver_app_runtime.publish_event(
            app_key,str(executor.get("topic") or ""),base,source="agent"
        )
    elif kind=="job.run":
        result=homeserver_app_runtime.run_job(app_key,str(executor.get("job_id") or ""))
    elif kind=="builtin":
        result=_builtin(app_key,str(executor.get("provider") or ""),action_key,args)
    else:
        raise AppControlError("App action executor is unsupported.")
    with db() as connection:
        app_row=connection.execute(
            "SELECT app_id FROM homeserver_apps WHERE app_key=?",
            (app_key,),
        ).fetchone()
        if app_row is not None:
            connection.execute(
                """INSERT INTO homeserver_app_events(
                     app_id,event_type,actor_type,actor_key,metadata_json
                   ) VALUES (?, 'app.action.invoked','system','universal-app-control',?)""",
                (
                    str(app_row["app_id"]),
                    json.dumps(
                        {"action":action_key,"risk":str(spec["risk"])},
                        separators=(",",":"),
                        sort_keys=True,
                    ),
                ),
            )
    return {
        "contract":"vp3.app.agent-action-result.v1",
        "app_key":app_key,
        "action":action_key,
        "risk":spec["risk"],
        "result":result,
    }



def validate_settings_schema(content_root:Path,app_key:str,schema_path:str)->dict[str,Any]:
    root=content_root.resolve()
    target=(root/_safe_rel(schema_path)).resolve()
    if root not in target.parents or not target.is_file():
        raise AppControlError("App settings schema is unavailable.",409)
    try:
        payload=json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AppControlError("App settings schema is invalid JSON.") from exc
    if not isinstance(payload,dict) or payload.get("contract")!="vp3.app.settings-schema.v1":
        raise AppControlError("App settings schema contract is unsupported.")
    fields=payload.get("fields",[])
    if not isinstance(fields,list) or len(fields)>128:
        raise AppControlError("App settings fields are invalid.")
    normalized=[]
    seen=set()
    secret_names=set()
    for row in fields:
        if not isinstance(row,dict):
            raise AppControlError("App setting definition must be an object.")
        key=str(row.get("key") or "").strip()
        if not re.fullmatch(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$",key) or key in seen:
            raise AppControlError("App setting key is invalid or duplicated.")
        seen.add(key)
        kind=str(row.get("type") or "string").strip().lower()
        if kind not in {"string","integer","number","boolean"}:
            raise AppControlError(f"App setting {key} has an unsupported type.")
        enum=row.get("enum")
        if enum is not None and (not isinstance(enum,list) or len(enum)>100):
            raise AppControlError(f"App setting {key} enum is invalid.")
        normalized_field={
            "key":key,
            "type":kind,
            "label":str(row.get("label") or key)[:160],
            "description":str(row.get("description") or "")[:500],
            "required":bool(row.get("required",False)),
            "secret":bool(row.get("secret",False)),
            "default":row.get("default"),
            "enum":enum,
            "minimum":row.get("minimum"),
            "maximum":row.get("maximum"),
        }
        if normalized_field["secret"] and normalized_field["default"] is not None and normalized_field["default"]!="":
            raise AppControlError(f"Secret setting {key} may not declare a package default.")
        if not normalized_field["secret"] and normalized_field["default"] is not None:
            _coerce_setting(normalized_field,normalized_field["default"])
        if normalized_field["secret"]:
            name=key.upper().replace(".","_").replace("-","_")
            if not homeserver_app_security._SECRET_KEY.fullmatch(name) or name in secret_names:
                raise AppControlError(f"Secret setting {key} has an invalid or duplicated vault key.")
            secret_names.add(name)
        normalized.append(normalized_field)
    return {"contract":"vp3.app.settings-schema.v1","app_key":app_key,"fields":normalized}


def _settings_schema(app_key:str)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    metadata=dict(app.get("metadata") or {})
    rel=str(metadata.get("settings_schema") or "settings.schema.json")
    try:
        root=homeserver_app_packages.active_content_root(app_key)
    except homeserver_app_packages.AppPackageError as exc:
        raise AppControlError(str(exc),exc.status_code) from exc
    return validate_settings_schema(root,app_key,rel)


def settings(app_key:str)->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    schema=_settings_schema(app_key)
    metadata=dict(app.get("metadata") or {})
    stored=dict(metadata.get("control_settings") or {})
    secret_status=homeserver_app_security.secret_status(app_key)
    configured=set(secret_status.get("configured_keys") or [])
    values={}
    for field in schema["fields"]:
        key=field["key"]
        if field["secret"]:
            values[key]={"configured":key.upper().replace(".","_").replace("-","_") in configured,"secret":True}
        elif key in stored:
            values[key]=stored[key]
        else:
            values[key]=field.get("default")
    return {
        "contract":"vp3.app.settings.v1",
        "app_key":app_key,
        "schema":schema,
        "values":values,
        "secret_values_exposed":False,
    }


def _coerce_setting(field:dict[str,Any],value:Any)->Any:
    kind=field["type"]
    if value is None:
        if field.get("required"):
            raise AppControlError(f"Setting {field['key']} is required.")
        return None
    if kind=="string":
        if not isinstance(value,str):
            raise AppControlError(f"Setting {field['key']} must be a string.")
        if len(value)>16000:
            raise AppControlError(f"Setting {field['key']} is too long.")
        parsed=value
    elif kind=="boolean":
        if not isinstance(value,bool):
            raise AppControlError(f"Setting {field['key']} must be a boolean.")
        parsed=value
    elif kind=="integer":
        if isinstance(value,bool) or not isinstance(value,int):
            raise AppControlError(f"Setting {field['key']} must be an integer.")
        parsed=value
    else:
        if isinstance(value,bool) or not isinstance(value,(int,float)):
            raise AppControlError(f"Setting {field['key']} must be numeric.")
        parsed=float(value)
        if not math.isfinite(parsed):
            raise AppControlError(f"Setting {field['key']} must be finite.")
    enum=field.get("enum")
    if enum is not None and parsed not in enum:
        raise AppControlError(f"Setting {field['key']} is not an allowed value.")
    if isinstance(parsed,(int,float)) and not isinstance(parsed,bool):
        minimum=field.get("minimum")
        maximum=field.get("maximum")
        if minimum is not None and parsed<float(minimum):
            raise AppControlError(f"Setting {field['key']} is below its minimum.")
        if maximum is not None and parsed>float(maximum):
            raise AppControlError(f"Setting {field['key']} is above its maximum.")
    return parsed


@serialized
def update_settings(app_key:str,values:dict[str,Any])->dict[str,Any]:
    app=homeserver_apps.get(app_key)
    schema=_settings_schema(app_key)
    fields={row["key"]:row for row in schema["fields"]}
    unknown=set(values)-set(fields)
    if unknown:
        raise AppControlError(f"Unknown app setting: {sorted(unknown)[0]}")
    # Validate the entire request before changing any persisted value.
    parsed={key:_coerce_setting(fields[key],value) for key,value in values.items()}
    secret_changes={}
    for key,value in parsed.items():
        if fields[key]["secret"]:
            name=key.upper().replace(".","_").replace("-","_")
            if not homeserver_app_security._SECRET_KEY.fullmatch(name):
                raise AppControlError(f"Setting {key} has an invalid secret key.")
            secret_changes[name]=None if value is None or value=="" else str(value)
    vault_path=homeserver_app_security._vault_path(app["app_id"])
    original=vault_path.read_bytes() if vault_path.is_file() else None
    vault=homeserver_app_security._load_vault(app["app_id"]) if secret_changes else {}
    for name,value in secret_changes.items():
        if value is None:
            vault.pop(name,None)
        else:
            vault[name]=value
    encoded=homeserver_app_security._encode(vault,app["app_id"]) if secret_changes and vault else None
    vault_changed=False
    try:
        with db() as connection:
            if connection.in_transaction:
                raise AppControlError("App settings require an independent persistence transaction.",409)
            connection.execute("BEGIN IMMEDIATE")
            row=connection.execute("SELECT metadata_json FROM homeserver_apps WHERE app_key=?",(app_key,)).fetchone()
            metadata=json.loads(row["metadata_json"] or "{}")
            stored=dict(metadata.get("control_settings") or {})
            changed=[]
            for key,value in parsed.items():
                if not fields[key]["secret"]:
                    if value is None:
                        stored.pop(key,None)
                    else:
                        stored[key]=value
                changed.append({"key":key,"secret":fields[key]["secret"]})
            metadata["control_settings"]=stored
            connection.execute(
                "UPDATE homeserver_apps SET metadata_json=?,updated_at=CURRENT_TIMESTAMP WHERE app_key=?",
                (json.dumps(metadata,separators=(",",":"),sort_keys=True),app_key),
            )
            connection.execute(
                """INSERT INTO homeserver_app_events(app_id,event_type,actor_type,actor_key,metadata_json)
                   VALUES (?, 'app.settings.updated','agent','homeserver-agent',?)""",
                (app["app_id"],json.dumps({"changed":changed},separators=(",",":"),sort_keys=True)),
            )
            if secret_changes:
                vault_changed=True
                if encoded is None:
                    vault_path.unlink(missing_ok=True)
                else:
                    homeserver_app_security._atomic_write(vault_path,encoded)
    except Exception:
        if vault_changed:
            if original is None:
                vault_path.unlink(missing_ok=True)
            else:
                homeserver_app_security._atomic_write(vault_path,original)
        raise
    return settings(app_key)



def compatibility(app_key:str)->dict[str,Any]:
    try:
        data=manifest(app_key)
        return {
            "contract":CONTRACT,
            "app_key":app_key,
            "compatible":bool(data.get("compatible")),
            "manifest_contract":data["manifest_contract"],
            "action_count":data["count"],
            "supported_manifest_contracts":sorted(MANIFEST_CONTRACTS),
        }
    except Exception as exc:
        return {
            "contract":CONTRACT,
            "app_key":app_key,
            "compatible":False,
            "reason":str(exc)[:500],
            "supported_manifest_contracts":sorted(MANIFEST_CONTRACTS),
        }


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "manifest_contracts":sorted(MANIFEST_CONTRACTS),
        "risk_classes":sorted(RISK_LEVELS),
        "generic_action_discovery":True,
        "generic_invocation":True,
        "generic_settings_control":True,
        "secret_settings_write_only":True,
        "generic_executors":["runtime.status","settings.read","event.emit","job.run","builtin"],
        "install_time_validation":True,
        "compatibility_negotiation":True,
        "owner_confirmation_for_high_risk":True,
        "homeserver_execution_authority":True,
    }
