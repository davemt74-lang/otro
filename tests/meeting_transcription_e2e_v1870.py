from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

VP3_EXPECTED_SHA = "30c660efbb16fe8996380016036b167cb0d15c5c"
PUBLIC_ID = "a" * 32
ROOM_NAME = "vp3-meeting-e2e-aabbcc"
WORKER_SECRET = "phase-18-7-test-worker-secret-0123456789abcdef0123456789abcdef"


def run(cmd: list[str], *, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        cmd,
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )
    return completed.stdout.strip()


def php_json(source: str, args: list[str], *, env: dict[str, str] | None = None) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as handle:
        handle.write(source)
        script = handle.name
    try:
        return json.loads(run(["php", script, *args], env=env))
    finally:
        Path(script).unlink(missing_ok=True)


def build_vp3_capability(vp3_dir: Path) -> dict:
    helper = vp3_dir / "includes" / "video-meetings-homeserver-v1850.php"
    php = r'''<?php
declare(strict_types=1);
function video_meeting_worker_secret_v1800(): string { return (string)getenv('VP3_TEST_WORKER_SECRET'); }
require $argv[1];
$publicId=$argv[2];$room=$argv[3];
$meeting=['public_id'=>$publicId,'room_name'=>$room];
$expires=time()+3600;
$token=video_meeting_homeserver_callback_token_v1850($meeting,$expires);
$expiredAt=time()-60;
$expired=video_meeting_homeserver_callback_token_v1850($meeting,$expiredAt);
echo json_encode([
  'expires'=>$expires,
  'token'=>$token,
  'valid'=>video_meeting_homeserver_callback_verify_v1850($publicId,$room,$token),
  'wrong_room'=>video_meeting_homeserver_callback_verify_v1850($publicId,$room.'-wrong',$token),
  'wrong_meeting'=>video_meeting_homeserver_callback_verify_v1850(str_repeat('b',32),$room,$token),
  'expired'=>video_meeting_homeserver_callback_verify_v1850($publicId,$room,$expired),
], JSON_UNESCAPED_SLASHES);
'''
    env = dict(os.environ)
    env["VP3_TEST_WORKER_SECRET"] = WORKER_SECRET
    return php_json(php, [str(helper), PUBLIC_ID, ROOM_NAME], env=env)


def vp3_canonical_source_key(vp3_dir: Path, segment: dict) -> str:
    helper = vp3_dir / "includes" / "video-meetings-transcription-v1800.php"
    php = r'''<?php
declare(strict_types=1);
require $argv[1];
$meeting=['public_id'=>$argv[2]];
$input=json_decode((string)getenv('VP3_SEGMENT_JSON'),true);
echo video_meeting_transcription_source_key_v1800($meeting,$input);
'''
    env = dict(os.environ)
    env["VP3_SEGMENT_JSON"] = json.dumps(segment, separators=(",", ":"))
    return run(["php", "-r", php.replace("<?php\n", ""), str(helper), PUBLIC_ID], env=env)


def assert_current_vp3_contract(vp3_dir: Path) -> None:
    worker = (vp3_dir / "api" / "video-meeting-worker.php").read_text(encoding="utf-8")
    executor = (vp3_dir / "includes" / "video-meetings-homeserver-v1850.php").read_text(encoding="utf-8")
    transcription = (vp3_dir / "includes" / "video-meetings-transcription-v1800.php").read_text(encoding="utf-8")
    agent = (vp3_dir / "includes" / "video-meetings-agent-v1800.php").read_text(encoding="utf-8")

    assert "video_meeting_homeserver_callback_verify_v1850($publicId,$roomName,$provided)" in worker
    assert "if($homeserverAuthorized&&strtolower(trim((string)($input['source']??'')))!=='homeserver')" in worker
    assert "if(empty($input['is_final']))" in worker
    assert "video_meeting_transcription_append_v1800($pdo,$meeting,$input)" in worker
    assert "hash_equals((string)$meeting['room_name'],$roomName)" in worker
    assert "return 'v1850.'.$expiresAt.'.'.$signature" in executor
    assert "'bearer_token'=>video_meeting_homeserver_callback_token_v1850" in executor
    assert "'final_only'=>true" in executor and "'source'=>'homeserver'" in executor
    assert "preg_match('/^[a-f0-9]{64}$/',$key)" in transcription
    assert "if($existingId>0)" in transcription and "'duplicate'=>true" in transcription
    assert "if($route==='homeserver'&&$status==='ready')" in agent
    assert "return ['ok'=>false,'dispatched'=>false,'reason'=>'homeserver_dispatch_failed']" in agent
    assert "if($route!=='cloud'||$status!=='ready')" in agent


