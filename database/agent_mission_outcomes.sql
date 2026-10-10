CREATE TABLE IF NOT EXISTS agent_mission_action_outcomes_v1 (
    action_id TEXT PRIMARY KEY REFERENCES agent_mission_actions_v1(id) ON DELETE CASCADE,
    state TEXT NOT NULL,
    receipt_revision TEXT NOT NULL DEFAULT '',
    observed_revision TEXT NOT NULL DEFAULT '',
    verified_at TEXT,
    checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS agent_mission_action_recoveries_v1 (
    reviewer TEXT NOT NULL,
    request_id TEXT NOT NULL,
    action_id TEXT NOT NULL REFERENCES agent_mission_actions_v1(id) ON DELETE CASCADE,
    payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(reviewer,request_id)
);
