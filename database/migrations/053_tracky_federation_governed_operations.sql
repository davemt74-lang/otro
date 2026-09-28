CREATE TABLE IF NOT EXISTS tracky_federation_operation_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id TEXT NOT NULL UNIQUE,
  idempotency_key TEXT NOT NULL UNIQUE,
  operation_type TEXT NOT NULL,
  target_site_id TEXT NOT NULL,
  device_id TEXT,
  new_authority_device_id TEXT,
  state TEXT NOT NULL,
  requires_approval INTEGER NOT NULL DEFAULT 0,
  requires_reconciliation INTEGER NOT NULL DEFAULT 0,
  expires_at_ms INTEGER NOT NULL DEFAULT 0,
  actor_json TEXT NOT NULL DEFAULT '{}',
  reason_codes_json TEXT NOT NULL DEFAULT '[]',
  parameters_json TEXT NOT NULL DEFAULT '{}',
  authority_epoch_before INTEGER NOT NULL DEFAULT 0,
  authority_epoch_after INTEGER NOT NULL DEFAULT 0,
  result_json TEXT NOT NULL DEFAULT '{}',
  last_error TEXT NOT NULL DEFAULT '',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tracky_fop_state ON tracky_federation_operation_ledger(state,updated_at);
CREATE INDEX IF NOT EXISTS idx_tracky_fop_site ON tracky_federation_operation_ledger(target_site_id,updated_at);
CREATE TABLE IF NOT EXISTS tracky_federation_operation_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id TEXT NOT NULL,
  state TEXT NOT NULL,
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(request_id) REFERENCES tracky_federation_operation_ledger(request_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tracky_fop_events_request ON tracky_federation_operation_events(request_id,id);
