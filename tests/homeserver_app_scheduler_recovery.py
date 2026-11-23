"""App job scheduling survives transient faults and bounded shutdown/restart."""
from __future__ import annotations

import logging
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


with tempfile.TemporaryDirectory(prefix="homeserver-app-scheduler-") as directory:
    os.environ["HOMESERVER_DATA_DIR"]=directory
    from app.database import db, initialize_database
    from app.services import homeserver_app_runtime as runtime

    initialize_database()
    original_tick=runtime.run_due_jobs
    recovered=threading.Event()
    calls=[]
    warnings=[]

    class Capture(logging.Handler):
        def emit(self,record):
            warnings.append(record.getMessage())

    capture=Capture()
    runtime._LOG.addHandler(capture)

    def transient_tick():
        calls.append("tick")
        if len(calls)==1:
            # A real SQLite lookup failure crosses the central-query boundary.
            # The following tick must run the actual scheduler successfully.
            with db() as connection:
                connection.execute("SELECT * FROM synthetic_missing_scheduler_table")
        assert original_tick()==0
        recovered.set()
        runtime._STOP.set()

    try:
        with patch.object(runtime,"JOB_LOOP_SECONDS",.01),patch.object(runtime,"run_due_jobs",transient_tick):
            runtime.start()
            assert recovered.wait(3), "App job worker died after its first SQLite failure"
            runtime.stop()
        assert calls==["tick","tick"],calls
        assert runtime._THREAD is None
        assert len(warnings)==1 and "OperationalError" in warnings[0],warnings
        assert "synthetic_missing_scheduler_table" not in warnings[0],warnings
        print("PASS app job worker retries a SQLite tick failure, recovers, and logs only a safe failure type")
    finally:
        runtime.stop()
        runtime._LOG.removeHandler(capture)

    entered=threading.Event()
    release=threading.Event()
    created=[]
    native_thread=threading.Thread

    def make_thread(*args,**kwargs):
        thread=native_thread(*args,**kwargs)
        created.append(thread)
        return thread

    def blocked_tick():
        entered.set()
        assert release.wait(3), "Blocked scheduler fixture was not released"

    try:
        with patch.object(runtime,"JOB_LOOP_SECONDS",.01),patch.object(runtime,"run_due_jobs",blocked_tick),patch.object(runtime.threading,"Thread",make_thread):
            runtime.start()
            assert entered.wait(3)
            first=runtime._THREAD
            # Simulate the real bounded join timing out while the job is still
            # executing, without spending two seconds in every test invocation.
            with patch.object(first,"join",return_value=None):
                runtime.stop()
            assert first.is_alive() and runtime._THREAD is first
            assert runtime._STOP.is_set()
            runtime.start()
            assert runtime._THREAD is first and len(created)==1, "Restart created an overlapping app job worker"
            assert runtime._STOP.is_set(), "Restart cleared the still-running worker's stop signal"
            release.set()
            first.join(3)
            assert not first.is_alive()
            runtime.start()
            second=runtime._THREAD
            assert second is not first and len(created)==2
            runtime.stop()
            assert not second.is_alive() and runtime._THREAD is None
        print("PASS bounded Stop retains a live worker, prevents duplicate restart, and permits restart after exit")
    finally:
        runtime._STOP.set()
        release.set()
        for thread in created:
            thread.join(3)
        runtime.stop()
