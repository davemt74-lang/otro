"""Durable phone routes, inbound queue and single-use outbound approvals."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .security import phone_number


class PhoneState:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS phone_routes (
                    number TEXT PRIMARY KEY, platform TEXT NOT NULL, agent_id TEXT NOT NULL,
                    active INTEGER NOT NULL, allow_sms INTEGER NOT NULL, allow_voice INTEGER NOT NULL,
                    sip_uri TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS phone_events (
                    event_id TEXT PRIMARY KEY, channel TEXT NOT NULL,
                    number TEXT NOT NULL, sender TEXT NOT NULL, agent_id TEXT NOT NULL,
                    platform TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt INTEGER NOT NULL DEFAULT 0, provider_id TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS phone_events_queue ON phone_events(state,next_attempt);
                CREATE TABLE IF NOT EXISTS phone_approvals (
                    id TEXT PRIMARY KEY, operation TEXT NOT NULL,
                    from_number TEXT NOT NULL, to_number TEXT NOT NULL,
                    payload_hash TEXT NOT NULL, agent_id TEXT NOT NULL,
                    approved_by TEXT NOT NULL, expires_at INTEGER NOT NULL,
                    consumed_at INTEGER, provider_id TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS phone_conversations (
                    number TEXT NOT NULL, sender TEXT NOT NULL,
                    platform_conversation_id TEXT NOT NULL DEFAULT '',
                    history_json TEXT NOT NULL DEFAULT '[]', updated_at INTEGER NOT NULL,
                    PRIMARY KEY(number,sender)
                );
                CREATE TABLE IF NOT EXISTS phone_consents (
                    number TEXT NOT NULL, recipient TEXT NOT NULL, channel TEXT NOT NULL,
                    granted INTEGER NOT NULL, consent_source TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(number,recipient,channel)
                );
            """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        try:
            yield db
        finally:
            db.close()

    def put_route(self, number: str, platform: str, agent_id: str, *, active: bool,
                  sms: bool, voice: bool, sip_uri: str) -> None:
        number = phone_number(number)
        if platform not in {"vp3", "stonefellow", "gelato"} or not agent_id or len(agent_id) > 100:
            raise ValueError("Invalid platform or agent identity")
        if sip_uri and (not sip_uri.startswith("sip:") or not sip_uri.endswith(".sip.livekit.cloud")):
            raise ValueError("Only a configured LiveKit SIP destination is allowed")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT platform,agent_id FROM phone_routes WHERE number=?", (number,)).fetchone()
            if previous and (previous["platform"] != platform or previous["agent_id"] != agent_id):
                # A DID reassignment never inherits another Agent's history.
                db.execute("DELETE FROM phone_conversations WHERE number=?", (number,))
                db.execute("UPDATE phone_events SET state='needs_review' WHERE number=? AND state='queued'", (number,))
                if previous["platform"] != platform:
                    # Consent is to a business / use case, not just a number.
                    db.execute("DELETE FROM phone_consents WHERE number=?", (number,))
            db.execute("""INSERT INTO phone_routes VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(number) DO UPDATE SET platform=excluded.platform, agent_id=excluded.agent_id,
                active=excluded.active, allow_sms=excluded.allow_sms,
                allow_voice=excluded.allow_voice, sip_uri=excluded.sip_uri""",
                (number, platform, agent_id, int(active), int(sms), int(voice), sip_uri))
            db.execute("COMMIT")

    def route(self, number: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM phone_routes WHERE number=? AND active=1", (number,)).fetchone()
        return dict(row) if row else None

    def consent(self, number: str, recipient: str, channel: str, granted: bool,
                source: str) -> None:
        number, recipient = phone_number(number), phone_number(recipient)
        if channel not in {"sms", "call"} or not source.strip() or len(source) > 240:
            raise ValueError("Consent channel and evidence source are required")
        with self.connect() as db:
            db.execute("""INSERT INTO phone_consents VALUES (?,?,?,?,?,?)
                ON CONFLICT(number,recipient,channel) DO UPDATE SET granted=excluded.granted,
                consent_source=excluded.consent_source,updated_at=excluded.updated_at""",
                (number, recipient, channel, int(granted), source, int(time.time())))

    def resubscribe_sms_replies(self, number: str, recipient: str) -> None:
        """START removes STOP for solicited replies; it is NOT outbound consent."""
        number, recipient = phone_number(number), phone_number(recipient)
        with self.connect() as db:
            db.execute("""INSERT INTO phone_consents VALUES (?,?,?,?,?,?)
                ON CONFLICT(number,recipient,channel) DO UPDATE SET granted=excluded.granted,
                consent_source=excluded.consent_source,updated_at=excluded.updated_at""",
                (number, recipient, "sms", 2, "inbound START: session replies only", int(time.time())))

    def has_consent(self, number: str, recipient: str, channel: str) -> bool:
        with self.connect() as db:
            row = db.execute("SELECT granted FROM phone_consents WHERE number=? AND recipient=? AND channel=?",
                             (number, recipient, channel)).fetchone()
        return bool(row and row["granted"] == 1)

    def explicitly_opted_out(self, number: str, recipient: str) -> bool:
        with self.connect() as db:
            row = db.execute("SELECT granted FROM phone_consents WHERE number=? AND recipient=? AND channel='sms'",
                             (number, recipient)).fetchone()
        return bool(row and row["granted"] == 0)

    def inbound(self, event_id: str, channel: str, route: dict, sender: str, body: str = "") -> bool:
        if not event_id or len(event_id) > 100 or channel not in {"sms", "voice"}:
            raise ValueError("Invalid provider event")
        with self.connect() as db:
            # Per-sender cap keeps an unknown caller from triggering unlimited
            # model requests and paid SMS replies. Duplicate event IDs do not
            # consume another slot.
            if channel == "sms":
                recent = db.execute("""SELECT COUNT(*) FROM phone_events WHERE channel='sms'
                    AND number=? AND sender=? AND created_at>?""",
                    (route["number"], sender, int(time.time()) - 3600)).fetchone()[0]
                if recent >= 12:
                    return False
            cursor = db.execute("""INSERT OR IGNORE INTO phone_events
                (event_id,channel,number,sender,agent_id,platform,body,state,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (event_id, channel, route["number"], sender, route["agent_id"],
                 route["platform"], body, "queued" if channel == "sms" else "connected", int(time.time())))
            return cursor.rowcount == 1

    def take_sms(self) -> dict | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT * FROM phone_events WHERE channel='sms' AND state='queued'
                AND next_attempt<=? ORDER BY created_at,event_id LIMIT 1""", (int(time.time()),)).fetchone()
            if not row:
                db.execute("COMMIT")
                return None
            db.execute("UPDATE phone_events SET state='processing', attempts=attempts+1 WHERE event_id=?", (row["event_id"],))
            db.execute("COMMIT")
            return dict(row)

    def finish_sms(self, event_id: str, provider_id: str = "") -> None:
        with self.connect() as db:
            db.execute("UPDATE phone_events SET state='complete', provider_id=? WHERE event_id=? AND state='processing'", (provider_id, event_id))

    def retry_sms(self, event_id: str, attempts: int) -> None:
        # A failed send is never blindly replayed; only pre-send callback errors are retried.
        state = "failed" if attempts >= 5 else "queued"
        delay = min(300, 2 ** min(attempts, 8))
        with self.connect() as db:
            db.execute("UPDATE phone_events SET state=?, next_attempt=? WHERE event_id=? AND state='processing'",
                       (state, int(time.time()) + delay, event_id))

    def fail_sms(self, event_id: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE phone_events SET state='needs_review' WHERE event_id=? AND state='processing'", (event_id,))

    def conversation(self, number: str, sender: str) -> dict:
        import json
        with self.connect() as db:
            row = db.execute("SELECT platform_conversation_id,history_json FROM phone_conversations WHERE number=? AND sender=?",
                             (number, sender)).fetchone()
        return {"conversation_id": row["platform_conversation_id"], "history": json.loads(row["history_json"])} if row else {"conversation_id": "", "history": []}

    def save_turn(self, number: str, sender: str, platform_id: str, message: str, reply: str) -> None:
        import json
        current = self.conversation(number, sender)
        history = (current["history"] + [{"role": "user", "content": message[:1200]},
                                          {"role": "assistant", "content": reply[:1200]}])[-6:]
        with self.connect() as db:
            db.execute("""INSERT INTO phone_conversations VALUES (?,?,?,?,?)
                ON CONFLICT(number,sender) DO UPDATE SET platform_conversation_id=excluded.platform_conversation_id,
                history_json=excluded.history_json, updated_at=excluded.updated_at""",
                (number, sender, platform_id or current["conversation_id"], json.dumps(history), int(time.time())))

    @staticmethod
    def payload_hash(operation: str, from_number: str, to_number: str, payload: str) -> str:
        return hashlib.sha256("\0".join((operation, from_number, to_number, payload)).encode()).hexdigest()

    def approve(self, operation: str, from_number: str, to_number: str, payload: str,
                agent_id: str, approved_by: str, lifetime: int = 900) -> str:
        from_number, to_number = phone_number(from_number), phone_number(to_number)
        route = self.route(from_number)
        if operation not in {"sms", "call"} or not route or route["agent_id"] != agent_id:
            raise ValueError("Outbound route or agent is not authorized")
        if (operation == "sms" and not route["allow_sms"]) or (operation == "call" and not route["allow_voice"]):
            raise ValueError("Outbound channel is disabled")
        if operation == "call" and not route["sip_uri"]:
            raise ValueError("A LiveKit SIP destination is required before approving calls")
        if not self.has_consent(from_number, to_number, operation):
            raise ValueError("The recipient has no documented channel consent")
        if not approved_by.strip() or not 60 <= lifetime <= 3600:
            raise ValueError("Named approver and bounded expiry required")
        approval_id = "pa_" + secrets.token_hex(16)
        with self.connect() as db:
            db.execute("INSERT INTO phone_approvals VALUES (?,?,?,?,?,?,?,?,NULL,'')",
                       (approval_id, operation, from_number, to_number,
                        self.payload_hash(operation, from_number, to_number, payload),
                        agent_id, approved_by, int(time.time()) + lifetime))
        return approval_id

    def consume(self, approval_id: str, operation: str, from_number: str,
                to_number: str, payload: str, agent_id: str) -> bool:
        if not self.has_consent(from_number, to_number, operation):
            return False
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("""UPDATE phone_approvals SET consumed_at=?
                WHERE id=? AND operation=? AND from_number=? AND to_number=? AND agent_id=?
                AND payload_hash=? AND consumed_at IS NULL AND expires_at>?""",
                (int(time.time()), approval_id, operation, from_number, to_number, agent_id,
                 self.payload_hash(operation, from_number, to_number, payload), int(time.time())))
            db.execute("COMMIT")
            return cursor.rowcount == 1

    def mark_provider(self, approval_id: str, provider_id: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE phone_approvals SET provider_id=? WHERE id=? AND consumed_at IS NOT NULL",
                       (provider_id, approval_id))

    def prune(self, retention_days: int = 30) -> None:
        # Never prune pending work; stale 'processing' needs human review.
        limit = int(time.time()) - max(1, retention_days) * 86400
        with self.connect() as db:
            db.execute("DELETE FROM phone_events WHERE state IN ('complete','failed','needs_review') AND created_at<?", (limit,))
            db.execute("DELETE FROM phone_approvals WHERE expires_at<?", (limit,))
            db.execute("DELETE FROM phone_conversations WHERE updated_at<?", (limit,))
