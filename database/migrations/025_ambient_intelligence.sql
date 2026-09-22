PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS automation_intelligence_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    scan_interval_seconds INTEGER NOT NULL DEFAULT 3600 CHECK (scan_interval_seconds BETWEEN 300 AND 86400),
    lookback_days INTEGER NOT NULL DEFAULT 21 CHECK (lookback_days BETWEEN 7 AND 90),
    min_occurrences INTEGER NOT NULL DEFAULT 4 CHECK (min_occurrences BETWEEN 3 AND 20),
    time_bucket_minutes INTEGER NOT NULL DEFAULT 30 CHECK (time_bucket_minutes IN (15,30,60,120)),
    max_proposals_per_scan INTEGER NOT NULL DEFAULT 12 CHECK (max_proposals_per_scan BETWEEN 1 AND 50),
    suppression_days INTEGER NOT NULL DEFAULT 30 CHECK (suppression_days BETWEEN 1 AND 365),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO automation_intelligence_settings(
    id,enabled,scan_interval_seconds,lookback_days,min_occurrences,
    time_bucket_minutes,max_proposals_per_scan,suppression_days
) VALUES (1,1,3600,21,4,30,12,30)
ON CONFLICT(id) DO NOTHING;

CREATE TABLE IF NOT EXISTS automation_context_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_kind TEXT NOT NULL,
    event_type TEXT NOT NULL,
    state TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_automation_context_events_type_time
ON automation_context_events(event_type, occurred_at DESC);

CREATE TABLE IF NOT EXISTS automation_learning_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_key TEXT NOT NULL UNIQUE,
    pattern_kind TEXT NOT NULL CHECK (pattern_kind IN ('time_action','action_sequence')),
    signature_json TEXT NOT NULL,
    time_bucket INTEGER NOT NULL,
    weekdays_json TEXT NOT NULL DEFAULT '[]',
    evidence_count INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 1),
    first_seen_at TEXT,
    last_seen_at TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','suppressed','materialized')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_automation_learning_patterns_status
ON automation_learning_patterns(status, confidence DESC, evidence_count DESC);

CREATE TABLE IF NOT EXISTS automation_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_key TEXT NOT NULL UNIQUE,
    pattern_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    rationale TEXT NOT NULL,
    draft_routine_json TEXT NOT NULL,
    draft_rule_json TEXT NOT NULL,
    simulation_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed','materialized','active','dismissed','suppressed')),
    confidence REAL NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 1),
    occurrence_count INTEGER NOT NULL DEFAULT 0,
    suppression_until TEXT,
    materialized_routine_key TEXT,
    materialized_rule_key TEXT,
    last_suggested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(pattern_id) REFERENCES automation_learning_patterns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_automation_proposals_status
ON automation_proposals(status, confidence DESC, occurrence_count DESC);

CREATE TABLE IF NOT EXISTS automation_proposal_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id INTEGER NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('reviewed','dismissed','materialized','enabled','suppressed')),
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(proposal_id) REFERENCES automation_proposals(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_automation_proposal_feedback_proposal
ON automation_proposal_feedback(proposal_id, id DESC);

CREATE TABLE IF NOT EXISTS automation_simulations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id INTEGER NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(proposal_id) REFERENCES automation_proposals(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_automation_simulations_proposal
ON automation_simulations(proposal_id, id DESC);
