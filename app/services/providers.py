from __future__ import annotations

from urllib.parse import urlparse

import httpx

from ..database import db


class ProviderError(RuntimeError):
    pass


_ALLOWED_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


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


def get_ollama() -> dict:
    with db() as connection:
        row = connection.execute(
            """
            SELECT provider_key, kind, name, base_url, model, enabled, created_at, updated_at
            FROM model_providers WHERE provider_key='ollama' LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise ProviderError("Ollama provider is not initialized.")
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    return item


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
        response = httpx.get(f"{url}/api/tags", timeout=4.0)
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


def generate_ollama(messages: list[dict[str, str]], model_override: str | None = None) -> dict:
    provider = get_ollama()
    if not provider["enabled"]:
        raise ProviderError("Ollama is disabled. Enable it in My Agent before starting a chat.")
    model = (model_override or provider["model"] or "").strip()
    if not model:
        raise ProviderError("No Ollama model is configured.")
    url = normalize_loopback_url(provider["base_url"])

    try:
        response = httpx.post(
            f"{url}/api/chat",
            json={"model": model, "messages": messages, "stream": False, "options": {"num_predict": 1024}},
            timeout=120.0,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderError(f"Ollama could not complete the request at {url}.") from exc

    message = payload.get("message") if isinstance(payload, dict) else None
    content = str(message.get("content") or "").strip() if isinstance(message, dict) else ""
    if not content:
        raise ProviderError("Ollama returned an empty response.")
    return {"provider": "ollama", "model": model, "content": content}
