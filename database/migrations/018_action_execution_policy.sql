CREATE TABLE IF NOT EXISTS app_tool_execution_policies (
    paired_app_id INTEGER NOT NULL,
    tool_key TEXT NOT NULL,
    policy_mode TEXT NOT NULL CHECK (policy_mode IN ('read_only','safe_automatic','approval_required','sensitive_high_impact')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (paired_app_id, tool_key),
    FOREIGN KEY (paired_app_id) REFERENCES paired_apps(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_app_tool_execution_policies_mode
ON app_tool_execution_policies(policy_mode, updated_at DESC);

CREATE TABLE IF NOT EXISTS action_policy_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paired_app_id INTEGER,
    source_app_key TEXT NOT NULL,
    tool_key TEXT NOT NULL,
    policy_mode TEXT NOT NULL CHECK (policy_mode IN ('read_only','safe_automatic','approval_required','sensitive_high_impact')),
    decision TEXT NOT NULL CHECK (decision IN ('allowed_read','allowed_automatic','approval_requested','blocked')),
    request_id TEXT,
    reason TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (paired_app_id) REFERENCES paired_apps(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_action_policy_decisions_app
ON action_policy_decisions(paired_app_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_policy_decisions_tool
ON action_policy_decisions(tool_key, created_at DESC);
