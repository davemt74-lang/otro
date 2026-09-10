CREATE TABLE IF NOT EXISTS local_apps (
    app_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    installed_version TEXT NOT NULL,
    catalog_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('installing','installed','updating','failed')),
    install_rel_path TEXT NOT NULL,
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    artifact_manifest_json TEXT NOT NULL DEFAULT '[]',
    manifest_sha256 TEXT,
    source_label TEXT,
    installed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_local_apps_status ON local_apps(status);
