CREATE TABLE IF NOT EXISTS homeserver_apps (
    app_id TEXT PRIMARY KEY,
    app_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    app_class TEXT NOT NULL CHECK(app_class IN ('system','user')),
    source_type TEXT NOT NULL CHECK(source_type IN ('vp3_system','user_created','zip','git','agent_builder','app_store')),
    lifecycle_state TEXT NOT NULL CHECK(lifecycle_state IN ('draft','installing','installed','starting','running','degraded','stopped','updating','recovering','failed','uninstalling','archived')),
    installed_version TEXT NOT NULL DEFAULT '',
    desired_version TEXT NOT NULL DEFAULT '',
    source_ref TEXT NOT NULL DEFAULT '',
    owner_key TEXT NOT NULL DEFAULT 'local_owner',
    protected_system_app INTEGER NOT NULL DEFAULT 0 CHECK(protected_system_app IN (0,1)),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_homeserver_apps_class_state ON homeserver_apps(app_class,lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_homeserver_apps_source ON homeserver_apps(source_type);

CREATE TABLE IF NOT EXISTS homeserver_app_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT,
    actor_type TEXT NOT NULL DEFAULT 'owner',
    actor_key TEXT NOT NULL DEFAULT 'local_owner',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(app_id) REFERENCES homeserver_apps(app_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_homeserver_app_events_app ON homeserver_app_events(app_id,id DESC);
