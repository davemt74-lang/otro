from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from ..database import db

CONTRACT="vp3.homeserver.members.v1"
_USERNAME=re.compile(r"^[a-z0-9][a-z0-9._-]{2,39}$")
_CONTEXT_KEY=re.compile(r"^[a-z][a-z0-9_.:-]{1,79}$")
_ROLES={"admin","member","guest"}
_STATUSES={"active","disabled"}
_SESSION_HOURS=12
_LOCK_THRESHOLD=5
_LOCK_MINUTES=5
_MAX_CONTEXT_KEYS=64
_MAX_CONTEXT_BYTES=16384


class MemberError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message)
        self.status_code=status_code


def _now()->datetime:
    return datetime.now(timezone.utc)


def _iso(value:datetime)->str:
    return value.astimezone(timezone.utc).isoformat()


def _parse(value:Any)->datetime|None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z","+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _username(value:str)->str:
    key=str(value or "").strip().lower()
    if not _USERNAME.fullmatch(key):
        raise MemberError("Username must be 3-40 characters using lowercase letters, numbers, dot, underscore, or dash.")
    return key


def _display_name(value:str)->str:
    name=" ".join(str(value or "").split())[:120]
    if not name:
        raise MemberError("Display name is required.")
    return name


def _password(value:str)->str:
    raw=str(value or "")
    if len(raw)<10 or len(raw)>256:
        raise MemberError("Password must be between 10 and 256 characters.")
    return raw


def _password_hash(password:str,salt:bytes)->str:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    ).hex()


def _session_hash(token:str)->str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _activity(member_id:str,action:str,resource_type:str="",resource_key:str="",metadata:dict[str,Any]|None=None)->None:
    safe={}
    for key,value in dict(metadata or {}).items():
        name=str(key)[:80]
        lowered=name.lower()
        if any(word in lowered for word in ("password","secret","token","credential","cookie","path")):
            continue
        if isinstance(value,(str,int,float,bool)) or value is None:
            safe[name]=str(value)[:240] if isinstance(value,str) else value
    with db() as connection:
        connection.execute(
            """INSERT INTO homeserver_member_activity(
                 member_id,action,resource_type,resource_key,metadata_json
               ) VALUES (?,?,?,?,?)""",
            (member_id,action,resource_type or None,resource_key or None,json.dumps(safe,separators=(",",":"),sort_keys=True)),
        )


def _public_row(row)->dict[str,Any]:
    return {
        "member_id":str(row["member_id"]),
        "username":str(row["username"]),
        "display_name":str(row["display_name"]),
        "role":str(row["role"]),
        "status":str(row["status"]),
        "locked_until":row["locked_until"],
        "created_at":str(row["created_at"]),
        "updated_at":str(row["updated_at"]),
    }


def get_member(member_id:str)->dict[str,Any]:
    with db() as connection:
        row=connection.execute(
            """SELECT member_id,username,display_name,role,status,locked_until,created_at,updated_at
               FROM homeserver_members WHERE member_id=?""",
            (str(member_id),),
        ).fetchone()
        if row is None:
            raise MemberError("Member not found.",404)
        app_count=int(connection.execute(
            "SELECT COUNT(*) FROM homeserver_member_app_access WHERE member_id=? AND allowed=1",
            (str(member_id),),
        ).fetchone()[0])
        context_count=int(connection.execute(
            "SELECT COUNT(*) FROM homeserver_member_context WHERE member_id=?",
            (str(member_id),),
        ).fetchone()[0])
        sessions=int(connection.execute(
            "SELECT COUNT(*) FROM homeserver_member_sessions WHERE member_id=? AND expires_at>?",
            (str(member_id),_iso(_now())),
        ).fetchone()[0])
    return {**_public_row(row),"app_count":app_count,"context_count":context_count,"active_sessions":sessions}


def list_members()->dict[str,Any]:
    with db() as connection:
        rows=connection.execute(
            """SELECT member_id,username,display_name,role,status,locked_until,created_at,updated_at
               FROM homeserver_members ORDER BY display_name,username"""
        ).fetchall()
    return {"contract":CONTRACT,"items":[get_member(str(row["member_id"])) for row in rows],"count":len(rows)}


