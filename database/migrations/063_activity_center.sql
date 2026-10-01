ALTER TABLE notifications ADD COLUMN category TEXT NOT NULL DEFAULT 'system';
ALTER TABLE notifications ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal';
ALTER TABLE notifications ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'system';
ALTER TABLE notifications ADD COLUMN source_key TEXT;
ALTER TABLE notifications ADD COLUMN event_key TEXT;
ALTER TABLE notifications ADD COLUMN dedupe_key TEXT;
ALTER TABLE notifications ADD COLUMN action_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE notifications ADD COLUMN archived_at TEXT;
ALTER TABLE notifications ADD COLUMN last_seen_at TEXT;
ALTER TABLE notifications ADD COLUMN occurrence_count INTEGER NOT NULL DEFAULT 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_dedupe
ON notifications(dedupe_key)
WHERE dedupe_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_notifications_attention
ON notifications(dismissed_at, archived_at, read_at, priority, id DESC);

CREATE TABLE IF NOT EXISTS notification_preferences (
    source_key TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    minimum_level TEXT NOT NULL DEFAULT 'info' CHECK (minimum_level IN ('info','success','warning','error','action_required')),
    sound_enabled INTEGER NOT NULL DEFAULT 0 CHECK (sound_enabled IN (0,1)),
    toast_enabled INTEGER NOT NULL DEFAULT 1 CHECK (toast_enabled IN (0,1)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activity_projection_state (
    source_kind TEXT PRIMARY KEY,
    last_id TEXT NOT NULL DEFAULT '0',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