def main() -> None:
    vp3_dir = Path(os.environ.get("VP3_SOFTWARE_DIR", ROOT_DIR / "vp3-software")).resolve()
    if not vp3_dir.is_dir():
        raise AssertionError(f"Pinned VP3 checkout is missing: {vp3_dir}")
    actual_sha = run(["git", "-C", str(vp3_dir), "rev-parse", "HEAD"])
    assert actual_sha == VP3_EXPECTED_SHA, f"VP3 contract drift: expected {VP3_EXPECTED_SHA}, got {actual_sha}"
    assert_current_vp3_contract(vp3_dir)

    capability = build_vp3_capability(vp3_dir)
    token = str(capability["token"])
    assert capability["valid"] is True
    assert capability["wrong_room"] is False
    assert capability["wrong_meeting"] is False
    assert capability["expired"] is False
    assert re.fullmatch(r"v1850\.\d{10}\.[a-f0-9]{64}", token)

    with tempfile.TemporaryDirectory(prefix="homeserver-meeting-v1870-") as data_dir:
        os.environ["HOMESERVER_DATA_DIR"] = data_dir
        from app.services import cloud_pairing, meeting_transcription

        original_pairing_endpoint = cloud_pairing.vp3_pairing_endpoint
        original_transcribe = meeting_transcription.local_voice.transcribe
        original_post_callback = meeting_transcription._post_callback
        try:
            cloud_pairing.vp3_pairing_endpoint = lambda: "https://vp3.me/api/homeserver-pair.php"
            expires = datetime.fromtimestamp(int(capability["expires"]), tz=timezone.utc)
            payload = {
                "contract": "vp3.meeting.transcription.v1",
                "idempotency_key": f"vp3-meeting-transcription:{PUBLIC_ID}",
                "meeting": {
                    "public_id": PUBLIC_ID,
                    "room_name": ROOM_NAME,
                    "title": "Phase 18.7 cross-repo validation",
                },
                "livekit": {
                    "url": "wss://example.livekit.cloud",
                    "participant_identity": "vp3-homeserver-" + ("b" * 24),
                    "participant_token": ("h" * 24) + "." + ("p" * 24) + "." + ("s" * 24),
                    "subscribe_audio_only": True,
                },
                "callback": {
                    "url": "https://vp3.me/api/video-meeting-worker.php",
                    "bearer_token": token,
                    "expires_at": expires.isoformat(),
                    "final_only": True,
                    "source": "homeserver",
                },
                "transcription": {"language": "en", "final_only": True},
            }
            clean = meeting_transcription._validate_payload(payload)
            assert clean["public_id"] == PUBLIC_ID
            assert clean["room_name"] == ROOM_NAME
            assert clean["callback_token"] == token

            rejected = json.loads(json.dumps(payload))
            rejected["callback"]["bearer_token"] = WORKER_SECRET
            try:
                meeting_transcription._validate_payload(rejected)
                raise AssertionError("HomeServer accepted the global VP3 worker secret")
            except meeting_transcription.MeetingTranscriptionError as exc:
                assert exc.status_code == 422

            captured: list[dict] = []
            meeting_transcription.local_voice.transcribe = lambda _: {
                "text": "Private local transcript from HomeServer",
                "provider": "whisper.cpp",
                "local": True,
            }
            meeting_transcription._post_callback = lambda job, body: captured.append(body)
            job = meeting_transcription._MeetingJob(
                idempotency_key=clean["idempotency_key"],
                public_id=clean["public_id"],
                room_name=clean["room_name"],
                title=clean["title"],
                app_key="vp3-phase-18-7",
                livekit_url=clean["livekit_url"],
                livekit_identity=clean["livekit_identity"],
                livekit_token=clean["livekit_token"],
                callback_url=clean["callback_url"],
                callback_token=clean["callback_token"],
                callback_expires_at=clean["callback_expires_at"],
                language=clean["language"],
            )
            pcm = b"\x01\x00" * 16000
            asyncio.run(
                meeting_transcription._transcribe_segment(
                    job,
                    pcm,
                    "user-alice",
                    "Alice",
                    "TR_audio_1",
                    1,
                    100,
                    1100,
                )
            )
            assert len(captured) == 1
            segment = captured[0]
            assert segment["meeting"] == PUBLIC_ID
            assert segment["room_name"] == ROOM_NAME
            assert segment["source"] == "homeserver"
            assert segment["is_final"] is True
            assert segment["text"] == "Private local transcript from HomeServer"
            assert token not in json.dumps(segment)
            assert clean["livekit_token"] not in json.dumps(segment)

            canonical_one = vp3_canonical_source_key(vp3_dir, segment)
            canonical_two = vp3_canonical_source_key(vp3_dir, dict(segment))
            assert re.fullmatch(r"[a-f0-9]{64}", canonical_one)
            assert canonical_two == canonical_one, "VP3 callback retry would not dedupe deterministically"

            changed = dict(segment)
            changed["text"] += " changed"
            canonical_changed = vp3_canonical_source_key(vp3_dir, changed)
            assert canonical_changed != canonical_one
        finally:
            cloud_pairing.vp3_pairing_endpoint = original_pairing_endpoint
            meeting_transcription.local_voice.transcribe = original_transcribe
            meeting_transcription._post_callback = original_post_callback

    print("Phase 18.7 VP3 <-> HomeServer meeting E2E contract passed.")


if __name__ == "__main__":
    main()
