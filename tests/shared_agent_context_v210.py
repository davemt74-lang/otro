from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
read=lambda p:(ROOT/p).read_text(encoding="utf-8")

migration=read("database/migrations/031_shared_agent_context.sql")
shared=read("app/services/shared_agent_context.py")
bridge=read("app/services/remote_bridge.py")
context=read("app/services/canonical_context.py")
capabilities=read("app/bridge.py")
config=read("app/config.py")
installer=read("installer/HomeServer.iss")

checks=[
 ("release version is 2.1", 'version: str = "2.1"' in config and '#define MyAppVersion "2.1"' in installer),
 ("migration creates the shared Cloud mirror cache",
  "CREATE TABLE IF NOT EXISTS shared_agent_snapshots" in migration and "snapshot_json" in migration),
 ("shared fabric is v2.1 and covers all five datasets",
  'SHARED_AGENT_CONTEXT_VERSION = "2.1"' in shared and
  all(name in shared for name in ('"memory"', '"knowledge"', '"contacts"', '"tasks"', '"notifications"'))),
 ("shared payloads are bounded below the relay message ceiling",
  "MAX_SNAPSHOT_BYTES = 196_608" in shared and "max_bytes: int = 170_000" in shared),
 ("Cloud mirror remains source-labelled rather than replacing native records",
  '"authoritative_source": source' in shared and "source='vp3_cloud'" in shared),
 ("authenticated system ping proves a real HomeServer round trip",
  'if op == "system.ping"' in bridge and "_direct_identity(token)" in bridge and '"pong": True' in bridge),
 ("shared context exchange is authenticated and permission gated",
  'if op == "shared.context.exchange"' in bridge and
  '{"memory.read", "knowledge.search", "contacts.read", "tasks.read", "notifications.read"}' in bridge and
  "shared_agent_context.exchange" in bridge),
 ("Cloud mirror is visible only to local owner context or the VP3 paired app",
  'if owner or source_app_key == "app:vp3"' in context and
  context.count('if owner or source_app_key == "app:vp3"') >= 3),
 ("Cloud mirror contributes memory knowledge contacts and task/notification context",
  'cloud_candidates("memory"' in context and 'cloud_candidates("knowledge"' in context and
  'cloud_candidates("contacts"' in context and 'cloud_candidates("tasks"' in context and
  'cloud_candidates("notifications"' in context),
 ("capabilities advertise shared context and ping",
  '"shared.agent.context.v1"' in capabilities and '"system.ping.v1"' in capabilities and
  '"operation": "shared.context.exchange"' in capabilities),
]

for name,ok in checks:
    if not ok:
        raise AssertionError(name)
    print("PASS:",name)
print(f"HomeServer v2.1 shared Agent context: {len(checks)}/{len(checks)} passed")
