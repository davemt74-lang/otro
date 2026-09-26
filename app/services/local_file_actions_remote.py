from __future__ import annotations

import re
from typing import Callable

import httpx

from ..config import settings
from . import remote_bridge


_REF_RE = re.compile(r"^hsf-[0-9]+-[0-9a-f]{16}$")
_MAX_UPDATE_CHARS = 50000


def _token(value: str | None) -> str:
    token = str(value or "").strip()
    if len(token) < 20 or len(token) > 512:
        raise remote_bridge.RemoteBridgeError(
            "A paired-app bearer token is required for governed file actions."
        )
    return token


def _ref(value) -> str:
    ref = str(value or "").strip().lower()
    if not _REF_RE.fullmatch(ref):
        raise remote_bridge.RemoteBridgeError(
            "Governed file actions require a valid HomeServer file reference."
        )
    return ref


def _local_response(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        payload = {"detail": "HomeServer returned a non-JSON response."}
    return {
        "status": int(response.status_code),
        "ok": 200 <= response.status_code < 300,
        "payload": payload,
    }


def install() -> None:
    if getattr(remote_bridge, "_local_file_actions_v039_installed", False):
        return

    original: Callable[[str, dict | None, str | None], dict] = remote_bridge.dispatch_remote_request

    def extended(operation: str, payload: dict | None, bearer_token: str | None = None) -> dict:
        op = str(operation or "").strip()
        if op not in {"files.update", "files.delete"}:
            return original(operation, payload, bearer_token)

        body = payload if isinstance(payload, dict) else {}
        token = _token(bearer_token)
        canonical_mode = any(
            key in body for key in ("canonical_id", "mutation_id", "expected_revision")
        )
        arguments: dict[str, object]
        if canonical_mode:
            if "ref" in body:
                raise remote_bridge.RemoteBridgeError(
                    "Use either a HomeServer file ref or canonical identity, not both."
                )
            allowed = {"canonical_id", "mutation_id", "expected_revision"}
            if op == "files.update":
                allowed.add("content")
            unknown = set(body) - allowed
            if unknown:
                raise remote_bridge.RemoteBridgeError(
                    f"{op} received an unsupported argument: {sorted(unknown)[0]}"
                )
            canonical = str(body.get("canonical_id") or "").strip().lower()
            mutation = str(body.get("mutation_id") or "").strip()
            revision = str(body.get("expected_revision") or "").strip().lower()
            if not re.fullmatch(r"fd24_[0-9a-f]{40}", canonical):
                raise remote_bridge.RemoteBridgeError(f"{op} requires a valid canonical_id.")
            if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", mutation):
                raise remote_bridge.RemoteBridgeError(f"{op} requires a valid mutation_id.")
            if not re.fullmatch(r"[0-9a-f]{64}", revision):
                raise remote_bridge.RemoteBridgeError(f"{op} requires a valid expected_revision.")
            arguments = {
                "canonical_id": canonical,
                "mutation_id": mutation,
                "expected_revision": revision,
            }
            if op == "files.update":
                content = body.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise remote_bridge.RemoteBridgeError("files.update requires non-empty text content.")
                if len(content) > _MAX_UPDATE_CHARS:
                    raise remote_bridge.RemoteBridgeError(
                        f"files.update content exceeds {_MAX_UPDATE_CHARS:,} characters."
                    )
                arguments["content"] = content
        else:
            file_ref = _ref(body.get("ref"))
            arguments = {"ref": file_ref}
            if op == "files.update":
                content = body.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise remote_bridge.RemoteBridgeError("files.update requires non-empty text content.")
                if len(content) > _MAX_UPDATE_CHARS:
                    raise remote_bridge.RemoteBridgeError(
                        f"files.update content exceeds {_MAX_UPDATE_CHARS:,} characters."
                    )
                arguments["content"] = content
                if set(body) - {"ref", "content"}:
                    raise remote_bridge.RemoteBridgeError("files.update accepts only ref and content.")
            elif set(body) - {"ref"}:
                raise remote_bridge.RemoteBridgeError("files.delete accepts only a HomeServer file reference.")

        base_url = f"http://{settings.host}:{settings.port}"
        headers = {"Authorization": f"Bearer {token}"}
        with httpx.Client(base_url=base_url, timeout=125.0, trust_env=False) as client:
            return _local_response(
                client.post(
                    f"/api/v1/tools/{op}/execute",
                    json={"arguments": arguments},
                    headers=headers,
                )
            )

    remote_bridge.dispatch_remote_request = extended
    remote_bridge._local_file_actions_v039_installed = True
