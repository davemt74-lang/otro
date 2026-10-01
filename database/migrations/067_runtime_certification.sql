-- Owner-initiated hardware certification stores only redacted outcomes and timings.
-- No samples, transcripts, camera frames, raw provider output, keys or hardware identifiers.
CREATE TABLE IF NOT EXISTS runtime_certification_runs (
    id TEXT PRIMARY KEY,
    test_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('operational','degraded','failed','unsupported','not_verified')),
    duration_ms INTEGER NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_runtime_certification_recent ON runtime_certification_runs(created_at DESC);
