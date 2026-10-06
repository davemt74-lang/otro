CREATE TABLE IF NOT EXISTS workspace_sync_settings (
    id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    peer_id TEXT NOT NULL DEFAULT '', session_hash TEXT NOT NULL DEFAULT '', last_attempt_at TEXT, last_success_at TEXT,
    last_error TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO workspace_sync_settings(id) VALUES(1);
CREATE TABLE IF NOT EXISTS workspace_sync_snapshots (
    peer_id TEXT NOT NULL,dataset TEXT NOT NULL,revision TEXT NOT NULL,body_json TEXT NOT NULL,
    record_count INTEGER NOT NULL,synced_at TEXT NOT NULL,PRIMARY KEY(peer_id,dataset)
);
CREATE TABLE IF NOT EXISTS workspace_sync_delivery (
    peer_id TEXT NOT NULL,dataset TEXT NOT NULL,revision TEXT NOT NULL,synced_at TEXT NOT NULL,
    PRIMARY KEY(peer_id,dataset)
);

CREATE TABLE IF NOT EXISTS local_transcription_sessions (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('active','completed')),
  cloud_share INTEGER NOT NULL DEFAULT 0 CHECK(cloud_share IN (0,1)),
  started_at TEXT NOT NULL,
  ended_at TEXT,
  segment_count INTEGER NOT NULL DEFAULT 0
);

ALTER TABLE local_transcription_sessions ADD COLUMN cloud_auto_sync INTEGER NOT NULL DEFAULT 0 CHECK(cloud_auto_sync IN (0,1));
