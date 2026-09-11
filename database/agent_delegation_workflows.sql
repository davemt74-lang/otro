CREATE TABLE IF NOT EXISTS agent_delegation_policy (
    id INTEGER PRIMARY KEY CHECK (id=1),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
    max_context_chars INTEGER NOT NULL DEFAULT 12000 CHECK (max_context_chars BETWEEN 2000 AND 24000),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO agent_delegation_policy(id, enabled, max_context_chars)
VALUES (1, 0, 12000);

CREATE TABLE IF NOT EXISTS agent_delegation_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_app_key TEXT NOT NULL,
    parent_agent_id INTEGER,
    worker_agent_id INTEGER,
    parent_agent_name TEXT NOT NULL,
    worker_agent_name TEXT NOT NULL,
    conversation_id TEXT,
    external_conversation_id TEXT,
    task TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','working','completed','failed','cancelled')),
    result TEXT NOT NULL DEFAULT '',
    error TEXT,
    provider_key TEXT,
    model TEXT,
    agent_run_id INTEGER,
    include_memory INTEGER NOT NULL DEFAULT 1 CHECK (include_memory IN (0,1)),
    include_knowledge INTEGER NOT NULL DEFAULT 1 CHECK (include_knowledge IN (0,1)),
    include_contacts INTEGER NOT NULL DEFAULT 0 CHECK (include_contacts IN (0,1)),
    cloud_allowed INTEGER NOT NULL DEFAULT 1 CHECK (cloud_allowed IN (0,1)),
    max_context_chars INTEGER NOT NULL DEFAULT 12000 CHECK (max_context_chars BETWEEN 2000 AND 24000),
    permission_snapshot_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (parent_agent_id) REFERENCES agents(id) ON DELETE SET NULL,
    FOREIGN KEY (worker_agent_id) REFERENCES agents(id) ON DELETE SET NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE SET NULL,
    FOREIGN KEY (agent_run_id) REFERENCES agent_runs(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_delegation_source_created
    ON agent_delegation_tasks(source_app_key, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_agent_delegation_conversation
    ON agent_delegation_tasks(conversation_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_agent_delegation_parent
    ON agent_delegation_tasks(parent_agent_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_agent_delegation_worker
    ON agent_delegation_tasks(worker_agent_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_agent_delegation_status
    ON agent_delegation_tasks(status, updated_at DESC, id DESC);
