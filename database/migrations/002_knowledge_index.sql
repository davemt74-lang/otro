CREATE TABLE IF NOT EXISTS knowledge_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_item_id INTEGER NOT NULL UNIQUE,
    stored_name TEXT NOT NULL,
    original_name TEXT NOT NULL,
    media_type TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (knowledge_item_id) REFERENCES knowledge_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_item_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    char_start INTEGER NOT NULL DEFAULT 0,
    char_end INTEGER NOT NULL DEFAULT 0,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (knowledge_item_id, chunk_index),
    FOREIGN KEY (knowledge_item_id) REFERENCES knowledge_items(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_item
ON knowledge_chunks(knowledge_item_id, chunk_index);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts USING fts5(
    content,
    title,
    knowledge_item_id UNINDEXED,
    chunk_id UNINDEXED,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS knowledge_chunks_ai
AFTER INSERT ON knowledge_chunks
BEGIN
    INSERT INTO knowledge_chunks_fts(rowid, content, title, knowledge_item_id, chunk_id)
    VALUES (
        NEW.id,
        NEW.content,
        COALESCE((SELECT title FROM knowledge_items WHERE id=NEW.knowledge_item_id), ''),
        NEW.knowledge_item_id,
        NEW.id
    );
END;

CREATE TRIGGER IF NOT EXISTS knowledge_chunks_ad
AFTER DELETE ON knowledge_chunks
BEGIN
    DELETE FROM knowledge_chunks_fts WHERE rowid=OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS knowledge_chunks_au
AFTER UPDATE ON knowledge_chunks
BEGIN
    DELETE FROM knowledge_chunks_fts WHERE rowid=OLD.id;
    INSERT INTO knowledge_chunks_fts(rowid, content, title, knowledge_item_id, chunk_id)
    VALUES (
        NEW.id,
        NEW.content,
        COALESCE((SELECT title FROM knowledge_items WHERE id=NEW.knowledge_item_id), ''),
        NEW.knowledge_item_id,
        NEW.id
    );
END;
