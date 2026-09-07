from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import httpx

from ..database import db
from . import provider_secrets


class ProviderError(RuntimeError):
    pass


_ALLOWED_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_PROVIDER_ORDER = ("ollama", "anthropic", "openai", "openrouter")
_EXTERNAL_BASE_URLS = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


def normalize_loopback_url(value: str) -> str:
    raw = value.strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ProviderError("Ollama URL must use http:// or https://.")
    if (parsed.hostname or "").lower() not in _ALLOWED_LOOPBACK_HOSTS:
        raise ProviderError("Ollama must run on this device (localhost, 127.0.0.1, or ::1).")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProviderError("Ollama URL cannot contain credentials, a query string, or a fragment.")
    if parsed.path not in {"", "/"}:
        raise ProviderError("Ollama URL must point to the local server root, for example http://127.0.0.1:11434.")
    return raw


def _provider_row(provider_key: str) -> dict:
    with db() as connection:
        row = connection.execute(
            """
            SELECT provider_key, kind, name, base_url, model, enabled, created_at, updated_at
            FROM model_providers WHERE provider_key=? LIMIT 1
            """,
            (provider_key,),
        ).fetchone()
    if row is None:
        raise ProviderError(f"Provider is not initialized: {provider_key}")
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    return item


def get_ollama() -> dict:
    return _provider_row("ollama")


def save_ollama(base_url: str, model: str, enabled: bool) -> dict:
    normalized_url = normalize_loopback_url(base_url)
    model_name = model.strip()[:160]
    if enabled and not model_name:
        raise ProviderError("Select an Ollama model before enabling the provider.")
    with db() as connection:
        connection.execute(
            """
            INSERT INTO model_providers(provider_key, kind, name, base_url, model, enabled)
            VALUES ('ollama', 'ollama', 'Ollama', ?, ?, ?)
            ON CONFLICT(provider_key) DO UPDATE SET
                base_url=excluded.base_url,
                model=excluded.model,
                enabled=excluded.enabled,
                updated_at=CURRENT_TIMESTAMP
            """,
            (normalized_url, model_name, 1 if enabled else 0),
        )
    return get_ollama()


def discover_ollama_models(base_url: str | None = None) -> dict:
    provider = get_ollama()
    url = normalize_loopback_url(base_url or provider["base_url"])
    try:
        with httpx.Client(timeout=4.0, trust_env=False) as client:
            response = client.get(f"{url}/api/tags")
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderError(f"Ollama is not reachable at {url}.") from exc

    models: list[str] = []
    for item in payload.get("models", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "").strip()
        if name and name not in models:
            models.append(name)
    return {"reachable": True, "base_url": url, "models": models}


def get_inference_settings() -> dict:
    with db() as connection:
        row = connection.execute(
            "SELECT preferred_provider, updated_at FROM inference_settings WHERE id=1 LIMIT 1"
        ).fetchone()
    if row is None:
        return {"preferred_provider": "auto", "updated_at": None}
    return dict(row)


def save_inference_settings(preferred_provider: str) -> dict:
    preferred = str(preferred_provider or "auto").strip().lower()
    if preferred not in {"auto", *_PROVIDER_ORDER}:
        raise ProviderError("Unknown inference provider.")
    with db() as connection:
        connection.execute(
            """
            INSERT INTO inference_settings(id, preferred_provider)
            VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET
                preferred_provider=excluded.preferred_provider,
                updated_at=CURRENT_TIMESTAMP
            """,
            (preferred,),
        )
    return get_inference_settings()


def save_external_provider(provider_key: str, model: str, enabled: bool) -> dict:
    key = str(provider_key or "").strip().lower()
    if key not in _EXTERNAL_BASE_URLS:
        raise ProviderError("Only Anthropic, OpenAI and OpenRouter can be configured here.")
    model_name = str(model or "").strip()[:200]
    if enabled and not model_name:
        raise ProviderError("Set a model before enabling this provider.")
    if enabled and not provider_secrets.get_api_key(key):
        raise ProviderError("Save this provider's API key before enabling it.")
    base_url = _EXTERNAL_BASE_URLS[key]
    names = {"anthropic": "Claude / Anthropic", "openai": "OpenAI", "openrouter": "OpenRouter"}
    kinds = {"anthropic": "anthropic", "openai": "openai", "openrouter": "openai-compatible"}
    with db() as connection:
        connection.execute(
            """
            INSERT INTO model_providers(provider_key, kind, name, base_url, model, enabled)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_key) DO UPDATE SET
                base_url=excluded.base_url,
                model=excluded.model,
                enabled=excluded.enabled,
                updated_at=CURRENT_TIMESTAMP
            """,
            (key, kinds[key], names[key], base_url, model_name, 1 if enabled else 0),
        )
    return _provider_row(key)


