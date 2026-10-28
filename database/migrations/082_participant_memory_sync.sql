-- Text-only participant memory; local visual/audio enrollment remains in IndexedDB.
CREATE TABLE IF NOT EXISTS participant_memory_cache (
    peer_id TEXT NOT NULL, profile_key TEXT NOT NULL, body_json TEXT NOT NULL,
    PRIMARY KEY(peer_id,profile_key)
);
CREATE TABLE IF NOT EXISTS participant_memory_outbox (
    peer_id TEXT NOT NULL, profile_key TEXT NOT NULL, local_id TEXT NOT NULL,
    change_id TEXT NOT NULL, body_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
    PRIMARY KEY(peer_id,profile_key)
);
CREATE TABLE IF NOT EXISTS participant_memory_state (
    peer_id TEXT PRIMARY KEY, pull_cursor INTEGER NOT NULL DEFAULT 0,
    verify_cursor TEXT NOT NULL DEFAULT '', initial_complete INTEGER NOT NULL DEFAULT 0,
    last_success_at TEXT, last_error TEXT NOT NULL DEFAULT ''
);
