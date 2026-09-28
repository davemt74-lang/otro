CREATE TABLE IF NOT EXISTS tracky_federated_automation_dispatches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dispatch_id TEXT NOT NULL UNIQUE,
  idempotency_key TEXT NOT NULL UNIQUE,
  run_id TEXT NOT NULL,
  step_id TEXT NOT NULL,
  authority_site_id TEXT NOT NULL,
  authority_epoch INTEGER NOT NULL,
  state TEXT NOT NULL,
  dispatch_json TEXT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tracky_fa_dispatch_run ON tracky_federated_automation_dispatches(run_id,step_id);
CREATE TABLE IF NOT EXISTS tracky_federated_automation_execution_receipts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  receipt_id TEXT NOT NULL UNIQUE,
  dispatch_id TEXT NOT NULL UNIQUE,
  idempotency_key TEXT NOT NULL,
  run_id TEXT NOT NULL,
  step_id TEXT NOT NULL,
  authority_site_id TEXT NOT NULL,
  authority_epoch INTEGER NOT NULL,
  status TEXT NOT NULL,
  receipt_json TEXT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tracky_fa_receipt_run ON tracky_federated_automation_execution_receipts(run_id,step_id);
CREATE TRIGGER IF NOT EXISTS trg_tracky_fa_execution_receipts_no_update
BEFORE UPDATE ON tracky_federated_automation_execution_receipts
BEGIN SELECT RAISE(ABORT,'tracky federated automation execution receipts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_tracky_fa_execution_receipts_no_delete
BEFORE DELETE ON tracky_federated_automation_execution_receipts
BEGIN SELECT RAISE(ABORT,'tracky federated automation execution receipts are immutable'); END;