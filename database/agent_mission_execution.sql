-- A5 additive per-task model-only execution profiles.
-- No filesystem, browser, network tool, or delegated authority is granted.
CREATE TABLE IF NOT EXISTS agent_mission_execution_v1 (
  task_id TEXT PRIMARY KEY REFERENCES agent_mission_tasks_v1(id) ON DELETE CASCADE,
  mission_id TEXT NOT NULL REFERENCES agent_missions_v1(id) ON DELETE CASCADE,
  provider_key TEXT NOT NULL DEFAULT 'auto'
    CHECK(provider_key IN ('auto','ollama','anthropic','openai','openrouter')),
  model_override TEXT NOT NULL DEFAULT '',
  environment TEXT NOT NULL DEFAULT 'isolated_model'
    CHECK(environment='isolated_model'),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_agent_execution_mission_v1 ON agent_mission_execution_v1(mission_id);
