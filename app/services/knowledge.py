from __future__ import annotations

import hashlib
import io
import json
import re
import uuid
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from docx import Document
from pypdf import PdfReader

from ..config import settings
from ..database import db

SUPPORTED_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".csv",
    ".html",
    ".htm",
    ".pdf",
    ".docx",
}


class KnowledgeImportError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self) -> str:
        return "\n".join(self.parts)


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_text(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise KnowledgeImportError(f"Unsupported file type. Supported types: {allowed}")

    if suffix == ".pdf":
        reader = PdfReader(io.BytesIO(data))
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
    elif suffix == ".docx":
        document = Document(io.BytesIO(data))
        parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        text = "\n".join(parts)
    elif suffix in {".html", ".htm"}:
        parser = _HTMLTextExtractor()
        parser.feed(_decode_text(data))
        text = parser.text()
    elif suffix == ".json":
        raw = _decode_text(data)
        try:
            parsed = json.loads(raw)
            text = json.dumps(parsed, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            text = raw
    else:
        text = _decode_text(data)

    normalized = _normalize_text(text)
    if not normalized:
        raise KnowledgeImportError("No readable text could be extracted from this file.")
    return normalized


def _chunk_text(text: str, chunk_size: int = 1800, overlap: int = 200) -> list[tuple[int, int, int, str]]:
    text = text.strip()
    if not text:
        return [(0, 0, 0, "")]

    chunks: list[tuple[int, int, int, str]] = []
    start = 0
    index = 0
    length = len(text)

    while start < length:
        ideal_end = min(start + chunk_size, length)
        end = ideal_end

        if ideal_end < length:
            search_start = min(ideal_end, start + int(chunk_size * 0.55))
            window = text[search_start:ideal_end]
            candidates = [
                window.rfind("\n\n"),
                window.rfind("\n"),
                window.rfind(". "),
                window.rfind(" "),
            ]
            cut = max(candidates)
            if cut > 0:
                end = search_start + cut + 1

        if end <= start:
            end = min(start + chunk_size, length)

        chunk = text[start:end].strip()
        if chunk:
            chunks.append((index, start, end, chunk))
            index += 1

        if end >= length:
            break
        start = max(end - overlap, start + 1)

    return chunks or [(0, 0, 0, "")]


def _replace_chunks(connection, item_id: int, title: str, content: str) -> int:
    connection.execute("DELETE FROM knowledge_chunks WHERE knowledge_item_id=?", (item_id,))
    chunks = _chunk_text(content)
    for chunk_index, char_start, char_end, chunk in chunks:
        connection.execute(
            """
            INSERT INTO knowledge_chunks(knowledge_item_id, chunk_index, char_start, char_end, content)
            VALUES (?, ?, ?, ?, ?)
            """,
            (item_id, chunk_index, char_start, char_end, chunk),
        )
    return len(chunks)


def ensure_knowledge_index() -> int:
    indexed = 0
    with db() as connection:
        rows = connection.execute(
            """
            SELECT ki.id, ki.title, COALESCE(ki.content, '') AS content
            FROM knowledge_items ki
            WHERE NOT EXISTS (
                SELECT 1 FROM knowledge_chunks kc WHERE kc.knowledge_item_id=ki.id
            )
            ORDER BY ki.id
            """
        ).fetchall()
        for row in rows:
            _replace_chunks(connection, row["id"], row["title"], row["content"])
            indexed += 1
    return indexed


def rebuild_knowledge_index() -> dict[str, int]:
    with db() as connection:
        rows = connection.execute(
            "SELECT id, title, COALESCE(content, '') AS content FROM knowledge_items ORDER BY id"
        ).fetchall()
        total_chunks = 0
        for row in rows:
            total_chunks += _replace_chunks(connection, row["id"], row["title"], row["content"])
    return {"items": len(rows), "chunks": total_chunks}


def create_knowledge_item(
    title: str,
    kind: str,
    content: str,
    source_path: str | None = None,
) -> dict[str, Any]:
    normalized = _normalize_text(content)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else None
    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO knowledge_items(title, kind, source_path, content, content_hash)
            VALUES (?, ?, ?, ?, ?)
            """,
            (title.strip(), kind.strip().lower(), source_path, normalized, digest),
        )
        item_id = int(cursor.lastrowid)
        chunk_count = _replace_chunks(connection, item_id, title.strip(), normalized)
    return {"created": True, "id": item_id, "chunk_count": chunk_count}


def ingest_document(filename: str, media_type: str | None, data: bytes) -> dict[str, Any]:
    original_name = Path(filename or "").name.strip()
    if not original_name:
        raise KnowledgeImportError("A file name is required.")
    if len(data) > settings.max_upload_bytes:
        raise KnowledgeImportError(
            f"File exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB upload limit.",
            status_code=413,
        )

    suffix = Path(original_name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise KnowledgeImportError(f"Unsupported file type. Supported types: {allowed}")

    file_hash = hashlib.sha256(data).hexdigest()
    with db() as connection:
        duplicate = connection.execute(
            """
            SELECT ki.id, ki.title, kd.original_name
            FROM knowledge_items ki
            JOIN knowledge_documents kd ON kd.knowledge_item_id=ki.id
            WHERE ki.kind='document' AND ki.content_hash=?
            LIMIT 1
            """,
            (file_hash,),
        ).fetchone()
    if duplicate is not None:
        return {
            "created": False,
            "duplicate": True,
            "id": duplicate["id"],
            "title": duplicate["title"],
            "original_name": duplicate["original_name"],
        }

    try:
        extracted = extract_text(original_name, data)
    except KnowledgeImportError:
        raise
    except Exception as exc:
        raise KnowledgeImportError(f"Unable to read {original_name}: {exc}") from exc

    settings.knowledge_files_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    target = settings.knowledge_files_dir / stored_name
    target.write_bytes(data)

    title = Path(original_name).stem.strip()[:240] or original_name[:240]
    metadata = {
        "original_name": original_name,
        "media_type": media_type or "",
        "size_bytes": len(data),
    }

    try:
        with db() as connection:
            cursor = connection.execute(
                """
                INSERT INTO knowledge_items(title, kind, source_path, content, content_hash, metadata_json)
                VALUES (?, 'document', ?, ?, ?, ?)
                """,
                (
                    title,
                    original_name,
                    extracted,
                    file_hash,
                    json.dumps(metadata, separators=(",", ":")),
                ),
            )
            item_id = int(cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO knowledge_documents(
                    knowledge_item_id, stored_name, original_name, media_type, size_bytes
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (item_id, stored_name, original_name, media_type, len(data)),
            )
            chunk_count = _replace_chunks(connection, item_id, title, extracted)
    except Exception:
        target.unlink(missing_ok=True)
        raise

    return {
        "created": True,
        "duplicate": False,
        "id": item_id,
        "title": title,
        "original_name": original_name,
        "size_bytes": len(data),
        "chunk_count": chunk_count,
    }


def _item_rows(connection, item_ids: list[int] | None = None, limit: int = 250):
    where = ""
    params: list[Any] = []
    if item_ids is not None:
        if not item_ids:
            return []
        placeholders = ",".join("?" for _ in item_ids)
        where = f"WHERE ki.id IN ({placeholders})"
        params.extend(item_ids)
    params.append(limit)
    return connection.execute(
        f"""
        SELECT
            ki.id,
            ki.title,
            ki.kind,
            CASE WHEN ki.kind='watched_document' THEN NULL ELSE ki.source_path END AS source_path,
            ki.content,
            ki.created_at,
            ki.updated_at,
            kd.original_name,
            kd.media_type,
            kd.size_bytes,
            kd.imported_at,
            (
                SELECT COUNT(*) FROM knowledge_chunks kc
                WHERE kc.knowledge_item_id=ki.id
            ) AS chunk_count
        FROM knowledge_items ki
        LEFT JOIN knowledge_documents kd ON kd.knowledge_item_id=ki.id
        {where}
        ORDER BY ki.id DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()


def _fts_expression(query: str) -> str:
    tokens = [token for token in re.findall(r"\w+", query, flags=re.UNICODE) if token]
    tokens = tokens[:12]
    return " AND ".join(f'"{token.replace(chr(34), "")}"' for token in tokens)


def list_knowledge(query: str = "", limit: int = 250) -> list[dict[str, Any]]:
    query = query.strip()
    with db() as connection:
        if not query:
            return [dict(row) | {"snippet": None} for row in _item_rows(connection, limit=limit)]

        expression = _fts_expression(query)
        if not expression:
            term = f"%{query}%"
            rows = connection.execute(
                """
                SELECT id FROM knowledge_items
                WHERE title LIKE ? OR content LIKE ?
                ORDER BY id DESC LIMIT ?
                """,
                (term, term, limit),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            items = [dict(row) for row in _item_rows(connection, ids, limit=limit)]
            by_id = {item["id"]: item for item in items}
            return [by_id[item_id] | {"snippet": None} for item_id in ids if item_id in by_id]

        matches = connection.execute(
            """
            SELECT
                CAST(knowledge_item_id AS INTEGER) AS knowledge_item_id,
                snippet(knowledge_chunks_fts, 0, '', '', ' … ', 32) AS snippet,
                bm25(knowledge_chunks_fts) AS score
            FROM knowledge_chunks_fts
            WHERE knowledge_chunks_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (expression, max(limit * 4, 50)),
        ).fetchall()

        ordered_ids: list[int] = []
        snippets: dict[int, str] = {}
        for match in matches:
            item_id = int(match["knowledge_item_id"])
            if item_id in snippets:
                continue
            ordered_ids.append(item_id)
            snippets[item_id] = match["snippet"] or ""
            if len(ordered_ids) >= limit:
                break

        items = [dict(row) for row in _item_rows(connection, ordered_ids, limit=limit)]
        by_id = {item["id"]: item for item in items}
        return [
            by_id[item_id] | {"snippet": snippets.get(item_id)}
            for item_id in ordered_ids
            if item_id in by_id
        ]


def delete_knowledge_item(item_id: int) -> bool:
    stored_name: str | None = None
    with db() as connection:
        document = connection.execute(
            "SELECT stored_name FROM knowledge_documents WHERE knowledge_item_id=?",
            (item_id,),
        ).fetchone()
        if document is not None:
            stored_name = document["stored_name"]

        cursor = connection.execute("DELETE FROM knowledge_items WHERE id=?", (item_id,))
        if cursor.rowcount == 0:
            return False

    if stored_name:
        (settings.knowledge_files_dir / stored_name).unlink(missing_ok=True)
    return True