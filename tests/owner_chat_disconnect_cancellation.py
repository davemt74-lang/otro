"""Actual owner HTTP disconnect cancels inference, late replies and proposals."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]

RUNTIME = r'''
from pathlib import Path
import os,time
from app.runtime import app
from app.security import OWNER_CONTROL_TOKEN
from app.services import providers,brain,approvals
providers.inference_status=lambda:{'available':True,'selected_provider':'ollama',
    'model':'fixture','compute_source':'homeserver_local','providers':[]}
root=Path(os.environ['HOMESERVER_DATA_DIR'])
def generate(messages,**kwargs):
    state=kwargs['state']
    state.update({'call_count':0,'run_ids':[],'action_request_ids':[],
        'provider_usage':{'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}})
    token=kwargs.get('cancellation_token')
    if messages[-1]['content']=='cancel-ui-fixture':
        assert token is not None,'Native UI request did not receive a cancellation token'
        request=approvals.create_task_create_request('owner',
            {'title':'Synthetic cancellable proposal','description':'HTTP cancellation fixture','priority':'normal'},owner=True)
        state['action_request_ids'].append(request['result']['request_id'])
        class ActiveProvider:
            def close(self):(root/'provider-closed').write_text('closed')
        provider=ActiveProvider()
        token.register_client(provider)
        (root/'provider-started').write_text('started')
        deadline=time.monotonic()+12
        try:
            while not (root/'release-provider').exists() and time.monotonic()<deadline:time.sleep(.01)
            # Deliberately ignore cancellation and return a late reply. The
            # canonical chat layer must still reject it and clean its proposal.
        finally:token.unregister_client(provider)
        content='Late reply that must never be retained'
    else:
        content='Synthetic owner reply'
    return {'provider':'ollama','model':'fixture','content':content},state
brain._generate_with_agent_tools=generate
print(OWNER_CONTROL_TOKEN,flush=True)
import uvicorn
uvicorn.run(app,host='127.0.0.1',port=int(os.environ['OWNER_CHAT_TEST_PORT']),log_level='error')
'''


def wait_until(check, *, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except (httpx.HTTPError, sqlite3.OperationalError):
            pass
        time.sleep(.025)
    raise AssertionError("Owner chat cancellation deadline exceeded")


def verify_disconnect(runtime, phase):
    with tempfile.TemporaryDirectory(prefix="owner-chat-disconnect-") as directory:
        data = Path(directory)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        env = {**os.environ, "HOMESERVER_DATA_DIR": directory,
               "OWNER_CHAT_TEST_PORT": str(port), "VP3_OS_HARDWARE_ADAPTER": "disabled",
               "NO_PROXY": "127.0.0.1,localhost"}
        with (data / "runtime.log").open("wb") as log:
            process = subprocess.Popen([sys.executable, "-u", "-c", runtime], cwd=ROOT,
                env=env, stdout=subprocess.PIPE, stderr=log, text=True)
            wire = None
            try:
                owner_token = process.stdout.readline().strip()
                assert owner_token
                base = f"http://127.0.0.1:{port}"
                cancel_header = {"X-HomeServer-Chat-Cancel": "disconnect"}
                with httpx.Client(base_url=base, trust_env=False, timeout=10) as client:
                    wait_until(lambda: client.get("/api/v1/health").status_code == 200)
                    denied = client.post("/api/v1/control/chat", headers=cancel_header,
                        json={"message": "Unauthenticated fixture"})
                    assert denied.status_code == 401
                    assert client.post("/__owner/session", headers={"x-homeserver-owner": owner_token}).status_code == 200
                    # Existing integrations retain their non-cancellable behavior.
                    legacy = client.post("/api/v1/control/chat", json={"message": "Legacy fixture"})
                    assert legacy.status_code == 200, legacy.text
                    conversation_id = legacy.json()["conversation_id"]
                    # The new path preserves owner privacy/read-only options and
                    # still produces an ordinary successful reply while connected.
                    success = client.post("/api/v1/control/chat", headers=cancel_header,
                        json={"message": "Connected UI fixture", "conversation_id": conversation_id,
                              "cloud_allowed": False, "read_only": True})
                    assert success.status_code == 200, success.text
                    assert success.json()["reply"] == "Synthetic owner reply"
                    assert success.json()["context"]["settings"]["cloud_allowed"] is False
                    assert success.json()["read_only"] is True
                    with sqlite3.connect(data / "homeserver.db") as db:
                        prior_messages = db.execute("SELECT id,role,content FROM conversation_messages ORDER BY id").fetchall()

                    body = json.dumps({"message": "cancel-ui-fixture", "conversation_id": conversation_id}).encode()
                    wire = socket.create_connection(("127.0.0.1", port), timeout=5)
                    request_headers = (f"POST /api/v1/control/chat HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                        f"Cookie: homeserver_owner={client.cookies.get('homeserver_owner')}\r\n"
                        "X-HomeServer-Chat-Cancel: disconnect\r\nContent-Type: application/json\r\n"
                        f"Content-Length: {len(body)}\r\n\r\n")
                    wire.sendall(request_headers.encode() + body)
                    wait_until(lambda: (data / "provider-started").exists())
                    wire.shutdown(socket.SHUT_RDWR)
                    wire.close()
                    wire = None
                    wait_until(lambda: (data / "provider-closed").exists(), timeout=3)
                    # Owner control stays available while cancellation waits for
                    # the deliberately uncooperative provider fixture to return.
                    assert client.get("/api/v1/control/conversations").status_code == 200
                    (data / "release-provider").write_text("release")

                    def completed_run():
                        with sqlite3.connect(data / "homeserver.db") as db:
                            return db.execute("SELECT status FROM agent_runs ORDER BY id DESC LIMIT 1").fetchone() == ("failed",)
                    wait_until(completed_run)
                    with sqlite3.connect(data / "homeserver.db") as db:
                        messages = db.execute("SELECT id,role,content FROM conversation_messages ORDER BY id").fetchall()
                        run = db.execute("SELECT status,error,metadata_json FROM agent_runs ORDER BY id DESC LIMIT 1").fetchone()
                        proposal = db.execute("SELECT status,error FROM action_requests ORDER BY created_at DESC LIMIT 1").fetchone()
                        history = db.execute("SELECT id FROM conversations").fetchall()
                    assert messages == prior_messages, "Stopped owner turn retained new messages or overwrote earlier history"
                    assert run[0] == "failed" and run[1] == "cancelled:client_disconnected", run
                    metadata = json.loads(run[2])
                    assert metadata["cancelled"] and metadata["cancelled_action_requests"] == 1
                    assert proposal[0] == "denied" and "Interrupted Agent turn" in proposal[1], proposal
                    assert history == [(conversation_id,)]
                    # The same owner can submit another fresh turn after cleanup.
                    retry = client.post("/api/v1/control/chat", headers=cancel_header,
                        json={"message": "Fresh UI fixture", "conversation_id": conversation_id})
                    assert retry.status_code == 200, retry.text
                    print("PASS " + phase + ": owner UI disconnect rejects late messages/proposals")
                    print("PASS " + phase + ": prior history, private/read-only settings, legacy calls and retry remain intact")
            finally:
                if wire is not None:
                    wire.close()
                (data / "release-provider").write_text("release")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


# Hold a real final SQLite INSERT after the last cooperative token check.
# Cancelling now must roll back those prepared writes before committing, then
# deny the already-created proposal using the normal cancellation cleanup.
LATE_INSERT_RUNTIME = RUNTIME.replace(
    "(root/'provider-started').write_text('started')", "pass"
).replace(
    "while not (root/'release-provider').exists() and time.monotonic()<deadline:time.sleep(.01)", "pass"
).replace(
    "finally:token.unregister_client(provider)", "finally:pass"
)
LATE_INSERT_RUNTIME = LATE_INSERT_RUNTIME.replace(
    "brain._generate_with_agent_tools=generate",
    "brain._generate_with_agent_tools=generate\n" + r'''
from app.services import context_chat
from contextlib import contextmanager
original_db=context_chat.db
class FinalInsertGate:
    def __init__(self,connection):self.connection=connection
    def __getattr__(self,name):return getattr(self.connection,name)
    def execute(self,sql,args=()):
        result=self.connection.execute(sql,args)
        if 'INSERT INTO conversation_messages' in sql and "'assistant'" in sql and any('Late reply' in str(x) for x in args):
            (root/'provider-started').write_text('at-final-insert')
            deadline=time.monotonic()+12
            while not (root/'release-provider').exists() and time.monotonic()<deadline:time.sleep(.01)
        return result
@contextmanager
def gated_db():
    with original_db() as connection:yield FinalInsertGate(connection)
context_chat.db=gated_db
'''
)

verify_disconnect(RUNTIME, "provider in flight")
verify_disconnect(LATE_INSERT_RUNTIME, "final history INSERT in flight")

# If the final commit wins first, a simultaneous disconnect must retain the
# successful result instead of reporting HTTP 499 for an already-durable turn.
COMMIT_WINS = r'''
import asyncio,threading
from app import brain_api
from app.services.inference_cancellation import CancellationToken
continued=CancellationToken()
continued.commit(lambda:None)
assert continued.cancel("physical_job_stop") is True and continued.cancelled
print("PASS physical and other longer jobs can still cancel after chat persistence")
committed=threading.Event()
release=threading.Event()
seen=[]
def chat(source,payload,**kwargs):
    token=kwargs["cancellation_token"]
    token.commit(lambda: committed.set())
    seen.append(token)
    assert release.wait(3)
    return {"reply":"Committed turn"}
brain_api._chat_or_http=chat
class Request:
    headers={"x-homeserver-chat-cancel":"disconnect"}
    async def is_disconnected(self):
        assert await asyncio.to_thread(committed.wait,3)
        release.set()
        return True
async def main():
    result=await brain_api._owner_chat_request(brain_api.ChatRequest(message="fixture"),Request())
    assert result=={"reply":"Committed turn"}
    assert seen[0].committed and not seen[0].cancelled
    assert seen[0].cancel("too_late") is False
    print("PASS final commit wins a simultaneous disconnect without a false cancellation result")
asyncio.run(main())
'''
with tempfile.TemporaryDirectory(prefix="owner-chat-commit-wins-") as directory:
    subprocess.run([sys.executable,"-c",COMMIT_WINS],cwd=ROOT,
        env={**os.environ,"HOMESERVER_DATA_DIR":directory},check=True)
