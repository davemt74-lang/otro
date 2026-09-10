from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
index = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
script = (ROOT / "ui" / "local-apps.js").read_text(encoding="utf-8")
styles = (ROOT / "ui" / "local-apps.css").read_text(encoding="utf-8")
bridge = (ROOT / "app" / "bridge.py").read_text(encoding="utf-8")
api = (ROOT / "app" / "local_apps_api.py").read_text(encoding="utf-8")
service = (ROOT / "app" / "services" / "local_apps.py").read_text(encoding="utf-8")
catalog = (ROOT / "app" / "services" / "local_app_catalog.py").read_text(encoding="utf-8")

assert '/assets/local-apps.css' in index
assert '/assets/local-apps.js' in index
assert index.index('/assets/local-apps.js') < index.index('/assets/shell.js')

for marker in (
    'Local Apps',
    'LOCAL CAPABILITY STORE',
    'data-view="local-apps"',
    '/api/v1/control/local-apps',
    'data-local-app-action="install"',
    'data-local-app-action="update"',
    'data-local-app-action="uninstall"',
    'SHA-256 verification',
    'embedded reviewed catalog',
):
    assert marker in script, marker

# Install/update are genuinely one-click. Only destructive uninstall asks for
# confirmation after the user has already selected the action.
assert "action === 'uninstall' && !window.confirm" in script
assert 'Install ${item.name}?' not in script

# Connected Apps remains a distinct paired-client surface.
assert 'data-view="apps">Connected Apps' in index
assert '/api/v1/control/apps' not in script

for marker in (
    '@media (max-width: 900px)',
    '@media (max-width: 620px)',
    '.local-apps-grid',
    '.local-app-card',
    '.local-app-warning',
):
    assert marker in styles, marker

for marker in (
    'local_apps_router',
    'local.apps.v1',
    'local.apps.install.v1',
):
    assert marker in bridge, marker

for route in (
    '@router.get("")',
    '@router.post("/{app_key}/install")',
    '@router.post("/{app_key}/update")',
    '@router.delete("/{app_key}")',
):
    assert route in api, route

# Security contracts are visible in implementation, not just copy.
for marker in (
    '_TrustedRedirectHandler',
    '_assert_trusted_url',
    '_safe_rel_path',
    '_extract_zip',
    '_active_health',
    'SHA-256',
    'os.replace',
    '.rollback',
):
    assert marker in service, marker

for marker in (
    'Piper TTS',
    'Whisper STT',
    'piper_windows_amd64.zip',
    'en_US-lessac-medium.onnx',
    'encoder_model_quantized.onnx',
    'decoder_model_merged_quantized.onnx',
):
    assert marker in catalog, marker

print('HomeServer v0.40 Local Apps UI contract passed')
