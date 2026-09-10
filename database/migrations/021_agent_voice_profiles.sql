CREATE TABLE IF NOT EXISTS agent_voice_profiles (
    agent_id INTEGER PRIMARY KEY,
    voice_key TEXT,
    speaking_rate REAL,
    sentence_silence REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE,
    CHECK (speaking_rate IS NULL OR (speaking_rate >= 0.6 AND speaking_rate <= 1.6)),
    CHECK (sentence_silence IS NULL OR (sentence_silence >= 0.0 AND sentence_silence <= 1.5))
);

CREATE INDEX IF NOT EXISTS idx_agent_voice_profiles_voice_key
ON agent_voice_profiles(voice_key);
