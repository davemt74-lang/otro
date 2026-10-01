"""Live hardware certification acceptance: no background capture, no key/media leaks."""
import io, json, os, sys, tempfile, wave
from pathlib import Path
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(prefix="hs-live-cert-v501-") as data:
    os.environ["HOMESERVER_DATA_DIR"]=data
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import live_certification as cert
    from app.services import runtime_diagnostics as inventory
    from app.services.tasks import scheduler

    assert cert.catalog()["automatic_execution"] is False
    assert cert.catalog()["raw_media_retained"] is False
    assert len(cert.catalog()["tests"])==7
    assert inventory.inventory()["certified_end_to_end"] is False

    # Must not access a single live device before explicit owner consent.
    with patch.object(cert,"_run_named",side_effect=AssertionError("unexpected hardware access")):
        for key in cert.TESTS:
            try:cert.execute(key,consent=False)
            except cert.CertificationError as exc:assert exc.status_code==403
            else:raise AssertionError("Missing consent accepted")
        for key in ("microphone_capture","agent_eyes"):
            try:cert.execute(key,consent=True,physical_capture_ack=False)
            except cert.CertificationError as exc:assert exc.status_code==403
            else:raise AssertionError("Missing capture acknowledgement accepted")

    # Direct engine tests mock only device/runtime boundaries; never exercise
    # the runner's production camera or physical microphone on CI.
    with patch.object(cert.providers,"get_ollama",return_value={
        "enabled":True,"model":"fixture","base_url":"http://127.0.0.1:11434"
    }),patch.object(cert.httpx,"Client") as client:
        http_response=Mock()
        http_response.json.return_value={
            "message":{"content":"CERTIFIED","tool_calls":[{"function":{
                "name":"report_ready","arguments":{"ready":True}
            }}]},"eval_count":4,
        }
        client.return_value.__enter__.return_value.post.return_value=http_response
        assert cert._run_ollama(tool=False)[0]=="operational"
        assert cert._run_ollama(tool=True)[0]=="operational"
        calls=client.return_value.__enter__.return_value.post.call_args_list
        assert len(calls)==2
        assert calls[0].kwargs["json"]["options"]["num_predict"]==32
        assert calls[1].kwargs["json"]["tools"][0]["function"]["name"]=="report_ready"

    with patch.object(cert.local_voice,"status",return_value={"stt":{"available":True},"tts":{"available":True}}), \
         patch.object(cert.local_voice,"synthesize",return_value=b"RIFF" + b"\0"*44), \
         patch.object(cert.local_voice,"transcribe",return_value={
             "text":"HomeServer certification test."
         }) as transcribe:
        assert cert._run_speech()[0]=="operational"
        assert transcribe.call_count==1

    with patch.object(cert.local_voice,"status",return_value={"tts":{"available":True}}), \
         patch.object(cert.local_voice,"synthesize",return_value=b"RIFF"+b"\0"*44), \
         patch.object(cert.device_audio.device_audio,"play_wav") as played:
        state,ev=cert._run_speaker()
        assert state=="not_verified" and ev["human_audibility_confirmed"] is False
        played.assert_called_once()

    audio=io.BytesIO()
    with wave.open(audio,"wb") as out:
        out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000)
        out.writeframes((b"\xe8\x03"*32000))
    mock_device=Mock()
    mock_device.stop_capture.return_value=audio.getvalue()
    with patch.object(cert.device_audio,"status",return_value={"available":True}), \
         patch.object(cert.device_audio,"DeviceAudio",return_value=mock_device), \
         patch.object(cert.time,"sleep") as sleep:
        state,ev=cert._run_microphone()
        assert state=="operational" and ev["buffer_discarded"] is True
        assert ev["saved_recording_certified"] is False
        sleep.assert_called_once_with(2.0)
        mock_device.start_capture.assert_called_once()
        mock_device.stop_capture.assert_called_once()

    with patch.object(cert.tracky_physical_context,"_provider_snapshot",return_value=(
        lambda p: {},{"requires_camera":True},"fixture"
    )),patch.object(cert.vp3_os,"hardware_inventory",return_value={
        "camera":{"ready":True},"privacy_switch":{"engaged":False}
    }),patch.object(cert.tracky_physical_context,"active_perception",return_value={
        "request":{"status":"completed","semantic_projection":{
            "private_camera_frame":"SECRET_NEVER_STORE"
        }}
    }) as camera:
        state,ev=cert._run_eyes()
        assert state=="not_verified" and ev["camera_frames_verified_by_certifier"] is False
        assert "SECRET_NEVER_STORE" not in json.dumps(ev)
        assert camera.call_args.kwargs["requested_by"]=="homeserver_owner_certification"

    with patch.object(cert.homeserver_media_tools,"require",return_value={
        "ffmpeg":Path("/fixture/ffmpeg"),"ffprobe":Path("/fixture/ffprobe")
    }),patch.object(cert.subprocess,"run") as sp:
        def fake_ffmpeg(cmd,**kwargs):
            if cmd[0].endswith("ffmpeg"):
                Path(cmd[-1]).write_bytes(b"x"*200)
                return Mock(returncode=0)
            return Mock(returncode=0,stdout=b'{"streams":[{"width":320,"height":180,"codec_name":"mpeg4"}]}')
        sp.side_effect=fake_ffmpeg
        state,ev=cert._run_video()
        assert state=="operational" and ev["saved_camera_recording_certified"] is False

    # A run is serialized, validates privacy and saves only allowlisted metrics.
    with TestClient(app) as client:
        scheduler.stop()
        assert client.get("/api/v1/control/runtime-certification").status_code==401
        assert client.post("/api/v1/control/runtime-certification/run",json={
            "test_key":"microphone_capture","consent":True,"physical_capture_ack":True,
        }).status_code==401
        assert client.post("/__owner/session",headers={
            "X-HomeServer-Owner":OWNER_CONTROL_TOKEN
        }).status_code==200
        assert client.get("/api/v1/control/runtime-certification").status_code==200
        def invoke(body,headers=None):
            return client.post("/api/v1/control/runtime-certification/run",
                json=body,headers=headers or {"X-Requested-With":"XMLHttpRequest"})
        assert invoke({"test_key":"ollama_generation","consent":True},headers={}).status_code==403
        assert invoke({"test_key":"ollama_generation","consent":False}).status_code==403
        assert invoke({"test_key":"agent_eyes","consent":True}).status_code==403
        assert invoke({"test_key":"unknown","consent":True}).status_code==422
        with patch.object(cert,"_run_named",return_value=(
            "operational",{"response_valid":True,"private_key":"SECRET","reason":"sksecret"}
        )):
            result=invoke({"test_key":"ollama_generation","consent":True})
        assert result.status_code==200,result.text
        record=result.json()
        assert record["status"]=="operational"
        assert record["evidence"]=={"response_valid":True}
        assert "SECRET" not in result.text
        recent=client.get("/api/v1/control/runtime-certification").json()
        assert recent["history"]["records"][0]["status"]=="operational"
        assert "SECRET" not in json.dumps(recent)
        assert recent["history"]["recording_content_retained"] is False
        assert recent["history"]["certified_saved_video_recording"] is False
        cert._RUN_LOCK.acquire()
        try:
            assert invoke({"test_key":"ollama_generation","consent":True}).status_code==409
        finally:cert._RUN_LOCK.release()

print("Live owner certification: consent, bounded mocked hardware tests, redacted ledger and API gates PASS")
