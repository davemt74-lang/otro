ALTER TABLE conversation_context_settings ADD COLUMN include_awareness INTEGER NOT NULL DEFAULT 1 CHECK (include_awareness IN (0,1));
ALTER TABLE agent_runs ADD COLUMN awareness_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE agent_memory ADD COLUMN memory_type TEXT NOT NULL DEFAULT 'semantic';
ALTER TABLE agent_memory ADD COLUMN source_app_key TEXT;
ALTER TABLE agent_memory ADD COLUMN source_event_id INTEGER;
ALTER TABLE agent_memory ADD COLUMN confidence REAL NOT NULL DEFAULT 0.75 CHECK (confidence BETWEEN 0 AND 1);
ALTER TABLE agent_memory ADD COLUMN reinforcement_count INTEGER NOT NULL DEFAULT 1;
ALTER TABLE agent_memory ADD COLUMN last_accessed_at TEXT;
ALTER TABLE agent_memory ADD COLUMN expires_at TEXT;
ALTER TABLE agent_memory ADD COLUMN entity_type TEXT;
ALTER TABLE agent_memory ADD COLUMN entity_key TEXT;

CREATE TABLE IF NOT EXISTS cognitive_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    source_app_key TEXT NOT NULL,
    source_kind TEXT NOT NULL DEFAULT 'app' CHECK (source_kind IN ('owner','app','plugin','system')),
    plugin_key TEXT,
    event_type TEXT NOT NULL,
    entity_type TEXT,
    entity_key TEXT,
    correlation_id TEXT,
    conversation_id TEXT,
    summary TEXT NOT NULL,
    importance REAL NOT NULL DEFAULT 0.5 CHECK (importance BETWEEN 0 AND 1),
    privacy_scope TEXT NOT NULL DEFAULT 'private' CHECK (privacy_scope IN ('private','shared')),
    memory_candidate INTEGER NOT NULL DEFAULT 0 CHECK (memory_candidate IN (0,1)),
    memory_type TEXT,
    memory_key TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_app_key, event_id),
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_cognitive_events_created ON cognitive_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cognitive_events_source ON cognitive_events(source_app_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cognitive_events_type ON cognitive_events(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cognitive_events_entity ON cognitive_events(entity_type, entity_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cognitive_events_correlation ON cognitive_events(correlation_id, created_at DESC);

CREATE TABLE IF NOT EXISTS cognition_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_row_id INTEGER NOT NULL,
    job_type TEXT NOT NULL CHECK (job_type IN ('awareness','memory_candidate','plugin_dispatch')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','completed','failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    UNIQUE (event_row_id, job_type),
    FOREIGN KEY (event_row_id) REFERENCES cognitive_events(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_cognition_jobs_status ON cognition_jobs(status, available_at, id);

CREATE TABLE IF NOT EXISTS awareness_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    entity_type TEXT,
    entity_key TEXT,
    importance REAL NOT NULL DEFAULT 0.5 CHECK (importance BETWEEN 0 AND 1),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','dismissed')),
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    source_apps_json TEXT NOT NULL DEFAULT '[]',
    first_event_id INTEGER,
    last_event_id INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TEXT,
    FOREIGN KEY (first_event_id) REFERENCES cognitive_events(id) ON DELETE SET NULL,
    FOREIGN KEY (last_event_id) REFERENCES cognitive_events(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_awareness_status_importance ON awareness_items(status, importance DESC, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_awareness_entity ON awareness_items(entity_type, entity_key, last_seen_at DESC);

CREATE TABLE IF NOT EXISTS memory_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_event_id INTEGER,
    source_awareness_id INTEGER,
    agent_id INTEGER,
    memory_type TEXT NOT NULL DEFAULT 'episodic',
    memory_key TEXT,
    content TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.75 CHECK (confidence BETWEEN 0 AND 1),
    importance REAL NOT NULL DEFAULT 0.5 CHECK (importance BETWEEN 0 AND 1),
    entity_type TEXT,
    entity_key TEXT,
    source_app_key TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','rejected')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at TEXT,
    consolidated_memory_id INTEGER,
    FOREIGN KEY (source_event_id) REFERENCES cognitive_events(id) ON DELETE SET NULL,
    FOREIGN KEY (source_awareness_id) REFERENCES awareness_items(id) ON DELETE SET NULL,
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE SET NULL,
    FOREIGN KEY (consolidated_memory_id) REFERENCES agent_memory(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_candidates_status ON memory_candidates(status, importance DESC, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_candidate_event_unique ON memory_candidates(source_event_id) WHERE source_event_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_candidate_awareness_unique ON memory_candidates(source_awareness_id) WHERE source_awareness_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS plugins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plugin_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','revoked')),
    trusted INTEGER NOT NULL DEFAULT 0 CHECK (trusted IN (0,1)),
    manifest_json TEXT NOT NULL DEFAULT '{}',
    installed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_plugins_status ON plugins(status, plugin_key);

CREATE TABLE IF NOT EXISTS plugin_event_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plugin_id INTEGER NOT NULL,
    event_pattern TEXT NOT NULL,
    handler_key TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (plugin_id, event_pattern, handler_key),
    FOREIGN KEY (plugin_id) REFERENCES plugins(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_plugin_subscriptions_enabled ON plugin_event_subscriptions(enabled, event_pattern);

CREATE TABLE IF NOT EXISTS cognition_cursors (
    cursor_key TEXT PRIMARY KEY,
    cursor_value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO cognition_cursors(cursor_key, cursor_value) VALUES ('activity_log_id', '0');
