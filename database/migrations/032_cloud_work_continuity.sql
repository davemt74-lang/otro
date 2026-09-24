CREATE TABLE IF NOT EXISTS cloud_work_continuity (
    continuity_key TEXT PRIMARY KEY,
    cloud_run_id INTEGER NOT NULL,
    cloud_action_id INTEGER NOT NULL,
    source_app_key TEXT NOT NULL DEFAULT 'vp3',
    conversation_id TEXT NULL,
    operation TEXT NOT NULL DEFAULT 'agent.chat',
    status TEXT NOT NULL DEFAULT 'queued'
      CHECK (status IN ('queued','running','completed','failed','cancel_requested','cancelled')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    result_json TEXT NULL,
    error_class TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    started_at TEXT NULL,
    heartbeat_at TEXT NULL,
    completed_at TEXT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cloud_work_continuity_status
ON cloud_work_continuity(status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_cloud_work_continuity_cloud_job
ON cloud_work_continuity(cloud_run_id, cloud_action_id);
