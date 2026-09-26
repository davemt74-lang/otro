-- Tracky V2.74 — End-to-end reliability hardening.
-- Adds local retry/request recovery metadata only. v2.4 federated
-- authority/reconciliation tables remain untouched and authoritative.

ALTER TABLE tracky_cloud_sync_state ADD COLUMN consecutive_failures INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tracky_cloud_sync_state ADD COLUMN next_retry_at TEXT;
ALTER TABLE tracky_cloud_sync_state ADD COLUMN last_failure_at TEXT;
ALTER TABLE tracky_cloud_sync_state ADD COLUMN last_backlog_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tracky_cloud_sync_state ADD COLUMN last_success_sequence INTEGER NOT NULL DEFAULT 0;

ALTER TABLE tracky_active_perception_requests ADD COLUMN deadline_at TEXT;
ALTER TABLE tracky_active_perception_requests ADD COLUMN superseded_by TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_tracky_active_requests_deadline
    ON tracky_active_perception_requests(status, deadline_at);

CREATE INDEX IF NOT EXISTS idx_tracky_active_requests_correlation_status
    ON tracky_active_perception_requests(correlation_id, status, updated_at DESC);