def create_member(username:str,display_name:str,password:str,role:str="member")->dict[str,Any]:
    key=_username(username)
    name=_display_name(display_name)
    secret=_password(password)
    role_key=str(role or "member").lower()
    if role_key not in _ROLES:
        raise MemberError("Unsupported member role.")
    salt=secrets.token_bytes(16)
    digest=_password_hash(secret,salt)
    member_id="member_"+uuid.uuid4().hex
    try:
        with db() as connection:
            connection.execute(
                """INSERT INTO homeserver_members(
                     member_id,username,display_name,role,status,password_salt,password_hash
                   ) VALUES (?,?,?,?, 'active',?,?)""",
                (member_id,key,name,role_key,salt.hex(),digest),
            )
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            raise MemberError("Username is already in use.",409) from exc
        raise
    _activity(member_id,"member.created","member",member_id,{"role":role_key})
    return get_member(member_id)


def update_member(member_id:str,values:dict[str,Any])->dict[str,Any]:
    unknown=set(values)-{"display_name","role","status"}
    if unknown:
        raise MemberError(f"Unsupported member field: {sorted(unknown)[0]}")
    current=get_member(member_id)
    name=_display_name(values.get("display_name",current["display_name"]))
    role=str(values.get("role",current["role"])).lower()
    status=str(values.get("status",current["status"])).lower()
    if role not in _ROLES:
        raise MemberError("Unsupported member role.")
    if status not in _STATUSES:
        raise MemberError("Unsupported member status.")
    with db() as connection:
        connection.execute(
            """UPDATE homeserver_members
               SET display_name=?,role=?,status=?,updated_at=CURRENT_TIMESTAMP
               WHERE member_id=?""",
            (name,role,status,member_id),
        )
        if status!="active":
            connection.execute("DELETE FROM homeserver_member_sessions WHERE member_id=?",(member_id,))
    _activity(member_id,"member.updated","member",member_id,{"role":role,"status":status})
    return get_member(member_id)


def set_password(member_id:str,password:str)->dict[str,Any]:
    get_member(member_id)
    secret=_password(password)
    salt=secrets.token_bytes(16)
    digest=_password_hash(secret,salt)
    with db() as connection:
        connection.execute(
            """UPDATE homeserver_members
               SET password_salt=?,password_hash=?,failed_attempts=0,locked_until=NULL,updated_at=CURRENT_TIMESTAMP
               WHERE member_id=?""",
            (salt.hex(),digest,member_id),
        )
        connection.execute("DELETE FROM homeserver_member_sessions WHERE member_id=?",(member_id,))
    _activity(member_id,"member.password.updated","member",member_id)
    return {"member_id":member_id,"sessions_revoked":True}


def _failed_login(row)->None:
    attempts=int(row["failed_attempts"] or 0)+1
    locked_until=None
    if attempts>=_LOCK_THRESHOLD:
        locked_until=_iso(_now()+timedelta(minutes=_LOCK_MINUTES))
        attempts=0
    with db() as connection:
        connection.execute(
            "UPDATE homeserver_members SET failed_attempts=?,locked_until=?,updated_at=CURRENT_TIMESTAMP WHERE member_id=?",
            (attempts,locked_until,row["member_id"]),
        )


def authenticate(username:str,password:str)->dict[str,Any]:
    key=str(username or "").strip().lower()
    if not _USERNAME.fullmatch(key):
        raise MemberError("Invalid member credentials.",401)
    with db() as connection:
        connection.execute("DELETE FROM homeserver_member_sessions WHERE expires_at<=?",(_iso(_now()),))
        row=connection.execute("SELECT * FROM homeserver_members WHERE username=?",(key,)).fetchone()
    if row is None or str(row["status"])!="active":
        raise MemberError("Invalid member credentials.",401)
    locked=_parse(row["locked_until"])
    if locked and locked>_now():
        raise MemberError("Member account is temporarily locked.",429)
    try:
        salt=bytes.fromhex(str(row["password_salt"]))
    except ValueError as exc:
        raise MemberError("Member credential record is invalid.",500) from exc
    candidate=_password_hash(str(password or ""),salt)
    if not hmac.compare_digest(candidate,str(row["password_hash"])):
        _failed_login(row)
        raise MemberError("Invalid member credentials.",401)
    token=secrets.token_urlsafe(48)
    expires=_now()+timedelta(hours=_SESSION_HOURS)
    with db() as connection:
        connection.execute(
            "UPDATE homeserver_members SET failed_attempts=0,locked_until=NULL,updated_at=CURRENT_TIMESTAMP WHERE member_id=?",
            (row["member_id"],),
        )
        connection.execute(
            """INSERT INTO homeserver_member_sessions(session_hash,member_id,expires_at)
               VALUES (?,?,?)""",
            (_session_hash(token),row["member_id"],_iso(expires)),
        )
        stale=connection.execute(
            """SELECT session_hash FROM homeserver_member_sessions
               WHERE member_id=? ORDER BY created_at DESC LIMIT -1 OFFSET 10""",
            (row["member_id"],),
        ).fetchall()
        for item in stale:
            connection.execute("DELETE FROM homeserver_member_sessions WHERE session_hash=?",(item["session_hash"],))
    _activity(str(row["member_id"]),"member.session.created","session","local")
    return {
        "contract":CONTRACT,
        "session_token":token,
        "expires_at":_iso(expires),
        "member":get_member(str(row["member_id"])),
    }


