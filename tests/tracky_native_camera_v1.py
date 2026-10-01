"""Native one-shot Tracky acceptance: synthetic camera fixtures, never real certification."""
from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
with tempfile.TemporaryDirectory(prefix="tracky-native-camera-") as root:
    os.environ["HOMESERVER_DATA_DIR"] = root
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import tracky_native_camera as native
    from app.services import tracky_physical_context as tracky
    from app.services import federated_data, live_certification, runtime_diagnostics
    from app.services.tasks import scheduler

    # Explicit mock; this suite never pretends a GitHub runner has a camera.
    created=[]
    model=Path(root)/"haarcascade_frontalface_default.xml"
    model.write_text("<synthetic-test-model/>")
    class FakeCapture:
        def __init__(self,index):
            self.index=index;self.released=False;self.reads=0
            created.append(self)
        def isOpened(self):return self.index==0
        def set(self,*args):return True
        def read(self):
            self.reads+=1
            return True,object()
        def release(self):self.released=True
    class FakeCascade:
        def __init__(self,p):self.path=p
        def empty(self):return False
        def detectMultiScale(self,*args,**kwargs):return [(5,5,75,75)]
    fake=types.ModuleType("cv2")
    fake.__spec__=__import__("importlib.machinery",fromlist=["ModuleSpec"]).ModuleSpec("cv2",loader=None)
    fake.data=types.SimpleNamespace(haarcascades=root+"/")
    fake.CAP_PROP_FRAME_WIDTH=3;fake.CAP_PROP_FRAME_HEIGHT=4;fake.COLOR_BGR2GRAY=6
    fake.VideoCapture=FakeCapture;fake.CascadeClassifier=FakeCascade
    fake.cvtColor=lambda frame,color:object()
    hdr={"X-Requested-With":"XMLHttpRequest"}
    base="/api/v1/control/onboarding/visual/native/"
    consent={"consent":True,"scope":native.SCOPE,"camera_index":0}

    with TestClient(app) as client:
        scheduler.stop()
        assert client.get(base+"status").status_code==401
        assert client.post(base+"test",json=consent,headers=hdr).status_code==401
        assert client.post("/__owner/session",
                           headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
        status=client.get(base+"status").json()
        assert status["capture_at_startup"] is False and not status["running"]
        assert status["identity_recognition"] is False and not status["hardware_certified"]
        assert client.post(base+"test",json=consent).status_code==403
        assert client.post(base+"test",json=dict(consent,consent=False),headers=hdr).status_code==403
        assert client.post(base+"test",json=dict(consent,scope="other-person"),
                           headers=hdr).status_code==403
        assert client.post(base+"test",json=dict(consent,camera_index=True),
                           headers=hdr).status_code==422
        assert not created

        with patch.object(native,"_privacy",return_value=True):
            assert client.post(base+"test",json=consent,headers=hdr).status_code==403
        assert not created

        # Use the canonical v2.4 reconciliation boundary rather than
        # bypassing it in an application-level acceptance test.
        datasets={k:[] for k in federated_data.DATASETS}
        reconciled=federated_data.reconcile_snapshot({
            "version":"2.2","federation_version":"2.4",
            "authoritative_source":"vp3_cloud","snapshot_mode":"full",
            "covered_datasets":list(datasets),"revision":"tracky-native-v1-fixture",
            "datasets":datasets,
        }, observed_source="homeserver",trigger_reason="tracky-native-v1-fixture")
        assert reconciled["status"]=="completed"

        with patch.dict(sys.modules,{"cv2":fake}):
            result=client.post(base+"test",json=consent,headers=hdr)
        assert result.status_code==200,result.text
        outcome=result.json()
        assert outcome["request"]["status"]=="completed",outcome
        assert not outcome["native"]["hardware_certified"]
        assert not outcome["native"]["identity_recognition"]
        assert outcome["native"]["last_test"]["owner_review_required"]
        assert len(created)==1 and created[0].reads==1 and created[0].released
        assert tracky._provider_snapshot()[0] is None
        assert not any(word in result.text for word in ("embedding","raw_frame","face_template"))
        observed=client.get(base+"status").json()
        assert not observed["running"] and observed["last_test"]["status"]=="native_detector_completed"
        assert observed["capture_at_startup"] is False

        # Native capture is unavailable if another local perception provider
        # already holds authority. Never replace or detach it.
        def existing_provider(request):return {"summary":"native hardware authority"}
        tracky.register_provider(existing_provider,name="another-native-provider")
        assert client.post(base+"test",json=consent,headers=hdr).status_code==409
        assert tracky._provider_snapshot()[0] is existing_provider
        tracky.unregister_provider(expected=existing_provider)

        with patch.dict(sys.modules,{"cv2":fake}):
            assert client.post(base+"test",json=dict(consent,camera_index=1),
                               headers=hdr).json()["request"]["status"]=="failed"
        assert created[-1].released and created[-1].reads==0

        # Under a native on-demand session generic certification never
        # opens the camera. Real owner review remains separate.
        with patch.object(tracky,"_provider_snapshot",
                          return_value=(lambda r:{}, {"surface":"native_owner_on_demand"}, "fixture")):
            outcome,evidence=live_certification._run_eyes()
        assert outcome=="unsupported" and evidence["reason"]=="native_owner_test_requires_agent_chat"

        diagnostic=runtime_diagnostics.inventory()
        row=next(x for x in diagnostic["checks"] if x["name"]=="tracky_native_camera")
        assert row["hardware_certified"] is False
        assert row["identity_recognition"] is False
        assert row["continuous_tracking"] is False

print("Native Tracky v1: owner gate, local capture/detection, canonical request, privacy, teardown, diagnostics PASS")
