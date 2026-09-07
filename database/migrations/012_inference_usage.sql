CREATE TABLE IF NOT EXISTS inference_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    preferred_provider TEXT NOT NULL DEFAULT 'auto' CHECK (preferred_provider IN ('auto','ollama','anthropic','openai','openrouter')),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO inference_settings(id, preferred_provider) VALUES (1, 'auto');

INSERT OR IGNORE INTO model_providers(provider_key, kind, name, base_url, model, enabled)
VALUES
    ('anthropic', 'anthropic', 'Claude / Anthropic', 'https://api.anthropic.com', '', 0),
    ('openai', 'openai', 'OpenAI', 'https://api.openai.com/v1', '', 0),
    ('openrouter', 'openai-compatible', 'OpenRouter', 'https://openrouter.ai/api/v1', '', 0);

CREATE TABLE IF NOT EXISTS inference_usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    source_app_key TEXT NOT NULL DEFAULT 'owner',
    compute_source TEXT NOT NULL CHECK (compute_source IN ('homeserver_local','user_provider','vp3_cloud')),
    provider_key TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    request_kind TEXT NOT NULL DEFAULT 'chat',
    prompt_tokens INTEGER NOT NULL DEFAULT 0 CHECK (prompt_tokens >= 0),
    completion_tokens INTEGER NOT NULL DEFAULT 0 CHECK (completion_tokens >= 0),
    total_tokens INTEGER NOT NULL DEFAULT 0 CHECK (total_tokens >= 0),
    billable_tokens INTEGER NOT NULL DEFAULT 0 CHECK (billable_tokens >= 0),
    balance_after_tokens INTEGER CHECK (balance_after_tokens IS NULL OR balance_after_tokens >= 0),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_inference_usage_created
ON inference_usage_events(created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_inference_usage_source
ON inference_usage_events(compute_source, created_at DESC);
