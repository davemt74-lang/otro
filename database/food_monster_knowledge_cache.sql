-- KB5B: owner-only Food Monster cache. Never insert these rows into the
-- universal knowledge_items/knowledge_chunks index exposed to paired apps.
CREATE TABLE IF NOT EXISTS food_monster_sync_state (
  connection_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('menu','recipe')),
  generation INTEGER NOT NULL DEFAULT 0,
  last_attempt_at TEXT,
  last_success_at TEXT,
  last_error TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (connection_id,kind),
  FOREIGN KEY (connection_id) REFERENCES food_monster_connections(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS food_monster_documents (
  id INTEGER PRIMARY KEY,
  connection_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('menu','recipe')),
  source_id TEXT NOT NULL,
  revision TEXT NOT NULL,
  title TEXT NOT NULL,
  content_json TEXT NOT NULL,
  search_text TEXT NOT NULL,
  source_updated_at TEXT,
  last_seen_generation INTEGER NOT NULL DEFAULT 0,
  missing_complete_sweeps INTEGER NOT NULL DEFAULT 0,
  indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(connection_id,kind,source_id),
  FOREIGN KEY (connection_id) REFERENCES food_monster_connections(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_food_monster_docs_connection
  ON food_monster_documents(connection_id,kind,missing_complete_sweeps);

-- A private FTS namespace, not the shared HomeServer knowledge index.
CREATE VIRTUAL TABLE IF NOT EXISTS food_monster_documents_fts
  USING fts5(title, body, connection_id UNINDEXED, kind UNINDEXED, source_id UNINDEXED,
  tokenize='unicode61');

CREATE TRIGGER IF NOT EXISTS fm_kb5b_fts_insert
 AFTER INSERT ON food_monster_documents BEGIN
  INSERT INTO food_monster_documents_fts(rowid,title,body,connection_id,kind,source_id)
  VALUES(new.id,new.title,new.search_text,new.connection_id,new.kind,new.source_id);
END;
CREATE TRIGGER IF NOT EXISTS fm_kb5b_fts_update
 AFTER UPDATE OF title, search_text ON food_monster_documents BEGIN
  DELETE FROM food_monster_documents_fts WHERE rowid=old.id;
  INSERT INTO food_monster_documents_fts(rowid,title,body,connection_id,kind,source_id)
  VALUES(new.id,new.title,new.search_text,new.connection_id,new.kind,new.source_id);
END;
CREATE TRIGGER IF NOT EXISTS fm_kb5b_fts_delete
 AFTER DELETE ON food_monster_documents BEGIN
  DELETE FROM food_monster_documents_fts WHERE rowid=old.id;
END;
