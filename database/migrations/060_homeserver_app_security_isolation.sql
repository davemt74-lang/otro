CREATE TABLE IF NOT EXISTS homeserver_app_permission_grants (
    app_id TEXT NOT NULL,
    permission TEXT NOT NULL,
    allowed INTEGER NOT NULL DEFAULT 0 CHECK(allowed IN (0,1)),
    approved_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(app_id,permission),
    FOREIGN KEY(app_id) REFERENCES homeserver_apps(app_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_homeserver_app_permission_allowed
ON homeserver_app_permission_grants(app_id,allowed);

CREATE TABLE IF NOT EXISTS homeserver_app_resource_limits (
    app_id TEXT PRIMARY KEY,
    storage_limit_bytes INTEGER NOT NULL DEFAULT 536870912 CHECK(storage_limit_bytes >= 16777216),
    sqlite_limit_bytes INTEGER NOT NULL DEFAULT 268435456 CHECK(sqlite_limit_bytes >= 8388608),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(app_id) REFERENCES homeserver_apps(app_id) ON DELETE CASCADE
);
