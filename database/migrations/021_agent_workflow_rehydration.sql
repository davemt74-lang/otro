CREATE TABLE IF NOT EXISTS agent_workflow_rehydrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_app_key TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    plan_id INTEGER NOT NULL,
    team_run_id INTEGER,
    state_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready','conflict')),
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    drift_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    FOREIGN KEY (plan_id) REFERENCES agent_team_plans(id) ON DELETE CASCADE,
    FOREIGN KEY (team_run_id) REFERENCES agent_team_runs(id) ON DELETE SET NULL,
    UNIQUE (source_app_key, conversation_id, plan_id, state_fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_agent_workflow_rehydrations_source_conversation
    ON agent_workflow_rehydrations(source_app_key, conversation_id, plan_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_agent_workflow_rehydrations_status
    ON agent_workflow_rehydrations(status, updated_at DESC, id DESC);
