-- Explicit owner-initiated saved recordings. No remote capture, no public media URLs.
-- Files are stored separately in the private HomeServer data directory.
CREATE TABLE IF NOT EXISTS governed_recordings (
    recording_id TEXT PRIMARY KEY,
    media_type TEXT NOT NULL CHECK(media_type IN ('audio','video')),
    duration_seconds INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    delete_after TEXT NOT NULL,
    transcript TEXT,
    transcript_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_governed_recordings_expiry
    ON governed_recordings(delete_after,created_at);
