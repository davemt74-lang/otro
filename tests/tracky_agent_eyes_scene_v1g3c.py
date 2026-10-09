"""Local scene transport, camera lifecycle, review and canonical chat contracts."""
import json
import os
import sys
import tempfile
import time
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory() as root:
    os.environ['HOMESERVER_DATA_DIR']=root
    os.environ['VP3_OS_HARDWARE_ADAPTER']='disabled'
    import httpx
    import numpy as np
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.database import db
    from app.services import (tracky_agent_eyes_scene as scene, tracky_agent_eyes as eyes,
        tracky_native_camera as native, tracky_native_certification as cert,
        tracky_native_managed_session as managed, tracky_physical_context as physical,
        tracky_agent_eyes_context as context, providers, federated_data)
    from app.services.tasks import scheduler
    valid={'objects':['chair','cup'],'setting':'indoor','lighting':'bright'}
    for bad in ({**valid,'caption':'INJECT'}, {**valid,'objects':['person']},
                {**valid,'objects':['chair','chair']}, {**valid,'lighting':[]},
                {**valid,'objects':list(scene.OBJECTS)[:9]}):
        try: scene.validate(bad);raise AssertionError(bad)
        except scene.SceneError: pass
    released=[]; opened=[]; requests=[]; digest=['a'*64]; output=[valid]; remote=[False]
    class Capture:
        def __init__(self,index):opened.append(index)
        def isOpened(self):return True
        def set(self,*a):pass
        def read(self):return True,np.zeros((480,640,3),dtype=np.uint8)
        def release(self):released.append(True)
    cv2=SimpleNamespace(data=SimpleNamespace(haarcascades=root),VideoCapture=Capture,
        CAP_PROP_FRAME_WIDTH=3,CAP_PROP_FRAME_HEIGHT=4,COLOR_BGR2GRAY=6,IMWRITE_JPEG_QUALITY=1,
        cvtColor=lambda a,b:a,resize=lambda a,b:a,
        imencode=lambda *a:(True,np.array([1,2,3],dtype=np.uint8)),
        CascadeClassifier=lambda *a:SimpleNamespace(empty=lambda:False,detectMultiScale=lambda *a,**k:[]))
    def handle(request):
        requests.append(request)
        path=request.url.path
        if path=='/api/tags':payload={'models':[{'name':'vision:local','digest':digest[0]}]}
        elif path=='/api/show':payload={'capabilities':['vision'],**({'remote_host':'evil'} if remote[0] else {})}
        elif path=='/api/chat':
            assert released and len(released)==len(opened),'Camera retained during model network request'
            body=json.loads(request.content)
            assert body['messages'][1]['images']==['AQID']
            assert body['stream'] is False and body['keep_alive']==0
            assert body['options']['num_predict']==256
            assert 'tools' not in body
            payload={'model':'vision:local','done':True,'message':{'content':json.dumps(output[0])}}
        else:raise AssertionError(path)
        return httpx.Response(200,json=payload)
    original_client=httpx.Client
    def local_client(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return original_client(transport=httpx.MockTransport(handle),**kwargs)
    approval={'owner_accepted_current_run':True,'latest_owner_review':{'id':'review'},
              'requires_new_owner_test_due_model_change':False}
    with TestClient(app) as client:
        scheduler.stop()
        route='/api/v1/control/onboarding/visual/agent-eyes/scene/'
        assert client.get(route+'status').status_code==401
        client.post('/__owner/session',headers={'X-HomeServer-Owner':OWNER_CONTROL_TOKEN})
        datasets={key:[] for key in federated_data.DATASETS}
        federated_data.reconcile_snapshot({'version':'2.2','federation_version':'2.4',
            'authoritative_source':'vp3_cloud','snapshot_mode':'full','covered_datasets':list(datasets),
            'revision':'scene-test','datasets':datasets},observed_source='homeserver',trigger_reason='scene-test')
        with patch.object(cert,'status',return_value=approval), \
             patch.object(native,'model_preflight',return_value={'installed':True,'model_present':True,'model_integrity_verified':True,'model_sha256':'b'*64}), \
             patch.object(native.integrity,'verify_file',return_value=True), \
             patch.object(physical,'canonical_rooms',return_value=[{'room_id':'studio','name':'Studio'}]), \
             patch.object(providers,'get_ollama',return_value={'enabled':True,'base_url':'http://127.0.0.1:11434'}), \
             patch.object(httpx,'Client',side_effect=local_client), \
             patch.dict(sys.modules,{'cv2':cv2}):
            payload={'model':'vision:local','room_id':'studio','consent':True}
            assert client.post(route+'configure',json=payload).status_code==403
            assert client.post(route+'configure',json={**payload,'consent':'true'},headers={'X-Requested-With':'XMLHttpRequest'}).status_code==422
            configured=client.post(route+'configure',json=payload,headers={'X-Requested-With':'XMLHttpRequest'})
            assert configured.status_code==200,configured.text
            assert not configured.json()['reviewed'] and not opened
            try:eyes.start(consent=True,scope=eyes.SCOPE,camera_index=0,sample_count=1,include_scene=True);raise AssertionError('Unreviewed scene reached normal session')
            except eyes.AgentEyesError:pass
            def run(test=False):
                eyes.start(consent=True,scope=eyes.SCOPE,camera_index=0,sample_count=1,include_scene=True,scene_test=test)
                for _ in range(200):
                    if not managed.status()['active']:break
                    time.sleep(.01)
                return managed.status()
            worker=run(True);assert worker['phase']=='completed',worker
            preview=scene.status()['preview'];assert preview['objects']==['chair','cup'],preview
            assert 'scene' not in context.projection(),'Unaccepted test promoted to chat'
            scene.accept(consent=True,output_observed=True,release_observed=True)
            scene.accept(consent=True,output_observed=True,release_observed=True)  # Idempotent owner click
            worker=run();assert worker['phase']=='completed',worker
            projected=context.projection();assert projected['scene']['objects']==['chair','cup'],projected
            observed_at=datetime.fromisoformat(worker['last_observed_at'])
            with db() as connection:
                completed_at=connection.execute('SELECT completed_at FROM tracky_active_perception_requests WHERE request_id=?',(worker['last_completed_request_id'],)).fetchone()[0]
            # The ledger uses whole seconds and slow runners may publish the
            # observation later. Keep both fresh at the initial check, then
            # cross the observation deadline during the final authority call.
            clock=[datetime.fromisoformat(completed_at).replace(tzinfo=timezone.utc)+timedelta(seconds=59)]
            authority_calls=[0];original_reason=context._session_reason
            projection_thread=threading.get_ident()
            class FinalClock(datetime):
                @classmethod
                def now(cls,tz=None):return clock[0]
            def slow_final_authority(snapshot):
                # Runtime background projections must not advance this test's
                # foreground clock before its final authority check.
                if threading.get_ident()==projection_thread:
                    authority_calls[0]+=1
                    if authority_calls[0]==3:clock[0]=observed_at+timedelta(seconds=61)
                return original_reason(snapshot)
            with patch.object(context,'datetime',FinalClock), patch.object(context,'_session_reason',side_effect=slow_final_authority):
                expired=context.projection()
                assert expired['reason']=='observation_expired',('Final authority check crossed freshness deadline',expired,authority_calls)
            fragment,_=context.prompt_fragment(max_chars=1500)
            assert fragment and 'chair' in fragment and 'binding' not in fragment and 'vision:local' not in fragment
            with db() as connection:
                evidence=connection.execute('SELECT result_json FROM tracky_active_perception_requests WHERE request_id=?',(worker['last_completed_request_id'],)).fetchone()[0]
            assert 'images' not in evidence and 'AQID' not in evidence
            digest[0]='c'*64
            assert 'scene' not in context.projection(),'Changed model still promoted to chat'
            digest[0]='a'*64
            with patch.object(native,'_privacy',return_value=True):
                assert context.projection()['reason']=='privacy_enabled'
            with patch.object(providers,'get_ollama',return_value={'enabled':True,'base_url':'https://cloud.example'}):
                try:scene.configure(**payload);raise AssertionError('Remote URL accepted')
                except providers.ProviderError:pass
                assert 'scene' not in context.projection()
            selected=scene.binding()
            event=threading.Event();event.set()
            try:scene.infer(np.zeros((480,640,3),dtype=np.uint8),cv2,event,selected);raise AssertionError('Cancelled scene accepted')
            except scene.SceneError:pass
            with patch.object(scene,'_digest',return_value='a'*64), patch.object(scene,'_json',return_value={'model':'vision:local','done':False}):
                try:scene.infer(np.zeros((480,640,3),dtype=np.uint8),cv2,threading.Event(),selected);raise AssertionError('Partial model output accepted')
                except scene.SceneError:pass
            for bad in ('wrong-model',):
                with patch.object(scene,'_digest',return_value='a'*64), patch.object(scene,'_json',return_value={'model':bad,'done':True}):
                    try:scene.infer(np.zeros((480,640,3),dtype=np.uint8),cv2,threading.Event(),selected);raise AssertionError('Wrong model accepted')
                    except scene.SceneError:pass
            oversize=original_client(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=b'x'*65537)))
            with oversize:
                try:scene._json(oversize,'http://127.0.0.1:11434','/api/chat',deadline=time.monotonic()+2);raise AssertionError('Oversized response accepted')
                except scene.SceneError:pass
            # Cancellation must be checked on every received transport chunk,
            # even when a server sends less than an aggregation buffer.
            class Trickle(httpx.SyncByteStream):
                count=0
                def __iter__(self):
                    for _ in range(16):
                        self.count+=1
                        if self.count==2:event.set()
                        yield b'x'*10
            trickle=Trickle();event=threading.Event()
            with original_client(transport=httpx.MockTransport(lambda request:httpx.Response(200,stream=trickle))) as transport:
                try:scene._json(transport,'http://127.0.0.1:11434','/api/chat',deadline=time.monotonic()+2,cancel=event);raise AssertionError('Cancelled transport accepted')
                except scene.SceneError:pass
            assert trickle.count<=2,'Buffered HTTP chunks postponed cancellation'
            clock=[0.0]
            class DeadlineTrickle(httpx.SyncByteStream):
                count=0
                def __iter__(self):
                    for _ in range(16):
                        self.count+=1
                        if self.count==2:clock[0]=10.0
                        yield b'x'*10
            late=DeadlineTrickle()
            with original_client(transport=httpx.MockTransport(lambda request:httpx.Response(200,stream=late))) as transport, patch.object(scene.time,'monotonic',side_effect=lambda:clock[0]):
                try:scene._json(transport,'http://127.0.0.1:11434','/api/chat',deadline=5);raise AssertionError('Late trickling transport accepted')
                except scene.SceneError:pass
            assert late.count<=2,'Buffered HTTP chunks postponed the deadline'
            scene.disable();assert not scene.status()['configured']
            remote[0]=True
            try:scene.configure(**payload);raise AssertionError('Remote model accepted')
            except scene.SceneError:pass
            remote[0]=False
            scene.configure(**payload)
            output[0]={**valid,'caption':'SECRET_INJECTION'}
            with patch.object(scene,'cancel',wraps=scene.cancel) as revoke:
                worker=run(True);assert worker['phase']=='failed',worker
                assert revoke.called,'Provider failure did not close scene HTTP clients'
            assert scene.status()['preview'] is None
            assert len(opened)==len(released),'Camera leak'
            assert all(r.url.host=='127.0.0.1' for r in requests)
    print('TRACKY_AGENT_EYES_SCENE_V1G3C: local transport, current model, native release, pending test, reviewed chat, malformed/remote rejection PASS')
