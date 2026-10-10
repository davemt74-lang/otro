CREATE TABLE IF NOT EXISTS agent_mission_schedules_v1 (
    id TEXT PRIMARY KEY,
    source_app_key TEXT NOT NULL,
    template_mission_id TEXT NOT NULL REFERENCES agent_missions_v1(id),
    request_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    template_json TEXT NOT NULL,
    authority_hash TEXT NOT NULL,
    timezone TEXT NOT NULL,
    frequency TEXT NOT NULL CHECK(frequency IN ('daily','weekly')),
    weekday INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 6),
    hour INTEGER NOT NULL CHECK(hour BETWEEN 0 AND 23),
    minute INTEGER NOT NULL CHECK(minute BETWEEN 0 AND 59),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','paused','blocked','cancelled')),
    revision INTEGER NOT NULL DEFAULT 1,
    next_run_at TEXT NOT NULL,
    last_run_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_app_key,request_id)
);
CREATE INDEX IF NOT EXISTS agent_mission_schedules_due ON agent_mission_schedules_v1(status,next_run_at);
CREATE TABLE IF NOT EXISTS agent_mission_schedule_runs_v1 (
    id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL REFERENCES agent_mission_schedules_v1(id),
    due_at TEXT NOT NULL,
    mission_id TEXT UNIQUE REFERENCES agent_missions_v1(id),
    status TEXT NOT NULL CHECK(status IN ('started','skipped','blocked')),
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(schedule_id,due_at)
);
CREATE TABLE IF NOT EXISTS agent_mission_schedule_requests_v1 (
    source_app_key TEXT NOT NULL,
    request_id TEXT NOT NULL,
    schedule_id TEXT NOT NULL REFERENCES agent_mission_schedules_v1(id),
    payload_hash TEXT NOT NULL,
    PRIMARY KEY(source_app_key,request_id)
);
