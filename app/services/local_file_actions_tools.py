from __future__ import annotations

import json
import time
from typing import Any, Callable

from . import local_file_actions, tools


FILE_ACTION_KEYS = {"files.update", "files.delete"}
MAX_UPDATE_CHARS = 50000

FILE_ACTION_DEFINITIONS: dict[str, dict[str, Any]] = {
    "files.update": {
        "key": "files.update",
        "name": "Update Local File",
        "description": "Replace the text contents of an existing owner-approved tracked file by opaque HomeServer file reference.",
        "mode": "write",
        "required_permissions": ["files.write"],
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "pattern": "^hsf-[0-9]+-[0-9a-f]{16}$", "maxLength": 96},
                "content": {"type": "string", "minLength": 1, "maxLength": MAX_UPDATE_CHARS},
            },
            "required": ["ref", "content"],
            "additionalProperties": False,
        },
    },
    "files.delete": {
        "key": "files.delete",
        "name": "Delete Local File",
        "description": "Delete an existing owner-approved tracked file by opaque HomeServer file reference.",
        "mode": "write",
        "required_permissions": ["files.write"],
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "pattern": "^hsf-[0-9]+-[0-9a-f]{16}$", "maxLength": 96},
            },
            "required": ["ref"],
            "additionalProperties": False,
        },
    },
}

FILE_ACTION_SKILL = {
    "key": "local.file-management",
    "name": "Local File Management",
    "description": "Read tracked local files and propose bounded updates or deletions through HomeServer policy and approvals.",
    "tools": ["files.list", "files.read", "files.update", "files.delete"],
}


def _validate_arguments(tool_key: str, arguments: dict[str, Any]) -> tuple[str, str | None]:
    if tool_key == "files.update":
        unknown = set(arguments) - {"ref", "content"}
        if unknown:
            raise tools.ToolError(f"Unsupported files.update argument: {sorted(unknown)[0]}")
        ref = str(arguments.get("ref") or "").strip().lower()
        content = arguments.get("content")
        if not ref:
            raise tools.ToolError("files.update requires a HomeServer file reference.")
        if not isinstance(content, str) or not content.strip():
            raise tools.ToolError("files.update requires non-empty text content.")
        if len(content) > MAX_UPDATE_CHARS:
            raise tools.ToolError(f"files.update content exceeds {MAX_UPDATE_CHARS:,} characters.", 413)
        return ref, content

    unknown = set(arguments) - {"ref"}
    if unknown:
        raise tools.ToolError(f"Unsupported files.delete argument: {sorted(unknown)[0]}")
    ref = str(arguments.get("ref") or "").strip().lower()
    if not ref:
        raise tools.ToolError("files.delete requires a HomeServer file reference.")
    return ref, None


