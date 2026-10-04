"""Owner-local persistent transcript, consented Cloud text relay, no audio exposure."""
import os,sys,tempfile
from unittest.mock import patch
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(prefix="hs-local-transcription-") as folder:
    os.environ["HOMESERVER_DATA_DIR"]=folder
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import local_transcription_sessions as tx, remote_bridge
    from app.services.tasks import scheduler

    from app.bridge import capabilities
    with TestClient(app) as client:
        scheduler.stop()
        # Application startup initializes all SQLite tables.
        published=capabilities()
        transcript_caps=published["local_transcriptions"]
        assert transcript_caps["explicit_owner_share"] is True
        assert transcript_caps["raw_audio_relay"] is False
        assert "transcription.shared.fetch" in transcript_caps["operations"]
        base="/api/v1/control/transcription-sessions"
        req={"X-Requested-With":"XMLHttpRequest"}
        assert client.get(base).status_code==401
        assert client.post(base,json={"title":"Private speech"},headers=req).status_code==401
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
        assert client.post(base,json={"title":"Private speech"}).status_code==403
        created=client.post(base,json={"title":"Private speech"},headers=req)
        assert created.status_code==200,created.text
        sid=created.json()["session"]["id"]
        assert created.json()["session"]["cloud_shared"] is False
        assert created.json()["session"]["diarization_available"] is False
        assert created.json()["session"]["speaker_attribution"]["source"] == "unknown"
        assert created.json()["session"]["speaker_attribution"]["authentication_authority"] is False
        assert client.post(base,json={"title":"Overlapping"},headers=req).status_code==409
        key="a"*32
        segment={"text":"My private local transcript.","client_key":key,"started_ms":100}
        appended=client.post(f"{base}/{sid}/segments",json=segment,headers=req)
        assert appended.status_code==200,appended.text
        again=client.post(f"{base}/{sid}/segments",json=segment,headers=req)
        assert again.status_code==200 and again.json()["duplicate"] is True
        assert client.get(f"{base}/{sid}").json()["session"]["segment_count"]==1
        assert client.get(base).json()["sessions"][0]["cloud_shared"] is False
        assert tx.list_sessions(paired=True)["sessions"]==[]
        try:tx.get(sid,paired=True)
        except tx.TranscriptError as exc:assert exc.status_code==403
        else:raise AssertionError("Cloud fetched private active text")
        assert client.put(f"{base}/{sid}/cloud-share",json={"cloud_share":True},headers=req).status_code==409
        stopped=client.post(f"{base}/{sid}/stop",headers=req)
        assert stopped.status_code==200
        assert stopped.json()["session"]["status"]=="completed"
        assert tx.list_sessions(paired=True)["sessions"]==[]
        assert client.post(f"{base}/{sid}/segments",json={**segment,"client_key":"b"*32},headers=req).status_code==409
        allow=client.put(f"{base}/{sid}/cloud-share",json={"cloud_share":True},headers=req)
        assert allow.status_code==200 and allow.json()["session"]["cloud_shared"]
        shared=tx.get(sid,paired=True)
        assert shared["session"]["segments"][0]["text"]=="My private local transcript."
        assert shared["session"]["segments"][0]["attribution"]["source"] == "unknown"
        assert shared["session"]["segments"][0]["attribution"]["speaker_identity_verified"] is False
        assert "audio" not in str(shared["session"]["segments"])
        assert [s["id"] for s in tx.list_sessions(paired=True)["sessions"]]==[sid]
        # The same consent gate applies at the existing paired HTTPS relay;
        # verify the relay does not expose private audio or unrelated sessions.
        with patch.object(remote_bridge,"_vp3_system_apps_identity",
                          return_value={"app_key":"vp3"}) as vp3, \
             patch.object(remote_bridge,"_direct_identity",
                          return_value={"app_key":"vp3","permissions":["knowledge.search"]}) as scope:
            relay_list=remote_bridge.dispatch_remote_request(
                "transcription.shared.list",{"limit":5},"t"*40)
            assert relay_list["status"]==200
            assert [v["id"] for v in relay_list["payload"]["sessions"]]==[sid]
            relay_doc=remote_bridge.dispatch_remote_request(
                "transcription.shared.fetch",{"session_id":sid},"t"*40)
            assert relay_doc["payload"]["session"]["segments"][0]["text"]=="My private local transcript."
            assert relay_doc["payload"]["raw_audio_included"] is False
            vp3.assert_called()
            scope.assert_called()

        revoke=client.put(f"{base}/{sid}/cloud-share",json={"cloud_share":False},headers=req)
        assert revoke.status_code==200
        try:tx.get(sid,paired=True)
        except tx.TranscriptError as exc:assert exc.status_code==403
        else:raise AssertionError("Cloud fetched revoked text")
        assert tx.list_sessions(paired=True)["sessions"]==[]
        assert client.delete(f"{base}/{sid}").status_code==403
        deleted=client.delete(f"{base}/{sid}",headers=req)
        assert deleted.status_code==200
        assert client.get(f"{base}/{sid}").status_code==404
print("Persistent local transcript permissions, dedupe and consented revocable paired text sharing PASS")
