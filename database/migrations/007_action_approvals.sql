ALTER TABLE agent_tool_policy
ADD COLUMN allow_write_proposals INTEGER NOT NULL DEFAULT 0 CHECK (allow_write_proposals IN (0,1));

CREATE TABLE IF NOT EXISTS action_requests (
    id TEXT PRIMARY KEY,
    action_key TEXT NOT NULL CHECK (action_key IN ('memory.write')),
    source_app_key TEXT NOT NULL,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('owner','app')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','executing','executed','denied','failed','expired')),
    arguments_json TEXT NOT NULL,
    arguments_meta_json TEXT NOT NULL DEFAULT '{}',
    request_tool_run_id INTEGER REFERENCES tool_runs(id) ON DELETE SET NULL,
    execution_tool_run_id INTEGER REFERENCES tool_runs(id) ON DELETE SET NULL,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL,
    decided_at TEXT,
    executed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_action_requests_status_created
ON action_requests(status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_action_requests_source_created
ON action_requests(source_app_key, created_at DESC);
