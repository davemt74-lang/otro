CREATE TABLE IF NOT EXISTS agent_mission_chat_routes_v1 (
    source_app_key TEXT NOT NULL,
    request_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    parent_agent_id INTEGER NOT NULL REFERENCES agents(id),
    objective TEXT NOT NULL,
    route TEXT NOT NULL CHECK(route IN ('chat','specialists','followup')),
    reason TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    provider_key TEXT NOT NULL,
    model TEXT NOT NULL,
    mission_id TEXT REFERENCES agent_missions_v1(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(source_app_key,request_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS agent_mission_chat_routes_mission
ON agent_mission_chat_routes_v1(mission_id) WHERE mission_id IS NOT NULL;
