CREATE TABLE IF NOT EXISTS app_collaboration_grants (
    consumer_app_id INTEGER NOT NULL,
    source_app_id INTEGER NOT NULL,
    memory_allowed INTEGER NOT NULL DEFAULT 0 CHECK (memory_allowed IN (0,1)),
    knowledge_allowed INTEGER NOT NULL DEFAULT 0 CHECK (knowledge_allowed IN (0,1)),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (consumer_app_id, source_app_id),
    CHECK (consumer_app_id <> source_app_id),
    FOREIGN KEY (consumer_app_id) REFERENCES paired_apps(id) ON DELETE CASCADE,
    FOREIGN KEY (source_app_id) REFERENCES paired_apps(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_app_collaboration_source
ON app_collaboration_grants(source_app_id, enabled);
