CREATE TABLE IF NOT EXISTS shared_agent_snapshots (
    source TEXT PRIMARY KEY,
    version TEXT NOT NULL DEFAULT '',
    revision TEXT NOT NULL DEFAULT '',
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_shared_agent_snapshots_updated
ON shared_agent_snapshots(updated_at);
