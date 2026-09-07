from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import settings
from ..database import db
from .knowledge import KnowledgeImportError, SUPPORTED_EXTENSIONS, _replace_chunks, extract_text


DEFAULT_EXCLUDES = (
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "$RECYCLE.BIN",
    "System Volume Information",
)


class KnowledgeSourceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


_SCAN_LOCK = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json_list(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _normalize_excludes(values: list[str] | tuple[str, ...] | None) -> list[str]:
    result: list[str] = []
    for raw in values or []:
        value = str(raw).strip().replace("\\", "/")[:240]
        if value and value not in result:
            result.append(value)
        if len(result) >= 40:
            break
    return result


def _normalize_path(raw_path: str, *, require_exists: bool = True) -> Path:
    value = os.path.expandvars(str(raw_path or "").strip())
    if not value:
        raise KnowledgeSourceError("A local folder path is required.")
    path = Path(value).expanduser()
    try:
        resolved = path.resolve(strict=require_exists)
    except (OSError, RuntimeError) as exc:
        raise KnowledgeSourceError("The selected local folder could not be resolved.") from exc
    if require_exists and not resolved.is_dir():
        raise KnowledgeSourceError("The selected knowledge source must be a folder.")
    return resolved


def _path_key(path: Path) -> str:
    value = str(path)
    return os.path.normcase(value) if os.name == "nt" else value


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_internal_path(path: Path) -> bool:
    try:
        private_root = settings.data_dir.resolve(strict=False)
        return path == private_root or _is_within(path, private_root)
    except (OSError, RuntimeError):
        return False


def _matches_exclude(relative_path: str, name: str, patterns: list[str]) -> bool:
    rel = relative_path.replace("\\", "/")
    for pattern in patterns:
        normalized = pattern.replace("\\", "/")
        if fnmatch.fnmatch(name, normalized) or fnmatch.fnmatch(rel, normalized):
            return True
    return False


def _source_row(source_id: int) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            """
            SELECT id, path, label, enabled, recursive, scan_interval_seconds,
                   exclude_json, status, last_scan_started_at, last_scan_completed_at,
                   last_error, last_scan_file_count, last_scan_indexed_count,
                   last_scan_updated_count, last_scan_moved_count,
                   last_scan_removed_count, last_scan_skipped_count,
                   last_scan_error_count, created_at, updated_at
            FROM knowledge_sources WHERE id=? LIMIT 1
            """,
            (source_id,),
        ).fetchone()
    if row is None:
        raise KnowledgeSourceError("Knowledge source not found.", status_code=404)
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    item["recursive"] = bool(item["recursive"])
    item["excludes"] = _json_list(item.pop("exclude_json", "[]"))
    return item


def _decorate_source(item: dict[str, Any]) -> dict[str, Any]:
    source_id = int(item["id"])
    with db() as connection:
        counts = connection.execute(
            """
            SELECT
              COUNT(*) AS tracked_files,
              COALESCE(SUM(CASE WHEN status='indexed' THEN 1 ELSE 0 END), 0) AS indexed_files,
              COALESCE(SUM(CASE WHEN status='error' THEN 1 ELSE 0 END), 0) AS error_files
            FROM knowledge_source_files WHERE source_id=?
            """,
            (source_id,),
        ).fetchone()
    result = dict(item)
    result["tracked_files"] = int(counts["tracked_files"] or 0)
    result["indexed_files"] = int(counts["indexed_files"] or 0)
    result["error_files"] = int(counts["error_files"] or 0)
    try:
        root = _normalize_path(result["path"], require_exists=False)
        result["available"] = root.is_dir()
    except KnowledgeSourceError:
        result["available"] = False
    return result


