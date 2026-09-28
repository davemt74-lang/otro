CREATE TABLE IF NOT EXISTS tracky_federated_automation_definitions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  automation_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  origin_site_id TEXT NOT NULL,
  state TEXT NOT NULL,
  name TEXT NOT NULL,
  semantic_hash TEXT NOT NULL,
  definition_json TEXT NOT NULL,
  actor_json TEXT NOT NULL DEFAULT '{}',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(automation_id, revision)
);
CREATE INDEX IF NOT EXISTS idx_tracky_fa_definition_origin ON tracky_federated_automation_definitions(origin_site_id,automation_id,revision);

CREATE TABLE IF NOT EXISTS tracky_federated_automation_heads (
  automation_id TEXT PRIMARY KEY,
  origin_site_id TEXT NOT NULL,
  current_revision INTEGER NOT NULL,
  state TEXT NOT NULL,
  name TEXT NOT NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tracky_federated_automation_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL UNIQUE,
  idempotency_key TEXT NOT NULL UNIQUE,
  automation_id TEXT NOT NULL,
  automation_revision INTEGER NOT NULL,
  origin_site_id TEXT NOT NULL,
  trigger_event_id TEXT,
  state TEXT NOT NULL,
  deadline_at_ms INTEGER NOT NULL DEFAULT 0,
  run_json TEXT NOT NULL,
  last_error TEXT NOT NULL DEFAULT '',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tracky_fa_run_state ON tracky_federated_automation_runs(state,updated_at);
CREATE INDEX IF NOT EXISTS idx_tracky_fa_run_automation ON tracky_federated_automation_runs(automation_id,automation_revision,created_at);

CREATE TABLE IF NOT EXISTS tracky_federated_automation_steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  step_id TEXT NOT NULL,
  authority_site_id TEXT NOT NULL,
  target_site_id TEXT NOT NULL,
  device_id TEXT,
  state TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 0,
  deadline_at_ms INTEGER NOT NULL DEFAULT 0,
  dispatch_id TEXT,
  last_error TEXT NOT NULL DEFAULT '',
  step_json TEXT NOT NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(run_id,step_id),
  FOREIGN KEY(run_id) REFERENCES tracky_federated_automation_runs(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tracky_fa_step_state ON tracky_federated_automation_steps(run_id,state,step_id);
CREATE INDEX IF NOT EXISTS idx_tracky_fa_step_authority ON tracky_federated_automation_steps(authority_site_id,state);

CREATE TABLE IF NOT EXISTS tracky_federated_automation_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  automation_id TEXT NOT NULL,
  run_id TEXT,
  step_id TEXT,
  event_kind TEXT NOT NULL,
  state TEXT,
  actor_json TEXT NOT NULL DEFAULT '{}',
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tracky_fa_event_run ON tracky_federated_automation_events(run_id,id);
CREATE INDEX IF NOT EXISTS idx_tracky_fa_event_automation ON tracky_federated_automation_events(automation_id,id);


CREATE TRIGGER IF NOT EXISTS trg_tracky_federated_automation_events_no_update
BEFORE UPDATE ON tracky_federated_automation_events
BEGIN
  SELECT RAISE(ABORT, 'tracky federated automation audit events are immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_tracky_federated_automation_events_no_delete
BEFORE DELETE ON tracky_federated_automation_events
BEGIN
  SELECT RAISE(ABORT, 'tracky federated automation audit events are immutable');
END;
