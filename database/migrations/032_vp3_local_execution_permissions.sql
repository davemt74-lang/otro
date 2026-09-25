-- HomeServer v2.3 Section 4
-- Expand the already-paired first-party VP3 app to the local execution scopes
-- that Cloud v2.3 can use. This does not bypass tool policy or approvals:
-- files.write and devices.control remain governed by the existing action engine.

INSERT OR IGNORE INTO app_permissions(paired_app_id, permission, allowed)
SELECT id, 'files.read', 1 FROM paired_apps WHERE app_key='vp3' AND status='active';

INSERT OR IGNORE INTO app_permissions(paired_app_id, permission, allowed)
SELECT id, 'files.write', 1 FROM paired_apps WHERE app_key='vp3' AND status='active';

INSERT OR IGNORE INTO app_permissions(paired_app_id, permission, allowed)
SELECT id, 'devices.read', 1 FROM paired_apps WHERE app_key='vp3' AND status='active';

INSERT OR IGNORE INTO app_permissions(paired_app_id, permission, allowed)
SELECT id, 'devices.control', 1 FROM paired_apps WHERE app_key='vp3' AND status='active';

UPDATE app_permissions
SET allowed=1, updated_at=CURRENT_TIMESTAMP
WHERE paired_app_id IN (
    SELECT id FROM paired_apps WHERE app_key='vp3' AND status='active'
)
AND permission IN ('files.read','files.write','devices.read','devices.control');