def session_identity(token:str|None)->dict[str,Any]:
    if not token:
        raise MemberError("Member session required.",401)
    hashed=_session_hash(token)
    now=_iso(_now())
    with db() as connection:
        connection.execute("DELETE FROM homeserver_member_sessions WHERE expires_at<=?",(now,))
        row=connection.execute(
            """SELECT s.session_hash,s.member_id,s.expires_at,m.username,m.display_name,m.role,m.status
               FROM homeserver_member_sessions s
               JOIN homeserver_members m ON m.member_id=s.member_id
               WHERE s.session_hash=? AND s.expires_at>? AND m.status='active'""",
            (hashed,now),
        ).fetchone()
        if row is None:
            raise MemberError("Member session is invalid or expired.",401)
        connection.execute(
            "UPDATE homeserver_member_sessions SET last_seen_at=CURRENT_TIMESTAMP WHERE session_hash=?",
            (hashed,),
        )
    return {
        "member_id":str(row["member_id"]),
        "username":str(row["username"]),
        "display_name":str(row["display_name"]),
        "role":str(row["role"]),
        "expires_at":str(row["expires_at"]),
    }


def revoke_session(token:str|None)->None:
    if not token:
        return
    with db() as connection:
        connection.execute("DELETE FROM homeserver_member_sessions WHERE session_hash=?",(_session_hash(token),))


def set_app_access(member_id:str,app_key:str,allowed:bool)->dict[str,Any]:
    get_member(member_id)
    key=str(app_key or "").strip().lower()
    if not key:
        raise MemberError("app_key is required.")
    with db() as connection:
        app=connection.execute(
            "SELECT app_key,name,installed_version FROM homeserver_apps WHERE app_key=?",
            (key,),
        ).fetchone()
        if app is None or not app["installed_version"]:
            raise MemberError("Installed app not found.",404)
        connection.execute(
            """INSERT INTO homeserver_member_app_access(member_id,app_key,allowed,updated_at)
               VALUES (?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(member_id,app_key) DO UPDATE SET allowed=excluded.allowed,updated_at=CURRENT_TIMESTAMP""",
            (member_id,key,1 if allowed else 0),
        )
    _activity(member_id,"member.app-access.updated","app",key,{"allowed":bool(allowed)})
    return {"member_id":member_id,"app_key":key,"allowed":bool(allowed)}


def app_access_matrix(member_id:str)->dict[str,Any]:
    get_member(member_id)
    with db() as connection:
        rows=connection.execute(
            """SELECT a.app_key,a.name,a.installed_version,a.lifecycle_state,
                      COALESCE(x.allowed,0) AS allowed
               FROM homeserver_apps a
               LEFT JOIN homeserver_member_app_access x
                 ON x.app_key=a.app_key AND x.member_id=?
               WHERE a.installed_version IS NOT NULL
               ORDER BY a.name""",
            (member_id,),
        ).fetchall()
    return {
        "contract":CONTRACT,
        "member_id":member_id,
        "items":[{**dict(row),"allowed":bool(row["allowed"])} for row in rows],
        "count":len(rows),
    }


def assigned_apps(member_id:str)->dict[str,Any]:
    get_member(member_id)
    with db() as connection:
        rows=connection.execute(
            """SELECT a.app_key,a.name,a.installed_version,a.lifecycle_state,x.allowed
               FROM homeserver_member_app_access x
               JOIN homeserver_apps a ON a.app_key=x.app_key
               WHERE x.member_id=? AND x.allowed=1 AND a.installed_version IS NOT NULL
               ORDER BY a.name""",
            (member_id,),
        ).fetchall()
    return {"contract":CONTRACT,"member_id":member_id,"items":[dict(row) for row in rows],"count":len(rows)}


