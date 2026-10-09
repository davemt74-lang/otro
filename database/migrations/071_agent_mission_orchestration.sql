-- A5C2: durable starts and cumulative read budgets. Ambiguous calls are not replayed.
CREATE TABLE IF NOT EXISTS agent_mission_orchestration_v1 (
    mission_id TEXT PRIMARY KEY REFERENCES agent_missions_v1(id) ON DELETE CASCADE,
    source_app_key TEXT NOT NULL,
    contract_revision INTEGER NOT NULL,
    authority_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS agent_mission_start_requests_v1 (
    source_app_key TEXT NOT NULL,
    request_id TEXT NOT NULL,
    mission_id TEXT NOT NULL REFERENCES agent_missions_v1(id) ON DELETE CASCADE,
    contract_revision INTEGER NOT NULL,
    PRIMARY KEY(source_app_key,request_id)
);
CREATE TABLE IF NOT EXISTS agent_mission_read_calls_v1 (
    id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES agent_missions_v1(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES agent_mission_tasks_v1(id) ON DELETE CASCADE,
    lease_id TEXT NOT NULL,
    tool_key TEXT NOT NULL,
    arguments_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('claimed','completed','failed','discarded')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_mission_read_budget ON agent_mission_read_calls_v1(task_id);