def list_sources() -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute(
            """
            SELECT id, path, label, enabled, recursive, scan_interval_seconds,
                   exclude_json, status, last_scan_started_at, last_scan_completed_at,
                   last_error, last_scan_file_count, last_scan_indexed_count,
                   last_scan_updated_count, last_scan_moved_count,
                   last_scan_removed_count, last_scan_skipped_count,
                   last_scan_error_count, created_at, updated_at
            FROM knowledge_sources ORDER BY id DESC
            """
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        item["recursive"] = bool(item["recursive"])
        item["excludes"] = _json_list(item.pop("exclude_json", "[]"))
        items.append(_decorate_source(item))
    return items


def list_source_files(source_id: int, limit: int = 500) -> list[dict[str, Any]]:
    _source_row(source_id)
    bounded = max(1, min(int(limit), 2000))
    with db() as connection:
        rows = connection.execute(
            """
            SELECT ksf.id, ksf.relative_path, ksf.knowledge_item_id, ksf.content_hash,
                   ksf.size_bytes, ksf.modified_ns, ksf.status, ksf.last_error,
                   ksf.created_at, ksf.updated_at, ki.title, ki.updated_at AS knowledge_updated_at
            FROM knowledge_source_files ksf
            LEFT JOIN knowledge_items ki ON ki.id=ksf.knowledge_item_id
            WHERE ksf.source_id=?
            ORDER BY ksf.relative_path COLLATE NOCASE LIMIT ?
            """,
            (source_id, bounded),
        ).fetchall()
    return [dict(row) for row in rows]


def create_source(
    path: str,
    *,
    label: str = "",
    recursive: bool = True,
    scan_interval_seconds: int = 120,
    excludes: list[str] | None = None,
) -> dict[str, Any]:
    root = _normalize_path(path)
    if _is_internal_path(root):
        raise KnowledgeSourceError("HomeServer's private data folder cannot be added as a knowledge source.")
    interval = max(30, min(int(scan_interval_seconds), 3600))
    combined = _normalize_excludes(list(DEFAULT_EXCLUDES) + list(excludes or []))
    canonical = str(root)
    canonical_key = _path_key(root)
    with _SCAN_LOCK:
        with db() as connection:
            existing = connection.execute("SELECT id, path FROM knowledge_sources").fetchall()
            for row in existing:
                try:
                    if _path_key(_normalize_path(row["path"], require_exists=False)) == canonical_key:
                        raise KnowledgeSourceError("That local folder is already a knowledge source.", status_code=409)
                except KnowledgeSourceError as exc:
                    if exc.status_code == 409:
                        raise
            cursor = connection.execute(
                """
                INSERT INTO knowledge_sources(
                    path, label, enabled, recursive, scan_interval_seconds, exclude_json, status
                ) VALUES (?, ?, 1, ?, ?, ?, 'pending')
                """,
                (
                    canonical,
                    str(label or "").strip()[:200],
                    1 if recursive else 0,
                    interval,
                    json.dumps(combined, separators=(",", ":")),
                ),
            )
            source_id = int(cursor.lastrowid)
    return _decorate_source(_source_row(source_id))


def update_source(
    source_id: int,
    *,
    label: str | None = None,
    enabled: bool | None = None,
    recursive: bool | None = None,
    scan_interval_seconds: int | None = None,
    excludes: list[str] | None = None,
) -> dict[str, Any]:
    current = _source_row(source_id)
    next_label = current["label"] if label is None else str(label).strip()[:200]
    next_enabled = current["enabled"] if enabled is None else bool(enabled)
    next_recursive = current["recursive"] if recursive is None else bool(recursive)
    next_interval = current["scan_interval_seconds"] if scan_interval_seconds is None else max(30, min(int(scan_interval_seconds), 3600))
    next_excludes = current["excludes"] if excludes is None else _normalize_excludes(list(DEFAULT_EXCLUDES) + list(excludes))
    next_status = "pending" if next_enabled else "paused"
    with _SCAN_LOCK:
        with db() as connection:
            connection.execute(
                """
                UPDATE knowledge_sources
                SET label=?, enabled=?, recursive=?, scan_interval_seconds=?, exclude_json=?,
                    status=?, last_error=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    next_label,
                    1 if next_enabled else 0,
                    1 if next_recursive else 0,
                    next_interval,
                    json.dumps(next_excludes, separators=(",", ":")),
                    next_status,
                    source_id,
                ),
            )
    return _decorate_source(_source_row(source_id))


def _discover_files(root: Path, recursive: bool, patterns: list[str]) -> list[tuple[Path, str, int, int]]:
    items: list[tuple[Path, str, int, int]] = []
    private_root = settings.data_dir.resolve(strict=False)

    if recursive:
        for current_root, dir_names, file_names in os.walk(root, topdown=True, followlinks=False):
            current = Path(current_root)
            kept_dirs: list[str] = []
            for name in dir_names:
                candidate = current / name
                rel = candidate.relative_to(root).as_posix()
                if candidate.is_symlink() or _matches_exclude(rel, name, patterns):
                    continue
                try:
                    resolved = candidate.resolve(strict=False)
                except (OSError, RuntimeError):
                    continue
                if resolved == private_root or _is_within(resolved, private_root):
                    continue
                kept_dirs.append(name)
            dir_names[:] = kept_dirs
            for name in file_names:
                candidate = current / name
                rel = candidate.relative_to(root).as_posix()
                if candidate.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                if candidate.is_symlink() or _matches_exclude(rel, name, patterns):
                    continue
                try:
                    resolved = candidate.resolve(strict=True)
                    if not _is_within(resolved, root) or _is_internal_path(resolved):
                        continue
                    stat = resolved.stat()
                    if not resolved.is_file():
                        continue
                except (OSError, RuntimeError):
                    continue
                items.append((resolved, rel, int(stat.st_size), int(stat.st_mtime_ns)))
    else:
        try:
            children = list(root.iterdir())
        except OSError as exc:
            raise KnowledgeSourceError("The local folder could not be listed.") from exc
        for candidate in children:
            if candidate.suffix.lower() not in SUPPORTED_EXTENSIONS or candidate.is_symlink():
                continue
            rel = candidate.name
            if _matches_exclude(rel, candidate.name, patterns):
                continue
            try:
                resolved = candidate.resolve(strict=True)
                stat = resolved.stat()
                if not resolved.is_file() or _is_internal_path(resolved):
                    continue
            except (OSError, RuntimeError):
                continue
            items.append((resolved, rel, int(stat.st_size), int(stat.st_mtime_ns)))

    items.sort(key=lambda item: item[1].casefold())
    return items


def _read_file(path: Path) -> tuple[bytes, str, int, int]:
    try:
        before = path.stat()
        if before.st_size > settings.max_upload_bytes:
            raise KnowledgeImportError(
                f"File exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB indexing limit.",
                status_code=413,
            )
        data = path.read_bytes()
        after = path.stat()
    except KnowledgeImportError:
        raise
    except OSError as exc:
        raise KnowledgeImportError(f"Unable to read {path.name}: {exc}") from exc
    if before.st_mtime_ns != after.st_mtime_ns or before.st_size != after.st_size:
        raise KnowledgeImportError("File changed while HomeServer was reading it; it will be retried on the next scan.")
    return data, hashlib.sha256(data).hexdigest(), int(after.st_size), int(after.st_mtime_ns)


def _write_knowledge_item(
    *,
    item_id: int | None,
    source_id: int,
    absolute_path: Path,
    relative_path: str,
    data: bytes,
    file_hash: str,
) -> tuple[int, int]:
    extracted = extract_text(absolute_path.name, data)
    title = absolute_path.stem.strip()[:240] or absolute_path.name[:240]
    metadata = {
        "source_type": "watched_folder",
        "knowledge_source_id": source_id,
        "relative_path": relative_path,
        "size_bytes": len(data),
    }
    with db() as connection:
        if item_id is not None:
            exists = connection.execute("SELECT id FROM knowledge_items WHERE id=?", (item_id,)).fetchone()
        else:
            exists = None
        if exists is None:
            cursor = connection.execute(
                """
                INSERT INTO knowledge_items(title, kind, source_path, content, content_hash, metadata_json)
                VALUES (?, 'watched_document', ?, ?, ?, ?)
                """,
                (
                    title,
                    str(absolute_path),
                    extracted,
                    file_hash,
                    json.dumps(metadata, separators=(",", ":")),
                ),
            )
            item_id = int(cursor.lastrowid)
        else:
            connection.execute(
                """
                UPDATE knowledge_items
                SET title=?, kind='watched_document', source_path=?, content=?, content_hash=?,
                    metadata_json=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    title,
                    str(absolute_path),
                    extracted,
                    file_hash,
                    json.dumps(metadata, separators=(",", ":")),
                    item_id,
                ),
            )
        chunk_count = _replace_chunks(connection, int(item_id), title, extracted)
    return int(item_id), chunk_count


