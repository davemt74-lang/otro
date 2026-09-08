CREATE TABLE IF NOT EXISTS app_capability_scopes (
    paired_app_id INTEGER PRIMARY KEY,
    cloud_allowed INTEGER NOT NULL DEFAULT 1,
    memory_key_prefixes TEXT NOT NULL DEFAULT '[]',
    knowledge_kinds TEXT NOT NULL DEFAULT '[]',
    tool_names TEXT NOT NULL DEFAULT '[]',
    plugin_keys TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(paired_app_id) REFERENCES paired_apps(id) ON DELETE CASCADE
);
