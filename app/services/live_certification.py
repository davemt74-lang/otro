"""Explicit owner-initiated hardware certification on the INSTALLED HomeServer.

No cron, no cloud relay and no automatic capture. Each POST runs one bounded
test, stores only timings / coarse outcomes, and discards raw audio and vision.
"""
from __future__ import annotations

import io
import json
import math
import re
import secrets
import struct
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Any

import httpx

from ..database import db
from . import (
    device_audio, homeserver_media_tools, local_voice, physical_meeting,
    providers, runtime_diagnostics, tracky_physical_context, vp3_os,
)

CONTRACT="vp3.homeserver.live-certification.v1"
TESTS={
    "ollama_generation": "Generate a bounded answer using the configured local model.",
    "ollama_tool_call": "Check a local model's structured tool-calling response.",
    "speech_loopback": "Synthesize a synthetic phrase and transcribe the generated WAV.",
    "speaker_playback": "Play a generated test phrase through the configured local speakers.",
    "microphone_capture": "Capture exactly two seconds on the local microphone; immediately discard PCM.",
    "synthetic_video": "Encode and probe a one-second synthetic video using managed FFmpeg.",
    "agent_eyes": "Request an owner-consented governed Tracky current-view observation.",
}
DEVICE_TESTS=frozenset({"microphone_capture","speaker_playback","agent_eyes"})
_RUN_LOCK=threading.Lock()


class CertificationError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message);self.status_code=status_code


def catalog()->dict[str,Any]:
    return {
        "contract":CONTRACT, "tests":[
            {"key":key,"description":description,"captures_personal_data":key in {"microphone_capture","agent_eyes"},
             "requires_explicit_consent":True,
             "may_modify_tracky_scene":key=="agent_eyes"}
            for key,description in TESTS.items()
        ],
        "automatic_execution":False,"raw_media_retained":False,
        "unsupported":["saved_camera_video_recording","production_audio_recording_and_retention"],
    }


def _privacy_check(test_key:str)->None:
    if test_key not in DEVICE_TESTS:return
    meeting=physical_meeting.status()
    if meeting.get("state") not in (None,"idle","error","stopped"):
        raise CertificationError("Meeting audio is in use; finish the meeting before testing.",409)
    privacy=vp3_os.hardware_inventory().get("privacy_switch",{})
    if privacy.get("engaged"):
        raise CertificationError("The physical privacy switch is engaged.",409)


def _run_ollama(*,tool:bool)->tuple[str,dict[str,Any]]:
    provider=providers.get_ollama()
    if not provider.get("enabled") or not provider.get("model"):
        return "unsupported",{"reason":"ollama_not_configured"}
    try:
        url=providers.normalize_loopback_url(provider["base_url"])
        request={
            "model":provider["model"],
            "stream":False,
            "messages":[{"role":"user","content":"Reply with exactly CERTIFIED if you can respond."}],
            "options":{"num_predict":32,"temperature":0},
        }
        if tool:
            request["messages"]=[{"role":"user","content":"Call report_ready with ready true and no prose."}]
            request["tools"]=[{"type":"function","function":{
                "name":"report_ready","description":"Report readiness; no external action.",
                "parameters":{"type":"object","properties":{"ready":{"type":"boolean"}},"required":["ready"]},
            }}]
        with httpx.Client(timeout=20.0,trust_env=False) as client:
            response=client.post(url+"/api/chat",json=request)
            response.raise_for_status()
            payload=response.json()
        message=payload.get("message") if isinstance(payload,dict) else None
        message=message if isinstance(message,dict) else {}
        if tool:
            raw=message.get("tool_calls")
            passed=isinstance(raw,list) and any(
                isinstance(call,dict) and (call.get("function") or {}).get("name")=="report_ready"
                and (call.get("function") or {}).get("arguments",{}).get("ready") is True
                for call in raw
            )
        else:
            passed=bool(str(message.get("content") or "").strip())
        usage=max(0,int(payload.get("eval_count") or 0))
        return ("operational" if passed else "degraded"),{
            "response_valid":passed,"output_tokens":usage,"local_only":True,
        }
    except (ValueError,TypeError,KeyError,httpx.HTTPError,OverflowError):
        return "failed",{"reason":"ollama_generation_unavailable"}


