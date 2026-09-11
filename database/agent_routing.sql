CREATE TABLE IF NOT EXISTS app_agent_grants (
    paired_app_id INTEGER NOT NULL,
    agent_id INTEGER NOT NULL,
    allowed INTEGER NOT NULL DEFAULT 1 CHECK (allowed IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (paired_app_id, agent_id),
    FOREIGN KEY (paired_app_id) REFERENCES paired_apps(id) ON DELETE CASCADE,
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_app_agent_grants_agent
ON app_agent_grants(agent_id, allowed);