def install() -> None:
    """Register file write tools while preserving the canonical tool audit pipeline."""
    if getattr(tools, "_local_file_actions_v039_installed", False):
        return

    for key, definition in FILE_ACTION_DEFINITIONS.items():
        existing = tools.TOOL_DEFINITIONS.get(key)
        if existing not in (None, definition):
            raise RuntimeError(f"Tool definition collision: {key}")
        tools.TOOL_DEFINITIONS[key] = definition

    if not any(item.get("key") == FILE_ACTION_SKILL["key"] for item in tools.SKILL_DEFINITIONS):
        tools.SKILL_DEFINITIONS = (*tools.SKILL_DEFINITIONS, FILE_ACTION_SKILL)

    original_meta: Callable[[str, dict[str, Any]], dict[str, Any]] = tools._safe_argument_metadata

    def safe_argument_metadata(tool_key: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_key in FILE_ACTION_KEYS:
            ref = str(arguments.get("ref") or "")
            content = str(arguments.get("content") or "")
            return {
                "ref_length": len(ref),
                "content_length": len(content),
                "content_bytes": len(content.encode("utf-8")),
                "argument_count": len(arguments),
            }
        return original_meta(tool_key, arguments)

    tools._safe_argument_metadata = safe_argument_metadata
    original_execute = tools.execute_tool

    def execute_tool(
        source_app_key: str,
        tool_key: str,
        arguments: dict[str, Any] | None,
        granted_permissions: set[str] | None = None,
        *,
        owner: bool = False,
    ) -> dict[str, Any]:
        if tool_key not in FILE_ACTION_KEYS:
            return original_execute(
                source_app_key,
                tool_key,
                arguments,
                granted_permissions,
                owner=owner,
            )

        tool = tools._tool_definition(tool_key)
        source = source_app_key.strip() or ("owner" if owner else "app:unknown")
        actor_type = "owner" if owner else "app"
        granted = set(granted_permissions or set())
        required = [] if owner else sorted({tools.TOOL_EXECUTE_PERMISSION, *tool["required_permissions"]})
        payload = dict(arguments or {})
        try:
            encoded = json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise tools.ToolError("Tool arguments must be JSON serializable.") from exc
        if len(encoded.encode("utf-8")) > 65536:
            raise tools.ToolError("Tool arguments exceed the 64 KB limit.", 413)

        arguments_meta = tools._safe_argument_metadata(tool_key, payload)
        policies = tools._policy_map()
        if not policies.get(tool_key, True):
            run_id = tools._record_run(
                tool_key=tool_key,
                source_app_key=source,
                actor_type=actor_type,
                status="denied",
                required_permissions=required,
                arguments_meta=arguments_meta,
                error="Tool is disabled by the HomeServer owner.",
            )
            raise tools.ToolError(f"Tool is disabled by the HomeServer owner. Run {run_id} was recorded.", 403)

        missing = tools._missing_permissions(tool, granted, owner)
        if missing:
            run_id = tools._record_run(
                tool_key=tool_key,
                source_app_key=source,
                actor_type=actor_type,
                status="denied",
                required_permissions=required,
                arguments_meta=arguments_meta,
                error=f"Missing permissions: {', '.join(missing)}",
            )
            raise tools.ToolError(f"Missing tool permissions: {', '.join(missing)}. Run {run_id} was recorded.", 403)

        started = time.perf_counter()
        try:
            ref, content = _validate_arguments(tool_key, payload)
            if tool_key == "files.update":
                result = local_file_actions.update_file(source, ref, content or "")
                result_meta = {
                    "updated": bool(result.get("updated")),
                    "unchanged": bool(result.get("unchanged")),
                    "bytes_written": int(result.get("bytes_written", 0)),
                    "capability_version": local_file_actions.FILE_ACTION_VERSION,
                }
            else:
                result = local_file_actions.delete_file(source, ref)
                result_meta = {
                    "deleted": bool(result.get("deleted")),
                    "capability_version": local_file_actions.FILE_ACTION_VERSION,
                }
        except (tools.ToolError, local_file_actions.LocalFileActionError) as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            run_id = tools._record_run(
                tool_key=tool_key,
                source_app_key=source,
                actor_type=actor_type,
                status="failed",
                required_permissions=required,
                arguments_meta=arguments_meta,
                duration_ms=duration_ms,
                error=str(exc),
            )
            status_code = getattr(exc, "status_code", 422)
            raise tools.ToolError(f"{exc} Run {run_id} was recorded.", status_code) from exc
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            run_id = tools._record_run(
                tool_key=tool_key,
                source_app_key=source,
                actor_type=actor_type,
                status="failed",
                required_permissions=required,
                arguments_meta=arguments_meta,
                duration_ms=duration_ms,
                error="Internal tool failure.",
            )
            raise tools.ToolError(f"Tool failed safely. Run {run_id} was recorded.", 500) from exc

        duration_ms = int((time.perf_counter() - started) * 1000)
        run_id = tools._record_run(
            tool_key=tool_key,
            source_app_key=source,
            actor_type=actor_type,
            status="completed",
            required_permissions=required,
            arguments_meta=arguments_meta,
            result_meta=result_meta,
            duration_ms=duration_ms,
        )
        return {"tool": tool_key, "run_id": run_id, "status": "completed", "result": result}

    tools.execute_tool = execute_tool
    tools._local_file_actions_v039_installed = True