def _run_speech()->tuple[str,dict[str,Any]]:
    state=local_voice.status()
    if not state.get("stt",{}).get("available") or not state.get("tts",{}).get("available"):
        return "unsupported",{"reason":"local_voice_models_not_installed"}
    phrase="HomeServer certification test."
    sample=local_voice.synthesize(phrase)
    transcribed=local_voice.transcribe(sample)
    # No transcript or generated audio is returned or persisted.
    heard=re.sub(r"[^a-z]","",str(transcribed.get("text") or "").lower())
    passed="homeserver" in heard and "certification" in heard
    return ("operational" if passed else "degraded"),{
        "generated_pcm_wav":sample[:4]==b"RIFF",
        "transcription_matches_fixture":passed,"raw_audio_retained":False,
    }


def _run_speaker()->tuple[str,dict[str,Any]]:
    if not local_voice.status().get("tts",{}).get("available"):
        return "unsupported",{"reason":"piper_not_installed"}
    sample=local_voice.synthesize("This is a HomeServer speaker test.")
    device_audio.device_audio.play_wav(sample)
    # Software playback succeeded; a person must still confirm hearing it.
    return "not_verified",{"speaker_stream_played":True,"human_audibility_confirmed":False}


def _run_microphone()->tuple[str,dict[str,Any]]:
    if not device_audio.status().get("available"):
        return "unsupported",{"reason":"microphone_backend_unavailable"}
    # An isolated capture instance, explicit owner action, bounded local duration.
    instance=device_audio.DeviceAudio()
    instance.start_capture()
    try:
        time.sleep(2.0)
    finally:
        recording=instance.stop_capture()
    with wave.open(io.BytesIO(recording),"rb") as wav:
        duration=wav.getnframes()/max(1,wav.getframerate())
        pcm=wav.readframes(wav.getnframes())
    # No raw recording, transcript or device ID is retained.
    if len(pcm)>=2:
        values=struct.iter_unpack("<h",pcm[:len(pcm)//2*2])
        sample_count=max(1,len(pcm)//2)
        amplitude=math.isqrt(sum(value*value for (value,) in values)//sample_count)
    else: amplitude=0
    del pcm,recording
    passed=1.5<=duration<=3.2 and amplitude>50
    return ("operational" if passed else "degraded"),{
        "capture_seconds":round(duration,2),"sound_detected":amplitude>50,
        "buffer_discarded":True,"saved_recording_certified":False,
    }


def _run_video()->tuple[str,dict[str,Any]]:
    try:
        paths=homeserver_media_tools.require()
    except (OSError,RuntimeError):
        return "unsupported",{"reason":"managed_ffmpeg_unavailable"}
    with tempfile.TemporaryDirectory(prefix="vp3-cert-video-") as folder:
        output=Path(folder)/"synthetic.mp4"
        cmd=[
            str(paths["ffmpeg"]),"-hide_banner","-loglevel","error","-nostdin","-y",
            "-f","lavfi","-i","testsrc=size=320x180:rate=10",
            "-t","1","-an","-c:v","mpeg4","-q:v","5",str(output),
        ]
        try:
            encoded=subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
                                   timeout=20,check=False)
            if encoded.returncode!=0 or not output.is_file() or output.stat().st_size<=100:
                return "failed",{"reason":"synthetic_encoder_failed"}
            inspected=subprocess.run([
                str(paths["ffprobe"]),"-v","error","-select_streams","v:0",
                "-show_entries","stream=width,height,codec_name",
                "-of","json",str(output),
            ],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=8,check=False)
            info=json.loads(inspected.stdout.decode("utf-8")) if inspected.returncode==0 else {}
            streams=info.get("streams") or []
            passed=bool(streams and streams[0].get("width")==320 and streams[0].get("height")==180)
        except (OSError,subprocess.TimeoutExpired,ValueError,IndexError):
            return "failed",{"reason":"synthetic_encode_or_probe_failed"}
        return ("operational" if passed else "failed"),{
            "synthetic_video_encoded":passed,"ffprobe_video_verified":passed,
            "camera_accessed":False,"saved_camera_recording_certified":False,
        }


def _run_eyes()->tuple[str,dict[str,Any]]:
    provider,caps,_=tracky_physical_context._provider_snapshot()
    if provider is None:
        return "unsupported",{"reason":"perception_provider_unavailable"}
    camera=vp3_os.hardware_inventory().get("camera",{})
    if caps.get("requires_camera",True) and not camera.get("ready"):
        return "unsupported",{"reason":"camera_not_ready"}
    # Reuse the governed Tracky request: honors physical privacy, sync authority
    # and bounded provider timeouts. May ingest semantic events, never raw frames.
    result=tracky_physical_context.active_perception(
        "refresh_current_view",reason="Owner-initiated live certification",
        requested_by="homeserver_owner_certification",
    )
    status=str(result.get("request",result).get("status") or "")
    passed=status=="completed"
    return ("not_verified" if passed else "degraded"),{
        "governed_perception_completed":passed,
        "camera_frames_verified_by_certifier":False,
        "semantic_output_not_recorded":True,
        "reason":"owner_review_of_visual_result_required" if passed else "provider_did_not_complete",
    }


def _run_named(key:str)->tuple[str,dict[str,Any]]:
    if key=="ollama_generation":return _run_ollama(tool=False)
    if key=="ollama_tool_call":return _run_ollama(tool=True)
    if key=="speech_loopback":return _run_speech()
    if key=="speaker_playback":return _run_speaker()
    if key=="microphone_capture":return _run_microphone()
    if key=="synthetic_video":return _run_video()
    if key=="agent_eyes":return _run_eyes()
    raise CertificationError("Unknown certification test.")


def execute(test_key:str,*,consent:bool,physical_capture_ack:bool=False)->dict[str,Any]:
    if test_key not in TESTS:raise CertificationError("Unknown certification test.",422)
    if not consent:raise CertificationError("Explicit owner consent is required for each test.",403)
    if test_key in {"microphone_capture","agent_eyes"} and not physical_capture_ack:
        raise CertificationError("Explicit microphone/camera capture acknowledgment required.",403)
    _privacy_check(test_key)
    if not _RUN_LOCK.acquire(blocking=False):
        raise CertificationError("Another certification test is running.",409)
    started=time.monotonic()
    try:
        try:
            status,evidence=_run_named(test_key)
        except CertificationError:
            raise
        except Exception:
            status,evidence="failed",{"reason":"test_failed_review_local_logs"}
        if status not in {"operational","degraded","failed","unsupported","not_verified"}:
            status,evidence="failed",{"reason":"invalid_test_result"}
        # Allowlist only known, non-sensitive, scalar evidence. No raw media,
        # model responses, provider URLs, device identifiers or transcripts.
        allowed_reasons=frozenset({
            "ollama_not_configured","ollama_generation_unavailable",
            "local_voice_models_not_installed","piper_not_installed",
            "microphone_backend_unavailable","managed_ffmpeg_unavailable",
            "synthetic_encoder_failed","synthetic_encode_or_probe_failed",
            "perception_provider_unavailable","camera_not_ready",
            "owner_review_of_visual_result_required","provider_did_not_complete",
            "test_failed_review_local_logs","invalid_test_result",
        })
        allowed={
            "reason","response_valid","output_tokens","local_only",
            "generated_pcm_wav","transcription_matches_fixture","raw_audio_retained",
            "speaker_stream_played","human_audibility_confirmed",
            "capture_seconds","sound_detected","buffer_discarded","saved_recording_certified",
            "synthetic_video_encoded","ffprobe_video_verified","camera_accessed",
            "saved_camera_recording_certified","governed_perception_completed",
            "camera_frames_verified_by_certifier","semantic_output_not_recorded",
        }
        clean={k:v for k,v in evidence.items()
               if k in allowed and (isinstance(v,bool) or isinstance(v,(int,float))
                                      or (k=="reason" and isinstance(v,str) and v in allowed_reasons))}
        elapsed=min(300000,max(0,round((time.monotonic()-started)*1000)))
        result={
            "id":secrets.token_hex(12),"test_key":test_key,"status":status,
            "duration_ms":elapsed,"evidence":clean,
        }
        with db() as connection:
            connection.execute(
                "INSERT INTO runtime_certification_runs(id,test_key,status,duration_ms,evidence_json) VALUES (?,?,?,?,?)",
                (result["id"],test_key,status,elapsed,json.dumps(clean,sort_keys=True,separators=(",",":"))),
            )
        return {"contract":CONTRACT,**result,"device_executed":True}
    finally:
        _RUN_LOCK.release()


def history(limit:int=30)->dict[str,Any]:
    with db() as connection:
        rows=connection.execute(
            "SELECT id,test_key,status,duration_ms,evidence_json,created_at "
            "FROM runtime_certification_runs ORDER BY rowid DESC LIMIT ?",
            (min(100,max(1,int(limit))),),
        ).fetchall()
    return {
        "contract":CONTRACT,"records":[{
            "id":row["id"],"test_key":row["test_key"],"status":row["status"],
            "duration_ms":row["duration_ms"],"evidence":json.loads(row["evidence_json"]),
            "created_at":row["created_at"],
        } for row in rows],
        "recording_content_retained":False,
        "certified_saved_video_recording":False,
    }
