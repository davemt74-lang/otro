"""Section 84B5 HomeServer media-transfer worker for Stonefellow vinyl jobs.

Cloud owns purchases, track licensing, jobs and signed transfer grants.
HomeServer only stages independently verified originals for human review.
This module has NO Kunaki login, Chromium controls or vendor submission.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
from urllib.parse import urlsplit

import httpx

PATH = "/api/kunaki-worker.php"
MAX_ASSET = 268435456
MAX_TOTAL = 1073741824
CHUNK = 65536
KEY = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
WORKER = re.compile(r"^HS-[a-zA-Z0-9_-]{5,64}$")
JOB = re.compile(r"^KJP-[A-F0-9]{24}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")
EXT = {"wav", "mp3", "jpg", "jpeg", "png"}


class StagingError(RuntimeError):
    """Operator-reviewable failure without secret content in the message."""


def check_config(url: str, worker_id: str, secret: str, root: Path) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise StagingError("Stonefellow must be a trusted HTTPS origin.")
    if parts.path not in ("", "/"):
        raise StagingError("Configure only the Stonefellow HTTPS origin.")
    if not WORKER.fullmatch(worker_id) or len(secret) < 32:
        raise StagingError("HomeServer media worker ID or signing key is not configured.")
    if root.is_symlink():
        raise StagingError("Staging root cannot be a symlink.")
    return "https://" + parts.netloc


def encode(body: dict) -> bytes:
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign(worker_id: str, secret: str, body: bytes, timestamp: int | None = None, nonce: str | None = None) -> dict[str, str]:
    ts = str(int(time.time() if timestamp is None else timestamp))
    n = nonce or secrets.token_hex(16)
    if len(ts) != 10 or not re.fullmatch(r"[a-f0-9]{32}", n):
        raise StagingError("Invalid signed worker request metadata.")
    payload = "\n".join((ts, n, "POST", PATH, hashlib.sha256(body).hexdigest())).encode()
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return {
        "X-SF-Worker-Id": worker_id,
        "X-SF-Worker-Timestamp": ts,
        "X-SF-Worker-Nonce": n,
        "X-SF-Worker-Signature": signature,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def request_json(client: httpx.Client, origin: str, worker_id: str, secret: str, body: dict) -> dict:
    raw = encode(body)
    response = client.post(origin + PATH, content=raw, headers=sign(worker_id, secret, raw))
    if response.status_code != 200:
        raise StagingError(f"Stonefellow worker request rejected (HTTP {response.status_code}).")
    try:
        data = response.json()
    except ValueError as ex:
        raise StagingError("Stonefellow worker returned an invalid response.") from ex
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise StagingError("Stonefellow worker did not confirm this operation.")
    return data


def _stage_dir(root: Path, job_id: str) -> Path:
    if not JOB.fullmatch(job_id):
        raise StagingError("Invalid vinyl publishing job identity.")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise StagingError("Staging root is not a protected directory.")
    os.chmod(root, 0o700)
    dst = root / job_id
    if dst.is_symlink():
        raise StagingError("Staging job directory cannot be a symlink.")
    dst.mkdir(mode=0o700, exist_ok=True)
    os.chmod(dst, 0o700)
    return dst


def validate_manifest(manifest: dict, job: dict, now: int | None = None) -> list[dict]:
    clock = int(time.time() if now is None else now)
    if (manifest.get("schema") != "stonefellow.kunaki.media-manifest.v1"
            or manifest.get("job_id") != job.get("job_id")
            or manifest.get("proof_sha256") != job.get("proof_sha256")
            or manifest.get("kunaki_submission_enabled") is not False
            or manifest.get("browser_upload_enabled") is not False):
        raise StagingError("Media manifest does not match the approved vinyl job.")
    until = manifest.get("expires_at")
    if not isinstance(until, int) or until <= clock or until > clock + 600:
        raise StagingError("Media grants are expired or outside their permitted window.")
    assets = manifest.get("assets")
    if not isinstance(assets, list) or not 3 <= len(assets) <= 42:
        raise StagingError("Vinyl manifest has an invalid number of media assets.")
    expected_ids = job.get("license_track_ids")
    ids = [a.get("track_id") for a in assets if isinstance(a, dict) and a.get("role") == "audio"]
    if not isinstance(expected_ids, list) or ids != expected_ids:
        raise StagingError("Audio sequence does not match the paid song-license reservation.")
    keys: set[str] = set()
    fingerprints: list[str] = []
    total = 0
    artwork: set[str] = set()
    for asset in assets:
        if not isinstance(asset, dict):
            raise StagingError("Malformed asset metadata.")
        key, ext, role = asset.get("asset_key"), asset.get("extension"), asset.get("role")
        length, sha, grant = asset.get("size_bytes"), asset.get("sha256"), asset.get("grant")
        if not isinstance(key, str) or not KEY.fullmatch(key) or key in keys or ext not in EXT:
            raise StagingError("Duplicate or unsupported media asset.")
        if role not in ("audio", "front", "back"):
            raise StagingError("Unsupported media asset role.")
        if role == "audio" and ext not in ("mp3", "wav"):
            raise StagingError("Unsupported audio original.")
        if role != "audio":
            if ext not in ("jpg", "jpeg", "png") or role in artwork:
                raise StagingError("Artwork is missing or duplicated.")
            artwork.add(role)
        if not isinstance(length, int) or not 1 <= length <= MAX_ASSET:
            raise StagingError("Unsafe size for source media.")
        if not isinstance(sha, str) or not DIGEST.fullmatch(sha) or not isinstance(grant, str) or len(grant) > 1800:
            raise StagingError("Invalid source digest or grant.")
        keys.add(key)
        total += length
        fingerprints.append(f"{key}|{sha}|{length}")
    if artwork != {"front", "back"} or total > MAX_TOTAL:
        raise StagingError("Both original album artworks and safe total size are required.")
    fingerprint = hashlib.sha256("\n".join(fingerprints).encode()).hexdigest()
    if not hmac.compare_digest(fingerprint, str(manifest.get("media_sha256", ""))):
        raise StagingError("Media manifest does not match its asset fingerprint.")
    return assets


def _copy_asset(
    client: httpx.Client, origin: str, worker_id: str, secret: str,
    job: dict, asset: dict, output_dir: Path,
) -> dict:
    key = asset["asset_key"]
    extension = asset["extension"]
    expected_length = asset["size_bytes"]
    expected_hash = asset["sha256"]
    target = output_dir / f"{key}.{extension}"
    if target.is_symlink():
        raise StagingError("Staging file cannot be a symlink.")
    if target.is_file():
        if target.stat().st_size == expected_length and hashlib.sha256(target.read_bytes()).hexdigest() == expected_hash:
            return {"asset_key": key, "file_name": target.name, "size_bytes": expected_length, "sha256": expected_hash}
        raise StagingError("Conflicting staged asset requires manual inspection.")
    body = {"operation": "download", "job_id": job["job_id"],
            "lease_token": job["lease_token"], "grant": asset["grant"]}
    raw = encode(body)
    pending = output_dir / (f".{key}.{secrets.token_hex(8)}.partial")
    copied = 0
    digest = hashlib.sha256()
    try:
        with client.stream("POST", origin + PATH, content=raw,
                           headers=sign(worker_id, secret, raw)) as response:
            if response.status_code != 200:
                raise StagingError(f"Media transfer denied (HTTP {response.status_code}).")
            if response.headers.get("content-type", "").split(";")[0].strip() != "application/octet-stream":
                raise StagingError("Expected binary media, received a different response.")
            if response.headers.get("x-sf-media-sha256") != expected_hash:
                raise StagingError("Cloud media digest header mismatch.")
            if response.headers.get("x-sf-media-bytes") != str(expected_length):
                raise StagingError("Cloud media size header mismatch.")
            fd = os.open(pending, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as out:
                for chunk in response.iter_bytes(CHUNK):
                    copied += len(chunk)
                    if copied > expected_length or copied > MAX_ASSET:
                        raise StagingError("Cloud media exceeded its authorized size.")
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
        if copied != expected_length or not hmac.compare_digest(digest.hexdigest(), expected_hash):
            raise StagingError("Downloaded media failed exact length or SHA-256 verification.")
        if target.exists() or target.is_symlink():
            raise StagingError("Target file changed during transfer.")
        os.replace(pending, target)
        os.chmod(target, 0o600)
        return {"asset_key": key, "file_name": target.name, "size_bytes": copied, "sha256": expected_hash}
    finally:
        pending.unlink(missing_ok=True)


def stage_manifest(client: httpx.Client, origin: str, worker_id: str, secret: str,
                   root: Path, job: dict, manifest: dict) -> dict:
    assets = validate_manifest(manifest, job)
    folder = _stage_dir(root, job["job_id"])
    files = [_copy_asset(client, origin, worker_id, secret, job, asset, folder) for asset in assets]
    report = {"schema": "stonefellow.kunaki.staged.v1",
              "job_id": job["job_id"], "proof_sha256": job["proof_sha256"],
              "media_sha256": manifest["media_sha256"], "files": files,
              "browser_upload_enabled": False, "kunaki_submission_enabled": False}
    marker = folder / "staging.json"
    if marker.is_symlink():
        raise StagingError("Staging evidence must be a private regular file.")
    temporary = folder / (".staging-" + secrets.token_hex(8) + ".tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(report, out, separators=(",", ":"), sort_keys=True)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, marker)
    os.chmod(marker, 0o600)
    return report


def run_once(url: str, worker_id: str, secret: str, root: Path, client: httpx.Client) -> dict:
    origin = check_config(url, worker_id, secret, root)
    result = request_json(client, origin, worker_id, secret, {"operation": "claim"})
    job = result.get("job")
    if job is None:
        return {"status": "idle"}
    if not isinstance(job, dict) or not JOB.fullmatch(str(job.get("job_id", ""))) or not job.get("lease_token"):
        raise StagingError("Claimed job is missing lease credentials.")
    try:
        answer = request_json(client, origin, worker_id, secret, {
            "operation": "manifest", "job_id": job["job_id"], "lease_token": job["lease_token"]})
        report = stage_manifest(client, origin, worker_id, secret, root, job, answer.get("manifest", {}))
        request_json(client, origin, worker_id, secret, {
            "operation": "report", "job_id": job["job_id"],
            "lease_token": job["lease_token"], "state": "awaiting_browser_approval"})
        return {"status": "awaiting_browser_approval", "job_id": job["job_id"],
                "file_count": len(report["files"])}
    except Exception:
        # Never auto-retry an ambiguous or incomplete transfer. An operator
        # decides whether the original design is still correct.
        try:
            request_json(client, origin, worker_id, secret, {
                "operation": "report", "job_id": job["job_id"],
                "lease_token": job["lease_token"], "state": "needs_review"})
        except Exception:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage one approved Stonefellow vinyl job; never publish.")
    parser.add_argument("--once", action="store_true", required=True)
    args = parser.parse_args()
    del args
    try:
        url = os.environ.get("STONEFELLOW_KUNAKI_CLOUD_URL", "")
        worker_id = os.environ.get("STONEFELLOW_KUNAKI_WORKER_ID", "")
        secret = os.environ.get("STONEFELLOW_KUNAKI_WORKER_SECRET", "")
        destination = os.environ.get("STONEFELLOW_KUNAKI_STAGING_DIR", "")
        if not destination:
            raise StagingError("Private media staging directory must be configured.")
        with httpx.Client(timeout=httpx.Timeout(90.0, connect=10.0),
                          follow_redirects=False, trust_env=False, verify=True) as client:
            result = run_once(url, worker_id, secret, Path(destination), client)
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except (StagingError, httpx.HTTPError, OSError, ValueError) as ex:
        # Never print the cloud API key, grant, filename, or customer data.
        print(f"Kunaki media staging requires review ({type(ex).__name__}).", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
