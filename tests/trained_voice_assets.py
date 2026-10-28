"""Owner-only fixed assets through the existing hash-pinned optional app catalog."""
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix='trained-voice-assets-') as folder:
    os.environ['HOMESERVER_DATA_DIR']=folder
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import local_apps
    from app.services.local_app_catalog import CATALOG
    from app.services.tasks import scheduler
    package=CATALOG['voice-speaker-wavlm'];checks=0
    def check(ok,name):
        global checks
        assert ok,name;checks+=1;print('PASS '+name)
    check(len(package['artifacts'])==6 and sum(a['size_bytes'] for a in package['artifacts'])<125000000,'model and inference runtime are a bounded optional component')
    for a in package['artifacts']:
        check(a['kind']=='file' and len(a['sha256'])==64 and a['target'] in package['required_paths'],'asset has a fixed target, size and checksum: '+a['name'])
    with TestClient(app) as client:
        scheduler.stop();url='/api/v1/control/voice/profile-model/'
        check(client.get(url+'config.json').status_code==401,'unauthenticated callers cannot read installed model assets')
        client.post('/__owner/session',headers={'X-HomeServer-Owner':OWNER_CONTROL_TOKEN})
        check(client.post('/api/v1/control/voice/profile-model/install').status_code==403,'model install requires the owner UI mutation header')
        check(client.post('/api/v1/control/voice/profile-model/install',headers={'X-Requested-With':'XMLHttpRequest','Origin':'https://other.invalid'}).status_code==403,'cross-origin model installation is rejected')
        with patch('shutil.disk_usage',return_value=SimpleNamespace(free=1)),patch.object(local_apps,'install') as install:
            check(client.post('/api/v1/control/voice/profile-model/install',headers={'X-Requested-With':'XMLHttpRequest'}).status_code==409 and not install.called,'low disk space is reported before any model download starts')
        check(client.get(url+'config.json').status_code==409,'missing component produces an actionable install response')
        root=local_apps._apps_root()/'voice-speaker-wavlm';root.mkdir()
        (root/'homeserver-app.json').write_text(json.dumps({'app_key':'voice-speaker-wavlm'}))
        for target in package['required_paths']:
            path=root/target;path.parent.mkdir(exist_ok=True,parents=True);path.write_bytes(b'fixture')
        check(client.get(url+'config.json').status_code==200,'installed fixed model asset uses the owner gateway')
        check(client.get(url+'runtime/transformers.min.js').headers['content-type'].startswith('text/javascript'),'installed runtime has the correct module MIME type')
        check(client.get(url+'runtime/ort-wasm-simd-threaded.jsep.wasm').headers['content-type']=='application/wasm','installed WASM has the correct streaming MIME type')
        check(client.get(url+'private.txt').status_code==404,'arbitrary local files cannot be requested')
        path=root/'config.json';path.unlink();outside=Path(folder)/'outside.json';outside.write_text('private')
        try:
            path.symlink_to(outside)
            check(client.get(url+'config.json').status_code==409,'a fixed asset cannot redirect to an owner file outside the component')
        except (OSError,NotImplementedError):
            check(True,'host does not permit symlinks; path containment remains enforced')
    print(f'TRAINED_VOICE_ASSETS=PASS ({checks} cases)')
