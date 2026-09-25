INSERT INTO app_permissions(paired_app_id, permission, allowed)
SELECT p.id, 'contacts.write', 1
FROM paired_apps p
WHERE p.app_key='vp3'
  AND p.status='active'
  AND EXISTS (
    SELECT 1 FROM app_permissions r
    WHERE r.paired_app_id=p.id AND r.permission='contacts.read' AND r.allowed=1
  )
ON CONFLICT(paired_app_id, permission)
DO UPDATE SET allowed=1, updated_at=CURRENT_TIMESTAMP;
