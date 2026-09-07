CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','in_progress','completed','cancelled')),
    priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('low','normal','high','urgent')),
    due_at TEXT,
    remind_at TEXT,
    recurrence TEXT NOT NULL DEFAULT 'none' CHECK(recurrence IN ('none','daily','weekly','monthly')),
    recurrence_interval INTEGER NOT NULL DEFAULT 1 CHECK(recurrence_interval BETWEEN 1 AND 365),
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    source_app_key TEXT,
    created_by_type TEXT NOT NULL DEFAULT 'owner' CHECK(created_by_type IN ('owner','app','agent','system')),
    last_reminded_at TEXT,
    completed_at TEXT,
    cancelled_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tasks_status_due ON tasks(status, due_at);
CREATE INDEX IF NOT EXISTS idx_tasks_remind_at ON tasks(remind_at);
CREATE INDEX IF NOT EXISTS idx_tasks_contact ON tasks(contact_id);
CREATE INDEX IF NOT EXISTS idx_tasks_source ON tasks(source_app_key);

ALTER TABLE notifications ADD COLUMN task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL;
ALTER TABLE notifications ADD COLUMN dismissed_at TEXT;
CREATE INDEX IF NOT EXISTS idx_notifications_unread ON notifications(read_at, dismissed_at, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_task ON notifications(task_id);

INSERT INTO tool_policies(tool_key, enabled)
VALUES ('tasks.list', 1)
ON CONFLICT(tool_key) DO NOTHING;
INSERT INTO tool_policies(tool_key, enabled)
VALUES ('notifications.list', 1)
ON CONFLICT(tool_key) DO NOTHING;
INSERT INTO tool_policies(tool_key, enabled)
VALUES ('tasks.create', 1)
ON CONFLICT(tool_key) DO NOTHING;
