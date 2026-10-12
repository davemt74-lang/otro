CREATE TABLE agent_mission_browser_sources_v11 (
    call_id TEXT PRIMARY KEY REFERENCES agent_mission_read_calls_v1(id) ON DELETE CASCADE,
    mission_id TEXT NOT NULL REFERENCES agent_missions_v1(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES agent_mission_tasks_v1(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX agent_mission_browser_sources_mission_v11 ON agent_mission_browser_sources_v11(mission_id, task_id);
