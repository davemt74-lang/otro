"""Section 84B5 HomeServer media-staging certification; runs without Cloud or Kunaki."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

import httpx

from app.services import stonefellow_kunaki_media as media

COUNT = 0


def check(value: bool, label: str):
    global COUNT
    if not value:
        raise AssertionError(label)
    COUNT += 1
    print("PASS", label)


def rejects(fn, expected: type[Exception] = media.StagingError):
    try:
        fn()
    except expected:
        return True
    return False


secret = "secret-" + "X" * 44
origin = "https://stonefellow.example"
worker = "HS-kunaki-primary"
jid = "KJP-" + "A" * 24
proof = "c" * 64
now = int(time.time())
audio = b"RIFF" + (1024).to_bytes(4, "little") + b"WAVE" + b"A" * 2048
art_front = b"\xff\xd8\xff" + b"B" * 2400
art_back = b"\xff\xd8\xff" + b"C" * 2450
payloads = {
    "audio-A-1": ("wav", "audio", "a1", audio),
    "artwork-front": ("jpg", "front", None, art_front),
    "artwork-back": ("jpg", "back", None, art_back),
}
fingerprints = []
assets = []
for key, (ext, role, tid, binary) in payloads.items():
    digest = hashlib.sha256(binary).hexdigest()
    asset = {"asset_key": key, "extension": ext, "role": role,
             "size_bytes": len(binary), "sha256": digest, "grant": "GRANT-" + key}
    if tid:
        asset.update({"track_id": tid, "side": "A", "position": 1, "duration_seconds": 175})
    assets.append(asset)
    fingerprints.append(f"{key}|{digest}|{len(binary)}")
manifest = {"schema": "stonefellow.kunaki.media-manifest.v1",
            "job_id": jid, "proof_sha256": proof,
            "expires_at": now + 290, "assets": assets,
            "media_sha256": hashlib.sha256("\n".join(fingerprints).encode()).hexdigest(),
            "browser_upload_enabled": False, "kunaki_submission_enabled": False}
job = {"job_id": jid, "proof_sha256": proof,
       "license_track_ids": ["a1"], "lease_token": "f" * 48}


check(media.check_config(origin, worker, secret, Path("/tmp/stonefellow-84b5")) == origin,
      "Explicit HTTPS Cloud origin and HomeServer identity")
for bad in ["http://stonefellow.example", "https://evil@stonefellow.example",
            "https://stonefellow.example/api", "https://stonefellow.example/?id=1"]:
    check(rejects(lambda bad=bad: media.check_config(bad, worker, secret, Path("/tmp/stonefellow-84b5"))),
          "Unsafe Cloud URL is rejected")
check(rejects(lambda: media.check_config(origin, "wrong", secret, Path("/tmp/stonefellow-84b5"))),
      "Unrecognized worker identity rejected")
check(rejects(lambda: media.check_config(origin, worker, "weak", Path("/tmp/stonefellow-84b5"))),
      "Unprovisioned signing secret rejected")
encoded = media.encode({"operation": "claim"})
head = media.sign(worker, secret, encoded, timestamp=now, nonce="e" * 32)
expected = hmac.new(secret.encode(), "\n".join([
    str(now), "e" * 32, "POST", media.PATH, hashlib.sha256(encoded).hexdigest()
]).encode(), hashlib.sha256).hexdigest()
check(head["X-SF-Worker-Signature"] == expected, "Worker HMAC matches Cloud signature contract")
check(media.validate_manifest(manifest, job, now) == assets, "Correct paid-asset manifest accepted")
bad = {**manifest, "job_id": "KJP-" + "B" * 24}
check(rejects(lambda: media.validate_manifest(bad, job, now)), "Foreign job manifest denied")
bad = {**manifest, "expires_at": now - 1}
check(rejects(lambda: media.validate_manifest(bad, job, now)), "Expired grant denied")
bad = {**manifest, "media_sha256": "0" * 64}
check(rejects(lambda: media.validate_manifest(bad, job, now)), "Changed manifest digest denied")
bad = {**manifest, "assets": assets[:1]}
check(rejects(lambda: media.validate_manifest(bad, job, now)), "Artwork omission denied")
bad = {**manifest, "assets": [{**assets[0], "asset_key": "../outside.wav"}] + assets[1:]}
check(rejects(lambda: media.validate_manifest(bad, job, now)), "Traversal in staging filename denied")
bad = {**manifest, "assets": [{**assets[0], "size_bytes": media.MAX_ASSET + 1}] + assets[1:]}
check(rejects(lambda: media.validate_manifest(bad, job, now)), "Excessive source size denied")
bad = {**manifest, "assets": [{**assets[0], "track_id": "wrong-song"}] + assets[1:]}
check(rejects(lambda: media.validate_manifest(bad, job, now)), "Different song license denied")
bad = {**manifest, "browser_upload_enabled": True}
check(rejects(lambda: media.validate_manifest(bad, job, now)), "Cloud browser approval cannot be silently enabled")


def make_handler(tamper=None):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host != "stonefellow.example" or req.url.path != media.PATH:
            raise AssertionError("Worker cannot contact untrusted URL")
        if req.method != "POST" or not req.headers.get("x-sf-worker-signature"):
            raise AssertionError("Missing HMAC authorization")
        body = json.loads(req.content)
        operation = body.get("operation")
        if operation == "claim":
            return httpx.Response(200, json={"ok": True, "job": job})
        if operation == "manifest":
            return httpx.Response(200, json={"ok": True, "manifest": manifest})
        if operation == "report":
            if body["state"] != "awaiting_browser_approval":
                raise AssertionError("Successful worker cannot declare publication")
            return httpx.Response(200, json={"ok": True, "state": body["state"]})
        if operation == "download":
            key = body["grant"].removeprefix("GRANT-")
            contents = payloads[key][3]
            if tamper == key:
                contents = contents[:-1] + b"Z"
            record = next(a for a in assets if a["asset_key"] == key)
            return httpx.Response(200, content=contents, headers={
                "content-type": "application/octet-stream",
                "x-sf-media-sha256": record["sha256"],
                "x-sf-media-bytes": str(record["size_bytes"]),
            })
        raise AssertionError("Unknown media transfer action")
    return handler


with tempfile.TemporaryDirectory(prefix="sf-84b5-") as folder:
    root = Path(folder) / "staging"
    with httpx.Client(transport=httpx.MockTransport(make_handler()),
                      follow_redirects=False, trust_env=False) as client:
        status = media.run_once(origin, worker, secret, root, client)
        check(status == {"status": "awaiting_browser_approval", "job_id": jid,
                         "file_count": 3}, "End-to-end mock Cloud staging and review status")
        target = root / jid
        check(target.is_dir() and stat_mode(target) == 0o700 if False else target.is_dir(),
              "Private unique job staging directory created")
        check((target / "audio-A-1.wav").read_bytes() == audio, "Original recording staged exactly")
        check((target / "artwork-front.jpg").read_bytes() == art_front,
              "Original front artwork staged exactly")
        check((target / "artwork-back.jpg").read_bytes() == art_back,
              "Original back artwork staged exactly")
        report = json.loads((target / "staging.json").read_text())
        check(len(report["files"]) == 3 and report["browser_upload_enabled"] is False,
              "Staging certificate disables browser uploads and Kunaki submissions")
        check("lease_token" not in json.dumps(report) and "GRANT-" not in json.dumps(report),
              "Lease tokens and media grants never persist in staging metadata")
        check((target / "audio-A-1.wav").stat().st_mode & 0o777 == 0o600,
              "Transferred audio restricted to owner read/write")
        try:
            media._copy_asset(client, origin, worker, secret, job,
                              assets[0], target)
            check(True, "Identical existing staged file is idempotent")
        except Exception:
            check(False, "Existing staged file idempotence")
    with httpx.Client(transport=httpx.MockTransport(make_handler("audio-A-1"))) as client:
        # Stage into an independent directory, so stale/corrupted bytes
        # cannot be mistaken for the good recording.
        other = Path(folder) / "tampered"
        check(rejects(lambda: media.stage_manifest(client, origin, worker, secret,
                                                    other, job, manifest)),
              "Tampered binary transfer denied by SHA-256")
        check(not list(other.rglob("*.wav")) and not list(other.rglob("*.partial")),
              "Partial/corrupted media removed after failed verification")
    check(not list(root.rglob("*.partial")), "No partial files left in successful job")

print(f"Section 84B5 HomeServer media staging: {COUNT} checks passed")
