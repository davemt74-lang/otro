CREATE TABLE IF NOT EXISTS tool_policies (
    tool_key TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO tool_policies(tool_key, enabled) VALUES
    ('knowledge.search', 1),
    ('memory.list', 1),
    ('memory.write', 1);

CREATE TABLE IF NOT EXISTS tool_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_key TEXT NOT NULL,
    source_app_key TEXT NOT NULL,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('owner','app','system')),
    status TEXT NOT NULL CHECK (status IN ('completed','denied','failed')),
    required_permissions_json TEXT NOT NULL DEFAULT '[]',
    arguments_meta_json TEXT NOT NULL DEFAULT '{}',
    result_meta_json TEXT NOT NULL DEFAULT '{}',
    duration_ms INTEGER,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tool_runs_created_at ON tool_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tool_runs_tool_key ON tool_runs(tool_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tool_runs_source ON tool_runs(source_app_key, created_at DESC);