def list_inference_providers() -> list[dict]:
    try:
        credential_state = provider_secrets.credential_status().get("providers", {})
    except provider_secrets.ProviderSecretError:
        credential_state = {}
    items: list[dict] = []
    for key in _PROVIDER_ORDER:
        try:
            item = _provider_row(key)
        except ProviderError:
            continue
        if key == "ollama":
            item["credential_configured"] = True
            item["compute_source"] = "homeserver_local"
            item["ready"] = bool(item["enabled"] and item["model"])
        else:
            configured = bool(credential_state.get(key, {}).get("configured"))
            item["credential_configured"] = configured
            item["credential_suffix"] = credential_state.get(key, {}).get("suffix", "")
            item["compute_source"] = "user_provider"
            item["ready"] = bool(item["enabled"] and item["model"] and configured)
        items.append(item)
    return items


def inference_status() -> dict:
    settings = get_inference_settings()
    providers = list_inference_providers()
    ready = {item["provider_key"]: item for item in providers if item.get("ready")}
    preferred = settings.get("preferred_provider") or "auto"
    selection: dict | None = None
    if preferred != "auto" and preferred in ready:
        selection = ready[preferred]
    if selection is None:
        for key in _PROVIDER_ORDER:
            if key in ready:
                selection = ready[key]
                break
    return {
        "available": selection is not None,
        "preferred_provider": preferred,
        "selected_provider": selection.get("provider_key") if selection else None,
        "model": selection.get("model") if selection else None,
        "compute_source": selection.get("compute_source") if selection else None,
        "cloud_fallback_required": selection is None,
        "providers": providers,
    }


def _selected_provider(model_override: str | None = None) -> tuple[dict, str]:
    status = inference_status()
    key = status.get("selected_provider")
    if not key:
        raise ProviderError(
            "No HomeServer inference provider is ready. Configure a local Ollama model or enable a provider in AGENT BRAIN."
        )
    provider = _provider_row(str(key))
    model = str(model_override or provider.get("model") or "").strip()
    if not model:
        raise ProviderError("No model is configured for the selected inference provider.")
    return provider, model


def _normalize_ollama_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    raw_calls = message.get("tool_calls")
    if not isinstance(raw_calls, list):
        return calls
    for index, raw in enumerate(raw_calls):
        if not isinstance(raw, dict):
            continue
        function = raw.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        arguments = function.get("arguments")
        if not name or not isinstance(arguments, dict):
            continue
        normalized_function: dict[str, Any] = {"name": name, "arguments": arguments}
        if isinstance(function.get("index"), int):
            normalized_function["index"] = function["index"]
        calls.append({
            "id": str(raw.get("id") or f"ollama_{index}"),
            "type": "function",
            "function": normalized_function,
        })
    return calls


def _normalize_openai_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    raw_calls = message.get("tool_calls")
    if not isinstance(raw_calls, list):
        return calls
    for index, raw in enumerate(raw_calls):
        if not isinstance(raw, dict):
            continue
        function = raw.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        arguments_raw = function.get("arguments")
        try:
            arguments = json.loads(arguments_raw) if isinstance(arguments_raw, str) else arguments_raw
        except (ValueError, json.JSONDecodeError):
            arguments = {}
        if not name or not isinstance(arguments, dict):
            continue
        calls.append({
            "id": str(raw.get("id") or f"call_{index}"),
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        })
    return calls


