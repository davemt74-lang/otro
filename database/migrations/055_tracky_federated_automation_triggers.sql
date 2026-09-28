CREATE TABLE IF NOT EXISTS tracky_federated_automation_trigger_receipts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  receipt_id TEXT NOT NULL UNIQUE,
  automation_id TEXT NOT NULL,
  automation_revision INTEGER NOT NULL,
  event_id TEXT NOT NULL,
  event_key TEXT NOT NULL,
  source_site_id TEXT NOT NULL,
  occurred_at_ms INTEGER NOT NULL DEFAULT 0,
  confidence REAL NOT NULL DEFAULT 0,
  decision TEXT NOT NULL,
  reason TEXT NOT NULL,
  run_id TEXT,
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(automation_id,automation_revision,event_id)
);
CREATE INDEX IF NOT EXISTS idx_tracky_fa_trigger_decision ON tracky_federated_automation_trigger_receipts(automation_id,decision,occurred_at_ms);
CREATE INDEX IF NOT EXISTS idx_tracky_fa_trigger_event ON tracky_federated_automation_trigger_receipts(event_id,automation_id);
CREATE TRIGGER IF NOT EXISTS trg_tracky_fa_trigger_receipts_no_update
BEFORE UPDATE ON tracky_federated_automation_trigger_receipts
BEGIN
  SELECT RAISE(ABORT, 'tracky federated automation trigger receipts are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_tracky_fa_trigger_receipts_no_delete
BEFORE DELETE ON tracky_federated_automation_trigger_receipts
BEGIN
  SELECT RAISE(ABORT, 'tracky federated automation trigger receipts are immutable');
END;