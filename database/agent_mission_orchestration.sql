-- Revocation remains effective after a grant is restored (no authority ABA).
CREATE TRIGGER IF NOT EXISTS a5c_authority_app_permissions_insert AFTER INSERT ON app_permissions
BEGIN
 UPDATE agent_mission_orchestration_v1 SET authority_hash='revoked' WHERE source_app_key IN (SELECT 'app:'||app_key FROM paired_apps WHERE id=NEW.paired_app_id);
END;
CREATE TRIGGER IF NOT EXISTS a5c_authority_app_permissions_update AFTER UPDATE ON app_permissions WHEN OLD.permission IS NOT NEW.permission OR OLD.allowed IS NOT NEW.allowed
BEGIN
 UPDATE agent_mission_orchestration_v1 SET authority_hash='revoked' WHERE source_app_key IN (SELECT 'app:'||app_key FROM paired_apps WHERE id=NEW.paired_app_id);
END;
CREATE TRIGGER IF NOT EXISTS a5c_authority_app_permissions_delete AFTER DELETE ON app_permissions
BEGIN
 UPDATE agent_mission_orchestration_v1 SET authority_hash='revoked' WHERE source_app_key IN (SELECT 'app:'||app_key FROM paired_apps WHERE id=OLD.paired_app_id);
END;
CREATE TRIGGER IF NOT EXISTS a5c_authority_app_capability_scopes_insert AFTER INSERT ON app_capability_scopes
BEGIN
 UPDATE agent_mission_orchestration_v1 SET authority_hash='revoked' WHERE source_app_key IN (SELECT 'app:'||app_key FROM paired_apps WHERE id=NEW.paired_app_id);
END;
CREATE TRIGGER IF NOT EXISTS a5c_authority_app_capability_scopes_update AFTER UPDATE ON app_capability_scopes WHEN OLD.cloud_allowed IS NOT NEW.cloud_allowed OR OLD.knowledge_kinds IS NOT NEW.knowledge_kinds OR OLD.tool_names IS NOT NEW.tool_names OR OLD.plugin_keys IS NOT NEW.plugin_keys OR OLD.memory_key_prefixes IS NOT NEW.memory_key_prefixes
BEGIN
 UPDATE agent_mission_orchestration_v1 SET authority_hash='revoked' WHERE source_app_key IN (SELECT 'app:'||app_key FROM paired_apps WHERE id=NEW.paired_app_id);
END;
CREATE TRIGGER IF NOT EXISTS a5c_authority_app_capability_scopes_delete AFTER DELETE ON app_capability_scopes
BEGIN
 UPDATE agent_mission_orchestration_v1 SET authority_hash='revoked' WHERE source_app_key IN (SELECT 'app:'||app_key FROM paired_apps WHERE id=OLD.paired_app_id);
END;
CREATE TRIGGER IF NOT EXISTS a5c_authority_app_knowledge_collection_scopes_insert AFTER INSERT ON app_knowledge_collection_scopes
BEGIN
 UPDATE agent_mission_orchestration_v1 SET authority_hash='revoked' WHERE source_app_key IN (SELECT 'app:'||app_key FROM paired_apps WHERE id=NEW.paired_app_id);
END;
CREATE TRIGGER IF NOT EXISTS a5c_authority_app_knowledge_collection_scopes_update AFTER UPDATE ON app_knowledge_collection_scopes WHEN OLD.collection_id IS NOT NEW.collection_id
BEGIN
 UPDATE agent_mission_orchestration_v1 SET authority_hash='revoked' WHERE source_app_key IN (SELECT 'app:'||app_key FROM paired_apps WHERE id=NEW.paired_app_id);
END;
CREATE TRIGGER IF NOT EXISTS a5c_authority_app_knowledge_collection_scopes_delete AFTER DELETE ON app_knowledge_collection_scopes
BEGIN
 UPDATE agent_mission_orchestration_v1 SET authority_hash='revoked' WHERE source_app_key IN (SELECT 'app:'||app_key FROM paired_apps WHERE id=OLD.paired_app_id);
END;
CREATE TRIGGER IF NOT EXISTS a5c_authority_paired_apps_update AFTER UPDATE ON paired_apps WHEN OLD.status IS NOT NEW.status OR OLD.token_hash IS NOT NEW.token_hash
BEGIN
 UPDATE agent_mission_orchestration_v1 SET authority_hash='revoked' WHERE source_app_key IN (SELECT 'app:'||app_key FROM paired_apps WHERE id=NEW.id);
END;
CREATE TRIGGER IF NOT EXISTS a5c_authority_agent_tool_policy AFTER UPDATE ON agent_tool_policy
WHEN OLD.enabled IS NOT NEW.enabled OR OLD.max_calls IS NOT NEW.max_calls
BEGIN
 UPDATE agent_mission_orchestration_v1 SET authority_hash='revoked';
END;
CREATE TRIGGER IF NOT EXISTS a5c_authority_conversation_privacy AFTER UPDATE ON conversation_context_settings
WHEN OLD.cloud_allowed IS NOT NEW.cloud_allowed
BEGIN
 UPDATE agent_mission_orchestration_v1 SET authority_hash='revoked' WHERE mission_id IN (SELECT id FROM agent_missions_v1 WHERE conversation_id=NEW.conversation_id);
END;
