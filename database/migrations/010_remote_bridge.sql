CREATE TABLE IF NOT EXISTS remote_bridge_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    broker_url TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO remote_bridge_settings(id, enabled, broker_url)
VALUES (1, 0, '');

CREATE TABLE IF NOT EXISTS remote_bridge_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT NOT NULL,
    status TEXT NOT NULL,
    operation TEXT,
    request_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_remote_bridge_events_created_at
ON remote_bridge_events(created_at DESC);
