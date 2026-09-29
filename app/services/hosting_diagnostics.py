from __future__ import annotations

import math
import re
from typing import Any

from ..database import db
from . import hosting_deployment, hosting_public, hosting_runtime, hosting_serving

CONTRACT = "vp3.hosting.diagnostics.v1"
SLOW_REQUEST_MS = 1000.0
_PATH_SAFE = re.compile(r"[^A-Za-z0-9/_\-.~]")


class HostingDiagnosticsError(hosting_runtime.HostingError):
    pass


def _safe_path(value: str) -> str:
    raw = str(value or "").split("?", 1)[0].replace("\\", "/")
    if not raw.startswith("/"):
        raw = "/" + raw.lstrip("/")
    raw = _PATH_SAFE.sub("_", raw)
    return raw[:240] or "/"


def _safe_error_class(value: str) -> str:
    raw = re.sub(r"[^A-Za-z0-9._:-]+", "_", str(value or "").strip().lower())
    return raw[:80]


def observe_request(
    site_id: str,
    *,
    source: str,
    method: str,
    path: str,
    status_code: int,
    duration_ms: float,
    response_bytes: int = 0,
    error_class: str = "",
) -> None:
    hosting_runtime.get_site(site_id)
    src = str(source or "public").strip().lower()
    if src not in {"public", "preview", "health"}:
        src = "public"
    verb = re.sub(r"[^A-Z]", "", str(method or "GET").upper())[:12] or "GET"
    status = max(100, min(599, int(status_code)))
    elapsed = max(0.0, min(float(duration_ms), 3_600_000.0))
    size = max(0, min(int(response_bytes), 2_147_483_647))
    with db() as connection:
        connection.execute(
            """
            INSERT INTO hosting_request_observations(
                site_id,source,method,path_hint,status_code,duration_ms,response_bytes,error_class
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                site_id,
                src,
                verb,
                _safe_path(path),
                status,
                elapsed,
                size,
                _safe_error_class(error_class),
            ),
        )


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(math.ceil((percentile / 100.0) * len(ordered))) - 1))
    return round(float(ordered[index]), 2)


def _last_deploy(site_id: str) -> dict[str, Any] | None:
    deployment = hosting_deployment.deployment_status(site_id)
    active = deployment.get("active_release") or {}
    if not active:
        return None
    return {
        "release_id": str(active.get("release_id") or ""),
        "app_version": str(active.get("app_version") or ""),
        "runtime": str(active.get("runtime") or ""),
        "created_at": str(active.get("created_at") or ""),
        "package_sha256": str(active.get("package_sha256") or ""),
    }


def summary(site_id: str, *, window_minutes: int = 60, recent_limit: int = 30) -> dict[str, Any]:
    site = hosting_runtime.get_site(site_id)
    minutes = max(1, min(int(window_minutes), 24 * 60))
    limit = max(0, min(int(recent_limit), 100))

    with db() as connection:
        rows = connection.execute(
            """
            SELECT source,method,path_hint,status_code,duration_ms,response_bytes,error_class,created_at
            FROM hosting_request_observations
            WHERE site_id=? AND created_at>=datetime('now', ?)
            ORDER BY id DESC
            """,
            (site_id, f"-{minutes} minutes"),
        ).fetchall()

    items = [dict(row) for row in rows]
    durations = [float(row.get("duration_ms") or 0) for row in items]
    requests_total = len(items)
    client_errors = sum(1 for row in items if 400 <= int(row.get("status_code") or 0) < 500)
    server_errors = sum(1 for row in items if int(row.get("status_code") or 0) >= 500)
    php_failures = sum(
        1 for row in items
        if str(row.get("error_class") or "").startswith("php.")
        or str(row.get("error_class") or "") in {"php_runtime", "php_timeout"}
    )
    slow_requests = sum(1 for row in items if float(row.get("duration_ms") or 0) >= SLOW_REQUEST_MS)
    response_bytes = sum(int(row.get("response_bytes") or 0) for row in items)
    usage = hosting_runtime.measure_usage(site_id)
    sqlite = hosting_runtime.database_health(site_id)
    serving = hosting_serving.runtime_health(site_id)
    route = hosting_public.route_status_for_site(site_id)
    last_deploy = _last_deploy(site_id)

    issues: list[str] = []
    if site["state"] in {"failed", "suspended"}:
        issues.append("site_" + str(site["state"]))
    if not serving.get("local_serving_ready") and site["state"] == "active":
        issues.append("runtime_not_ready")
    if not sqlite.get("healthy"):
        issues.append("sqlite_unhealthy")
    if route and not route.get("route_ready"):
        issues.append("public_route_not_ready")
    if server_errors:
        issues.append("recent_5xx")
    if php_failures:
        issues.append("recent_php_failures")
    if slow_requests:
        issues.append("recent_slow_requests")

    recent = []
    for row in items[:limit]:
        recent.append({
            "source": str(row.get("source") or ""),
            "method": str(row.get("method") or ""),
            "path": str(row.get("path_hint") or "/"),
            "status": int(row.get("status_code") or 0),
            "duration_ms": round(float(row.get("duration_ms") or 0), 2),
            "response_bytes": int(row.get("response_bytes") or 0),
            "error_class": str(row.get("error_class") or ""),
            "created_at": str(row.get("created_at") or ""),
        })

    return {
        "contract": CONTRACT,
        "site_id": site_id,
        "window_minutes": minutes,
        "requests_total": requests_total,
        "client_error_total": client_errors,
        "server_error_total": server_errors,
        "php_failure_total": php_failures,
        "slow_request_total": slow_requests,
        "average_duration_ms": round(sum(durations) / requests_total, 2) if requests_total else 0.0,
        "p95_duration_ms": _percentile(durations, 95),
        "max_duration_ms": round(max(durations), 2) if durations else 0.0,
        "response_bytes_total": response_bytes,
        "storage_bytes": int(usage.get("storage_bytes") or 0),
        "sqlite_bytes": int(usage.get("sqlite_bytes") or 0),
        "sqlite": sqlite,
        "runtime": {
            "state": str(site.get("state") or ""),
            "kind": str(site.get("runtime_kind") or ""),
            "serving_ready": bool(serving.get("local_serving_ready")),
            "php_cgi_available": bool(serving.get("php_cgi_available")),
            "scheduler": serving.get("scheduler") or {},
        },
        "route": {
            "configured": bool(route),
            "ready": bool(route and route.get("route_ready")),
            "hostname": str((route or {}).get("hostname") or ""),
            "tls_state": str((route or {}).get("tls_state") or ""),
        },
        "last_deploy": last_deploy,
        "issues": issues,
        "recent": recent,
        "authoritative_source": "homeserver",
    }


def cloud_summary(cloud_site_id: str, *, window_minutes: int = 60, recent_limit: int = 30) -> dict[str, Any]:
    from . import hosting_cloud_control

    projection = hosting_cloud_control.status(str(cloud_site_id or "").strip())
    result = summary(
        str(projection["site_id"]),
        window_minutes=window_minutes,
        recent_limit=recent_limit,
    )
    result["cloud_site_id"] = str(projection["cloud_site_id"])
    return result


def public_capability() -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "request_counts": True,
        "http_4xx_5xx": True,
        "php_failures": True,
        "slow_requests": True,
        "duration_percentiles": True,
        "storage_sqlite_usage": True,
        "last_deploy": True,
        "route_health": True,
        "recent_request_log": True,
        "query_strings_retained": False,
        "request_bodies_retained": False,
        "authorization_headers_retained": False,
        "homeserver_authoritative": True,
        "slow_request_threshold_ms": SLOW_REQUEST_MS,
    }
