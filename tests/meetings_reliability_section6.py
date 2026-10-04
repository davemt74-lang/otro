from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def acceptance() -> None:
    from app.services import meeting_transcription as m, meeting_transcription_control as control

    cases: list[str] = []

    def passed(name: str) -> None:
        cases.append(name)
        print("PASS " + name)

    def job() -> m._MeetingJob:
        return m._MeetingJob(
            idempotency_key="vp3-meeting-transcription:" + "a" * 32,
            public_id="a" * 32, room_name="room", title="Meeting", app_key="owner",
            livekit_url="wss://example.invalid", livekit_identity="worker",
            livekit_token="synthetic", callback_url="https://example.invalid/api/video-meeting-worker.php",
            callback_token="synthetic", callback_expires_at=datetime.now(timezone.utc) + timedelta(hours=1), language="en",
        )

    async def until(predicate) -> None:
        for _ in range(200):
            if predicate():
                return
            await asyncio.sleep(0.005)
        raise AssertionError("runtime condition did not settle")

    class Room:
        latest = None
        connect_gate = None
        hang_disconnect = False

        def __init__(self):
            type(self).latest = self
            self.events = {}
            self.remote_participants = {}
            self.disconnects = 0

        def on(self, event):
            def register(handler):
                self.events[event] = handler
                return handler
            return register

        def emit(self, event, *args):
            self.events[event](*args)

        async def connect(self, *_args, **_kwargs):
            if type(self).connect_gate is not None:
                await type(self).connect_gate.wait()

        async def disconnect(self):
            self.disconnects += 1
            if type(self).hang_disconnect:
                await asyncio.Event().wait()
            self.emit("disconnected")

    rtc = SimpleNamespace(Room=Room, RoomOptions=lambda **kw: kw, TrackKind=SimpleNamespace(KIND_AUDIO="audio"), DisconnectReason=SimpleNamespace(ROOM_DELETED=9))
    entered: list[str] = []
    closed: list[str] = []

    async def consume(_job, _rtc, track, _publication, _participant):
        entered.append(track.sid)
        try:
            await asyncio.Event().wait()
        finally:
            closed.append(track.sid)

    with patch.object(m, "_rtc_module", return_value=rtc), patch.object(m, "_consume_track", consume), patch.object(m, "DISCONNECT_TIMEOUT_SECONDS", 0.03):
        active = job()
        running = asyncio.create_task(m._run_job(active))
        await until(lambda: active.status == "running")
        room = Room.latest
        track = SimpleNamespace(kind="audio", sid="track")
        publication = SimpleNamespace(sid="track")
        participant = SimpleNamespace(identity="person", name="Guest")
        room.emit("track_subscribed", track, publication, participant)
        room.emit("track_subscribed", track, publication, participant)
        await until(lambda: len(entered) == 1)
        assert len(entered) == 1
        passed("duplicate subscription has one consumer")

        room.emit("reconnecting")
        assert active.status == "reconnecting"
        room.emit("reconnected")
        assert active.status == "running"
        passed("reconnect status follows transport without replacement worker")

        room.emit("track_unsubscribed", track, publication, participant)
        room.emit("track_subscribed", track, publication, participant)
        await until(lambda: len(entered) == 2)
        assert closed == ["track"]
        passed("unsubscribe closes old consumer before same-SID replacement")

        m._JOBS[active.idempotency_key] = active
        result = control.stop_for({"meeting": active.public_id}, {"app_key": "owner"})
        assert result["status"] == "stopping"
        room.emit("reconnected")
        assert active.status == "stopping"
        await asyncio.wait_for(running, 2)
        assert active.status == "stopped" and closed == ["track", "track"]
        snapshot = control.status_for({"meeting": active.public_id}, {"app_key": "owner"})
        assert snapshot['status'] == 'stopped' and snapshot['active'] is False
        passed("Stop cannot be undone by reconnect and reaches stopped after stream cleanup")

        before = len(entered)
        room.emit("track_subscribed", track, publication, participant)
        await asyncio.sleep(0)
        assert len(entered) == before
        passed("closed runtime rejects late track events")

        Room.connect_gate = asyncio.Event()
        delayed = job()
        opening = asyncio.create_task(m._run_job(delayed))
        await until(lambda: delayed.status == "connecting")
        delayed.stop_event.set()
        Room.connect_gate.set()
        await asyncio.wait_for(opening, 2)
        assert delayed.status == "stopped" and delayed.start_event.is_set()
        Room.connect_gate = None
        passed("Stop during connect signals terminal startup and never enters running")

        Room.hang_disconnect = True
        interrupted = job()
        task = asyncio.create_task(m._run_job(interrupted))
        await until(lambda: interrupted.status == "running")
        latest = Room.latest
        latest.emit("track_subscribed", track, publication, participant)
        await until(lambda: len(entered) > before)
        latest.emit("disconnected")
        await asyncio.wait_for(task, 2)
        assert interrupted.status == "completed" and len(closed) == len(entered)
        Room.hang_disconnect = False
        passed("failed disconnect is bounded and releases audio consumers")

        ended = job()
        end_task = asyncio.create_task(m._run_job(ended))
        await until(lambda: ended.status == "running")
        Room.latest.emit("disconnected", rtc.DisconnectReason.ROOM_DELETED)
        await asyncio.wait_for(end_task, 2)
        assert ended.status == "stopped" and ended.stop_event.is_set()
        passed("provider room closure suppresses buffered callbacks and ends cleanly")

    async def fail_consumer(*_args):
        raise RuntimeError("injected audio failure")

    with patch.object(m, "_rtc_module", return_value=rtc), patch.object(m, "_consume_track", fail_consumer):
        failed = job()
        running = asyncio.create_task(m._run_job(failed))
        await until(lambda: failed.status == "running")
        Room.latest.emit("track_subscribed", track, publication, participant)
        await asyncio.wait_for(running, 2)
        assert failed.status == "failed" and failed.last_error == "audio_track_failed"
        passed("consumer failure survives cleanup as failed rather than completed")

    class Alive:
        @staticmethod
        def is_alive():
            return True

    existing = job()
    existing.thread = Alive()
    clean = {key: getattr(existing, key) for key in ["idempotency_key", "public_id", "room_name", "title", "livekit_url", "livekit_identity", "livekit_token", "callback_url", "callback_token", "callback_expires_at", "language"]}
    with patch.object(m, "status", return_value={"available": True}), patch.object(m, "_validate_payload", return_value=clean):
        m._JOBS.clear()
        m._JOBS[existing.idempotency_key] = existing
        for state in ["stopping", "failed", "stopped", "expired", "completed"]:
            existing.status = state
            existing.stop_event.set()
            try:
                m.start({}, {"app_key": "owner", "permissions": ["agent.chat"]})
                raise AssertionError("live old thread replaced")
            except m.MeetingTranscriptionError as error:
                assert error.status_code == 409
            assert m._JOBS[existing.idempotency_key] is existing
        passed("no replacement while any previous worker thread remains alive")

        existing.updated_at = 0
        m._prune_jobs_locked(5000)
        assert m._JOBS[existing.idempotency_key] is existing
        passed("terminal status does not prune a live thread")

        try:
            m.start({}, {"app_key": "other", "permissions": ["agent.chat"]})
            raise AssertionError("foreign wrapper reused job")
        except m.MeetingTranscriptionError as error:
            assert error.status_code == 404
        passed("start idempotency remains bound to the paired app owner")

    stopped = job()
    stopped.stop_event.set()
    with patch.object(m.httpx, "Client", side_effect=AssertionError("callback after Stop")):
        m._post_callback(stopped, {"text": "late"})
    passed("Stop suppresses callbacks before new retry attempts")
    m._JOBS.clear()
    print(f"MEETINGS_RELIABILITY_SECTION6=PASS ({len(cases)} lifecycle cases)")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="meeting-section6-") as data_dir:
        os.environ["HOMESERVER_DATA_DIR"] = data_dir
        asyncio.run(acceptance())
