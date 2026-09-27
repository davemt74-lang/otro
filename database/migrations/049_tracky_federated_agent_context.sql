CREATE TABLE IF NOT EXISTS tracky_federated_agent_context (
  id INTEGER PRIMARY KEY CHECK (id=1),
  revision INTEGER NOT NULL DEFAULT 0,
  fingerprint TEXT NOT NULL DEFAULT '',
  agent_state TEXT NOT NULL DEFAULT 'current',
  physical_state TEXT NOT NULL DEFAULT 'unknown',
  focus_identity_id TEXT NULL,
  authority_site_id TEXT NULL,
  authority_device_id TEXT NULL,
  authority_epoch INTEGER NOT NULL DEFAULT 0,
  current_site_id TEXT NULL,
  context_json TEXT NOT NULL DEFAULT '{}',
  generated_at_ms INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO tracky_federated_agent_context(
  id,revision,fingerprint,agent_state,physical_state,authority_epoch,context_json,generated_at_ms
) VALUES (1,0,'','current','unknown',0,'{}',0);

CREATE TABLE IF NOT EXISTS tracky_federated_agent_context_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  revision INTEGER NOT NULL UNIQUE,
  fingerprint TEXT NOT NULL,
  agent_state TEXT NOT NULL,
  physical_state TEXT NOT NULL,
  authority_site_id TEXT NULL,
  current_site_id TEXT NULL,
  context_json TEXT NOT NULL,
  generated_at_ms INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tracky_agent_context_history_recent
ON tracky_federated_agent_context_history(revision DESC);
