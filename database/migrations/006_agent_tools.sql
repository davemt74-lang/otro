CREATE TABLE IF NOT EXISTS agent_tool_policy (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
    max_calls INTEGER NOT NULL DEFAULT 3 CHECK (max_calls BETWEEN 1 AND 3),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO agent_tool_policy(id, enabled, max_calls)
VALUES (1, 0, 3);

ALTER TABLE agent_runs ADD COLUMN tool_call_count INTEGER NOT NULL DEFAULT 0;
