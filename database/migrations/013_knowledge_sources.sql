CREATE TABLE IF NOT EXISTS knowledge_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    recursive INTEGER NOT NULL DEFAULT 1 CHECK (recursive IN (0,1)),
    scan_interval_seconds INTEGER NOT NULL DEFAULT 120 CHECK (scan_interval_seconds BETWEEN 30 AND 3600),
    exclude_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','scanning','ready','paused','error')),
    last_scan_started_at TEXT,
    last_scan_completed_at TEXT,
    last_error TEXT,
    last_scan_file_count INTEGER NOT NULL DEFAULT 0,
    last_scan_indexed_count INTEGER NOT NULL DEFAULT 0,
    last_scan_updated_count INTEGER NOT NULL DEFAULT 0,
    last_scan_moved_count INTEGER NOT NULL DEFAULT 0,
    last_scan_removed_count INTEGER NOT NULL DEFAULT 0,
    last_scan_skipped_count INTEGER NOT NULL DEFAULT 0,
    last_scan_error_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS knowledge_source_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    relative_path TEXT NOT NULL,
    knowledge_item_id INTEGER,
    content_hash TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
    modified_ns INTEGER NOT NULL DEFAULT 0 CHECK (modified_ns >= 0),
    status TEXT NOT NULL DEFAULT 'indexed' CHECK (status IN ('indexed','error')),
    last_error TEXT,
    last_seen_scan TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, relative_path),
    FOREIGN KEY (source_id) REFERENCES knowledge_sources(id) ON DELETE CASCADE,
    FOREIGN KEY (knowledge_item_id) REFERENCES knowledge_items(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_knowledge_sources_enabled
ON knowledge_sources(enabled, status, last_scan_completed_at);

CREATE INDEX IF NOT EXISTS idx_knowledge_source_files_source
ON knowledge_source_files(source_id, relative_path);

CREATE INDEX IF NOT EXISTS idx_knowledge_source_files_hash
ON knowledge_source_files(source_id, content_hash);

CREATE TRIGGER IF NOT EXISTS knowledge_source_item_before_delete
BEFORE DELETE ON knowledge_items
BEGIN
    UPDATE knowledge_source_files
    SET status='error',
        last_error='Indexed knowledge item was removed; source will be re-indexed on the next scan.',
        updated_at=CURRENT_TIMESTAMP
    WHERE knowledge_item_id=OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS knowledge_source_before_delete
BEFORE DELETE ON knowledge_sources
BEGIN
    UPDATE knowledge_items
    SET source_path=NULL,
        updated_at=CURRENT_TIMESTAMP
    WHERE id IN (
        SELECT knowledge_item_id
        FROM knowledge_source_files
        WHERE source_id=OLD.id AND knowledge_item_id IS NOT NULL
    );
END;
