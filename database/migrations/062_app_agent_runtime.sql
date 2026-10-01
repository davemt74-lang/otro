CREATE TABLE IF NOT EXISTS homeserver_app_ai_policies (
    app_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    cloud_allowed INTEGER NOT NULL DEFAULT 0 CHECK (cloud_allowed IN (0,1)),
    max_daily_requests INTEGER NOT NULL DEFAULT 50 CHECK (max_daily_requests BETWEEN 1 AND 10000),
    max_prompt_chars INTEGER NOT NULL DEFAULT 12000 CHECK (max_prompt_chars BETWEEN 1000 AND 32000),
    max_context_chars INTEGER NOT NULL DEFAULT 8000 CHECK (max_context_chars BETWEEN 0 AND 24000),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(app_id) REFERENCES homeserver_apps(app_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS homeserver_app_ai_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_key TEXT NOT NULL UNIQUE,
    app_id TEXT NOT NULL,
    job_id TEXT,
    request_kind TEXT NOT NULL DEFAULT 'app_agent',
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','succeeded','failed','cancelled')),
    compute_source TEXT NOT NULL DEFAULT '',
    provider_key TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    prompt_chars INTEGER NOT NULL DEFAULT 0,
    context_chars INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    FOREIGN KEY(app_id) REFERENCES homeserver_apps(app_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_homeserver_app_ai_runs_app_created
ON homeserver_app_ai_runs(app_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_homeserver_app_ai_runs_status
ON homeserver_app_ai_runs(status, created_at DESC, id DESC);
