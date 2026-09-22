PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS vp3_rollout_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    release_channel TEXT NOT NULL DEFAULT 'stable'
        CHECK (release_channel IN ('stable','beta','dev')),
    rollout_ring TEXT NOT NULL DEFAULT 'pilot'
        CHECK (rollout_ring IN ('pilot','staged','broad')),
    automatic_apply INTEGER NOT NULL DEFAULT 0 CHECK (automatic_apply IN (0,1)),
    watchdog_enabled INTEGER NOT NULL DEFAULT 1 CHECK (watchdog_enabled IN (0,1)),
    max_failed_starts INTEGER NOT NULL DEFAULT 3 CHECK (max_failed_starts BETWEEN 1 AND 10),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO vp3_rollout_settings(
    id,release_channel,rollout_ring,automatic_apply,watchdog_enabled,max_failed_starts
) VALUES (1,'stable','pilot',0,1,3)
ON CONFLICT(id) DO NOTHING;

CREATE TABLE IF NOT EXISTS vp3_hardware_certifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_key TEXT NOT NULL,
    os_version TEXT NOT NULL,
    hardware_revision TEXT,
    firmware_version TEXT,
    controller_id TEXT,
    result TEXT NOT NULL CHECK (result IN ('passed','degraded','failed')),
    report_json TEXT NOT NULL DEFAULT '{}',
    certified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_vp3_certifications_profile
ON vp3_hardware_certifications(profile_key,certified_at DESC);

CREATE TABLE IF NOT EXISTS vp3_rollout_packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('stable','beta','dev')),
    package_sha256 TEXT NOT NULL,
    installer_sha256 TEXT NOT NULL,
    package_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('staged','approved','applying','applied','failed','rolled_back','discarded')
    ),
    rollback_backup_name TEXT,
    failure_reason TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    staged_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    approved_at TEXT,
    applied_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_vp3_rollout_packages_status
ON vp3_rollout_packages(status,staged_at DESC);

CREATE TABLE IF NOT EXISTS vp3_rollout_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info'
        CHECK (severity IN ('info','warning','error')),
    summary TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_vp3_rollout_events_created
ON vp3_rollout_events(created_at DESC);
