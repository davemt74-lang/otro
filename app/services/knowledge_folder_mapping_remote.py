from __future__ import annotations

import re
from typing import Callable

import httpx

from ..config import settings
from . import remote_bridge

_MAPPING_ID = re.compile(r"^source-\d{1,18}$")


def _token(value: str | None) -> str:
    token = str(value or "").strip()
    if len(token) < 20 or len(token) > 512:
        raise remote_bridge.RemoteBridgeError("A paired-app bearer token is required for knowledge operations.")
    return token


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
    """Install v0.62 folder/collection write operations over the fail-closed bridge."""
    if getattr(remote_bridge, "_knowledge_folder_mapping_v062_installed", False):
        return

    original: Callable[[str, dict | None, str | None], dict] = remote_bridge.dispatch_remote_request

    def extended(operation: str, payload: dict | None, bearer_token: str | None = None) -> dict:
        op = str(operation or "").strip()
        if op not in {
            "knowledge.collections.list",
            "knowledge.folders.list",
            "knowledge.folder.map",
            "knowledge.folder.unmap",
            "knowledge.item.write",
        }:
            return original(operation, payload, bearer_token)

        token = _token(bearer_token)
        body = payload if isinstance(payload, dict) else {}
        headers = {"Authorization": f"Bearer {token}"}
        base_url = f"http://{settings.host}:{settings.port}"
        timeout = 660.0 if op == "knowledge.folder.map" else 30.0

        with httpx.Client(base_url=base_url, timeout=timeout, trust_env=False) as client:
            if op == "knowledge.collections.list":
                return _local_response(client.get("/api/v1/knowledge/collections-v062", headers=headers))
            if op == "knowledge.folders.list":
                return _local_response(client.get("/api/v1/knowledge/folder-mappings-v062", headers=headers))
            if op == "knowledge.folder.map":
                return _local_response(
                    client.post("/api/v1/knowledge/folder-mappings-v062", json=body, headers=headers)
                )
            if op == "knowledge.item.write":
                return _local_response(client.post("/api/v1/knowledge/items-v062", json=body, headers=headers))

            mapping_id = str(body.get("mapping_id") or "").strip()
            if not _MAPPING_ID.fullmatch(mapping_id):
                raise remote_bridge.RemoteBridgeError("knowledge.folder.unmap requires a valid mapping_id.")
            return _local_response(
                client.delete(f"/api/v1/knowledge/folder-mappings-v062/{mapping_id}", headers=headers)
            )

    remote_bridge.dispatch_remote_request = extended
    remote_bridge._knowledge_folder_mapping_v062_installed = True