def _record_file_error(
    source_id: int,
    relative_path: str,
    *,
    size_bytes: int,
    modified_ns: int,
    scan_token: str,
    message: str,
    existing_id: int | None = None,
    knowledge_item_id: int | None = None,
) -> None:
    with db() as connection:
        if existing_id is None:
            connection.execute(
                """
                INSERT INTO knowledge_source_files(
                    source_id, relative_path, knowledge_item_id, size_bytes, modified_ns,
                    status, last_error, last_seen_scan
                ) VALUES (?, ?, ?, ?, ?, 'error', ?, ?)
                ON CONFLICT(source_id, relative_path) DO UPDATE SET
                    size_bytes=excluded.size_bytes,
                    modified_ns=excluded.modified_ns,
                    status='error',
                    last_error=excluded.last_error,
                    last_seen_scan=excluded.last_seen_scan,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (source_id, relative_path, knowledge_item_id, size_bytes, modified_ns, message[:1000], scan_token),
            )
        else:
            connection.execute(
                """
                UPDATE knowledge_source_files
                SET size_bytes=?, modified_ns=?, status='error', last_error=?,
                    last_seen_scan=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (size_bytes, modified_ns, message[:1000], scan_token, existing_id),
            )


def scan_source(source_id: int) -> dict[str, Any]:
    if not _SCAN_LOCK.acquire(blocking=False):
        raise KnowledgeSourceError("Another knowledge-source operation is already running.", status_code=409)
    try:
        source = _source_row(source_id)
        started_at = _utc_now()
        with db() as connection:
            connection.execute(
                """
                UPDATE knowledge_sources
                SET status='scanning', last_scan_started_at=?, last_error=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (started_at, source_id),
            )

        try:
            root = _normalize_path(source["path"])
            if _is_internal_path(root):
                raise KnowledgeSourceError("HomeServer's private data folder cannot be scanned as a knowledge source.")
            candidates = _discover_files(root, bool(source["recursive"]), list(source["excludes"]))
        except Exception as exc:
            message = str(exc) or type(exc).__name__
            with db() as connection:
                connection.execute(
                    """
                    UPDATE knowledge_sources SET status='error', last_error=?,
                        last_scan_completed_at=?, updated_at=CURRENT_TIMESTAMP WHERE id=?
                    """,
                    (message[:1000], _utc_now(), source_id),
                )
            if isinstance(exc, KnowledgeSourceError):
                raise
            raise KnowledgeSourceError(message) from exc

        scan_token = uuid.uuid4().hex
        with db() as connection:
            rows = connection.execute(
                """
                SELECT id, relative_path, knowledge_item_id, content_hash, size_bytes,
                       modified_ns, status, last_error
                FROM knowledge_source_files WHERE source_id=?
                """,
                (source_id,),
            ).fetchall()
        tracked = {str(row["relative_path"]): dict(row) for row in rows}
        current_paths = {relative for _, relative, _, _ in candidates}
        missing = {key: value for key, value in tracked.items() if key not in current_paths}
        missing_by_hash: dict[str, list[dict[str, Any]]] = {}
        for row in missing.values():
            digest = str(row.get("content_hash") or "")
            if digest:
                missing_by_hash.setdefault(digest, []).append(row)

        indexed = updated = moved = removed = skipped = errors = 0
        for absolute_path, relative_path, discovered_size, discovered_mtime in candidates:
            existing = tracked.get(relative_path)
            if (
                existing
                and existing.get("status") == "indexed"
                and int(existing.get("size_bytes") or 0) == discovered_size
                and int(existing.get("modified_ns") or 0) == discovered_mtime
            ):
                with db() as connection:
                    connection.execute(
                        "UPDATE knowledge_source_files SET last_seen_scan=?, last_error=NULL WHERE id=?",
                        (scan_token, existing["id"]),
                    )
                skipped += 1
                continue

            try:
                data, digest, size_bytes, modified_ns = _read_file(absolute_path)
                if existing and str(existing.get("content_hash") or "") == digest and existing.get("knowledge_item_id"):
                    with db() as connection:
                        connection.execute(
                            """
                            UPDATE knowledge_source_files
                            SET size_bytes=?, modified_ns=?, status='indexed', last_error=NULL,
                                last_seen_scan=?, updated_at=CURRENT_TIMESTAMP WHERE id=?
                            """,
                            (size_bytes, modified_ns, scan_token, existing["id"]),
                        )
                    skipped += 1
                    continue

                move_candidates = missing_by_hash.get(digest, []) if existing is None else []
                move_row = move_candidates[0] if len(move_candidates) == 1 else None
                prior_item_id = int(move_row["knowledge_item_id"]) if move_row and move_row.get("knowledge_item_id") else None
                item_id = int(existing["knowledge_item_id"]) if existing and existing.get("knowledge_item_id") else prior_item_id
                item_id, _ = _write_knowledge_item(
                    item_id=item_id,
                    source_id=source_id,
                    absolute_path=absolute_path,
                    relative_path=relative_path,
                    data=data,
                    file_hash=digest,
                )

                with db() as connection:
                    if move_row is not None:
                        connection.execute(
                            """
                            UPDATE knowledge_source_files
                            SET relative_path=?, knowledge_item_id=?, content_hash=?, size_bytes=?,
                                modified_ns=?, status='indexed', last_error=NULL, last_seen_scan=?,
                                updated_at=CURRENT_TIMESTAMP WHERE id=?
                            """,
                            (
                                relative_path,
                                item_id,
                                digest,
                                size_bytes,
                                modified_ns,
                                scan_token,
                                move_row["id"],
                            ),
                        )
                        missing.pop(str(move_row["relative_path"]), None)
                        move_candidates.remove(move_row)
                        moved += 1
                    elif existing is None:
                        connection.execute(
                            """
                            INSERT INTO knowledge_source_files(
                                source_id, relative_path, knowledge_item_id, content_hash,
                                size_bytes, modified_ns, status, last_seen_scan
                            ) VALUES (?, ?, ?, ?, ?, ?, 'indexed', ?)
                            """,
                            (source_id, relative_path, item_id, digest, size_bytes, modified_ns, scan_token),
                        )
                        indexed += 1
                    else:
                        connection.execute(
                            """
                            UPDATE knowledge_source_files
                            SET knowledge_item_id=?, content_hash=?, size_bytes=?, modified_ns=?,
                                status='indexed', last_error=NULL, last_seen_scan=?, updated_at=CURRENT_TIMESTAMP
                            WHERE id=?
                            """,
                            (item_id, digest, size_bytes, modified_ns, scan_token, existing["id"]),
                        )
                        updated += 1
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                _record_file_error(
                    source_id,
                    relative_path,
                    size_bytes=discovered_size,
                    modified_ns=discovered_mtime,
                    scan_token=scan_token,
                    message=message,
                    existing_id=int(existing["id"]) if existing else None,
                    knowledge_item_id=int(existing["knowledge_item_id"]) if existing and existing.get("knowledge_item_id") else None,
                )
                errors += 1

        for relative_path, row in list(missing.items()):
            item_id = row.get("knowledge_item_id")
            with db() as connection:
                if item_id:
                    connection.execute("DELETE FROM knowledge_items WHERE id=?", (item_id,))
                connection.execute("DELETE FROM knowledge_source_files WHERE id=?", (row["id"],))
            removed += 1

        completed_at = _utc_now()
        summary_error = f"{errors} file(s) could not be indexed." if errors else None
        with db() as connection:
            connection.execute(
                """
                UPDATE knowledge_sources
                SET status=?, last_scan_completed_at=?, last_error=?,
                    last_scan_file_count=?, last_scan_indexed_count=?, last_scan_updated_count=?,
                    last_scan_moved_count=?, last_scan_removed_count=?, last_scan_skipped_count=?,
                    last_scan_error_count=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    "ready" if source["enabled"] else "paused",
                    completed_at,
                    summary_error,
                    len(candidates),
                    indexed,
                    updated,
                    moved,
                    removed,
                    skipped,
                    errors,
                    source_id,
                ),
            )
        return {
            "source": _decorate_source(_source_row(source_id)),
            "scan": {
                "files": len(candidates),
                "indexed": indexed,
                "updated": updated,
                "moved": moved,
                "removed": removed,
                "skipped": skipped,
                "errors": errors,
                "started_at": started_at,
                "completed_at": completed_at,
            },
        }
    finally:
        _SCAN_LOCK.release()


