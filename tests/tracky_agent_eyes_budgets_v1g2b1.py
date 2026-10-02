"""Tracky 1G2B1 fail-closed budgets and independent watchdog; synthetic only."""
from __future__ import annotations
import os, sys, tempfile, time, threading
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix="agent-eyes-budgets-") as root:
    os.environ["HOMESERVER_DATA_DIR"]=root
    os.environ["VP3_OS_HARDWARE_ADAPTER"]="disabled"
    from app.services import tracky_agent_eyes as eyes
    from app.services import tracky_native_managed_session as managed
    from app.services import tracky_native_camera as native
    from app.services import tracky_native_certification as cert
    from app.services import tracky_physical_context as physical
    from app.services import tracky_native_session_evidence as evidence
    assert eyes.status()["available_wall_budgets"] == [60, 120]
    assert eyes.status()["available_cpu_budgets"] == [4, 8, 12]
    model={"model_integrity_verified":True}
    approval={"owner_accepted_current_run":True,
              "requires_new_owner_test_due_model_change":False}
    with patch.object(cert,"status",return_value=approval), \
         patch.object(native,"model_preflight",return_value=model), \
         patch.object(native,"_privacy",return_value=False):
        for budget in (-1, 61, True, 300):
            try:
                eyes.start(consent=True,scope=eyes.SCOPE,camera_index=0,
                           sample_count=1,max_session_seconds=budget)
                raise AssertionError("Invalid wall budget accepted")
            except eyes.AgentEyesError as err:
                assert err.status_code==422
        try:
            eyes.start(consent=True,scope=eyes.SCOPE,camera_index=0,
                       sample_count=1,max_cpu_seconds=3)
            raise AssertionError("Invalid CPU budget accepted")
        except eyes.AgentEyesError as err:
            assert err.status_code==422
        # No second runtime. Synthetic capture through the actual canonical
        # request path; request ledger and shared driver remain authoritative.
        entered=threading.Event()
        release=threading.Event()
        def observe(_index,_cancel):
            entered.set()
            release.wait(3)
            if _cancel.is_set():
                raise RuntimeError("revoked")
            return {"summary":"synthetic possible face region"}
        with patch.object(native,"_observe",side_effect=observe):
            running=eyes.start(consent=True,scope=eyes.SCOPE,camera_index=0,
                               sample_count=1,max_session_seconds=60,
                               max_cpu_seconds=4)
            assert entered.wait(3)
            assert running["resource_budget"]["wall_limit_seconds"]==60
            assert running["resource_budget"]["cpu_limit_seconds"]==4
            assert managed.status()["owner_surface"]=="agent_eyes"
            assert physical.provider_exposure()["remote_requestable"] is False
            # Deterministic forced watchdog stall, not a 16-second sleep.
            with managed._LOCK:
                managed._ATTEMPT_STARTED=time.monotonic()-managed.WATCHDOG_STALL_SECONDS-2
            for _ in range(35):
                if managed._STOP.is_set():break
                time.sleep(.05)
            assert managed._STOP.is_set(),"Watchdog failed to revoke stalled attempt"
            assert managed._stopping_reason()=="watchdog_stall"
            release.set()
            for _ in range(60):
                if not managed.status()["active"]:break
                time.sleep(.05)
            assert managed.status()["active"] is False
            assert physical.provider_exposure()["registered"] is False
            assert eyes.status()["auto_resume"] is False
            assert eyes.status()["remotely_requestable"] is False
            assert eyes.status()["raw_media_retained"] is False
print("TRACKY_AGENT_EYES_BUDGETS_V1G2B1: budget rejection, watchdog, no auto-resume, canonical provider PASS")
