"""Tracky 1E3: restart-safe redacted supervised-session evidence.

Shares HomeServer's existing system_settings and runtime_certification_runs.
Every record is a software observation, never real hardware certification.
Passive reads never activate hardware, replay sessions or advance consent.
"""
from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timezone
from typing import Any

from ..database import db
from . import system_state

CONTRACT = "tracky.native.supervised-evidence.v1e3"
KEY = "tracky.native.supervised-evidence.v1e3"
TEST_KEY = "tracky_native_supervised_session"
_BOOT = secrets.token_hex(12)
_LOCK = threading.RLock()
_TERMINAL = frozenset({"completed", "stopped", "failed", "interrupted"})
_REASONS = frozenset({
    "completed", "owner_stopped", "stopped_or_privacy", "privacy_engaged",
    "owner_presence_expired", "time_limit", "observation_unavailable",
    "startup_failed", "interrupted_by_restart", "acceptance_revoked", "unknown",
    "watchdog_stall", "cpu_budget_exhausted",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _saved() -> dict[str, Any]:
    row = system_state._read_setting(KEY, {})
    return row if isinstance(row, dict) else {}


def _public(row: dict[str, Any]) -> dict[str, Any]:
    phase = str(row.get("phase") or "never_started")
    restarted = phase == "running" and row.get("boot_id") != _BOOT
    if restarted:
        phase = "interrupted"
    return {
        "contract": CONTRACT,
        "owner_surface": str(row.get("owner_surface") or "native_supervised")[:30],
        "phase": phase,
        "reason": "interrupted_by_restart" if restarted else str(row.get("reason") or "")[:50],
        "started_at": str(row.get("started_at") or "")[:40],
        "finished_at": str(row.get("finished_at") or "")[:40],
        "requested_samples": max(0, min(12, int(row.get("requested_samples") or 0))),
        "completed_samples": max(0, min(12, int(row.get("completed_samples") or 0))),
        "current_process": row.get("boot_id") == _BOOT,
        "recover_before_new_session": restarted,
        "automatic_resume": False,
        "hardware_certified": False,
        "identity_recognition": False,
        "raw_media_retained": False,
    }


def latest() -> dict[str, Any]:
    # A read remains passive even if an old session was interrupted by a crash.
    return _public(_saved())


def _finish_locked(row: dict[str, Any], phase: str, reason: str,
                   completed_samples: int) -> dict[str, Any]:
    if phase not in _TERMINAL:
        raise ValueError("Invalid supervised-session result")
    reason = reason if reason in _REASONS else "unknown"
    count = max(0, min(int(row["requested_samples"]), int(completed_samples)))
    run_id = str(row["run_id"])
    updated = dict(row, phase=phase, reason=reason, completed_samples=count,
                   finished_at=_now())
    clean = {
        "phase": phase, "reason": reason,
        "owner_surface": str(row.get("owner_surface") or "native_supervised")[:30],
        "requested_samples": int(row["requested_samples"]),
        "completed_samples": count,
        "restarted": phase == "interrupted",
        "owner_supervised": True,
        "physical_hardware_certified": False,
        "identity_recognition_certified": False,
        "raw_media_retained": False,
    }
    # Atomically close the current session and add exactly one terminal record.
    with db() as connection:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO runtime_certification_runs"
            "(id,test_key,status,duration_ms,evidence_json) VALUES(?,?,?,?,?)",
            ("tracky-session-" + run_id, TEST_KEY,
             "failed" if phase in {"interrupted", "failed"} else "not_verified",
             0, json.dumps(clean, sort_keys=True, separators=(",", ":"))),
        )
        if cursor.rowcount:
            connection.execute(
                "INSERT INTO system_settings(setting_key,value_json) VALUES(?,?) "
                "ON CONFLICT(setting_key) DO UPDATE SET "
                "value_json=excluded.value_json,updated_at=CURRENT_TIMESTAMP",
                (KEY, json.dumps(updated, sort_keys=True, separators=(",", ":"))),
            )
    return _public(updated)


def recover_prior(*, worker_active: bool = False) -> dict[str, Any]:
    """Only called on a new owner-approved start, never from passive status."""
    with _LOCK:
        row = _saved()
        if row.get("phase") == "running":
            if row.get("boot_id") != _BOOT or not worker_active:
                return _finish_locked(row, "interrupted", "interrupted_by_restart",
                                      row.get("completed_samples") or 0)
            raise RuntimeError("A supervised session is still active")
        return _public(row)


def begin(*, sample_count: int, owner_surface: str = "native_supervised") -> str:
    if owner_surface not in {"native_supervised", "agent_eyes"}:
        raise ValueError("Invalid owner surface")
    if type(sample_count) is not int or not 1 <= sample_count <= 12:
        raise ValueError("Invalid sample limit")
    with _LOCK:
        prior = _saved()
        if prior.get("phase") == "running":
            raise RuntimeError("Previous supervised session must be reconciled")
        run_id = secrets.token_hex(12)
        row = {
            "run_id": run_id, "boot_id": _BOOT, "phase": "running",
            "reason": "owner_approved", "requested_samples": sample_count,
            "owner_surface": owner_surface,
            "completed_samples": 0, "started_at": _now(), "finished_at": "",
        }
        system_state._write_setting(KEY, row)
        return run_id


def finish(*, run_id: str, phase: str, reason: str,
           completed_samples: int) -> dict[str, Any]:
    with _LOCK:
        row = _saved()
        if not row or row.get("run_id") != run_id:
            raise ValueError("Unknown supervised session")
        if row.get("phase") in _TERMINAL:
            return _public(row)
        if row.get("boot_id") != _BOOT:
            raise ValueError("Previous process cannot close a restarted session")
        return _finish_locked(row, phase, reason, completed_samples)


def history(limit: int = 10) -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute(
            "SELECT status, evidence_json, created_at FROM runtime_certification_runs "
            "WHERE test_key=? ORDER BY rowid DESC LIMIT ?",
            (TEST_KEY, min(30, max(1, int(limit)))),
        ).fetchall()
    return [
        {
            "status": row["status"], "created_at": row["created_at"],
            "evidence": json.loads(row["evidence_json"] or "{}"),
            "hardware_certified": False,
        } for row in rows
    ]