def context(member_id:str)->dict[str,Any]:
    get_member(member_id)
    with db() as connection:
        rows=connection.execute(
            """SELECT context_key,value_json,created_at,updated_at
               FROM homeserver_member_context WHERE member_id=? ORDER BY context_key""",
            (member_id,),
        ).fetchall()
    items=[]
    for row in rows:
        try:
            value=json.loads(str(row["value_json"]))
        except json.JSONDecodeError:
            value=None
        items.append({"context_key":row["context_key"],"value":value,"created_at":row["created_at"],"updated_at":row["updated_at"]})
    return {"contract":"vp3.homeserver.member-context.v1","member_id":member_id,"items":items,"count":len(items)}


def set_context(member_id:str,context_key:str,value:Any)->dict[str,Any]:
    get_member(member_id)
    key=str(context_key or "").strip().lower()
    if not _CONTEXT_KEY.fullmatch(key):
        raise MemberError("Context key is invalid.")
    encoded=json.dumps(value,ensure_ascii=False,separators=(",",":"),sort_keys=True)
    if len(encoded.encode("utf-8"))>_MAX_CONTEXT_BYTES:
        raise MemberError("Member context value exceeds 16 KB.",413)
    with db() as connection:
        exists=connection.execute(
            "SELECT 1 FROM homeserver_member_context WHERE member_id=? AND context_key=?",
            (member_id,key),
        ).fetchone()
        if exists is None:
            count=int(connection.execute(
                "SELECT COUNT(*) FROM homeserver_member_context WHERE member_id=?",
                (member_id,),
            ).fetchone()[0])
            if count>=_MAX_CONTEXT_KEYS:
                raise MemberError("Member context key limit reached.",409)
        connection.execute(
            """INSERT INTO homeserver_member_context(member_id,context_key,value_json,updated_at)
               VALUES (?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(member_id,context_key) DO UPDATE SET value_json=excluded.value_json,updated_at=CURRENT_TIMESTAMP""",
            (member_id,key,encoded),
        )
    _activity(member_id,"member.context.updated","context",key,{"bytes":len(encoded.encode("utf-8"))})
    return {"member_id":member_id,"context_key":key,"value":value}


def delete_context(member_id:str,context_key:str)->bool:
    get_member(member_id)
    key=str(context_key or "").strip().lower()
    with db() as connection:
        cursor=connection.execute(
            "DELETE FROM homeserver_member_context WHERE member_id=? AND context_key=?",
            (member_id,key),
        )
    if cursor.rowcount:
        _activity(member_id,"member.context.deleted","context",key)
    return bool(cursor.rowcount)


def member_activity(member_id:str,limit:int=100)->dict[str,Any]:
    get_member(member_id)
    bounded=max(1,min(int(limit),250))
    with db() as connection:
        rows=connection.execute(
            """SELECT id,action,resource_type,resource_key,metadata_json,created_at
               FROM homeserver_member_activity WHERE member_id=?
               ORDER BY id DESC LIMIT ?""",
            (member_id,bounded),
        ).fetchall()
    items=[]
    for row in rows:
        try:
            metadata=json.loads(str(row["metadata_json"] or "{}"))
        except json.JSONDecodeError:
            metadata={}
        items.append({
            "id":int(row["id"]),"action":str(row["action"]),
            "resource_type":row["resource_type"],"resource_key":row["resource_key"],
            "metadata":metadata,"created_at":str(row["created_at"]),
        })
    return {"contract":CONTRACT,"member_id":member_id,"items":items,"count":len(items)}


def brain_context(member_id:str)->dict[str,Any]:
    member=get_member(member_id)
    ctx=context(member_id)
    apps=assigned_apps(member_id)
    return {
        "contract":"vp3.homeserver.member-brain-context.v1",
        "member":{
            "member_id":member["member_id"],
            "username":member["username"],
            "display_name":member["display_name"],
            "role":member["role"],
        },
        "context":ctx["items"],
        "apps":[{"app_key":row["app_key"],"name":row["name"]} for row in apps["items"]],
        "isolation":{
            "member_id":member_id,
            "owner_memory_included":False,
            "other_member_context_included":False,
            "unassigned_apps_included":False,
        },
    }


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "implicit_owner_principal":True,
        "member_roles":["admin","member","guest"],
        "member_admin_is_not_owner":True,
        "hashed_credentials":True,
        "scrypt":True,
        "hashed_sessions":True,
        "session_hours":_SESSION_HOURS,
        "lockout_threshold":_LOCK_THRESHOLD,
        "per_member_app_access":True,
        "private_member_context":True,
        "member_agent_context_isolation":True,
        "owner_control_inherited_by_members":False,
        "owner_bootstrap_and_session_unchanged":True,
    }
