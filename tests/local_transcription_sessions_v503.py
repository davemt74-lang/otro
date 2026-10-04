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
    from app.database import db
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
        assert created.json()["session"]["speaker_attribution"] == "unidentified_single_channel"
        assert created.json()["session"]["attribution"]["source"] == "unknown"
        assert created.json()["session"]["attribution"]["authentication_authority"] is False
        assert client.post(base,json={"title":"Overlapping"},headers=req).status_code==409
        key="a"*32
        segment={"text":"My private local transcript.","client_key":key,"started_ms":100}
        appended=client.post(f"{base}/{sid}/segments",json=segment,headers=req)
        assert appended.status_code==200,appended.text
        again=client.post(f"{base}/{sid}/segments",json=segment,headers=req)
        assert again.status_code==200 and again.json()["duplicate"] is True
        assert client.get(f"{base}/{sid}").json()["session"]["segment_count"]==1
        diarized={
            "text":"A second speaker overlaps.","client_key":"c"*32,"started_ms":200,"ended_ms":850,
            "speaker_label":"Speaker 2",
            "attribution":{
                "contract":"speaker-attribution-v1-20261004","source":"provider_diarization",
                "speaker_label":"Speaker 2","confidence":0,"participant_id":0,
                "participant_identity":"","speaker_identity_verified":False,
                "authentication_authority":False,"overlap":True,"overlap_group":"overlap-1"
            }
        }
        second=client.post(f"{base}/{sid}/segments",json=diarized,headers=req)
        assert second.status_code==200,second.text
        retry=client.post(f"{base}/{sid}/segments",json=diarized,headers=req)
        assert retry.status_code==200 and retry.json()["duplicate"] is True
        changed={**diarized,"speaker_label":"Speaker 3"}
        assert client.post(f"{base}/{sid}/segments",json=changed,headers=req).status_code==409
        live_doc=client.get(f"{base}/{sid}").json()["session"]
        assert live_doc["segment_count"]==2 and live_doc["diarization_available"] is True
        assert live_doc["speaker_attribution"]=="provider_diarization"
        assert live_doc["segments"][1]["speaker"]=="Speaker 2"
        assert live_doc["segments"][1]["attribution"]["overlap"] is True
        assert live_doc["segments"][1]["attribution"]["speaker_identity_verified"] is False
        identified={
            "text":"The enrolled owner is speaking.","client_key":"d"*32,"started_ms":900,"ended_ms":1400,
            "speaker_label":"Speaker 1",
            "speaker_evidence":[
                {"source":"provider_diarization","speaker_label":"Speaker 1","confidence":0},
                {"source":"verified_voice","speaker_label":"Speaker 1","confidence":0.96,
                 "participant_identity":"tracky:owner-one"},
                {"source":"visual_corroboration","speaker_label":"Speaker 1","confidence":0.91,
                 "participant_identity":"tracky:owner-one"},
            ]
        }
        identity_write=client.post(f"{base}/{sid}/segments",json=identified,headers=req)
        assert identity_write.status_code==200,identity_write.text
        identity_retry=client.post(f"{base}/{sid}/segments",json=identified,headers=req)
        assert identity_retry.status_code==200 and identity_retry.json()["duplicate"] is True
        forged={**identified,"client_key":"e"*32,"speaker_evidence":None,
                "attribution":{"contract":"speaker-attribution-v1-20261004","source":"verified_voice",
                               "speaker_label":"Speaker 1","participant_identity":"tracky:owner-one",
                               "speaker_identity_verified":True}}
        assert client.post(f"{base}/{sid}/segments",json=forged,headers=req).status_code==422
        changed_identity={**identified,"speaker_evidence":[
            identified["speaker_evidence"][0],
            {**identified["speaker_evidence"][1],"participant_identity":"tracky:other"},
        ]}
        assert client.post(f"{base}/{sid}/segments",json=changed_identity,headers=req).status_code==409
        live_doc=client.get(f"{base}/{sid}").json()["session"]
        assert live_doc["segment_count"]==3
        assert live_doc["speaker_attribution"]=="verified_voice" and live_doc["speaker_identity_verified"] is True
        owner_turn=live_doc["segments"][2]
        assert owner_turn["speaker"]=="Speaker 1"
        assert owner_turn["attribution"]["participant_identity"]=="tracky:owner-one"
        assert owner_turn["attribution"]["speaker_identity_verified"] is True
        assert owner_turn["attribution"]["visual_corroborated"] is True
        assert owner_turn["attribution"]["authentication_authority"] is False
        assert owner_turn["attribution"]["diarization_source"]=="provider_diarization"
        conflict={
            "text":"Voice and camera disagree.","client_key":"f"*32,"started_ms":1500,"ended_ms":1900,
            "speaker_label":"Speaker 1",
            "speaker_evidence":[
                {"source":"provider_diarization","speaker_label":"Speaker 1","confidence":0},
                {"source":"verified_voice","speaker_label":"Speaker 1","confidence":0.97,
                 "participant_identity":"tracky:owner-one"},
                {"source":"visual_corroboration","speaker_label":"Speaker 1","confidence":0.93,
                 "participant_identity":"tracky:other"},
            ]
        }
        conflicted=client.post(f"{base}/{sid}/segments",json=conflict,headers=req)
        assert conflicted.status_code==200,conflicted.text
        overlap_identity={
            "text":"Overlapping identity attempt.","client_key":"9"*32,"started_ms":2000,"ended_ms":2400,
            "speaker_label":"Speaker 1",
            "speaker_evidence":[
                {"source":"provider_diarization","speaker_label":"Speaker 1","confidence":0,"overlap":True,"overlap_group":"g"},
                {"source":"verified_voice","speaker_label":"Speaker 1","confidence":0.99,
                 "participant_identity":"tracky:owner-one","overlap":True,"overlap_group":"g"},
            ]
        }
        assert client.post(f"{base}/{sid}/segments",json=overlap_identity,headers=req).status_code==422
        live_doc=client.get(f"{base}/{sid}").json()["session"]
        assert live_doc["segment_count"]==4
        conflict_turn=live_doc["segments"][3]
        assert conflict_turn["attribution"]["visual_conflict"] is True
        assert conflict_turn["attribution"]["speaker_identity_verified"] is False
        assert conflict_turn["attribution"]["participant_identity"]==""
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
        assert shared["session"]["segments"][1]["speaker"]=="Speaker 2"
        assert shared["session"]["segments"][1]["attribution"]["source"]=="provider_diarization"
        assert shared["session"]["segments"][1]["attribution"]["speaker_identity_verified"] is False
        assert shared["session"]["segments"][2]["speaker"]=="Speaker 1"
        assert shared["session"]["segments"][2]["attribution"]["source"]=="provider_diarization"
        assert shared["session"]["segments"][2]["attribution"]["participant_identity"]==""
        assert shared["session"]["segments"][2]["attribution"]["speaker_identity_verified"] is False
        assert shared["session"]["segments"][2]["attribution"]["visual_corroborated"] is False
        assert shared["session"]["segments"][3]["attribution"]["source"]=="provider_diarization"
        assert shared["session"]["segments"][3]["attribution"]["participant_identity"]==""
        assert shared["session"]["segments"][3]["attribution"]["visual_conflict"] is False
        assert shared["local_identity_included"] is False
        assert "tracky:owner-one" not in str(shared)
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
            assert relay_doc["payload"]["session"]["segments"][2]["attribution"]["participant_identity"]==""
            assert "tracky:owner-one" not in str(relay_doc["payload"])
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
        with db() as connection:
            assert connection.execute("SELECT COUNT(*) FROM local_transcription_segment_attribution WHERE session_id=?",(sid,)).fetchone()[0]==0
print("Persistent local transcript permissions, dedupe and consented revocable paired text sharing PASS")
