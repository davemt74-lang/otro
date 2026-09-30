from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

CONTRACT="vp3.homeserver.media-tools.v1"
MANIFEST_NAME="manifest.json"
_CACHE_LOCK=threading.RLock()
_CACHE_VALUE:dict[str,Any]|None=None
_CACHE_FINGERPRINT:tuple[Any,...]|None=None
_CACHE_AT=0.0
CACHE_SECONDS=60.0


def _install_root()->Path:
    override=str(os.environ.get("HOMESERVER_MEDIA_TOOLS_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys,"frozen",False):
        return Path(sys.executable).resolve().parent/"tools"/"ffmpeg"
    return Path(__file__).resolve().parents[2]/"tools"/"ffmpeg"


def ffmpeg_path()->Path:
    name="ffmpeg.exe" if os.name=="nt" else "ffmpeg"
    return _install_root()/name


def ffprobe_path()->Path:
    name="ffprobe.exe" if os.name=="nt" else "ffprobe"
    return _install_root()/name


def manifest_path()->Path:
    return _install_root()/MANIFEST_NAME


def _sha256(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda:handle.read(1024*1024),b""):
            h.update(block)
    return h.hexdigest()


def _read_manifest()->dict[str,Any]:
    path=manifest_path()
    if not path.is_file():
        return {}
    try:
        value=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError):
        return {}
    return value if isinstance(value,dict) else {}


def _version(binary:Path)->str:
    if not binary.is_file():
        return ""
    try:
        result=subprocess.run(
            [str(binary),"-version"],capture_output=True,text=True,timeout=8,check=False,
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),
        )
    except (OSError,subprocess.SubprocessError):
        return ""
    first=(result.stdout or result.stderr or "").splitlines()
    return first[0].strip()[:240] if first and result.returncode==0 else ""


def _fingerprint()->tuple[Any,...]:
    values=[]
    for path in (ffmpeg_path(),ffprobe_path(),manifest_path()):
        try:
            stat=path.stat()
            values.extend([str(path),int(stat.st_mtime_ns),int(stat.st_size)])
        except OSError:
            values.extend([str(path),None,None])
    return tuple(values)


def invalidate_cache()->None:
    global _CACHE_VALUE,_CACHE_FINGERPRINT,_CACHE_AT
    with _CACHE_LOCK:
        _CACHE_VALUE=None
        _CACHE_FINGERPRINT=None
        _CACHE_AT=0.0


def health(*,force:bool=False)->dict[str,Any]:
    global _CACHE_VALUE,_CACHE_FINGERPRINT,_CACHE_AT
    fingerprint=_fingerprint()
    now=time.monotonic()
    with _CACHE_LOCK:
        if not force and _CACHE_VALUE is not None and _CACHE_FINGERPRINT==fingerprint and now-_CACHE_AT<CACHE_SECONDS:
            return dict(_CACHE_VALUE)
    ffmpeg=ffmpeg_path()
    ffprobe=ffprobe_path()
    manifest=_read_manifest()
    ffmpeg_version=_version(ffmpeg)
    ffprobe_version=_version(ffprobe)
    packaged=bool(ffmpeg.is_file() and ffprobe.is_file())
    hashes_match=None
    expected_ffmpeg=str(manifest.get("ffmpeg_sha256") or "").lower()
    expected_ffprobe=str(manifest.get("ffprobe_sha256") or "").lower()
    if packaged and expected_ffmpeg and expected_ffprobe:
        try:
            hashes_match=(
                _sha256(ffmpeg).lower()==expected_ffmpeg
                and _sha256(ffprobe).lower()==expected_ffprobe
            )
        except OSError:
            hashes_match=False
    healthy=bool(packaged and ffmpeg_version and ffprobe_version and hashes_match is not False)
    result={
        "contract":CONTRACT,
        "managed":True,
        "root_kind":"homeserver_install",
        "packaged":packaged,
        "healthy":healthy,
        "ffmpeg_available":bool(ffmpeg_version),
        "ffprobe_available":bool(ffprobe_version),
        "ffmpeg_version":ffmpeg_version,
        "ffprobe_version":ffprobe_version,
        "manifest_version":str(manifest.get("version") or ""),
        "source":str(manifest.get("source") or ""),
        "hashes_verified":hashes_match,
        "absolute_paths_exposed":False,
        "upgrade_managed_by_homeserver":True,
    }
    with _CACHE_LOCK:
        _CACHE_VALUE=dict(result)
        _CACHE_FINGERPRINT=fingerprint
        _CACHE_AT=now
    return result


def require()->dict[str,Path]:
    state=health()
    if not state["healthy"]:
        raise RuntimeError("HomeServer managed FFmpeg runtime is unavailable or unhealthy.")
    return {"ffmpeg":ffmpeg_path(),"ffprobe":ffprobe_path()}


def public_capability()->dict[str,Any]:
    state=health()
    return {
        "contract":CONTRACT,
        "managed":True,
        "healthy":state["healthy"],
        "ffmpeg_available":state["ffmpeg_available"],
        "ffprobe_available":state["ffprobe_available"],
        "ffmpeg_version":state["ffmpeg_version"],
        "ffprobe_version":state["ffprobe_version"],
        "hashes_verified":state["hashes_verified"],
        "upgrade_managed_by_homeserver":True,
        "absolute_paths_exposed":False,
    }
