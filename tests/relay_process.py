from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
from websockets.sync.client import connect


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_MARKER = "SYNTHETIC_RELAY_PRIVATE_MEMORY_81532"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_health(client: httpx.Client, base_url: str, process: subprocess.Popen) -> None:
    for _ in range(50):
        if process.poll() is not None:
            raise AssertionError(f"Relay process exited early with {process.returncode}")
        try:
            response = client.get(f"{base_url}/health", timeout=1)
            if response.status_code == 200 and response.json().get("ok") is True:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise AssertionError("Relay process did not become healthy")


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


with tempfile.TemporaryDirectory(prefix="homeserver-relay-test-") as raw_data_dir:
    data_dir = Path(raw_data_dir)
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["HOMESERVER_RELAY_DATA_DIR"] = str(data_dir)
    env["HOMESERVER_RELAY_ALLOWED_ORIGINS"] = "https://vp3.me"
    env["PYTHONPATH"] = str(ROOT)

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "relay.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--ws-max-size",
            "262144",
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    client = httpx.Client(timeout=12.0, trust_env=False)
    relay_token = ""
    rotated_token = ""
    home_token = "synthetic_homeserver_app_token_" + "h" * 40
    device_secret = secrets.token_urlsafe(48)
    device_id = f"hs-{hashlib.sha256(device_secret.encode('utf-8')).hexdigest()[:24]}"

    try:
        wait_health(client, base_url, process)
        health = client.get(f"{base_url}/health").json()
        assert health["protocol"] == "homeserver-relay-v1"
        assert health["end_to_end_payload_encryption"] is False

        ws_headers = {
            "Authorization": f"Bearer {device_secret}",
            "X-HomeServer-Device": device_id,
        }
        with connect(
            f"ws://127.0.0.1:{port}/bridge",
            subprotocols=["homeserver.bridge.v1"],
            additional_headers=ws_headers,
            proxy=None,
            open_timeout=5,
            ping_interval=None,
        ) as websocket:
            websocket.send(
                json.dumps(
                    {
                        "type": "hello",
                        "protocol": "homeserver-relay-v1",
                        "device_id": device_id,
                        "version": "0.12.0-test",
                    }
                )
            )
            hello_ok = json.loads(websocket.recv(timeout=5))
            assert hello_ok["type"] == "hello.ok"
            assert hello_ok["claimed"] is False
            claim_code = str(hello_ok.get("claim_code") or "")
            assert len(claim_code.replace("-", "")) == 12

            bad_claim = client.post(
                f"{base_url}/v1/claim",
                json={"claim_code": "AAAA-BBBB-CCCC"},
            )
            assert bad_claim.status_code == 404

            claimed = client.post(
                f"{base_url}/v1/claim",
                json={"claim_code": claim_code},
            )
            assert claimed.status_code == 200, claimed.text
            claim_payload = claimed.json()
            assert claim_payload["device_id"] == device_id
            relay_token = str(claim_payload["relay_token"])
            assert len(relay_token) >= 40

            claimed_update = json.loads(websocket.recv(timeout=5))
            assert claimed_update["type"] == "hello.ok"
            assert claimed_update["claimed"] is True

            status = client.get(f"{base_url}/v1/session", headers=auth(relay_token))
            assert status.status_code == 200
            assert status.json()["connected"] is True

            invalid_operation = client.post(
                f"{base_url}/v1/request",
                headers=auth(relay_token),
                json={"operation": "http.proxy", "payload": {"url": "http://127.0.0.1/admin"}},
            )
            assert invalid_operation.status_code == 400

            allowed_result: dict[str, httpx.Response] = {}

            def allowed_request() -> None:
                allowed_result["response"] = httpx.post(
                    f"{base_url}/v1/request",
                    headers=auth(relay_token),
                    json={
                        "operation": "memory.read",
                        "payload": {},
                        "bearer_token": home_token,
                    },
                    timeout=10,
                    trust_env=False,
                )

            allowed_thread = threading.Thread(target=allowed_request)
            allowed_thread.start()
            forwarded = json.loads(websocket.recv(timeout=5))
            assert forwarded["type"] == "request"
            assert forwarded["operation"] == "memory.read"
            assert forwarded["bearer_token"] == home_token
            websocket.send(
                json.dumps(
                    {
                        "type": "response",
                        "request_id": forwarded["request_id"],
                        "status": 200,
                        "ok": True,
                        "payload": {
                            "app": "vp3",
                            "items": [{"content": PRIVATE_MARKER}],
                        },
                    }
                )
            )
            allowed_thread.join(timeout=10)
            assert not allowed_thread.is_alive()
            allowed_response = allowed_result["response"]
            assert allowed_response.status_code == 200
            assert allowed_response.json()["payload"]["items"][0]["content"] == PRIVATE_MARKER

            denied_result: dict[str, httpx.Response] = {}

            def denied_request() -> None:
                denied_result["response"] = httpx.post(
                    f"{base_url}/v1/request",
                    headers=auth(relay_token),
                    json={
                        "operation": "contacts.search",
                        "payload": {"query": "synthetic relationship"},
                        "bearer_token": home_token,
                    },
                    timeout=10,
                    trust_env=False,
                )

            denied_thread = threading.Thread(target=denied_request)
            denied_thread.start()
            forwarded_denied = json.loads(websocket.recv(timeout=5))
            assert forwarded_denied["operation"] == "contacts.search"
            websocket.send(
                json.dumps(
                    {
                        "type": "response",
                        "request_id": forwarded_denied["request_id"],
                        "status": 403,
                        "ok": False,
                        "payload": {"detail": "Permission denied"},
                    }
                )
            )
            denied_thread.join(timeout=10)
            assert not denied_thread.is_alive()
            denied_response = denied_result["response"]
            assert denied_response.status_code == 403
            assert denied_response.json()["payload"]["detail"] == "Permission denied"

            rotated = client.post(
                f"{base_url}/v1/session/rotate",
                headers=auth(relay_token),
            )
            assert rotated.status_code == 200
            rotated_token = str(rotated.json()["relay_token"])
            assert rotated_token and rotated_token != relay_token
            assert client.get(f"{base_url}/v1/session", headers=auth(relay_token)).status_code == 401
            assert client.get(f"{base_url}/v1/session", headers=auth(rotated_token)).status_code == 200

        offline = client.post(
            f"{base_url}/v1/request",
            headers=auth(rotated_token),
            json={
                "operation": "memory.read",
                "payload": {},
                "bearer_token": home_token,
            },
        )
        assert offline.status_code == 503

        wrong_secret = secrets.token_urlsafe(48)
        rejected = False
        try:
            with connect(
                f"ws://127.0.0.1:{port}/bridge",
                subprotocols=["homeserver.bridge.v1"],
                additional_headers={
                    "Authorization": f"Bearer {wrong_secret}",
                    "X-HomeServer-Device": device_id,
                },
                proxy=None,
                open_timeout=5,
                ping_interval=None,
            ) as wrong_socket:
                wrong_socket.send(
                    json.dumps(
                        {
                            "type": "hello",
                            "protocol": "homeserver-relay-v1",
                            "device_id": device_id,
                            "version": "test",
                        }
                    )
                )
                wrong_socket.recv(timeout=3)
        except Exception:
            rejected = True
        assert rejected, "Relay accepted a device secret that did not derive the claimed device ID"
    finally:
        client.close()
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        if process.returncode not in (0, -15, 15):
            stderr = process.stderr.read() if process.stderr else ""
            if stderr:
                print(stderr)

    database_path = data_dir / "relay.db"
    assert database_path.is_file()
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        device = connection.execute(
            "SELECT device_id, secret_hash, claim_code_hash FROM relay_devices WHERE device_id=?",
            (device_id,),
        ).fetchone()
        assert device is not None
        assert device["secret_hash"] != device_secret
        assert len(device["secret_hash"]) == 64
        assert device["claim_code_hash"] is None

        session_rows = connection.execute(
            "SELECT token_hash, revoked_at FROM relay_sessions WHERE device_id=? ORDER BY id",
            (device_id,),
        ).fetchall()
        assert len(session_rows) == 2
        assert session_rows[0]["revoked_at"] is not None
        assert all(len(row["token_hash"]) == 64 for row in session_rows)
        assert all(row["token_hash"] not in {relay_token, rotated_token} for row in session_rows)

        audit = "\n".join(
            str(row[0] or "")
            for row in connection.execute(
                "SELECT event || '|' || status || '|' || COALESCE(operation,'') || '|' || metadata_json FROM relay_events"
            ).fetchall()
        )
        assert PRIVATE_MARKER not in audit
        assert home_token not in audit
        assert relay_token not in audit
        assert rotated_token not in audit
        assert "memory.read" in audit
        assert "contacts.search" in audit
    finally:
        connection.close()

print("HomeServer deployable relay process test passed")
