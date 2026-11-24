"""Phase 8A.1: durable, local-only Operator session lifecycle simulation.

This is NOT a Cloud authority source or a live dialer. The session database
contains opaque fixture references and synthetic transport receipts only.
No production routes, phone numbers, credentials, audio, Chat, or network
adapters are accepted. An eventual Cloud-signed executor must be separate
from this deliberately simulation-only entry point.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

_ID = re.compile(r"[0-9a-f]{32}")
_WORKER = re.compile(r"[A-Za-z0-9_-]{3,64}")
_SCHEMA = "vp3_operator_simulation_v1"
_TABLES = {"operator_sim_schema", "operator_sim_sessions",
           "operator_sim_events", "operator_sim_legs"}
_TERMINAL = frozenset({"completed", "canceled", "expired", "failed"})
_HELD = ("leased", "dispatch_reserved", "ringing", "active", "needs_review")
_LEASE_SECONDS = 30
_MAX_DURATION_SECONDS = 900
_MAX_PENDING_PER_OWNER = 20


class OperatorSessionError(RuntimeError):
    """A lifecycle action could not be verified."""


class SimulatedUncertainDispatch(OperatorSessionError):
    """The fake transport may have started; never repeat the dispatch."""


def _opaque(value: str) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _positive(value: int) -> bool:
    return type(value) is int and 0 < value <= 9007199254740991


def _time(value: int) -> int:
    if type(value) is not int or not 0 < value <= 4102444800:
        raise OperatorSessionError("Invalid simulation clock")
    return value


@dataclass(frozen=True)
class SessionBinding:
    owner_id: int
    agent_id: int
    conversation_id: int
    contact_id: int
    workspace_id: str
    source: str
    occurrence_id: str

    def __post_init__(self) -> None:
        if not all(_positive(value) for value in
                   (self.owner_id, self.agent_id, self.conversation_id, self.contact_id)):
            raise OperatorSessionError("Invalid simulated session principal")
        if not _opaque(self.workspace_id) or not _opaque(self.occurrence_id):
            raise OperatorSessionError("Invalid simulated workspace or occurrence")
        if not isinstance(self.source, str) or self.source not in {"manual", "scheduled", "proactive"}:
            raise OperatorSessionError("Unsupported simulated call source")

    def values(self) -> tuple:
        return (self.owner_id, self.agent_id, self.conversation_id,
                self.contact_id, self.workspace_id, self.source, self.occurrence_id)


@dataclass(frozen=True)
class WorkerLease:
    session_id: str
    binding: SessionBinding
    worker_id: str
    boot_id: str
    generation: int
    token: str = field(repr=False)


@dataclass(frozen=True)
class SessionSnapshot:
    session_id: str
    binding: SessionBinding
    state: str
    created_at: int
    deadline_at: int
    updated_at: int
    lease_until: int
    generation: int
    dispatch_count: int
    reason: str
    execution_enabled: bool = field(default=False, init=False)
    transport: str = field(default="simulation", init=False)


class OperatorSimulationStore:
    """Private, explicitly created scratch database; never open a production DB."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().absolute()
        if self.path.is_symlink() or not self.path.parent.is_dir():
            raise OperatorSessionError("Use a dedicated existing private scratch directory")
        # Create with restrictive permissions on POSIX. Windows users must
        # place the file under their account's own restricted directory.
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(self.path, flags, 0o600)
        except FileExistsError:
            if self.path.is_symlink() or not self.path.is_file():
                raise OperatorSessionError("Invalid simulation database")
        else:
            os.close(fd)
        if os.name == "posix" and self.path.stat().st_mode & 0o077:
            raise OperatorSessionError("Simulation database must be private (0600)")
        with self._transaction() as db:
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if tables and tables != _TABLES:
                raise OperatorSessionError("Refusing to modify a non-Operator scratch database")
            if tables:
                markers = db.execute("SELECT version FROM operator_sim_schema").fetchall()
                if len(markers) != 1 or markers[0][0] != _SCHEMA:
                    raise OperatorSessionError("Unknown simulation schema")
            else:
                self._install(db)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("PRAGMA foreign_keys=ON")
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
    def _install(db: sqlite3.Connection) -> None:
        # Do not use executescript: it commits an open SQLite transaction.
        db.execute("CREATE TABLE operator_sim_schema(version TEXT NOT NULL)")
        db.execute("INSERT INTO operator_sim_schema VALUES (?)", (_SCHEMA,))
        db.execute("""CREATE TABLE operator_sim_sessions(
            id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, agent_id INTEGER NOT NULL,
            conversation_id INTEGER NOT NULL, contact_id INTEGER NOT NULL,
            workspace_id TEXT NOT NULL, source TEXT NOT NULL, occurrence_id TEXT NOT NULL,
            state TEXT NOT NULL, created_at INTEGER NOT NULL, deadline_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL, lease_until INTEGER NOT NULL DEFAULT 0,
            generation INTEGER NOT NULL DEFAULT 0, worker_id TEXT NOT NULL DEFAULT '',
            boot_id TEXT NOT NULL DEFAULT '', lease_hash TEXT NOT NULL DEFAULT '',
            dispatch_count INTEGER NOT NULL DEFAULT 0 CHECK(dispatch_count IN (0,1)),
            reason TEXT NOT NULL DEFAULT '', UNIQUE(owner_id, occurrence_id)
        )""")
        db.execute("CREATE INDEX operator_sim_owner_state ON operator_sim_sessions(owner_id,state)")
        db.execute("""CREATE UNIQUE INDEX operator_sim_exclusive_owner
            ON operator_sim_sessions(owner_id)
            WHERE state IN ('leased','dispatch_reserved','ringing','active','needs_review')""")
        db.execute("""CREATE TABLE operator_sim_events(
            session_id TEXT NOT NULL REFERENCES operator_sim_sessions(id),
            sequence INTEGER NOT NULL, state TEXT NOT NULL, at INTEGER NOT NULL,
            reason TEXT NOT NULL, PRIMARY KEY(session_id,sequence)
        )""")
        db.execute("""CREATE TABLE operator_sim_legs(
            session_id TEXT PRIMARY KEY REFERENCES operator_sim_sessions(id),
            receipt TEXT UNIQUE NOT NULL, state TEXT NOT NULL,
            started_at INTEGER NOT NULL, ended_at INTEGER
        )""")

    @staticmethod
    def _binding(row: sqlite3.Row) -> SessionBinding:
        return SessionBinding(*(row[key] for key in
            ("owner_id", "agent_id", "conversation_id", "contact_id",
             "workspace_id", "source", "occurrence_id")))

    @classmethod
    def _snapshot(cls, row: sqlite3.Row) -> SessionSnapshot:
        return SessionSnapshot(row["id"], cls._binding(row), row["state"], row["created_at"],
                               row["deadline_at"], row["updated_at"], row["lease_until"],
                               row["generation"], row["dispatch_count"], row["reason"])

    @classmethod
    def _row(cls, db: sqlite3.Connection, session_id: str, binding: SessionBinding) -> sqlite3.Row:
        if not _opaque(session_id) or not isinstance(binding, SessionBinding):
            raise OperatorSessionError("Session unavailable")
        row = db.execute("SELECT * FROM operator_sim_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None or cls._binding(row) != binding:
            raise OperatorSessionError("Session unavailable")
        return row

    @staticmethod
    def _event(db: sqlite3.Connection, session_id: str, state: str, now: int, reason: str) -> None:
        sequence = db.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 FROM operator_sim_events WHERE session_id=?",
            (session_id,)).fetchone()[0]
        db.execute("INSERT INTO operator_sim_events VALUES(?,?,?,?,?)",
                   (session_id, sequence, state, now, reason))

    @staticmethod
    def _clock(row: sqlite3.Row, now: int) -> None:
        _time(now)
        if now < row["updated_at"]:
            raise OperatorSessionError("Simulation clock moved backwards")

    @classmethod
    def _transition(cls, db: sqlite3.Connection, row: sqlite3.Row, state: str,
                    now: int, reason: str, *, fence: bool = False) -> sqlite3.Row:
        cls._clock(row, now)
        if fence:
            db.execute("""UPDATE operator_sim_sessions SET state=?,updated_at=?,reason=?,
                lease_until=0,lease_hash='',worker_id='',boot_id='',generation=generation+1 WHERE id=?""",
                       (state, now, reason, row["id"]))
        else:
            db.execute("UPDATE operator_sim_sessions SET state=?,updated_at=?,reason=? WHERE id=?",
                       (state, now, reason, row["id"]))
        cls._event(db, row["id"], state, now, reason)
        return db.execute("SELECT * FROM operator_sim_sessions WHERE id=?", (row["id"],)).fetchone()

    @classmethod
    def _lease_row(cls, db: sqlite3.Connection, lease: WorkerLease, now: int,
                   states: tuple[str, ...]) -> sqlite3.Row:
        if not isinstance(lease, WorkerLease):
            raise OperatorSessionError("Invalid worker lease")
        row = cls._row(db, lease.session_id, lease.binding)
        cls._clock(row, now)
        if (row["state"] not in states or row["generation"] != lease.generation
                or row["worker_id"] != lease.worker_id or row["boot_id"] != lease.boot_id
                or row["lease_until"] <= now or row["deadline_at"] <= now
                or not isinstance(lease.token, str) or not re.fullmatch(r"[0-9a-f]{64}", lease.token)
                or not hmac.compare_digest(row["lease_hash"], hashlib.sha256(lease.token.encode()).hexdigest())):
            raise OperatorSessionError("Worker lease is stale, invalid, or revoked")
        return row

    def prepare(self, binding: SessionBinding, now: int, *, lifetime_seconds: int = 300) -> SessionSnapshot:
        """One immutable session per owner/occurrence; replay does not create a new one."""
        _time(now)
        if not isinstance(binding, SessionBinding) or type(lifetime_seconds) is not int or not 60 <= lifetime_seconds <= _MAX_DURATION_SECONDS:
            raise OperatorSessionError("Invalid simulated session lifetime or binding")
        with self._transaction() as db:
            old = db.execute("SELECT * FROM operator_sim_sessions WHERE owner_id=? AND occurrence_id=?",
                             (binding.owner_id, binding.occurrence_id)).fetchone()
            if old is not None:
                if self._binding(old) != binding or old["deadline_at"] - old["created_at"] != lifetime_seconds:
                    raise OperatorSessionError("Occurrence already has a different immutable binding")
                return self._snapshot(old)
            count = db.execute("SELECT COUNT(*) FROM operator_sim_sessions WHERE owner_id=? AND state NOT IN ('completed','canceled','expired','failed')",
                               (binding.owner_id,)).fetchone()[0]
            if count >= _MAX_PENDING_PER_OWNER:
                raise OperatorSessionError("Simulation pending-session limit reached")
            session_id = secrets.token_hex(16)
            db.execute("""INSERT INTO operator_sim_sessions(
                id,owner_id,agent_id,conversation_id,contact_id,workspace_id,source,occurrence_id,
                state,created_at,deadline_at,updated_at,reason)
                VALUES(?,?,?,?,?,?,?,?,'prepared',?,?,?,'prepared')""",
                       (session_id, *binding.values(), now, now + lifetime_seconds, now))
            self._event(db, session_id, "prepared", now, "prepared")
            return self._snapshot(self._row(db, session_id, binding))

    def snapshot(self, session_id: str, binding: SessionBinding) -> SessionSnapshot:
        with self._transaction() as db:
            return self._snapshot(self._row(db, session_id, binding))

    def events(self, session_id: str, binding: SessionBinding) -> list[dict]:
        with self._transaction() as db:
            self._row(db, session_id, binding)
            return [dict(row) for row in db.execute("""SELECT sequence,state,at,reason
                FROM operator_sim_events WHERE session_id=? ORDER BY sequence""", (session_id,))]

    def claim(self, session_id: str, binding: SessionBinding, worker_id: str,
              boot_id: str, now: int) -> WorkerLease:
        """An exclusive bounded lease, never a call authorization."""
        if not isinstance(worker_id, str) or not _WORKER.fullmatch(worker_id) or not _opaque(boot_id):
            raise OperatorSessionError("Invalid simulated worker identity")
        with self._transaction() as db:
            row = self._row(db, session_id, binding)
            self._clock(row, now)
            if row["state"] != "prepared" or row["deadline_at"] <= now or row["dispatch_count"]:
                raise OperatorSessionError("Session cannot be leased")
            marks = ",".join("?" for _ in _HELD)
            if db.execute(f"SELECT 1 FROM operator_sim_sessions WHERE owner_id=? AND state IN ({marks}) LIMIT 1",
                          (binding.owner_id, *_HELD)).fetchone():
                raise OperatorSessionError("Owner already has an unresolved simulated session")
            token = secrets.token_hex(32)
            generation = row["generation"] + 1
            db.execute("""UPDATE operator_sim_sessions SET state='leased',reason='claimed',
                worker_id=?,boot_id=?,lease_hash=?,lease_until=?,generation=?,updated_at=? WHERE id=?""",
                       (worker_id, boot_id, hashlib.sha256(token.encode()).hexdigest(),
                        min(now + _LEASE_SECONDS, row["deadline_at"]), generation, now, session_id))
            self._event(db, session_id, "leased", now, "claimed")
            return WorkerLease(session_id, binding, worker_id, boot_id, generation, token)

    def heartbeat(self, lease: WorkerLease, now: int) -> SessionSnapshot:
        with self._transaction() as db:
            row = self._lease_row(db, lease, now, ("leased", "dispatch_reserved", "ringing", "active"))
            db.execute("UPDATE operator_sim_sessions SET lease_until=?,updated_at=? WHERE id=?",
                       (min(now + _LEASE_SECONDS, row["deadline_at"]), now, lease.session_id))
            return self._snapshot(self._row(db, lease.session_id, lease.binding))

    def reserve_dispatch(self, lease: WorkerLease, now: int) -> SessionSnapshot:
        """Commit one dispatch reservation BEFORE the simulated external effect."""
        with self._transaction() as db:
            row = self._lease_row(db, lease, now, ("leased",))
            if row["dispatch_count"]:
                raise OperatorSessionError("Dispatch is already spent")
            db.execute("UPDATE operator_sim_sessions SET dispatch_count=1 WHERE id=?", (lease.session_id,))
            return self._snapshot(self._transition(db, row, "dispatch_reserved", now, "dispatch_reserved"))

    def _fake_start(self, lease: WorkerLease, now: int) -> str:
        """Fake provider transaction. Accepts no phone number or provider credential."""
        with self._transaction() as db:
            self._lease_row(db, lease, now, ("dispatch_reserved",))
            receipt = "sim-" + lease.session_id
            if db.execute("SELECT 1 FROM operator_sim_legs WHERE session_id=?", (lease.session_id,)).fetchone():
                raise OperatorSessionError("A simulated transport leg already exists")
            db.execute("INSERT INTO operator_sim_legs VALUES(?,?,'ringing',?,NULL)",
                       (lease.session_id, receipt, now))
            # Fence out timestamps earlier than the fake effect, even if the
            # worker crashes before confirming the resulting receipt.
            db.execute("UPDATE operator_sim_sessions SET updated_at=? WHERE id=?",
                       (now, lease.session_id))
            return receipt

    def confirm_start(self, lease: WorkerLease, receipt: str, now: int) -> SessionSnapshot:
        with self._transaction() as db:
            row = self._lease_row(db, lease, now, ("dispatch_reserved",))
            leg = db.execute("SELECT * FROM operator_sim_legs WHERE session_id=?", (lease.session_id,)).fetchone()
            if (leg is None or receipt != "sim-" + lease.session_id or leg["receipt"] != receipt
                    or leg["state"] != "ringing" or leg["started_at"] > now):
                raise OperatorSessionError("Simulated transport receipt not verified")
            return self._snapshot(self._transition(db, row, "ringing", now, "start_confirmed"))

    def mark_answered(self, lease: WorkerLease, receipt: str, now: int) -> SessionSnapshot:
        with self._transaction() as db:
            row = self._lease_row(db, lease, now, ("ringing",))
            q = db.execute("""UPDATE operator_sim_legs SET state='active'
                WHERE session_id=? AND receipt=? AND state='ringing'""", (lease.session_id, receipt))
            if q.rowcount != 1:
                raise OperatorSessionError("Simulated answer receipt not verified")
            return self._snapshot(self._transition(db, row, "active", now, "answer_confirmed"))

    def finish(self, lease: WorkerLease, receipt: str, now: int, *, outcome: str = "completed") -> SessionSnapshot:
        if outcome not in {"completed", "busy", "no_answer", "failed"}:
            raise OperatorSessionError("Unsupported simulated terminal outcome")
        with self._transaction() as db:
            row = self._lease_row(db, lease, now, ("ringing", "active"))
            if outcome == "completed" and row["state"] != "active":
                raise OperatorSessionError("No verified simulated answer")
            if outcome in {"busy", "no_answer"} and row["state"] != "ringing":
                raise OperatorSessionError("Simulated ring outcome conflicts with answered state")
            q = db.execute("""UPDATE operator_sim_legs SET state=?,ended_at=?
                WHERE session_id=? AND receipt=? AND state=?""",
                           (outcome, now, lease.session_id, receipt, row["state"]))
            if q.rowcount != 1:
                raise OperatorSessionError("Simulated terminal receipt not verified")
            terminal = "completed" if outcome == "completed" else "failed"
            return self._snapshot(self._transition(db, row, terminal, now, outcome, fence=True))

    def uncertain(self, lease: WorkerLease, now: int) -> SessionSnapshot:
        with self._transaction() as db:
            row = self._lease_row(db, lease, now, ("dispatch_reserved", "ringing", "active"))
            return self._snapshot(self._transition(db, row, "needs_review", now, "uncertain_transport", fence=True))

    def cancel(self, session_id: str, binding: SessionBinding, now: int) -> SessionSnapshot:
        """Fence a worker immediately. After dispatch, require fake-leg reconciliation."""
        with self._transaction() as db:
            row = self._row(db, session_id, binding)
            self._clock(row, now)
            if row["state"] in _TERMINAL or row["state"] == "needs_review":
                return self._snapshot(row)
            state = "needs_review" if row["dispatch_count"] else "canceled"
            return self._snapshot(self._transition(db, row, state, now, "cancel_requested", fence=True))

    def recover(self, now: int, *, limit: int = 100) -> list[SessionSnapshot]:
        """Bounded lease/deadline maintenance. Never repeats a spent dispatch."""
        _time(now)
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise OperatorSessionError("Invalid simulation maintenance limit")
        with self._transaction() as db:
            rows = db.execute("""SELECT * FROM operator_sim_sessions
                WHERE state IN ('prepared','leased','dispatch_reserved','ringing','active')
                  AND (deadline_at<=? OR (state<>'prepared' AND lease_until<=?))
                ORDER BY deadline_at,id LIMIT ?""", (now, now, limit)).fetchall()
            recovered = []
            for row in rows:
                if now < row["updated_at"]:
                    continue
                if row["dispatch_count"]:
                    state, reason = "needs_review", "lease_or_deadline_lost"
                elif row["deadline_at"] <= now:
                    state, reason = "expired", "deadline_expired"
                else:
                    state, reason = "prepared", "unspent_lease_expired"
                recovered.append(self._snapshot(self._transition(db, row, state, now, reason, fence=True)))
            return recovered

    def reconcile_simulation(self, session_id: str, binding: SessionBinding, now: int) -> SessionSnapshot:
        """Inspect and close ONLY the exact local fake leg after fencing its worker.

        This is not carrier reconciliation. In a future real transport, an
        authoritative carrier/room terminal receipt is required before release.
        """
        with self._transaction() as db:
            row = self._row(db, session_id, binding)
            self._clock(row, now)
            if row["state"] in _TERMINAL:
                return self._snapshot(row)
            if row["state"] != "needs_review" or row["dispatch_count"] != 1:
                raise OperatorSessionError("No fenced simulated dispatch to reconcile")
            leg = db.execute("SELECT * FROM operator_sim_legs WHERE session_id=?", (session_id,)).fetchone()
            if leg is not None:
                if leg["receipt"] != "sim-" + session_id:
                    raise OperatorSessionError("Unexpected simulated leg binding")
                if leg["state"] in {"ringing", "active"}:
                    db.execute("UPDATE operator_sim_legs SET state='canceled',ended_at=? WHERE session_id=?",
                               (now, session_id))
                elif leg["state"] not in {"canceled", "completed", "busy", "no_answer", "failed"}:
                    raise OperatorSessionError("Unverified simulated leg state")
                else:
                    # Even if the simulated leg is terminal, this path is
                    # reconciliation of an uncertain call, not a claimed success.
                    pass
            return self._snapshot(self._transition(db, row, "canceled", now,
                        "simulated_leg_closed" if leg is not None else "simulated_leg_absent", fence=True))

    def health(self, now: int) -> dict:
        """Local metadata only: not media/provider readiness or a live-call grant."""
        _time(now)
        with self._transaction() as db:
            pending = db.execute("""SELECT COUNT(*) FROM operator_sim_sessions
                WHERE state NOT IN ('completed','canceled','expired','failed')""").fetchone()[0]
            leases = db.execute("""SELECT COUNT(*) FROM operator_sim_sessions
                WHERE state IN ('leased','dispatch_reserved','ringing','active')
                  AND lease_until>? AND deadline_at>? AND updated_at<=?""", (now, now, now)).fetchone()[0]
            review = db.execute("SELECT COUNT(*) FROM operator_sim_sessions WHERE state='needs_review'").fetchone()[0]
            return {"phase": "8A.1", "transport": "simulation", "execution_enabled": False,
                    "live_certified": False, "pending_sessions": pending,
                    "current_leases": leases, "needs_review": review}


