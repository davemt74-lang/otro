"""A5B2 concurrency budget acceptance for persistent browser actors."""
import sys
import time
import threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.services import agent_browser_live_actor as actor
from app.services.agent_mission_runtime import MissionError

class FakeActor:
    def __init__(self, tid, origin, ip, url):
        self.task_id=tid
        self.origin=origin
        self.expires=time.monotonic()+600
        self.closed=False
        self.stopped=threading.Event()
        self.url=url
    def submit(self, action, url=""):
        if self.closed:
            raise MissionError("Session is closed.",409)
        return {"url":url or self.url,"image_base64":"","text_snapshot":"","links":[]}
    def stop(self):
        self.closed=True
        self.stopped.set()

actor.LiveActor=FakeActor
actor._registry.clear()
try:
    actor.open_session("task-1","https://example.com","93.184.215.14","https://example.com/1")
    actor.open_session("task-2","https://example.com","93.184.215.14","https://example.com/2")
    assert actor.active("task-1") and actor.active("task-2")
    try:
        actor.open_session("task-3","https://example.com","93.184.215.14","https://example.com/3")
        raise AssertionError("Allowed more than two browser sessions")
    except MissionError as exc:
        assert exc.status_code==409
    try:
        actor.open_session("task-1","https://example.com","93.184.215.14","https://example.com/1")
        raise AssertionError("Duplicate task session allowed")
    except MissionError as exc:
        assert exc.status_code==409
    actor.close_session("task-1")
    assert not actor.active("task-1")
    actor.open_session("task-3","https://example.com","93.184.215.14","https://example.com/3")
    assert actor.active("task-3")
    actor._registry["task-2"].expires=time.monotonic()-1
    assert not actor.active("task-2")
    actor.open_session("task-4","https://example.com","93.184.215.14","https://example.com/4")
    assert actor.active("task-4")
finally:
    actor.shutdown()
    assert not actor._registry
print("MISSION_A5B2_ACTOR PASS: two-session limit, duplicate exclusion, expiry, close, shutdown")
