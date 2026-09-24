from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
read = lambda p: (ROOT / p).read_text(encoding="utf-8")

config = read("app/config.py")
migration = read("database/migrations/030_https_remote_bridge.sql")
session = read("app/services/https_bridge_session.py")
bridge = read("app/services/remote_bridge.py")
pairing = read("app/services/cloud_pairing.py")
api = read("app/remote_bridge_api.py")
ui = read("ui/remote.js")
html = read("ui/remote.html")
shell = read("ui/shell.js")
app_ui = read("ui/app.js")
pairing_service = read("app/services/pairing.py")
installer = read("installer/HomeServer.iss")

checks = [
    ("HomeServer release is v0.19.4", 'version: str = "0.19.4"' in config and '#define MyAppVersion "0.19.4"' in installer),
    ("migration adds a first-class transport and HTTPS endpoint without removing broker_url",
     "ADD COLUMN transport" in migration and "ADD COLUMN https_endpoint" in migration and "broker_url" in bridge),
    ("HTTPS session credential is stored in protected local storage",
     "remote_https_session_path" in config and "_protect_windows" in session and "session_token" in session),
    ("normal VP3 pairing uses the v13.00 HTTPS pairing endpoint",
     "homeserver-https-pair-v1300.php" in pairing),
    ("normal pairing locally authorizes VP3 from the explicit local Pair action",
     "create_pairing_request" in pairing and "approve_pairing_request" in pairing and "_VP3_PERMISSIONS" in pairing),
    ("pairing posts device identity, local bearer, version and capabilities to Cloud",
     all(x in pairing for x in ['"device_id": device_id','"homeserver_token": local_token','"version": settings.version','"capabilities": capabilities'])),
    ("Cloud response installs the official HTTPS session automatically",
     "save_https_session(poll_endpoint, session_token)" in pairing and "save_vp3_https_settings(poll_endpoint, True)" in pairing),
    ("relative poll URLs are resolved against the trusted pairing endpoint and pinned to the same host",
     "urljoin(pairing_endpoint, str(poll_url or \"\").strip())" in pairing and
     "if pairing_host != poll_host" in pairing),
    ("HTTPS worker is outbound-only and authenticated with device/session headers",
     'client.post(' in bridge and '"X-VP3-HomeServer-Session"' in bridge and '"X-HomeServer-Device"' in bridge),
    ("same HTTPS exchange sends heartbeat/capabilities/results to Cloud",
     '"version": settings.version' in bridge and '"capabilities": capabilities' in bridge and '"results": pending_results' in bridge),
    ("same HTTPS exchange receives Cloud requests and dispatches them locally",
     'requests = data.get("requests")' in bridge and "dispatch_remote_request(" in bridge and '"bearer_token"' in bridge),
    ("revoked Cloud sessions clear the protected local credential and disable automatic relay",
     "clear_https_session()" in bridge and "disable_vp3_https_settings()" in bridge),
    ("custom WebSocket transport remains available only as an explicit advanced mode",
     "custom_websocket" in bridge and "normalize_broker_url" in bridge and "ADVANCED RELAY SETTINGS · OPTIONAL" in html),
    ("normal pairing stack has no WebSocket bootstrap or second approval path",
     "bootstrap-vp3" not in ui and "bootstrap-vp3" not in api and
     "bootstrap_vp3_remote_bridge" not in pairing and "approve-vp3" not in api and
     "approveVp3Pairing" not in ui and "waitForRelayProof" not in ui),
    ("normal UI is paste key and Pair with automatic reconnection wording",
     "Paste the pairing key from VP3 and click Pair" in ui and "reconnection are automatic" in ui),
    ("canonical Cloud API owns normal pairing while custom relay settings remain separate",
     '"/api/v1/control/cloud-connection/pair"' in api and
     "'/api/v1/control/cloud-connection/pair'" in ui and
     "save_bridge_settings" in api and "remote-bridge/pair-vp3" not in api),
    ("all normal owner surfaces use one canonical VP3 Cloud connection endpoint",
     "/api/v1/control/cloud-connection" in api and
     "shellApi('/api/v1/control/cloud-connection')" in shell and
     "api('/api/v1/control/cloud-connection')" in app_ui and
     "remoteApi('/api/v1/control/cloud-connection')" in ui),
    ("normal connection UI no longer derives status from legacy Remote Bridge fields",
     "VP3 cloud fallback required" not in shell and
     "<span>Remote bridge</span>" not in shell and
     "Promise.all([" not in shell[shell.index("async function loadConnectionModal"):shell.index("function primaryButton")]),
    ("successful HTTPS heartbeats update VP3 app last-seen through the canonical pairing lifecycle",
     'touch_paired_app("vp3")' in bridge and "def touch_paired_app" in pairing_service),
    ("revoked HTTPS sessions revoke the VP3 paired app through the canonical lifecycle",
     'revoke_paired_app("vp3")' in bridge and "def revoke_paired_app" in pairing_service),
]

for name, ok in checks:
    if not ok:
        raise AssertionError(name)
    print("PASS:", name)

print(f"VP3 HTTPS Relay v0.19 contract: {len(checks)}/{len(checks)} passed")