class SimulatedTransport:
    """The sole transport in this release. No network/client injection seam."""

    def __init__(self, store: OperatorSimulationStore):
        self.store = store

    def start(self, lease: WorkerLease, now: int, *, fault: str = "none") -> str:
        if fault not in {"none", "before_effect", "after_effect"}:
            raise OperatorSessionError("Unknown simulation fault")
        if fault == "before_effect":
            raise SimulatedUncertainDispatch("Simulated transport response lost before effect")
        receipt = self.store._fake_start(lease, now)
        if fault == "after_effect":
            raise SimulatedUncertainDispatch("Simulated transport response lost after effect")
        return receipt


def run_simulated_dispatch(store: OperatorSimulationStore, lease: WorkerLease,
                           now: int, *, fault: str = "none") -> SessionSnapshot:
    """Reserve, attempt the fake transport once, then confirm or fence."""
    if fault not in {"none", "before_effect", "after_effect"}:
        raise OperatorSessionError("Unknown simulation fault")
    store.reserve_dispatch(lease, now)
    try:
        receipt = SimulatedTransport(store).start(lease, now, fault=fault)
        return store.confirm_start(lease, receipt, now)
    except OperatorSessionError:
        # Cancellation or a racing lease loss may already have fenced us.
        # Never attempt another dispatch or manufacture a successful receipt.
        try:
            return store.uncertain(lease, now)
        except OperatorSessionError:
            return store.snapshot(lease.session_id, lease.binding)
