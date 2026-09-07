from __future__ import annotations

import json
import uuid
from typing import Any

from ..database import db


class UsageError(RuntimeError):
    pass


def record_usage(
    *,
    source_app_key: str,
    compute_source: str,
    provider_key: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int | None = None,
    billable_tokens: int = 0,
    balance_after_tokens: int | None = None,
    request_kind: str = "chat",
    event_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict:
    if compute_source not in {"homeserver_local", "user_provider", "vp3_cloud"}:
        raise UsageError("Invalid compute source.")
    prompt = max(0, int(prompt_tokens or 0))
    completion = max(0, int(completion_tokens or 0))
    total = max(0, int(total_tokens if total_tokens is not None else prompt + completion))
    billable = max(0, int(billable_tokens or 0))
    balance = None if balance_after_tokens is None else max(0, int(balance_after_tokens))
    key = str(event_id or uuid.uuid4().hex).strip()[:160]
    if not key:
        raise UsageError("Usage event id is required.")
    with db() as connection:
        connection.execute(
            """
            INSERT INTO inference_usage_events(
                event_id, source_app_key, compute_source, provider_key, model,
                request_kind, prompt_tokens, completion_tokens, total_tokens,
                billable_tokens, balance_after_tokens, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO NOTHING
            """,
            (
                key,
                str(source_app_key or "owner")[:160],
                compute_source,
                str(provider_key or "")[:80],
                str(model or "")[:200],
                str(request_kind or "chat")[:80],
                prompt,
                completion,
                total,
                billable,
                balance,
                json.dumps(metadata or {}, separators=(",", ":")),
            ),
        )
        row = connection.execute(
            """
            SELECT id, event_id, source_app_key, compute_source, provider_key, model,
                   request_kind, prompt_tokens, completion_tokens, total_tokens,
                   billable_tokens, balance_after_tokens, created_at
            FROM inference_usage_events WHERE event_id=?
            """,
            (key,),
        ).fetchone()
    if row is None:
        raise UsageError("Usage event could not be recorded.")
    return dict(row)


def _usage_where(
    *,
    compute_source: str | None = None,
    source_app_key: str | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if compute_source:
        if compute_source not in {"homeserver_local", "user_provider", "vp3_cloud"}:
            raise UsageError("Invalid compute source filter.")
        clauses.append("compute_source=?")
        params.append(compute_source)
    if source_app_key is not None:
        source = str(source_app_key).strip()
        if not source:
            raise UsageError("Invalid usage source filter.")
        clauses.append("source_app_key=?")
        params.append(source[:160])
    return ("WHERE " + " AND ".join(clauses) if clauses else "", params)


def list_usage(
    limit: int = 200,
    compute_source: str | None = None,
    source_app_key: str | None = None,
) -> list[dict]:
    bounded = max(1, min(int(limit), 1000))
    where, params = _usage_where(
        compute_source=compute_source,
        source_app_key=source_app_key,
    )
    params.append(bounded)
    with db() as connection:
        rows = connection.execute(
            f"""
            SELECT id, event_id, source_app_key, compute_source, provider_key, model,
                   request_kind, prompt_tokens, completion_tokens, total_tokens,
                   billable_tokens, balance_after_tokens, created_at
            FROM inference_usage_events
            {where}
            ORDER BY id DESC LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def usage_summary(source_app_key: str | None = None) -> dict:
    where, params = _usage_where(source_app_key=source_app_key)
    with db() as connection:
        row = connection.execute(
            f"""
            SELECT
              COALESCE(SUM(CASE WHEN compute_source='vp3_cloud' THEN billable_tokens ELSE 0 END), 0) AS cloud_tokens_debited,
              COALESCE(SUM(CASE WHEN compute_source='vp3_cloud' THEN total_tokens ELSE 0 END), 0) AS cloud_model_tokens,
              COALESCE(SUM(CASE WHEN compute_source!='vp3_cloud' THEN total_tokens ELSE 0 END), 0) AS homeserver_tokens,
              COUNT(CASE WHEN compute_source='vp3_cloud' THEN 1 END) AS cloud_requests,
              COUNT(CASE WHEN compute_source!='vp3_cloud' THEN 1 END) AS homeserver_requests
            FROM inference_usage_events
            {where}
            """,
            params,
        ).fetchone()

        balance_clauses = ["compute_source='vp3_cloud'", "balance_after_tokens IS NOT NULL"]
        balance_params: list[Any] = []
        if source_app_key is not None:
            source = str(source_app_key).strip()
            if not source:
                raise UsageError("Invalid usage source filter.")
            balance_clauses.append("source_app_key=?")
            balance_params.append(source[:160])
        balance_where = " AND ".join(balance_clauses)
        balance = connection.execute(
            f"""
            SELECT balance_after_tokens FROM inference_usage_events
            WHERE {balance_where}
            ORDER BY id DESC LIMIT 1
            """,
            balance_params,
        ).fetchone()
    result = dict(row) if row else {}
    result["balance_tokens"] = balance["balance_after_tokens"] if balance else None
    return result
