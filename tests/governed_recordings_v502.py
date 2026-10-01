"""Saved private recording / transcription mode: owner consent, no Cloud or chat autopost."""
import io, os, sys, tempfile, wave
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(prefix="hs-recording-v502-") as data:
    os.environ["HOMESERVER_DATA_DIR"]=data
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import governed_recordings as rec
    from app.services.tasks import scheduler
    from app.database import db

    sample=io.BytesIO()
    with wave.open(sample,"wb") as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000)
        w.writeframes(b"\x80\x00"*48000)
    wav=sample.getvalue()
    state={"privacy_switch":{"engaged":False},"camera":{"ready":False}}
    def fake_audio(_seconds,target):
        target.write_bytes(wav)
    with TestClient(app,base_url='http://localhost') as client:
        scheduler.stop()
        headers={"X-Requested-With":"XMLHttpRequest"}
        assert client.get("/api/v1/control/governed-recordings").status_code==401
        assert client.post("/api/v1/control/governed-recordings/capture",
               json={"kind":"audio","seconds":3,"consent":True,"physical_capture_ack":True},
               headers=headers).status_code==401
        login=client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN})
        assert login.status_code==200,login.text
        no_header=client.post("/api/v1/control/governed-recordings/capture",
              json={"kind":"audio","seconds":3,"consent":True,"physical_capture_ack":True})
        assert no_header.status_code==403
        with patch.object(rec.vp3_os,"hardware_inventory",side_effect=lambda:state), \
             patch.object(rec.physical_meeting,"status",return_value={"state":"idle"}), \
             patch.object(rec.device_audio,"status",return_value={"available":True}), \
             patch.object(rec,"_capture_audio",side_effect=fake_audio) as record:
            missing=client.post("/api/v1/control/governed-recordings/capture",
                json={"kind":"audio","seconds":3,"consent":False,"physical_capture_ack":True},
                headers=headers)
            assert missing.status_code==403
            record.assert_not_called()
            state["privacy_switch"]["engaged"]=True
            blocked=client.post("/api/v1/control/governed-recordings/capture",
                json={"kind":"audio","seconds":3,"consent":True,"physical_capture_ack":True},
                headers=headers)
            assert blocked.status_code==409
            record.assert_not_called()
            state["privacy_switch"]["engaged"]=False
            ok=client.post("/api/v1/control/governed-recordings/capture",
                json={"kind":"audio","seconds":3,"consent":True,"physical_capture_ack":True},
                headers=headers)
            assert ok.status_code==200,ok.text
            item=ok.json()["recording"]
            assert item["sha256"] and item["kind"]=="audio"
            rid=item["id"]
            record.assert_called_once()
            assert rec._file(rid,"audio").read_bytes()==wav
            listed=client.get("/api/v1/control/governed-recordings")
            assert listed.status_code==200
            assert listed.json()["recordings"]["items"][0]["id"]==rid
            assert listed.json()["recordings"]["items"][0]["has_transcript"] is False
            private=client.get(f"/api/v1/control/governed-recordings/{rid}/download")
            assert private.status_code==200
            assert private.content==wav
            missing_transcript=client.get(f"/api/v1/control/governed-recordings/{rid}/transcript")
            assert missing_transcript.status_code==404
            with patch.object(rec.local_voice,"status",return_value={"stt":{"available":True}}), \
                 patch.object(rec.local_voice,"transcribe",return_value={
                    "text":"A private HomeServer test.","provider":"whisper.cpp"
                 }) as whisper:
                no_post=client.post(f"/api/v1/control/governed-recordings/{rid}/transcribe")
                assert no_post.status_code==403
                transcript=client.post(
                    f"/api/v1/control/governed-recordings/{rid}/transcribe",headers=headers)
                assert transcript.status_code==200,transcript.text
                assert transcript.json()["transcript"]=="A private HomeServer test."
                assert transcript.json()["sent_to_agent"] is False
                assert transcript.json()["sent_to_cloud"] is False
                second=client.post(
                    f"/api/v1/control/governed-recordings/{rid}/transcribe",headers=headers)
                assert second.status_code==200
                whisper.assert_called_once_with(wav)
            read=client.get(f"/api/v1/control/governed-recordings/{rid}/transcript")
            assert read.status_code==200 and read.json()["transcript"]=="A private HomeServer test."
            history=client.get("/api/v1/control/governed-recordings").json()
            assert history["recordings"]["items"][0]["has_transcript"] is True
            assert "A private HomeServer test" not in str(history)
            invalid=client.get("/api/v1/control/governed-recordings/%2E%2E/download")
            assert invalid.status_code in (404,422)
            # Operator selects the trusted camera input; HTTP cannot inject ffmpeg flags.
            state["camera"]["ready"]=True
            with patch.dict(os.environ,{"HOMESERVER_RECORD_CAMERA_FORMAT":"dshow",
                "HOMESERVER_RECORD_CAMERA_DEVICE":"-f lavfi"},clear=False):
                if os.name!="nt":
                    try:rec._camera_input()
                    except rec.RecordingError:pass
                    else:raise AssertionError("Unsafe camera source accepted")
            remove=client.delete(f"/api/v1/control/governed-recordings/{rid}",headers=headers)
            assert remove.status_code==200,remove.text
            assert not rec._file(rid,"audio").exists()
            assert client.get(f"/api/v1/control/governed-recordings/{rid}/transcript").status_code==404
            # Video requires actual configured ready capture; do not fake certification.
            state["camera"]["ready"]=False
            unavailable=client.post("/api/v1/control/governed-recordings/capture",
                json={"kind":"video","seconds":3,"consent":True,"physical_capture_ack":True},
                headers=headers)
            assert unavailable.status_code==503
    assert not any(Path(data).rglob("*.pending.*"))
print("Private owner recording, local saved transcription, no auto-chat/Cloud, consent and deletion PASS")
