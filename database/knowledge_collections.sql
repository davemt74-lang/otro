CREATE TABLE IF NOT EXISTS knowledge_collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS knowledge_collection_sources (
    source_id INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES knowledge_sources(id) ON DELETE CASCADE,
    FOREIGN KEY (collection_id) REFERENCES knowledge_collections(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS knowledge_collection_items (
    knowledge_item_id INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (knowledge_item_id) REFERENCES knowledge_items(id) ON DELETE CASCADE,
    FOREIGN KEY (collection_id) REFERENCES knowledge_collections(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app_knowledge_collection_scopes (
    paired_app_id INTEGER NOT NULL,
    collection_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (paired_app_id, collection_id),
    FOREIGN KEY (paired_app_id) REFERENCES paired_apps(id) ON DELETE CASCADE,
    FOREIGN KEY (collection_id) REFERENCES knowledge_collections(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_knowledge_collection_sources_collection
ON knowledge_collection_sources(collection_id, source_id);

CREATE INDEX IF NOT EXISTS idx_knowledge_collection_items_collection
ON knowledge_collection_items(collection_id, knowledge_item_id);

CREATE INDEX IF NOT EXISTS idx_app_knowledge_collection_scopes_app
ON app_knowledge_collection_scopes(paired_app_id, collection_id);

INSERT OR IGNORE INTO knowledge_collections(collection_key, name, description)
VALUES ('general', 'General', 'Default HomeServer knowledge collection.');
