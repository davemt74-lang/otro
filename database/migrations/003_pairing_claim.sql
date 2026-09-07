ALTER TABLE pairing_requests ADD COLUMN request_id TEXT;
ALTER TABLE pairing_requests ADD COLUMN claim_hash TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_pairing_request_id
ON pairing_requests(request_id)
WHERE request_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_pairing_claim_hash
ON pairing_requests(claim_hash)
WHERE claim_hash IS NOT NULL;
