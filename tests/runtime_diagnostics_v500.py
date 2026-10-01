"""Section 32 installed-machine diagnostics: truthful evidence and non-recording probe."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(prefix="hs-runtime-diagnostics-") as data:
    os.environ["HOMESERVER_DATA_DIR"]=data
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import runtime_diagnostics as diag
    from app.services import tools, agent_tools

    # Preserve unrelated hardware fields used by the actual HomeServer startup.
    hardware={**diag.vp3_os.hardware_inventory(),"camera":{"present":True,"ready":True}}
    stt={"stt":{"available":True,"model":"en"},"tts":{"available":True,"voice":"en"}}
    audio={"available":True,"default_input_index":0,"default_output_index":1}
    media={"healthy":True,"ffmpeg_available":True,"ffprobe_available":True,
           "ffmpeg_version":"test","hashes_verified":True}
    inference={"providers":[
        {"provider_key":"ollama","enabled":True,"model":"test-model","ready":True},
        {"provider_key":"openai","credential_configured":True,"ready":True,"api_key":"SECRET_NEVER_EXPORT"},
    ]}
    with patch.object(diag.local_voice,"status",return_value=stt), \
         patch.object(diag.device_audio,"status",return_value=audio), \
         patch.object(diag.homeserver_media_tools,"public_capability",return_value=media), \
         patch.object(diag.providers,"inference_status",return_value=inference), \
         patch.object(diag.providers,"discover_ollama_models",return_value={
             "reachable":True,"models":["test-model"],"base_url":"http://localhost:11434"
         }) as discover, \
         patch.object(diag.physical_meeting,"public_capability",return_value={
             "local_streaming_stt":"whisper.cpp","raw_audio_persisted":False
         }), \
         patch.object(diag.vp3_os,"hardware_inventory",return_value=hardware), \
         patch.object(diag.tracky_physical_context,"sync_status",return_value={
             "consecutive_failures":0
         }), \
         patch.object(diag.tracky_physical_context,"_provider_snapshot",return_value=(
             lambda x: {},{},"test-provider"
         )):
        overview=diag.inventory()
        assert overview["contract"]==diag.CONTRACT
        assert overview["certified_end_to_end"] is False
        assert overview["recorded_media"] is False
        assert overview["requested_repairs"] is False
        checks={x["name"]:x for x in overview["checks"]}
        assert len(checks)==13
        assert checks["tracky_native_camera"]["hardware_certified"] is False
        assert checks["tracky_native_camera"]["identity_recognition"] is False
        assert checks["tracky_native_camera"]["continuous_tracking"] is False
        assert checks["tracky_native_camera"]["status"] in ("not_verified", "missing")
        assert checks["tracky_owner_browser"]["status"]=="missing"
        assert checks["tracky_owner_browser"]["hardware_certified"] is False
        assert checks["tracky_owner_browser"]["face_identity_verified"] is False
        for name in ("transcription","voice_output","microphone_capture","speaker_device",
                     "video_processing","agent_eyes","local_llm"):
            assert checks[name]["status"]=="not_verified",(name,checks[name])
        assert checks["recording_retention"]["status"]=="unsupported"
        assert not checks["local_llm"]["connectivity_tested"]
        assert not checks["agent_eyes"]["frame_inference_tested"]
        assert not checks["microphone_capture"]["recording_tested"]
        assert "SECRET_NEVER_EXPORT" not in json.dumps(overview)
        discover.assert_not_called()
        # Before app startup there is no tool policy table. Validate registry
        # metadata here; authenticated integration exercises the live API below.
        assert agent_tools.MODEL_TOOL_NAMES["homeserver_runtime_diagnostics"]=="runtime.diagnostics"
        assert tools.TOOL_DEFINITIONS["runtime.diagnostics"]["owner_only"] is True
        tool_result=tools._runtime_diagnostics({})
        assert tool_result[0]["read_only"] is True
        discover.assert_not_called()
        probe=diag.inventory(probe=True)
        discover.assert_called_once()
        linked={x["name"]:x for x in probe["checks"]}
        assert linked["local_llm"]["model_installed"] is True
        assert linked["local_llm"]["generation_tested"] is False
        with TestClient(app) as client:
            denied=client.get("/api/v1/control/runtime-diagnostics")
            assert denied.status_code==401,denied.text
            denied_probe=client.post("/api/v1/control/runtime-diagnostics/safe-probe")
            assert denied_probe.status_code==401,denied_probe.text
            auth=client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN})
            assert auth.status_code==200,auth.text
            page=client.get("/api/v1/control/runtime-diagnostics")
            assert page.status_code==200 and page.json()["source"]=="installed_homeserver"
    # Loss of a probe must be reported, not mistaken for operational readiness.
    with patch.object(diag.local_voice,"status",side_effect=RuntimeError("secret/test/private-path")):
        result=diag.inventory()
        row=next(item for item in result["checks"] if item["name"]=="transcription")
        assert row["status"]=="missing"
        assert "private-path" not in json.dumps(result)
print("Installed runtime inventory, opt-in Ollama probe and owner-only route PASS")
