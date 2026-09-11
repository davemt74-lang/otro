CREATE TABLE IF NOT EXISTS agent_workflow_supervisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_app_key TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    plan_id INTEGER NOT NULL,
    team_run_id INTEGER,
    rehydration_id INTEGER NOT NULL UNIQUE,
    starting_state_fingerprint TEXT NOT NULL,
    final_rehydration_id INTEGER,
    final_state_fingerprint TEXT,
    max_steps INTEGER NOT NULL DEFAULT 2 CHECK (max_steps BETWEEN 1 AND 2),
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','stopped','conflict','error')),
    step_count INTEGER NOT NULL DEFAULT 0 CHECK (step_count BETWEEN 0 AND 2),
    steps_json TEXT NOT NULL DEFAULT '[]',
    stop_boundary TEXT NOT NULL DEFAULT '',
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    FOREIGN KEY (plan_id) REFERENCES agent_team_plans(id) ON DELETE CASCADE,
    FOREIGN KEY (team_run_id) REFERENCES agent_team_runs(id) ON DELETE SET NULL,
    FOREIGN KEY (rehydration_id) REFERENCES agent_workflow_rehydrations(id) ON DELETE CASCADE,
    FOREIGN KEY (final_rehydration_id) REFERENCES agent_workflow_rehydrations(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_workflow_supervisions_source_conversation
    ON agent_workflow_supervisions(source_app_key, conversation_id, plan_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_agent_workflow_supervisions_status
    ON agent_workflow_supervisions(status, updated_at DESC, id DESC);
