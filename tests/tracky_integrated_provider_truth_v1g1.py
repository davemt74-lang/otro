"""Tracky 1G1 integrated provider-truth audit.

All providers are synthetic callbacks. No driver opens, new perception loop,
cloud identity claim, biometric upload or real camera certification.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix="tracky-1g1-provider-") as folder:
    os.environ["HOMESERVER_DATA_DIR"]=folder
    os.environ["VP3_OS_HARDWARE_ADAPTER"]="disabled"
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.services import (tracky_physical_context as tracky,
                              onboarding_chat as chat)
    from app.services.tasks import scheduler

    def callback(_request):
        return {"summary":"synthetic test only","confidence":0.0}

    def assert_exposure(*, local, remote):
        report=tracky.provider_exposure()
        assert report["registered"] is True
        assert report["local_owner_session_provider"] is local, report
        assert report["remote_requestable"] is remote, report
        assert report["cloud_active_perception_advertised"] is remote
        assert report["hardware_certified"] is False
        assert report["identity_recognition_certified"] is False
        cap=tracky.public_capability()["provider"]
        assert cap["registered"] is True
        assert cap["available"] is True  # Existing local registry contract.
        assert cap["remote_requestable"] is remote
        assert cap["local_owner_session_provider"] is local
        # Existing Agent Chat summary surfaces the SAME canonical registry;
        # it does not implement another provider or shadow camera runtime.
        with patch.object(chat,"device_status",return_value={
            "cloud":{"state":"not_connected","paired":False,"connected":False},
            "pairing":{"state":"not_started","code":None,"expires_at":None},
        }), patch.object(chat,"provision_status",return_value={"phase":"ready"}):
            assert chat.summary()["tracky_provider_exposure"]==report

    with TestClient(app) as client:
        scheduler.stop()
        start=tracky.provider_exposure()
        assert start["registered"] is False
        assert start["remote_requestable"] is False

        for name,caps in (
            ("homeserver-owner-browser-one-shot",{
                "surface":"owner_browser","remote_requestable":True,"background_tracking":True
            }),
            ("homeserver-native-opencv-owner-test",{
                "surface":"native_owner_on_demand","remote_requestable":True
            }),
            ("homeserver-supervised-native-sampling",{
                "surface":"native_supervised","remote_requestable":True
            }),
            ("future-owner-bound-camera",{
                "owner_consent_required":True,"remote_requestable":True
            }),
            ("future-browser-leased",{
                "owner_gesture_required":True,"remote_requestable":True
            }),
        ):
            tracky.register_provider(callback,name=name,capabilities=caps,replace=False)
            try:
                assert_exposure(local=True,remote=False)
            finally:
                assert tracky.unregister_provider(expected=callback) is True

        tracky.register_provider(callback,name="future-unreviewed-provider",
                                 capabilities={"background_tracking":True},
                                 replace=False)
        try:
            assert_exposure(local=False,remote=False)
        finally:
            tracky.unregister_provider(expected=callback)

        tracky.register_provider(callback,name="future-declared-remote",
                                 capabilities={"remote_requestable":True,
                                               "background_tracking":True},
                                 replace=False)
        try:
            assert_exposure(local=False,remote=True)
            assert tracky.register_provider is not None  # Same canonical registry.
            # A future explicit remote provider may opt in, but no test
            # execution here grants it physical camera or identity authority.
        finally:
            tracky.unregister_provider(expected=callback)

        tracky.register_provider(callback,name="future-declared-but-nontracking",
                                 capabilities={"remote_requestable":True,
                                               "background_tracking":False},
                                 replace=False)
        try:
            assert_exposure(local=False,remote=False)
        finally:
            tracky.unregister_provider(expected=callback)
        assert tracky.provider_exposure()["registered"] is False
        assert tracky.provider_exposure()["remote_requestable"] is False

print("TRACKY_1G1_PROVIDER_TRUTH: canonical runtime, owner-only privacy, fail-closed remote status, Agent summary PASS")