def delete_source(source_id: int, *, keep_indexed: bool = False) -> dict[str, Any]:
    if not _SCAN_LOCK.acquire(blocking=False):
        raise KnowledgeSourceError("Another knowledge-source operation is already running.", status_code=409)
    try:
        source = _source_row(source_id)
        with db() as connection:
            rows = connection.execute(
                "SELECT knowledge_item_id FROM knowledge_source_files WHERE source_id=? AND knowledge_item_id IS NOT NULL",
                (source_id,),
            ).fetchall()
            if not keep_indexed:
                for row in rows:
                    connection.execute("DELETE FROM knowledge_items WHERE id=?", (row["knowledge_item_id"],))
            else:
                for row in rows:
                    connection.execute(
                        """
                        UPDATE knowledge_items
                        SET kind='document', updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (row["knowledge_item_id"],),
                    )
            cursor = connection.execute("DELETE FROM knowledge_sources WHERE id=?", (source_id,))
            if cursor.rowcount == 0:
                raise KnowledgeSourceError("Knowledge source not found.", status_code=404)
        return {"deleted": True, "path": source["path"], "kept_indexed": bool(keep_indexed)}
    finally:
        _SCAN_LOCK.release()


def scan_due_sources() -> dict[str, int]:
    now = datetime.now(timezone.utc)
    due: list[int] = []
    for source in list_sources():
        if not source["enabled"]:
            continue
        last_raw = source.get("last_scan_completed_at")
        if not last_raw:
            due.append(int(source["id"]))
            continue
        try:
            parsed = datetime.fromisoformat(str(last_raw).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age = (now - parsed.astimezone(timezone.utc)).total_seconds()
        except (TypeError, ValueError):
            age = float(source["scan_interval_seconds"])
        if age >= int(source["scan_interval_seconds"]):
            due.append(int(source["id"]))

    scanned = failed = 0
    for source_id in due:
        try:
            scan_source(source_id)
            scanned += 1
        except KnowledgeSourceError:
            failed += 1
    return {"scanned": scanned, "failed": failed}


class KnowledgeSourceScheduler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="homeserver-knowledge-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=8)
        self._thread = None

    def _run(self) -> None:
        if self._stop.wait(3.0):
            return
        while not self._stop.is_set():
            try:
                scan_due_sources()
            except Exception:
                pass
            self._stop.wait(15.0)


scheduler = KnowledgeSourceScheduler()
