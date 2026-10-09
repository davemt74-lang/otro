-- A5C3: immutable links to existing approvals and revision-bound review receipts.
CREATE TABLE IF NOT EXISTS agent_mission_actions_v1 (
    id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES agent_missions_v1(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES agent_mission_tasks_v1(id) ON DELETE CASCADE,
    source_app_key TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 0 AND 2),
    action_key TEXT NOT NULL,
    approval_id TEXT NOT NULL UNIQUE REFERENCES action_requests(id),
    payload_hash TEXT NOT NULL,
    contract_revision INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(task_id,ordinal)
);
CREATE TABLE IF NOT EXISTS agent_mission_action_reviews_v1 (
    reviewer TEXT NOT NULL,
    request_id TEXT NOT NULL,
    action_id TEXT NOT NULL REFERENCES agent_mission_actions_v1(id),
    payload_hash TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('approve','deny')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(reviewer,request_id)
);
