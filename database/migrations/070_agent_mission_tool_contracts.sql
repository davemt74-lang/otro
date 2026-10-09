-- A5C1: contracts grant bounded capabilities, never arbitrary model tools.
CREATE TABLE IF NOT EXISTS agent_mission_tool_contracts_v1 (
    mission_id TEXT PRIMARY KEY REFERENCES agent_missions_v1(id) ON DELETE CASCADE,
    source_app_key TEXT NOT NULL,
    request_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    contract_json TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL,
    UNIQUE(source_app_key,request_id)
);
CREATE TABLE IF NOT EXISTS agent_mission_contract_requests_v1 (
    source_app_key TEXT NOT NULL,
    request_id TEXT NOT NULL,
    mission_id TEXT NOT NULL REFERENCES agent_missions_v1(id) ON DELETE CASCADE,
    payload_hash TEXT NOT NULL,
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(source_app_key,request_id)
);
