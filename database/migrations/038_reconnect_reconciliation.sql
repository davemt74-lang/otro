-- HomeServer v2.4 Section 7 — Disconnect/Reconnect Reconciliation.
-- Adds durable reconciliation state only. Native records, app permissions and
-- existing v2.4 authority tables remain authoritative and unchanged.

CREATE TABLE IF NOT EXISTS federated_reconciliation_state (
    peer_source TEXT PRIMARY KEY CHECK (peer_source IN ('homeserver','vp3_cloud')),
    needs_reconciliation INTEGER NOT NULL DEFAULT 1,
    last_disconnect_at TEXT,
    last_connected_at TEXT,
    last_reconciled_at TEXT,
    last_snapshot_revision TEXT NOT NULL DEFAULT '',
    last_run_id TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS federated_reconciliation_runs (
    id TEXT PRIMARY KEY,
    peer_source TEXT NOT NULL CHECK (peer_source IN ('homeserver','vp3_cloud')),
    observed_source TEXT NOT NULL CHECK (observed_source IN ('homeserver','vp3_cloud')),
    snapshot_revision TEXT NOT NULL DEFAULT '',
    snapshot_mode TEXT NOT NULL CHECK (snapshot_mode IN ('full','filtered')),
    trigger_reason TEXT NOT NULL DEFAULT 'exchange',
    status TEXT NOT NULL CHECK (status IN ('running','completed','failed')),
    created_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    restored_count INTEGER NOT NULL DEFAULT 0,
    unchanged_count INTEGER NOT NULL DEFAULT 0,
    tombstoned_count INTEGER NOT NULL DEFAULT 0,
    conflict_count INTEGER NOT NULL DEFAULT 0,
    dataset_summary_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_federated_reconciliation_runs_peer
    ON federated_reconciliation_runs(peer_source, started_at DESC);
