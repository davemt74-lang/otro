-- Local persistent transcription sessions for dedicated Listening mode.
-- Transcripts remain on HomeServer unless the owner explicitly shares a
-- completed session with the paired VP3 Cloud identity.
CREATE TABLE IF NOT EXISTS local_transcription_sessions (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('active','completed')),
  cloud_share INTEGER NOT NULL DEFAULT 0 CHECK(cloud_share IN (0,1)),
  started_at TEXT NOT NULL,
  ended_at TEXT,
  segment_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS local_transcription_segments (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES local_transcription_sessions(id) ON DELETE CASCADE,
  client_key TEXT NOT NULL,
  text TEXT NOT NULL,
  started_ms INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (session_id,client_key)
);
CREATE INDEX IF NOT EXISTS idx_local_transcription_sessions_recent ON local_transcription_sessions(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_local_transcription_segments_order ON local_transcription_segments(session_id,started_ms,created_at);

CREATE TABLE IF NOT EXISTS local_transcription_segment_attribution (
  segment_id TEXT PRIMARY KEY REFERENCES local_transcription_segments(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL REFERENCES local_transcription_sessions(id) ON DELETE CASCADE,
  ended_ms INTEGER NOT NULL,
  speaker_label TEXT NOT NULL,
  source TEXT NOT NULL,
  attribution_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_local_transcription_attribution_session
  ON local_transcription_segment_attribution(session_id, ended_ms, segment_id);


CREATE TABLE IF NOT EXISTS local_transcription_speaker_corrections (
  segment_id TEXT PRIMARY KEY REFERENCES local_transcription_segments(id) ON DELETE CASCADE,
  speaker_label TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision > 0),
  corrected_at TEXT NOT NULL
);
