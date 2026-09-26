-- Tracky V2.73 — OTRO/Cloud physical-context contract join.
-- This schema is Tracky's local semantic authority only. It does NOT replace,
-- extend, or duplicate v2.4 federated authority/reconciliation tables.

CREATE TABLE IF NOT EXISTS tracky_physical_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    sequence_no INTEGER NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'informational',
    confidence REAL NOT NULL DEFAULT 0,
    privacy_class TEXT NOT NULL DEFAULT 'cloud_derived',
    occurred_at TEXT NOT NULL,
    event_json TEXT NOT NULL,
    cloud_synced INTEGER NOT NULL DEFAULT 0,
    cloud_synced_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tracky_physical_events_sync
    ON tracky_physical_events(cloud_synced, sequence_no);

CREATE INDEX IF NOT EXISTS idx_tracky_physical_events_time
    ON tracky_physical_events(occurred_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS tracky_physical_world_state (
    relation_key TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object_id TEXT NOT NULL DEFAULT '',
    value_json TEXT NOT NULL DEFAULT '{}',
    confidence REAL NOT NULL DEFAULT 0,
    temporal_state TEXT NOT NULL DEFAULT 'current',
    source_event_id TEXT NOT NULL DEFAULT '',
    sequence_no INTEGER NOT NULL DEFAULT 0,
    as_of TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tracky_world_subject
    ON tracky_physical_world_state(subject_id, predicate);

CREATE TABLE IF NOT EXISTS tracky_physical_context (
    id INTEGER PRIMARY KEY CHECK(id=1),
    sequence_no INTEGER NOT NULL DEFAULT 0,
    context_json TEXT NOT NULL DEFAULT '{}',
    observed_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tracky_active_perception_requests (
    request_id TEXT PRIMARY KEY,
    correlation_id TEXT NOT NULL,
    request_type TEXT NOT NULL,
    site_id TEXT NOT NULL,
    target_json TEXT NOT NULL DEFAULT '{}',
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK(status IN (
        'requested','accepted','observing','completed','unable','denied','failed','superseded'
    )),
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    requested_by TEXT NOT NULL DEFAULT 'vp3_cloud',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tracky_active_requests_correlation
    ON tracky_active_perception_requests(correlation_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_tracky_active_requests_status
    ON tracky_active_perception_requests(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS tracky_cloud_sync_state (
    id INTEGER PRIMARY KEY CHECK(id=1),
    last_sequence INTEGER NOT NULL DEFAULT 0,
    sync_cursor TEXT NOT NULL DEFAULT '',
    last_attempt_at TEXT,
    last_success_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO tracky_physical_context(id) VALUES (1);
INSERT OR IGNORE INTO tracky_cloud_sync_state(id) VALUES (1);
