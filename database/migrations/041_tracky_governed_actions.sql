-- Tracky V2.75 — governed physical action intents + Tracky-event automation.
-- These tables store Tracky references/correlation only. Existing action_requests,
-- automation_suggestions and automation_rule_executions remain execution authority.

CREATE TABLE IF NOT EXISTS tracky_action_intents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id TEXT NOT NULL UNIQUE,
    correlation_id TEXT NOT NULL,
    site_id TEXT NOT NULL,
    origin_kind TEXT NOT NULL CHECK(origin_kind IN ('user_request','agent_suggestion','automation_trigger')),
    source_event_id TEXT NOT NULL DEFAULT '',
    source_event_sequence INTEGER NOT NULL DEFAULT 0,
    requested_mode TEXT NOT NULL CHECK(requested_mode IN ('suggest_only','request_approval')),
    effective_mode TEXT NOT NULL CHECK(effective_mode IN ('suggest_only','request_approval')),
    device_key TEXT NOT NULL,
    command TEXT NOT NULL,
    arguments_json TEXT NOT NULL DEFAULT '{}',
    reason TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK(status IN ('suggested','requested','denied','failed','completed','expired','superseded')),
    suggestion_id INTEGER,
    action_request_id TEXT NOT NULL DEFAULT '',
    permission_upgrade_required INTEGER NOT NULL DEFAULT 0 CHECK(permission_upgrade_required IN (0,1)),
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tracky_action_intents_correlation
    ON tracky_action_intents(correlation_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_tracky_action_intents_action_request
    ON tracky_action_intents(action_request_id);

CREATE INDEX IF NOT EXISTS idx_tracky_action_intents_event
    ON tracky_action_intents(source_event_id, source_event_sequence);

CREATE TABLE IF NOT EXISTS tracky_event_automation_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
    event_type TEXT NOT NULL,
    room_id TEXT NOT NULL DEFAULT '',
    subject_type TEXT NOT NULL DEFAULT '',
    min_confidence REAL NOT NULL DEFAULT 0.80 CHECK(min_confidence BETWEEN 0 AND 1),
    routine_key TEXT NOT NULL,
    cooldown_seconds INTEGER NOT NULL DEFAULT 60 CHECK(cooldown_seconds BETWEEN 0 AND 86400),
    last_fired_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tracky_event_automation_rules_event
    ON tracky_event_automation_rules(enabled, event_type);

CREATE TABLE IF NOT EXISTS tracky_event_automation_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    event_sequence INTEGER NOT NULL DEFAULT 0,
    matched INTEGER NOT NULL DEFAULT 0 CHECK(matched IN (0,1)),
    execution_id INTEGER,
    result_status TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    claimed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    FOREIGN KEY(rule_id) REFERENCES tracky_event_automation_rules(id) ON DELETE CASCADE,
    UNIQUE(rule_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_tracky_event_automation_claims_rule
    ON tracky_event_automation_claims(rule_id, event_sequence DESC);
