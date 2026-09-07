CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT,
    organization TEXT,
    email TEXT,
    phone TEXT,
    relationship TEXT,
    notes TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_contacts_display_name ON contacts(display_name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(email COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_contacts_organization ON contacts(organization COLLATE NOCASE);
