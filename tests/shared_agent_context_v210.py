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
 ("release version is 2.4", 'version: str = "2.4"' in config and '#define MyAppVersion "2.4"' in installer),
 ("migration creates the shared Cloud mirror cache",
  "CREATE TABLE IF NOT EXISTS shared_agent_snapshots" in migration and "snapshot_json" in migration),
 ("shared fabric is v2.2 and covers all federated datasets",
  'SHARED_AGENT_CONTEXT_VERSION = "2.2"' in shared and
  all(name in shared for name in ('"memory"', '"knowledge"', '"contacts"', '"tasks"', '"calendar"', '"files"', '"notifications"'))),
 ("shared payloads are bounded below the relay message ceiling",
  "MAX_SNAPSHOT_BYTES = 196_608" in shared and "max_bytes: int = 170_000" in shared),
 ("Cloud mirror remains source-labelled rather than replacing native records",
  '"authoritative_source": source' in shared and "source='vp3_cloud'" in shared),
 ("authenticated system ping proves a real HomeServer round trip",
  'if op == "system.ping"' in bridge and "_direct_identity(token)" in bridge and '"pong": True' in bridge),
 ("shared context exchange is authenticated and permission gated",
  'if op == "shared.context.exchange"' in bridge and
  '{"memory.read", "knowledge.search", "contacts.read", "tasks.read", "events.read", "files.read", "notifications.read"}' in bridge and
  "shared_agent_context.exchange" in bridge),
 ("Cloud mirror is visible only to local owner context or the VP3 paired app",
  'if owner or source_app_key == "app:vp3"' in context and
  context.count('if owner or source_app_key == "app:vp3"') >= 3),
 ("Cloud mirror contributes memory knowledge contacts task calendar and notification context",
  'cloud_candidates("memory"' in context and 'cloud_candidates("knowledge"' in context and
  'cloud_candidates("contacts"' in context and 'cloud_candidates("tasks"' in context and
  'cloud_candidates("calendar"' in context and 'cloud_candidates("notifications"' in context),
 ("capabilities advertise shared context and ping",
  '"shared.agent.context.v1"' in capabilities and '"system.ping.v1"' in capabilities and
  '"operation": "shared.context.exchange"' in capabilities),
]

for name,ok in checks:
    if not ok:
        raise AssertionError(name)
    print("PASS:",name)
print(f"HomeServer v2.2 shared Agent context: {len(checks)}/{len(checks)} passed")


import os
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-shared-agent-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.config import settings
    from app.database import db, initialize_database
    from app.services import pairing, remote_bridge, shared_agent_context

    initialize_database()
    assert settings.version=="2.4"

    with db() as connection:
        assert connection.execute("SELECT 1 FROM schema_migrations WHERE version=31").fetchone() is not None
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='shared_agent_snapshots'").fetchone() is not None

    cloud_snapshot={
        "version":"2.2",
        "revision":"cloud-revision-1",
        "generated_at":"2026-09-24T17:00:00Z",
        "datasets":{
            "memory":[{"id":"cloud-memory-1","title":"Cloud memory","content":"Shared launch context from VP3 Cloud","updated_at":"2026-09-24T17:00:00Z"}],
            "knowledge":[{"id":"cloud-knowledge-1","title":"Cloud note","content":"Knowledge shared from Cloud","updated_at":"2026-09-24T17:00:00Z"}],
            "contacts":[{"id":"cloud-contact-1","title":"Cloud contact","content":"Relationship context","updated_at":"2026-09-24T17:00:00Z"}],
            "tasks":[{"id":"cloud-task-1","title":"Cloud task","content":"status: open","updated_at":"2026-09-24T17:00:00Z"}],
            "calendar":[{"id":"cloud-calendar-1","title":"Cloud calendar","content":"starts: 2026-09-25T18:00:00Z","updated_at":"2026-09-24T17:00:00Z"}],
            "notifications":[{"id":"cloud-notification-1","title":"Cloud notice","content":"unread","updated_at":"2026-09-24T17:00:00Z"}],
        },
    }
    applied=shared_agent_context.apply_cloud_snapshot(cloud_snapshot)
    assert applied["revision"]=="cloud-revision-1"
    assert applied["dataset_counts"]["memory"]==1
    mirrored=shared_agent_context.cloud_candidates("memory","launch")
    assert len(mirrored)==1
    assert mirrored[0]["authoritative_source"]=="vp3_cloud"
    assert "Shared launch context" in mirrored[0]["content"]

    request=pairing.create_pairing_request(
        "vp3",
        "VP3",
        ["memory.read","knowledge.search","contacts.read","tasks.read","events.read","files.read","notifications.read"],
    )
    approved=pairing.approve_pairing_request(request["request_id"])
    assert approved is not None
    token=request["claim_token"]

    ping=remote_bridge.dispatch_remote_request("system.ping",{"nonce":"roundtrip-v21"},token)
    assert ping["ok"] is True
    assert ping["payload"]["pong"] is True
    assert ping["payload"]["echo"]=="roundtrip-v21"
    assert ping["payload"]["version"]=="2.4"
    assert ping["payload"]["app"]=="vp3"

    exchanged=remote_bridge.dispatch_remote_request(
        "shared.context.exchange",
        {"query":"launch","cloud_snapshot":cloud_snapshot},
        token,
    )
    assert exchanged["ok"] is True
    assert exchanged["payload"]["version"]=="2.2"
    assert exchanged["payload"]["cloud_mirror"]["revision"]=="cloud-revision-1"
    assert exchanged["payload"]["homeserver_snapshot"]["authoritative_source"]=="homeserver"
    assert set(exchanged["payload"]["homeserver_snapshot"]["datasets"])=={
        "memory","knowledge","contacts","tasks","calendar","files","notifications"
    }

    try:
        remote_bridge.dispatch_remote_request("system.ping",{"nonce":"bad"},"x"*48)
        raise AssertionError("system.ping accepted an invalid paired-app token")
    except remote_bridge.RemoteBridgeError:
        pass

    limited=pairing.create_pairing_request("limited","Limited",["memory.read"])
    pairing.approve_pairing_request(limited["request_id"])
    try:
        remote_bridge.dispatch_remote_request(
            "shared.context.exchange",
            {"query":"","cloud_snapshot":cloud_snapshot},
            limited["claim_token"],
        )
        raise AssertionError("shared.context.exchange accepted insufficient permissions")
    except remote_bridge.RemoteBridgeError:
        pass

print("HomeServer v2.2 shared Agent runtime exchange passed")
