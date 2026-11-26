"""Read-only inventory foundation; discovery is not inference certification."""
from __future__ import annotations

import re
from typing import Any

VERSION = "vp3.local-model-inventory.v1"
MAX_MODELS = 256
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}\Z")


def valid_model_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and _MODEL_ID.fullmatch(value) is not None
        and not any(part in {"", ".", ".."} for part in value.split("/"))
        and not value.endswith((":", ".", "-"))
    )


def inventory_snapshot(provider: dict, discovery: dict | None = None) -> dict:
    """Project bounded metadata only; never expose endpoint, credentials or prompts.

    Ollama tags can represent remote/cloud-backed models. A reported tag is not
    proof of local weights, authorization, capability or successful execution.
    No record in this initial inventory is eligible for automatic routing.
    """
    enabled = provider.get("enabled") is True
    configured = provider.get("model")
    default = configured if valid_model_id(configured) else None
    result = {
        "version": VERSION,
        "provider": "ollama",
        "enabled": enabled,
        "configured_model": default,
        "discovery_state": "disabled" if not enabled else "unreachable",
        "configured_model_reported": False,
        "truncated": False,
        "models": [],
    }
    if not enabled or not isinstance(discovery, dict) or discovery.get("reachable") is not True:
        return result
    names = discovery.get("models")
    if not isinstance(names, list):
        result["discovery_state"] = "invalid_response"
        return result
    result["discovery_state"] = "reported"
    seen = set()
    # Bound the inspected input as well as the emitted inventory.
    result["truncated"] = len(names) > MAX_MODELS
    for name in names[:MAX_MODELS]:
        if not valid_model_id(name) or name in seen:
            continue
        seen.add(name)
        result["models"].append({
            "model": name,
            "configured_default": name == default,
            "locality": "unverified",
            "execution_verified": False,
            "routing_eligible": False,
        })
    result["configured_model_reported"] = default in seen
    return result


def collect_inventory() -> dict:
    """Use the existing provider discovery seam; no inference or state mutation."""
    from . import providers

    provider = providers.get_ollama()
    if provider.get("enabled") is not True:
        return inventory_snapshot(provider)
    try:
        discovered = providers.discover_ollama_models(provider["base_url"])
    except providers.ProviderError:
        return inventory_snapshot(provider)
    return inventory_snapshot(provider, discovered)
