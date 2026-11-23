"""Real continuity, bounded feature isolation and ordered HTTPS receipt recovery."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


with tempfile.TemporaryDirectory(prefix="bridge-receipt-latency-") as directory:
    os.environ["HOMESERVER_DATA_DIR"] = directory
    from app.config import settings
    from app.database import initialize_database
    from app.services import cloud_pairing, contacts, federated_data, pairing, remote_bridge as bridge
    from app.services.https_bridge_session import load_https_session, save_https_session

    initialize_database()
    contacts.create_contact({"display_name": "Native continuity contact"})
    request = pairing.create_pairing_request("vp3", "VP3", cloud_pairing._VP3_PERMISSIONS)
    pairing.approve_pairing_request(request["request_id"])
    local_token = request["claim_token"]
    permissions = pairing.authenticate(local_token)["permissions"]
    endpoint = "https://cloud.invalid/api/homeserver-https-poll-v1300.php"
    session_token = "S" * 64
    save_https_session(endpoint, session_token)
    bridge.save_vp3_https_settings(endpoint, True)
    session_before = settings.remote_https_session_path.read_bytes()
    cloud = {"authoritative_source": "vp3_cloud", "snapshot_mode": "full",
             "covered_datasets": ["memory", "knowledge", "contacts", "tasks", "calendar", "files", "notifications"],
             "revision": "cloud-latency-test", "datasets": {
                 name: [] for name in ("memory", "knowledge", "contacts", "tasks", "calendar", "files", "notifications")}}

    class Response:
        status_code = 200
        def __init__(self, requests):
            self.requests = requests
        def json(self):
            return {"ok": True, "requests": self.requests, "poll_after_ms": 250}

    # The first poll is idle; the feature HTTP attempt is then held indefinitely.
    # A later real full continuity command must still be accepted and acknowledged
    # before that attempt finishes, rather than waiting out its eight-second limit.
    feature_started, feature_release, receipt_received = (threading.Event() for _ in range(3))
    worker = bridge.RemoteBridgeWorker()
    posts, receipts, errors = [], [], []
    real_dispatch = bridge.dispatch_remote_request

    class Client:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def post(self, url, **kwargs):
            assert url == endpoint
            posts.append(json.loads(json.dumps(kwargs["json"])))
            if posts[-1]["results"]:
                assert feature_started.is_set() and not feature_release.is_set()
                receipts.extend(posts[-1]["results"])
                receipt_received.set()
                worker._stop.set()
                return Response([])
            if len(posts) == 1:
                return Response([])
            assert feature_started.wait(2), "Supplementary sync never started"
            return Response([{"request_id": "real-full-continuity", "operation": "shared.context.exchange",
                              "payload": {"cloud_snapshot": cloud}, "bearer_token": local_token}])

    def dispatch(operation, payload, token):
        if operation == "capabilities":
            return {"ok": True, "payload": {"version": "2.4"}}
        return real_dispatch(operation, payload, token)

    def feature_sync(*, timeout, active):
        assert timeout == 8.0 and active()
        feature_started.set()
        assert feature_release.wait(5)
        return {"ok": True}

    def run():
        try:
            worker._run_https(bridge.get_bridge_settings())
        except BaseException as exc:
            errors.append(exc)

    bridge._RELOAD_EVENT.clear()
    with patch.object(bridge.httpx, "Client", Client), \
         patch.object(bridge, "dispatch_remote_request", dispatch), \
         patch.object(bridge.tracky_physical_context, "sync_due", return_value=True), \
         patch.object(bridge.tracky_physical_context, "sync_cloud", side_effect=feature_sync):
        relay_thread = threading.Thread(target=run)
        relay_thread.start()
        try:
            assert receipt_received.wait(3), "Continuity receipt blocked behind supplementary feature HTTP"
        finally:
            worker._stop.set()
            feature_release.set()
            relay_thread.join(5)
            if worker._feature_sync_thread:
                worker._feature_sync_thread.join(5)
        assert not relay_thread.is_alive() and not errors, errors
    assert len(receipts) == 1 and receipts[0]["ok"], receipts
    reply = receipts[0]["payload"]
    assert reply["homeserver_snapshot"]["snapshot_mode"] == "full"
    assert reply["homeserver_snapshot"]["datasets"]["contacts"][0]["title"] == "Native continuity contact"
    assert reply["reconciliation"]["cloud_to_homeserver"]["status"] == "completed"
    assert not federated_data.reconciliation_state("vp3_cloud")["needs_reconciliation"]
    assert pairing.authenticate(local_token)["permissions"] == permissions
    assert settings.remote_https_session_path.read_bytes() == session_before
    print("PASS real full continuity and heartbeat complete while supplementary HTTP remains blocked")

    # A receipt POST can accept another batch. An interrupted later upload must
    # retain both batches and its completed receipt, without replaying any action.
    bridge._RELOAD_EVENT.clear()
    worker = bridge.RemoteBridgeWorker()
    worker._wait_local_api = lambda: True
    posted, executed = [], []
    failed = False

    class Stop:
        stopped = False
        def is_set(self): return self.stopped
        def set(self): self.stopped = True
        def wait(self, seconds): return self.stopped
    worker._stop = Stop()

    def command(number):
        return {"request_id": f"ordered-{number}", "operation": "system.ping",
                "payload": {"nonce": number}, "bearer_token": local_token}

    class BatchClient(Client):
        def post(self, url, **kwargs):
            global failed
            sent = json.loads(json.dumps(kwargs["json"]))
            posted.append(sent)
            if len(posted) == 1:
                return Response([command(n) for n in range(1, 9)])
            assert len(sent["results"]) == 1
            receipt = sent["results"][0]
            if receipt["request_id"] == "ordered-2" and not failed:
                failed = True
                raise bridge.httpx.ConnectError("Synthetic interrupted receipt upload")
            if receipt["request_id"] == "ordered-9":
                worker._stop.set()
            return Response([command(9)] if receipt["request_id"] == "ordered-1" else [])

    def batch_dispatch(operation, payload, token):
        if operation == "capabilities":
            return {"ok": True, "payload": {}}
        assert pairing.authenticate(token)["app_key"] == "vp3"
        executed.append(payload["nonce"])
        return {"ok": True, "status": 200, "payload": {"pong": True}}

    with patch.object(bridge.httpx, "Client", BatchClient), \
         patch.object(bridge, "dispatch_remote_request", batch_dispatch), \
         patch.object(bridge.tracky_physical_context, "sync_due", return_value=False):
        worker._run_guarded()
    assert executed == list(range(1, 10)), executed
    ids = [r["request_id"] for post in posted for r in post["results"]]
    assert ids == ["ordered-1", "ordered-2", "ordered-2"] + [f"ordered-{n}" for n in range(3, 10)], ids
    assert load_https_session()["session_token"] == session_token
    print("PASS per-command receipts preserve two accepted batches and retry upload without replay")

    # Rapid polls cannot create parallel feature senders. A superseded or stopped
    # worker must not publish a warning or change retry state for the new session.
    for supersede in (True, False):
        bridge._RELOAD_EVENT.clear()
        save_https_session(endpoint, session_token)
        worker = bridge.RemoteBridgeWorker()
        started, release = threading.Event(), threading.Event()
        calls = []
        def stalled_sync(*, timeout, active):
            calls.append(timeout)
            started.set()
            assert release.wait(3)
            raise RuntimeError("Old feature failure")
        with patch.object(bridge.tracky_physical_context, "sync_due", return_value=True), \
             patch.object(bridge.tracky_physical_context, "sync_cloud", side_effect=stalled_sync):
            worker._schedule_feature_sync(session_token)
            assert started.wait(2)
            for _ in range(100): worker._schedule_feature_sync(session_token)
            assert calls == [8.0]
            if supersede:
                save_https_session(endpoint, "N" * 64)
            else:
                worker._stop.set()
            bridge._set_state(feature_sync_error="Current session warning")
            release.set()
            worker._feature_sync_thread.join(3)
            assert not worker._feature_sync_thread.is_alive()
            worker._schedule_feature_sync(session_token)
            assert calls == [8.0]
            assert bridge._STATE["feature_sync_error"] == "Current session warning"
            assert worker._feature_sync_retry_at == 0.0
    print("PASS one bounded feature worker; stopped and superseded results cannot alter current relay state")
