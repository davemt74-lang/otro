CREATE TABLE IF NOT EXISTS federated_data_sources (
    source_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    authority_scope TEXT NOT NULL DEFAULT 'native_records',
    writable INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 100,
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO federated_data_sources(source_id,source_type,authority_scope,writable,priority,capabilities_json)
VALUES
('homeserver','local','native_records',1,100,'{"mirror_only_remote":true}'),
('vp3_cloud','cloud','native_records',0,90,'{"mirror_only_remote":true}');

CREATE TABLE IF NOT EXISTS federated_record_links (
    authority_source TEXT NOT NULL,
    dataset TEXT NOT NULL,
    authority_key TEXT NOT NULL,
    canonical_id TEXT NOT NULL,
    observed_source TEXT NOT NULL,
    record_hash TEXT NOT NULL DEFAULT '',
    source_updated_at TEXT,
    tombstoned INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(authority_source,dataset,authority_key,observed_source)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_federated_record_links_canonical_observed
ON federated_record_links(canonical_id,observed_source);

CREATE INDEX IF NOT EXISTS idx_federated_record_links_dataset
ON federated_record_links(dataset,authority_source,last_seen_at);

CREATE TABLE IF NOT EXISTS federated_sync_cursors (
    peer_source TEXT NOT NULL,
    dataset TEXT NOT NULL,
    revision TEXT NOT NULL DEFAULT '',
    cursor TEXT NOT NULL DEFAULT '',
    last_sync_at TEXT,
    last_success_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(peer_source,dataset)
);
