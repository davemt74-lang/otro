CREATE TABLE IF NOT EXISTS homeserver_app_sources (
    source_id TEXT PRIMARY KEY,
    app_key TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK(source_type IN ('zip','git')),
    source_ref TEXT NOT NULL,
    source_revision TEXT NOT NULL DEFAULT '',
    package_sha256 TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    validation_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('inspected','installed','detached','failed')),
    cache_path TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_homeserver_app_sources_app ON homeserver_app_sources(app_key,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_homeserver_app_sources_status ON homeserver_app_sources(status,created_at DESC);

CREATE TABLE IF NOT EXISTS homeserver_app_source_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    app_key TEXT NOT NULL,
    event_type TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(source_id) REFERENCES homeserver_app_sources(source_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_homeserver_app_source_events_source ON homeserver_app_source_events(source_id,id DESC);
