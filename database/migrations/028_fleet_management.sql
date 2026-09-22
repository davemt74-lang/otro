PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS vp3_fleet_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
    controller_app_key TEXT,
    device_label TEXT NOT NULL DEFAULT '',
    remote_diagnostics INTEGER NOT NULL DEFAULT 0 CHECK (remote_diagnostics IN (0,1)),
    remote_update_requests INTEGER NOT NULL DEFAULT 0 CHECK (remote_update_requests IN (0,1)),
    remote_support_summary INTEGER NOT NULL DEFAULT 0 CHECK (remote_support_summary IN (0,1)),
    telemetry_interval_seconds INTEGER NOT NULL DEFAULT 300
        CHECK (telemetry_interval_seconds BETWEEN 60 AND 86400),
    stale_after_seconds INTEGER NOT NULL DEFAULT 900
        CHECK (stale_after_seconds BETWEEN 120 AND 604800),
    rollout_failure_threshold INTEGER NOT NULL DEFAULT 2
        CHECK (rollout_failure_threshold BETWEEN 1 AND 100),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO vp3_fleet_settings(
    id,enabled,controller_app_key,device_label,
    remote_diagnostics,remote_update_requests,remote_support_summary,
    telemetry_interval_seconds,stale_after_seconds,rollout_failure_threshold
) VALUES (1,0,NULL,'',0,0,0,300,900,2)
ON CONFLICT(id) DO NOTHING;

CREATE TABLE IF NOT EXISTS vp3_fleet_inventory (
    device_id TEXT PRIMARY KEY,
    label TEXT NOT NULL DEFAULT '',
    profile_key TEXT NOT NULL DEFAULT 'custom',
    os_version TEXT NOT NULL,
    release_channel TEXT NOT NULL CHECK (release_channel IN ('stable','beta','dev')),
    rollout_ring TEXT NOT NULL CHECK (rollout_ring IN ('pilot','staged','broad')),
    commissioning_state TEXT NOT NULL CHECK (commissioning_state IN ('ready','degraded','blocked')),
    certification_result TEXT CHECK (certification_result IN ('passed','degraded','failed')),
    privacy_fault INTEGER NOT NULL DEFAULT 0 CHECK (privacy_fault IN (0,1)),
    update_status TEXT NOT NULL DEFAULT 'idle',
    backup_state TEXT NOT NULL DEFAULT 'unknown'
        CHECK (backup_state IN ('ready','stale','missing','unknown')),
    storage_state TEXT NOT NULL DEFAULT 'ok'
        CHECK (storage_state IN ('ok','low','critical','unknown')),
    watchdog_failures INTEGER NOT NULL DEFAULT 0 CHECK (watchdog_failures >= 0),
    last_seen_at TEXT NOT NULL,
    enrolled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_vp3_fleet_inventory_seen
ON vp3_fleet_inventory(last_seen_at DESC);

CREATE TABLE IF NOT EXISTS vp3_fleet_rollouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    release_version TEXT NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('stable','beta','dev')),
    rollout_ring TEXT NOT NULL CHECK (rollout_ring IN ('pilot','staged','broad')),
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned','active','paused','completed','cancelled')),
    failure_threshold INTEGER NOT NULL DEFAULT 2 CHECK (failure_threshold BETWEEN 1 AND 100),
    pause_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_vp3_fleet_rollouts_status
ON vp3_fleet_rollouts(status,created_at DESC);

CREATE TABLE IF NOT EXISTS vp3_fleet_rollout_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rollout_id INTEGER NOT NULL REFERENCES vp3_fleet_rollouts(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (
        outcome IN ('pending','staged','approved','applying','healthy','failed','rolled_back','offline','degraded')
    ),
    detail_code TEXT NOT NULL DEFAULT '',
    reported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(rollout_id,device_id)
);

CREATE INDEX IF NOT EXISTS idx_vp3_fleet_rollout_outcomes
ON vp3_fleet_rollout_outcomes(rollout_id,outcome);

CREATE TABLE IF NOT EXISTS vp3_fleet_update_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_key TEXT NOT NULL UNIQUE,
    requester_app_key TEXT NOT NULL,
    package_sha256 TEXT NOT NULL,
    release_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_owner'
        CHECK (status IN ('pending_owner','approved','dismissed','unavailable')),
    rollout_id INTEGER REFERENCES vp3_fleet_rollouts(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_vp3_fleet_update_requests_status
ON vp3_fleet_update_requests(status,created_at DESC);

CREATE TABLE IF NOT EXISTS vp3_fleet_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info'
        CHECK (severity IN ('info','warning','error')),
    device_id TEXT,
    rollout_id INTEGER,
    summary TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_vp3_fleet_events_created
ON vp3_fleet_events(created_at DESC);
