"""Local reference authority for the Phase 8A.2 worker protocol.

This is not a Cloud service, provisioning endpoint or production lease store.
The reference authority uses provisioned synthetic credentials supplied by
its caller. SQLite persists metadata and the exact last receipt, never keys.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .operator_worker_protocol import (
    LEASE_SECONDS, MAX_INTEGER, PROTOCOL, SignedFrame, WorkerCredential,
    WorkerRegistrationError, capability_list, check_clock, opaque, integer,
    parse_body, request_digest, sign_frame, validate_request, verify_frame,
)

_SCHEMA = "vp3_operator_worker_reference_v1"
_TABLES = {"operator_worker_schema", "operator_worker_registry", "operator_worker_boots"}
_MAX_BOOTS = 4096


class ReferenceWorkerRegistry:
    def __init__(self, path: str | Path, credentials: tuple[WorkerCredential, ...]):
        self.path = Path(path).expanduser().absolute()
        if (not isinstance(credentials, tuple) or len(credentials) > 128
                or any(not isinstance(item, WorkerCredential) for item in credentials)):
            raise WorkerRegistrationError("Invalid reference credential inventory")
        self._credentials = {item.credential_id: item for item in credentials}
        if (len(self._credentials) != len(credentials)
                or len({item.secret for item in credentials}) != len(credentials)
                or len({(item.owner_id, item.device_id, item.worker_id)
                        for item in credentials}) != len(credentials)):
            raise WorkerRegistrationError("Reference credentials must be independently bound")
        if self.path.is_symlink() or not self.path.parent.is_dir():
            raise WorkerRegistrationError("Use a dedicated private scratch directory")
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(self.path, flags, 0o600)
        except FileExistsError:
            if self.path.is_symlink() or not self.path.is_file():
                raise WorkerRegistrationError("Invalid reference database")
        else:
            os.close(fd)
        if os.name == "posix" and self.path.stat().st_mode & 0o077:
            raise WorkerRegistrationError("Reference database must be private (0600)")
        with self._transaction() as db:
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if tables and tables != _TABLES:
                raise WorkerRegistrationError("Refusing to modify an unrelated database")
            if tables:
                markers = db.execute("SELECT version FROM operator_worker_schema").fetchall()
                if len(markers) != 1 or markers[0][0] != _SCHEMA:
                    raise WorkerRegistrationError("Unknown reference registry schema")
            else:
                db.execute("CREATE TABLE operator_worker_schema(version TEXT NOT NULL)")
                db.execute("INSERT INTO operator_worker_schema VALUES(?)", (_SCHEMA,))
                db.execute("""CREATE TABLE operator_worker_registry(
                    credential_id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL,
                    device_id TEXT NOT NULL, worker_id TEXT NOT NULL, boot_id TEXT NOT NULL,
                    generation INTEGER NOT NULL, sequence INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL, lease_until INTEGER NOT NULL,
                    state TEXT NOT NULL, request_hash TEXT NOT NULL, receipt BLOB NOT NULL
                )""")
                db.execute("CREATE INDEX operator_worker_owner ON operator_worker_registry(owner_id)")
                db.execute("""CREATE TABLE operator_worker_boots(
                    credential_id TEXT NOT NULL, boot_id TEXT NOT NULL,
                    PRIMARY KEY(credential_id,boot_id)
                )""")

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    @staticmethod
    def _binding_matches(row: sqlite3.Row, credential: WorkerCredential) -> bool:
        return all(row[key] == value for key, value in credential.binding().items())

    def process(self, frame: SignedFrame, now: int) -> SignedFrame:
        """Authenticate exact bytes, provisioned identity and ordered fresh request."""
        check_clock(now)
        if not isinstance(frame, SignedFrame):
            raise WorkerRegistrationError("Invalid worker frame")
        untrusted = parse_body(frame.body)
        key = untrusted.get("credential_id")
        if not opaque(key) or key not in self._credentials:
            raise WorkerRegistrationError("Worker credential is not provisioned")
        credential = self._credentials[key]
        request = verify_frame(frame, credential, "request")
        validate_request(request, credential, now)
        digest = request_digest(frame)
        boot = request["boot_id"]
        action = request["action"]
        seq = request["sequence"]
        caps = capability_list(request["capabilities"])
        with self._transaction() as db:
            old = db.execute("SELECT * FROM operator_worker_registry WHERE credential_id=?",
                             (key,)).fetchone()
            if old is not None:
                if not self._binding_matches(old, credential):
                    raise WorkerRegistrationError("Credential binding changed; provision a new identity")
                if now < old["last_seen"]:
                    raise WorkerRegistrationError("Authority clock moved backwards")
                if old["boot_id"] == boot and old["sequence"] == seq:
                    if old["request_hash"] != digest:
                        raise WorkerRegistrationError("Conflicting worker sequence")
                    # Retransmitting a lost receipt cannot renew a lease.
                    if old["state"] != "retired" and old["lease_until"] <= now:
                        raise WorkerRegistrationError("Worker lease expired")
                    # Cached signed payload is reconstructed only after checking
                    # the exact authenticated request hash and current scope.
                    payload = parse_body(old["receipt"])
                    return sign_frame(payload, credential, "response")
            if action == "register":
                if old is not None and old["state"] == "reporting" and old["lease_until"] > now:
                    raise WorkerRegistrationError("Another worker boot holds the registration")
                if db.execute("SELECT 1 FROM operator_worker_boots WHERE credential_id=? AND boot_id=?",
                              (key, boot)).fetchone():
                    raise WorkerRegistrationError("A previous worker boot cannot return")
                count = db.execute("SELECT COUNT(*) FROM operator_worker_boots WHERE credential_id=?",
                                   (key,)).fetchone()[0]
                if count >= _MAX_BOOTS:
                    raise WorkerRegistrationError("Reference boot history requires new credential")
                generation = 1 if old is None else old["generation"] + 1
                if generation > MAX_INTEGER:
                    raise WorkerRegistrationError("Worker generation limit reached")
                db.execute("INSERT INTO operator_worker_boots VALUES(?,?)", (key, boot))
            else:
                if (old is None or old["state"] != "reporting" or old["lease_until"] <= now
                        or old["boot_id"] != boot or old["generation"] != request["generation"]
                        or seq != old["sequence"] + 1):
                    raise WorkerRegistrationError("Worker boot, lease or sequence is stale")
                generation = old["generation"]
            retired = action == "retire"
            lease_until = 0 if retired else now + LEASE_SECONDS
            if lease_until > 4102444800:
                raise WorkerRegistrationError("Worker lease exceeds clock bounds")
            payload = {name: value for name, value in request.items()
                       if name not in {"sent_at", "generation"}}
            payload.update(generation=generation, request_sha256=digest, received_at=now,
                           lease_until=lease_until, status={"register": "registered",
                           "heartbeat": "reporting", "retire": "retired"}[action],
                           execution_enabled=False, live_certified=False)
            receipt = sign_frame(payload, credential, "response")
            db.execute("""INSERT INTO operator_worker_registry(
                    credential_id,owner_id,device_id,worker_id,boot_id,generation,sequence,
                    last_seen,lease_until,state,request_hash,receipt)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(credential_id) DO UPDATE SET
                    boot_id=excluded.boot_id,generation=excluded.generation,sequence=excluded.sequence,
                    last_seen=excluded.last_seen,lease_until=excluded.lease_until,
                    state=excluded.state,request_hash=excluded.request_hash,receipt=excluded.receipt""",
                       (key, credential.owner_id, credential.device_id, credential.worker_id, boot,
                        generation, seq, now, lease_until,
                        "retired" if retired else "reporting", digest, receipt.body))
            return receipt

    def list_workers(self, owner_id: int, now: int) -> list[dict]:
        """Internal fixture projection. A future Cloud route must authenticate owner."""
        check_clock(now)
        if not integer(owner_id):
            raise WorkerRegistrationError("Invalid reference owner")
        with self._transaction() as db:
            rows = db.execute("""SELECT * FROM operator_worker_registry WHERE owner_id=?
                ORDER BY worker_id,credential_id""", (owner_id,)).fetchall()
            result = []
            for row in rows:
                credential = self._credentials.get(row["credential_id"])
                if credential is None or not self._binding_matches(row, credential):
                    continue  # Revoked or changed credentials cannot report availability.
                receipt = parse_body(row["receipt"])
                caps = capability_list(receipt["capabilities"])
                current = (row["state"] == "reporting"
                           and row["last_seen"] <= now < row["lease_until"]
                           and set(caps).issubset(credential.allowed_capabilities))
                state = "retired" if row["state"] == "retired" else "reporting" if current else "stale"
                result.append({"worker_id": row["worker_id"], "device_id": row["device_id"],
                               "generation": row["generation"], "state": state,
                               "last_seen": row["last_seen"], "lease_until": row["lease_until"],
                               "capabilities": list(caps) if current else [],
                               "current": current, "execution_enabled": False,
                               "live_certified": False, "reference_only": True})
            return result