def _usage(prompt: int = 0, completion: int = 0, total: int | None = None) -> dict[str, int]:
    prompt_value = max(0, int(prompt or 0))
    completion_value = max(0, int(completion or 0))
    return {
        "prompt_tokens": prompt_value,
        "completion_tokens": completion_value,
        "total_tokens": max(0, int(total if total is not None else prompt_value + completion_value)),
    }


def _generate_ollama_step(
    provider: dict,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    url = normalize_loopback_url(provider["base_url"])
    request_body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"num_predict": 1024},
    }
    if tools:
        request_body["tools"] = tools
    try:
        with httpx.Client(timeout=120.0, trust_env=False) as client:
            response = client.post(f"{url}/api/chat", json=request_body)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderError(f"Ollama could not complete the request at {url}.") from exc
    message = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(message, dict):
        raise ProviderError("Ollama returned an invalid chat response.")
    content = str(message.get("content") or "").strip()
    tool_calls = _normalize_ollama_tool_calls(message)
    if not content and not tool_calls:
        raise ProviderError("Ollama returned an empty response.")
    return {
        "provider": "ollama",
        "model": model,
        "content": content,
        "tool_calls": tool_calls,
        "usage": _usage(payload.get("prompt_eval_count", 0), payload.get("eval_count", 0)),
    }


def _openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        if role == "tool":
            output.append({
                "role": "tool",
                "tool_call_id": str(message.get("tool_call_id") or ""),
                "content": str(message.get("content") or ""),
            })
            continue
        item: dict[str, Any] = {"role": role, "content": str(message.get("content") or "")}
        raw_calls = message.get("tool_calls")
        if role == "assistant" and isinstance(raw_calls, list) and raw_calls:
            converted = []
            for index, call in enumerate(raw_calls):
                function = call.get("function") if isinstance(call, dict) else None
                if not isinstance(function, dict):
                    continue
                converted.append({
                    "id": str(call.get("id") or f"call_{index}"),
                    "type": "function",
                    "function": {
                        "name": str(function.get("name") or ""),
                        "arguments": json.dumps(function.get("arguments") or {}, separators=(",", ":")),
                    },
                })
            if converted:
                item["tool_calls"] = converted
        output.append(item)
    return output


def _generate_openai_compatible_step(
    provider: dict,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    key = provider["provider_key"]
    api_key = provider_secrets.get_api_key(key)
    if not api_key:
        raise ProviderError(f"{provider['name']} API key is not configured.")
    request_body: dict[str, Any] = {
        "model": model,
        "messages": _openai_messages(messages),
        "stream": False,
        "max_tokens": 1024,
    }
    if tools:
        request_body["tools"] = tools
        request_body["tool_choice"] = "auto"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if key == "openrouter":
        headers["HTTP-Referer"] = "https://vp3.me"
        headers["X-Title"] = "VP3 HomeServer"
    url = str(provider["base_url"]).rstrip("/") + "/chat/completions"
    try:
        with httpx.Client(timeout=120.0, trust_env=True) as client:
            response = client.post(url, json=request_body, headers=headers)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderError(f"{provider['name']} could not complete the request.") from exc
    choices = payload.get("choices") if isinstance(payload, dict) else None
    message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise ProviderError(f"{provider['name']} returned an invalid chat response.")
    content = str(message.get("content") or "").strip()
    tool_calls = _normalize_openai_tool_calls(message)
    if not content and not tool_calls:
        raise ProviderError(f"{provider['name']} returned an empty response.")
    raw_usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return {
        "provider": key,
        "model": str(payload.get("model") or model),
        "content": content,
        "tool_calls": tool_calls,
        "usage": _usage(
            raw_usage.get("prompt_tokens", 0),
            raw_usage.get("completion_tokens", 0),
            raw_usage.get("total_tokens"),
        ),
    }


def _anthropic_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in tools or []:
        function = item.get("function") if isinstance(item, dict) else None
        if not isinstance(function, dict):
            continue
        result.append({
            "name": str(function.get("name") or ""),
            "description": str(function.get("description") or ""),
            "input_schema": function.get("parameters") if isinstance(function.get("parameters"), dict) else {"type": "object"},
        })
    return [item for item in result if item["name"]]


def _anthropic_payload_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    output: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = str(message.get("content") or "")
        if role == "system":
            if content:
                system_parts.append(content)
            continue
        if role == "tool":
            tool_id = str(message.get("tool_call_id") or "")
            if tool_id:
                output.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": content}],
                })
            continue
        if role == "assistant" and isinstance(message.get("tool_calls"), list) and message["tool_calls"]:
            blocks: list[dict[str, Any]] = []
            if content:
                blocks.append({"type": "text", "text": content})
            for index, call in enumerate(message["tool_calls"]):
                function = call.get("function") if isinstance(call, dict) else None
                if not isinstance(function, dict):
                    continue
                blocks.append({
                    "type": "tool_use",
                    "id": str(call.get("id") or f"toolu_{index}"),
                    "name": str(function.get("name") or ""),
                    "input": function.get("arguments") if isinstance(function.get("arguments"), dict) else {},
                })
            output.append({"role": "assistant", "content": blocks})
            continue
        output.append({"role": "assistant" if role == "assistant" else "user", "content": content})
    return "\n\n".join(system_parts), output


