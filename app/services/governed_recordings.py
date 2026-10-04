"""Private saved audio/video clips; explicit local owner consent, no unattended capture.

Capture reuses DeviceAudio and the verified managed FFmpeg package. Recording
files never enter public media routes, Cloud synchronization or Agent tool output.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import time
from typing import Any

from ..config import settings
from ..database import db
from . import device_audio, homeserver_media_tools, local_voice, physical_meeting, vp3_os, meeting_speaker_context

CONTRACT = "vp3.homeserver.governed-recordings.v1"
MAX_CLIP_SECONDS = 30
MIN_CLIP_SECONDS = 2
RETENTION_DAYS = 7
MAX_CLIPS = 25
MAX_CLIP_BYTES = 40 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MIN_FREE_DISK_BYTES = 50 * 1024 * 1024
_CAPTURE = threading.Lock()
_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_REASON_CODES = {
    "microphone_unavailable", "ffmpeg_unavailable", "camera_not_configured",
    "camera_not_ready", "capture_failed", "capture_timeout",
    "recording_exceeds_limit", "storage_unavailable", "storage_full",
    "capture_in_use", "privacy_switch_engaged", "meeting_in_progress",
    "transcription_unavailable",
}

class RecordingError(RuntimeError):
    def __init__(self, reason: str, status_code: int = 409):
        if reason not in _REASON_CODES:
            reason = "capture_failed"
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


def _store() -> Path:
    root = settings.data_dir / "recordings"
    if root.is_symlink():
        raise RecordingError("storage_unavailable", 503)
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            root.chmod(0o700)
    except OSError as exc:
        raise RecordingError("storage_unavailable", 503) from exc
    return root


def _file(recording_id: str, kind: str) -> Path:
    if not isinstance(recording_id, str) or not _ID_RE.fullmatch(recording_id):
        raise RecordingError("capture_failed", 404)
    if kind not in ("audio", "video"):
        raise RecordingError("capture_failed", 404)
    return _store() / (recording_id + (".wav" if kind == "audio" else ".mp4"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_record(row: Any) -> dict[str, Any]:
    return {
        "has_transcript": bool(row["transcript"]) if "transcript" in row.keys() else False,
        "id": str(row["recording_id"]),
        "kind": str(row["media_type"]),
        "seconds": int(row["duration_seconds"]),
        "bytes": int(row["size_bytes"]),
        "sha256": str(row["sha256"]),
        "created_at": str(row["created_at"]),
        "delete_after": str(row["delete_after"]),
        "owner_only": True,
    }


def _prune() -> int:
    """Bound total private storage and delete expired / oldest clips first."""
    removed = 0
    now = _now().isoformat()
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            "SELECT * FROM governed_recordings ORDER BY created_at,recording_id"
        ).fetchall()
        total = sum(int(row["size_bytes"]) for row in rows)
        count = len(rows)
        for row in rows:
            expired = str(row["delete_after"]) <= now
            if not (expired or total > MAX_TOTAL_BYTES or count > MAX_CLIPS):
                continue
            target = _file(str(row["recording_id"]), str(row["media_type"]))
            try:
                # Unlinking a managed symlink removes only the link, never its target.
                target.unlink(missing_ok=True)
            except OSError as exc:
                raise RecordingError("storage_unavailable", 503) from exc
            connection.execute(
                "DELETE FROM governed_recordings WHERE recording_id=?",
                (row["recording_id"],),
            )
            total -= int(row["size_bytes"])
            count -= 1
            removed += 1
    return removed


def _preflight(kind: str, seconds: int) -> None:
    if kind not in ("audio", "video") or type(seconds) is not int or not MIN_CLIP_SECONDS <= seconds <= MAX_CLIP_SECONDS:
        raise RecordingError("capture_failed", 422)
    hardware = vp3_os.hardware_inventory()
    if (hardware.get("privacy_switch") or {}).get("engaged"):
        raise RecordingError("privacy_switch_engaged", 409)
    state = physical_meeting.status()
    if state.get("state") not in (None, "idle", "stopped", "error"):
        raise RecordingError("meeting_in_progress", 409)
    try:
        if shutil.disk_usage(_store()).free < MIN_FREE_DISK_BYTES:
            raise RecordingError("storage_full", 507)
    except OSError as exc:
        raise RecordingError("storage_unavailable", 503) from exc
    if kind == "audio" and not device_audio.status().get("available"):
        raise RecordingError("microphone_unavailable", 503)
    if kind == "video" and not (hardware.get("camera") or {}).get("ready"):
        raise RecordingError("camera_not_ready", 503)


def _camera_input() -> list[str]:
    """Device selection belongs to trusted local operator configuration, not the HTTP client."""
    fmt = os.getenv("HOMESERVER_RECORD_CAMERA_FORMAT", "").strip().lower()
    name = os.getenv("HOMESERVER_RECORD_CAMERA_DEVICE", "").strip()
    if not name or len(name) > 160 or any(ord(c) < 32 for c in name):
        raise RecordingError("camera_not_configured", 503)
    if os.name == "nt" and fmt == "dshow":
        return ["-f", "dshow", "-i", "video=" + name]
    if os.name == "posix" and fmt == "v4l2" and re.fullmatch(r"/dev/video[0-9]{1,3}", name):
        return ["-f", "v4l2", "-i", name]
    if os.name == "posix" and fmt == "avfoundation" and re.fullmatch(r"[0-9]{1,3}", name):
        return ["-f", "avfoundation", "-i", name + ":none"]
    raise RecordingError("camera_not_configured", 503)


def _capture_audio(seconds: int, target: Path) -> None:
    device = device_audio.DeviceAudio()
    started = False
    try:
        device.start_capture()
        started = True
        time.sleep(seconds)
        wav = device.stop_capture()
        started = False
        with target.open("xb") as handle:
            handle.write(wav)
            handle.flush()
            os.fsync(handle.fileno())
        del wav
    except Exception as exc:
        if started:
            try:
                device.cancel_capture()
            except Exception:
                pass
        raise RecordingError("capture_failed", 503) from exc


def _capture_video(seconds: int, target: Path) -> None:
    source = _camera_input()
    try:
        binaries = homeserver_media_tools.require()
    except Exception as exc:
        raise RecordingError("ffmpeg_unavailable", 503) from exc
    cmd = [
        str(binaries["ffmpeg"]), "-nostdin", "-hide_banner", "-loglevel", "error",
        "-y", *source, "-t", str(seconds), "-an",
        "-vf", "fps=15,scale=640:-2", "-c:v", "mpeg4", "-b:v", "900k",
        "-movflags", "+faststart", "-f", "mp4", str(target),
    ]
    try:
        process = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=seconds + 12, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if process.returncode != 0:
            raise RecordingError("capture_failed", 503)
        check = subprocess.run([
            str(binaries["ffprobe"]), "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_type", "-of", "default=noprint_wrappers=1:nokey=1",
            str(target),
        ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=8, check=False)
        if check.returncode != 0 or check.stdout.strip() != b"video":
            raise RecordingError("capture_failed", 503)
    except subprocess.TimeoutExpired as exc:
        raise RecordingError("capture_timeout", 504) from exc
    except OSError as exc:
        raise RecordingError("capture_failed", 503) from exc


def _sha256(target: Path) -> str:
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def capture(kind: str, seconds: int, *, consent: bool, capture_ack: bool) -> dict[str, Any]:
    if not consent or not capture_ack:
        raise RecordingError("capture_failed", 403)
    if not _CAPTURE.acquire(blocking=False):
        raise RecordingError("capture_in_use", 409)
    recording_id = secrets.token_hex(16)
    target: Path | None = None
    part: Path | None = None
    try:
        _preflight(kind, seconds)
        _prune()
        root = _store()
        target = _file(recording_id, kind)
        # Keep extension while capturing so FFmpeg selects the requested muxer.
        part = root / (recording_id + ".pending" + target.suffix)
        if kind == "audio":
            _capture_audio(seconds, part)
        else:
            _capture_video(seconds, part)
        size = part.stat().st_size
        if size < 44 or size > MAX_CLIP_BYTES:
            raise RecordingError("recording_exceeds_limit", 507)
        if shutil.disk_usage(root).free < MIN_FREE_DISK_BYTES:
            raise RecordingError("storage_full", 507)
        checksum = _sha256(part)
        os.replace(part, target)
        created = _now()
        expires = created + timedelta(days=RETENTION_DAYS)
        try:
            with db() as connection:
                connection.execute(
                    "INSERT INTO governed_recordings(recording_id,media_type,duration_seconds,size_bytes,sha256,created_at,delete_after)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (recording_id, kind, seconds, size, checksum, created.isoformat(), expires.isoformat()),
                )
        except Exception:
            target.unlink(missing_ok=True)
            raise
        _prune()  # Enforce count/byte limits before acknowledging the new clip.
        return {
            "contract": CONTRACT, "recording": {
                "id": recording_id, "kind": kind, "seconds": seconds, "bytes": size,
                "sha256": checksum, "created_at": created.isoformat(),
                "delete_after": expires.isoformat(), "owner_only": True,
            },
            "captured_on_installed_device": True,
            "cloud_upload": False, "public_access": False,
        }
    finally:
        if part is not None:
            part.unlink(missing_ok=True)
        _CAPTURE.release()


def list_recordings(limit: int = 30) -> dict[str, Any]:
    with _CAPTURE:
        removed = _prune()
        with db() as connection:
            rows = connection.execute(
                "SELECT * FROM governed_recordings ORDER BY created_at DESC,recording_id DESC LIMIT ?",
                (min(100, max(1, int(limit))),),
            ).fetchall()
        return {
            "contract": CONTRACT, "items": [_safe_record(row) for row in rows],
            "expired_deleted": removed, "owner_only": True,
            "retention_days": RETENTION_DAYS, "max_total_bytes": MAX_TOTAL_BYTES,
        }


def maintain_retention() -> dict[str, Any]:
    """Scheduler maintenance must not wait behind capture or model inference."""
    if not _CAPTURE.acquire(blocking=False):
        return {"deferred": True, "expired_deleted": 0}
    try:
        removed = _prune()
        # Crash leftovers are limited to canonical generated names and are never followed.
        with db() as connection:
            ids = {str(row[0]) for row in connection.execute("SELECT recording_id FROM governed_recordings")}
        cutoff = time.time() - 3600
        for path in _store().iterdir():
            match = re.fullmatch(r"([a-f0-9]{32})(?:\.pending)?\.(?:wav|mp4)", path.name)
            if match and match[1] not in ids and path.lstat().st_mtime < cutoff and not path.is_dir():
                path.unlink(missing_ok=True)
        return {"deferred": False, "expired_deleted": removed}
    finally:
        _CAPTURE.release()


def resolve(recording_id: str) -> tuple[Path, dict[str, Any]]:
    if not isinstance(recording_id, str) or not _ID_RE.fullmatch(recording_id):
        raise RecordingError("capture_failed", 404)
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM governed_recordings WHERE recording_id=?",
            (recording_id,),
        ).fetchone()
    if row is None or str(row["delete_after"]) <= _now().isoformat():
        raise RecordingError("capture_failed", 404)
    path = _file(recording_id, str(row["media_type"]))
    if path.is_symlink() or not path.is_file() or path.stat().st_size != row["size_bytes"] or _sha256(path) != row["sha256"]:
        raise RecordingError("capture_failed", 404)
    return path, _safe_record(row)


def transcribe_saved(recording_id: str) -> dict[str, Any]:
    """Separate Transcription mode: private saved text, never an Agent command."""
    if not _CAPTURE.acquire(blocking=False):
        raise RecordingError("capture_in_use",409)
    try:
        path, item = resolve(recording_id)
        if item["kind"] != "audio":
            raise RecordingError("transcription_unavailable",422)
        if path.stat().st_size > min(MAX_CLIP_BYTES, 8 * 1024 * 1024):
            raise RecordingError("transcription_unavailable",413)
        with db() as connection:
            prior=connection.execute(
                "SELECT transcript FROM governed_recordings WHERE recording_id=?",
                (recording_id,),
            ).fetchone()
        if prior is not None and prior["transcript"] is not None:
            return {"contract": CONTRACT, "recording_id":recording_id,
                    "transcript":str(prior["transcript"]), "source":"local_whisper",
                    "speaker_attribution":meeting_speaker_context.attribution(None,"Room"),
                    "sent_to_agent":False,"sent_to_cloud":False}
        if not local_voice.status().get("stt",{}).get("available"):
            raise RecordingError("transcription_unavailable",503)
        try:
            result=local_voice.transcribe(path.read_bytes())
            text=str(result.get("text") or "").strip()
        except Exception as exc:
            raise RecordingError("transcription_unavailable",503) from exc
        # Keep output bounded. Do not retain Whisper output paths or raw audio in DB.
        if len(text)>12000:
            text=text[:12000]
        # Expiry may occur during inference. Do not save or return expired audio's text.
        resolve(recording_id)
        with db() as connection:
            connection.execute(
                "UPDATE governed_recordings SET transcript=?,transcript_at=? "
                "WHERE recording_id=?",
                (text,_now().isoformat(),recording_id),
            )
        return {"contract":CONTRACT,"recording_id":recording_id,
                "transcript":text,"source":"local_whisper",
                "speaker_attribution":meeting_speaker_context.attribution(None,"Room"),
                    "sent_to_agent":False,"sent_to_cloud":False}
    finally:
        _CAPTURE.release()


def private_transcript(recording_id: str) -> dict[str, Any]:
    _,recording=resolve(recording_id)
    if recording["kind"]!="audio":
        raise RecordingError("transcription_unavailable",422)
    with db() as connection:
        row=connection.execute(
            "SELECT transcript,transcript_at FROM governed_recordings WHERE recording_id=?",
            (recording_id,),
        ).fetchone()
    if row is None or row["transcript"] is None:
        raise RecordingError("transcription_unavailable",404)
    return {"contract":CONTRACT,"recording_id":recording_id,
            "transcript":str(row["transcript"]),"created_at":row["transcript_at"],
            "speaker_attribution":meeting_speaker_context.attribution(None,"Room"),
                    "sent_to_agent":False,"sent_to_cloud":False}


def delete(recording_id: str) -> dict[str, Any]:
    with _CAPTURE:
        if not isinstance(recording_id, str) or not _ID_RE.fullmatch(recording_id):
            raise RecordingError("capture_failed", 404)
        with db() as connection:
            row = connection.execute("SELECT media_type FROM governed_recordings WHERE recording_id=?", (recording_id,)).fetchone()
        if row is None:
            return {"contract": CONTRACT, "deleted": True, "recording_id": recording_id}
        # Deletion remains possible for expired, missing or damaged clips.
        path = _file(recording_id, str(row["media_type"]))
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise RecordingError("storage_unavailable", 503) from exc
        with db() as connection:
            connection.execute(
                "DELETE FROM governed_recordings WHERE recording_id=?", (recording_id,),
            )
        return {"contract": CONTRACT, "deleted": True, "recording_id": recording_id}


def readiness() -> dict[str, Any]:
    hardware = vp3_os.hardware_inventory()
    media = homeserver_media_tools.public_capability()
    return {
        "contract": CONTRACT, "audio": bool(device_audio.status().get("available")),
        "video": bool(
            (hardware.get("camera") or {}).get("ready")
            and media.get("healthy")
            and os.getenv("HOMESERVER_RECORD_CAMERA_DEVICE", "").strip()
        ),
        "camera_operator_configuration_required": not bool(
            os.getenv("HOMESERVER_RECORD_CAMERA_DEVICE", "").strip()
        ),
        "max_clip_seconds": MAX_CLIP_SECONDS,
        "retention_days": RETENTION_DAYS,
        "saved_camera_video_tested": False,
        "raw_device_identifiers_exposed": False,
    }

