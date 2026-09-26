from __future__ import annotations

import json
import time
from typing import Any, Callable

from . import file_continuity, local_file_actions, tools


FILE_ACTION_KEYS = {"files.update", "files.delete"}
MAX_UPDATE_CHARS = 50000

FILE_ACTION_DEFINITIONS: dict[str, dict[str, Any]] = {
    "files.update": {
        "key": "files.update",
        "name": "Update Local File",
        "description": "Replace text in an owner-approved tracked HomeServer file. Legacy opaque refs remain supported; v2.4 callers may use canonical identity with mutation/revision guards.",
        "mode": "write",
        "required_permissions": ["files.write"],
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "pattern": "^hsf-[0-9]+-[0-9a-f]{16}$", "maxLength": 96},
                "canonical_id": {"type": "string", "pattern": "^fd24_[0-9a-f]{40}$", "maxLength": 45},
                "mutation_id": {"type": "string", "minLength": 8, "maxLength": 128},
                "expected_revision": {"type": "string", "pattern": "^[0-9a-f]{64}$", "maxLength": 64},
                "content": {"type": "string", "minLength": 1, "maxLength": MAX_UPDATE_CHARS},
            },
            "required": ["content"],
            "oneOf": [
                {"required": ["ref"]},
                {"required": ["canonical_id", "mutation_id", "expected_revision"]},
            ],
            "additionalProperties": False,
        },
    },
    "files.delete": {
        "key": "files.delete",
        "name": "Delete Local File",
        "description": "Delete an owner-approved tracked HomeServer file. Legacy opaque refs remain supported; v2.4 callers may use canonical identity with mutation/revision guards.",
        "mode": "write",
        "required_permissions": ["files.write"],
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "pattern": "^hsf-[0-9]+-[0-9a-f]{16}$", "maxLength": 96},
                "canonical_id": {"type": "string", "pattern": "^fd24_[0-9a-f]{40}$", "maxLength": 45},
                "mutation_id": {"type": "string", "minLength": 8, "maxLength": 128},
                "expected_revision": {"type": "string", "pattern": "^[0-9a-f]{64}$", "maxLength": 64},
            },
            "oneOf": [
                {"required": ["ref"]},
                {"required": ["canonical_id", "mutation_id", "expected_revision"]},
            ],
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


def _validate_arguments(tool_key: str, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    canonical_mode = any(
        key in arguments for key in ("canonical_id", "mutation_id", "expected_revision")
    )
    if canonical_mode and "ref" in arguments:
        raise tools.ToolError("Use either a HomeServer file ref or canonical identity, not both.")

    if canonical_mode:
        try:
            if tool_key == "files.update":
                return "federated", file_continuity.normalize_file_update_arguments(arguments)
            return "federated", file_continuity.normalize_file_delete_arguments(arguments)
        except file_continuity.FileContinuityError as exc:
            raise tools.ToolError(str(exc), exc.status_code) from exc

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
        return "legacy", {"ref": ref, "content": content}

    unknown = set(arguments) - {"ref"}
    if unknown:
        raise tools.ToolError(f"Unsupported files.delete argument: {sorted(unknown)[0]}")
    ref = str(arguments.get("ref") or "").strip().lower()
    if not ref:
        raise tools.ToolError("files.delete requires a HomeServer file reference.")
    return "legacy", {"ref": ref}

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
            return file_continuity.safe_file_mutation_meta(tool_key, arguments)
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
        approval_request_id: str | None = None,
    ) -> dict[str, Any]:
        if tool_key not in FILE_ACTION_KEYS:
            return original_execute(
                source_app_key,
                tool_key,
                arguments,
                granted_permissions,
                owner=owner,
                approval_request_id=approval_request_id,
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
            mode, normalized = _validate_arguments(tool_key, payload)
            if mode == "federated":
                if tool_key == "files.update":
                    result = file_continuity.update_federated_file(
                        normalized,
                        source_app_key=source,
                    )
                    result_meta = {
                        "updated": bool(result.get("updated")),
                        "unchanged": bool(result.get("unchanged")),
                        "canonical_id": str((result.get("file") or {}).get("canonical_id") or "")[:45],
                        "federation_version": "2.4",
                        "capability_version": local_file_actions.FILE_ACTION_VERSION,
                    }
                else:
                    result = file_continuity.delete_federated_file(
                        normalized,
                        source_app_key=source,
                    )
                    result_meta = {
                        "deleted": bool(result.get("deleted")),
                        "canonical_id": str(result.get("canonical_id") or "")[:45],
                        "federation_version": "2.4",
                        "capability_version": local_file_actions.FILE_ACTION_VERSION,
                    }
            elif tool_key == "files.update":
                result = local_file_actions.update_file(
                    source,
                    str(normalized["ref"]),
                    str(normalized["content"]),
                )
                result_meta = {
                    "updated": bool(result.get("updated")),
                    "unchanged": bool(result.get("unchanged")),
                    "bytes_written": int(result.get("bytes_written", 0)),
                    "capability_version": local_file_actions.FILE_ACTION_VERSION,
                }
            else:
                result = local_file_actions.delete_file(source, str(normalized["ref"]))
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