def _generate_anthropic_step(
    provider: dict,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    api_key = provider_secrets.get_api_key("anthropic")
    if not api_key:
        raise ProviderError("Claude / Anthropic API key is not configured.")
    system, converted = _anthropic_payload_messages(messages)
    request_body: dict[str, Any] = {"model": model, "max_tokens": 1024, "messages": converted}
    if system:
        request_body["system"] = system
    converted_tools = _anthropic_tools(tools)
    if converted_tools:
        request_body["tools"] = converted_tools
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    url = str(provider["base_url"]).rstrip("/") + "/v1/messages"
    try:
        with httpx.Client(timeout=120.0, trust_env=True) as client:
            response = client.post(url, json=request_body, headers=headers)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderError("Claude / Anthropic could not complete the request.") from exc
    blocks = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(blocks, list):
        raise ProviderError("Claude / Anthropic returned an invalid chat response.")
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = str(block.get("text") or "").strip()
            if text:
                text_parts.append(text)
        elif block.get("type") == "tool_use":
            name = str(block.get("name") or "").strip()
            arguments = block.get("input") if isinstance(block.get("input"), dict) else {}
            if name:
                tool_calls.append({
                    "id": str(block.get("id") or f"toolu_{index}"),
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                })
    content = "\n\n".join(text_parts)
    if not content and not tool_calls:
        raise ProviderError("Claude / Anthropic returned an empty response.")
    raw_usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    prompt = raw_usage.get("input_tokens", 0)
    completion = raw_usage.get("output_tokens", 0)
    return {
        "provider": "anthropic",
        "model": str(payload.get("model") or model),
        "content": content,
        "tool_calls": tool_calls,
        "usage": _usage(prompt, completion),
    }


def generate_step(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    model_override: str | None = None,
) -> dict[str, Any]:
    provider, model = _selected_provider(model_override)
    key = provider["provider_key"]
    if key == "ollama":
        return _generate_ollama_step(provider, model, messages, tools)
    if key == "anthropic":
        return _generate_anthropic_step(provider, model, messages, tools)
    if key in {"openai", "openrouter"}:
        return _generate_openai_compatible_step(provider, model, messages, tools)
    raise ProviderError("Selected provider is not supported.")


def generate(messages: list[dict[str, Any]], model_override: str | None = None) -> dict:
    generated = generate_step(messages, model_override=model_override)
    if not generated["content"]:
        raise ProviderError("Inference provider returned no final response text.")
    return generated


def generate_ollama_step(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    model_override: str | None = None,
) -> dict[str, Any]:
    provider = get_ollama()
    if not provider["enabled"]:
        raise ProviderError("Ollama is disabled. Enable it in AGENT BRAIN before starting a local chat.")
    model = str(model_override or provider["model"] or "").strip()
    if not model:
        raise ProviderError("No Ollama model is configured.")
    return _generate_ollama_step(provider, model, messages, tools)


def generate_ollama(messages: list[dict[str, Any]], model_override: str | None = None) -> dict:
    generated = generate_ollama_step(messages, model_override=model_override)
    if not generated["content"]:
        raise ProviderError("Ollama returned no final response text.")
    return generated
