CREATE TABLE IF NOT EXISTS agent_workflow_automations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_app_key TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    plan_id INTEGER NOT NULL,
    trigger_type TEXT NOT NULL CHECK (trigger_type IN ('once','interval','activity')),
    trigger_config_json TEXT NOT NULL DEFAULT '{}',
    next_run_at TEXT,
    last_activity_id INTEGER NOT NULL DEFAULT 0 CHECK (last_activity_id >= 0),
    max_steps INTEGER NOT NULL DEFAULT 2 CHECK (max_steps BETWEEN 1 AND 2),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    last_fired_at TEXT,
    last_status TEXT NOT NULL DEFAULT 'idle' CHECK (last_status IN ('idle','claimed','completed','stopped','conflict','error','disabled')),
    last_result_json TEXT NOT NULL DEFAULT '{}',
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    FOREIGN KEY (plan_id) REFERENCES agent_team_plans(id) ON DELETE CASCADE,
    UNIQUE (source_app_key, conversation_id, plan_id, trigger_type, trigger_config_json)
);

CREATE INDEX IF NOT EXISTS idx_agent_workflow_automations_due
    ON agent_workflow_automations(enabled, trigger_type, next_run_at, id);
CREATE INDEX IF NOT EXISTS idx_agent_workflow_automations_activity
    ON agent_workflow_automations(enabled, trigger_type, last_activity_id, id);
CREATE INDEX IF NOT EXISTS idx_agent_workflow_automations_source
    ON agent_workflow_automations(source_app_key, conversation_id, plan_id, id DESC);

CREATE TABLE IF NOT EXISTS agent_workflow_automation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    automation_id INTEGER NOT NULL,
    trigger_key TEXT NOT NULL,
    trigger_type TEXT NOT NULL CHECK (trigger_type IN ('once','interval','activity')),
    trigger_value TEXT NOT NULL DEFAULT '',
    rehydration_id INTEGER,
    state_fingerprint TEXT,
    supervision_id INTEGER,
    status TEXT NOT NULL DEFAULT 'claimed' CHECK (status IN ('claimed','running','completed','stopped','conflict','error')),
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (automation_id) REFERENCES agent_workflow_automations(id) ON DELETE CASCADE,
    FOREIGN KEY (rehydration_id) REFERENCES agent_workflow_rehydrations(id) ON DELETE SET NULL,
    FOREIGN KEY (supervision_id) REFERENCES agent_workflow_supervisions(id) ON DELETE SET NULL,
    UNIQUE (automation_id, trigger_key)
);

CREATE INDEX IF NOT EXISTS idx_agent_workflow_automation_runs_automation
    ON agent_workflow_automation_runs(automation_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_agent_workflow_automation_runs_status
    ON agent_workflow_automation_runs(status, id DESC);