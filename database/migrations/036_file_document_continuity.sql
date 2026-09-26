-- HomeServer v2.4 Section 5 — Files & Document continuity.
-- Adds idempotent mutation receipts only. Existing paired-app permissions are unchanged.

CREATE TABLE IF NOT EXISTS federated_file_mutations (
    source_app_key TEXT NOT NULL,
    mutation_id TEXT NOT NULL,
    action_key TEXT NOT NULL CHECK (action_key IN ('files.update','files.delete')),
    request_hash TEXT NOT NULL,
    canonical_id TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(source_app_key, mutation_id)
);

CREATE INDEX IF NOT EXISTS idx_federated_file_mutations_created
    ON federated_file_mutations(created_at DESC);
