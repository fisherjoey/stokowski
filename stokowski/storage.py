"""SQLite-backed durable orchestrator state.

Persists the subset of orchestrator state that is load-bearing across restarts:
internal state machine state, pending_gate, per-(issue, gate) run counters,
last_session_id (for `claude --resume`), last_completed_at, and rework context.

See docs/specs/2026-05-15-durable-state-design.md (§State partition, §Schema).

Concurrency model: one StateStore per orchestrator process holds a single
sqlite3 connection in WAL mode. All writes go through a re-entrant lock so
async callers can safely run methods from any task without colliding.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _from_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s)


SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS issue_state (
  issue_id              TEXT PRIMARY KEY,
  issue_identifier      TEXT NOT NULL,
  project_name          TEXT NOT NULL,
  internal_state        TEXT,
  pending_gate          TEXT,
  last_session_id       TEXT,
  last_completed_at     TEXT,
  last_rework_reason    TEXT,
  last_rework_detector  TEXT,
  last_rework_at        TEXT,
  created_at            TEXT NOT NULL,
  updated_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gate_runs (
  issue_id    TEXT NOT NULL,
  gate_state  TEXT NOT NULL,
  run         INTEGER NOT NULL DEFAULT 1,
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (issue_id, gate_state)
);

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
"""


@dataclass
class IssueState:
    issue_id: str
    issue_identifier: str
    project_name: str
    internal_state: str | None
    pending_gate: str | None
    last_session_id: str | None
    last_completed_at: datetime | None
    last_rework_reason: str | None
    last_rework_detector: str | None
    last_rework_at: datetime | None
    created_at: datetime
    updated_at: datetime


_COLUMNS = (
    "issue_id",
    "issue_identifier",
    "project_name",
    "internal_state",
    "pending_gate",
    "last_session_id",
    "last_completed_at",
    "last_rework_reason",
    "last_rework_detector",
    "last_rework_at",
    "created_at",
    "updated_at",
)


def _row_to_state(row: sqlite3.Row) -> IssueState:
    return IssueState(
        issue_id=row["issue_id"],
        issue_identifier=row["issue_identifier"],
        project_name=row["project_name"],
        internal_state=row["internal_state"],
        pending_gate=row["pending_gate"],
        last_session_id=row["last_session_id"],
        last_completed_at=_from_iso(row["last_completed_at"]),
        last_rework_reason=row["last_rework_reason"],
        last_rework_detector=row["last_rework_detector"],
        last_rework_at=_from_iso(row["last_rework_at"]),
        created_at=_from_iso(row["created_at"]),
        updated_at=_from_iso(row["updated_at"]),
    )


class StateStore:
    """Durable orchestrator state, backed by a single SQLite file.

    Thread-safe via an internal re-entrant lock. The lock guards every
    connection method so async callers (running inside `loop.run_in_executor`
    or scheduled across tasks) can call any method without coordination.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; we manage transactions explicitly
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA_V1)
            cur = self._conn.execute("SELECT COUNT(*) FROM schema_version")
            (n,) = cur.fetchone()
            if n == 0:
                self._conn.execute(
                    "INSERT INTO schema_version (version) VALUES (1)"
                )

    # ── Reads ──────────────────────────────────────────────────────────────

    def get_issue(self, issue_id: str) -> IssueState | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM issue_state WHERE issue_id = ?", (issue_id,)
            )
            row = cur.fetchone()
            return _row_to_state(row) if row else None

    def list_active(self, project_name: str) -> list[IssueState]:
        """Rows for this project with a non-NULL internal_state (i.e. not terminal)."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM issue_state WHERE project_name = ? "
                "AND internal_state IS NOT NULL ORDER BY issue_identifier",
                (project_name,),
            )
            return [_row_to_state(r) for r in cur.fetchall()]

    def iter_pending_gates(self, project_name: str) -> list[IssueState]:
        """Rows currently parked at a gate (pending_gate IS NOT NULL)."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM issue_state WHERE project_name = ? "
                "AND pending_gate IS NOT NULL ORDER BY issue_identifier",
                (project_name,),
            )
            return [_row_to_state(r) for r in cur.fetchall()]

    def get_run(self, issue_id: str, gate_state: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "SELECT run FROM gate_runs WHERE issue_id = ? AND gate_state = ?",
                (issue_id, gate_state),
            )
            row = cur.fetchone()
            return int(row["run"]) if row else 1

    # ── Writes ─────────────────────────────────────────────────────────────

    def upsert_issue(
        self,
        issue_id: str,
        issue_identifier: str,
        project_name: str,
        internal_state: str | None,
        pending_gate: str | None,
        now: datetime | None = None,
    ) -> None:
        when = _to_iso(now or datetime.now(timezone.utc))
        with self._lock:
            existing = self._conn.execute(
                "SELECT created_at FROM issue_state WHERE issue_id = ?",
                (issue_id,),
            ).fetchone()
            created = existing["created_at"] if existing else when
            self._conn.execute(
                "INSERT INTO issue_state ("
                "  issue_id, issue_identifier, project_name, internal_state, "
                "  pending_gate, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(issue_id) DO UPDATE SET "
                "  issue_identifier = excluded.issue_identifier, "
                "  project_name = excluded.project_name, "
                "  internal_state = excluded.internal_state, "
                "  pending_gate = excluded.pending_gate, "
                "  updated_at = excluded.updated_at",
                (
                    issue_id,
                    issue_identifier,
                    project_name,
                    internal_state,
                    pending_gate,
                    created,
                    when,
                ),
            )

    def set_pending_gate(
        self,
        issue_id: str,
        gate_state: str | None,
        now: datetime | None = None,
    ) -> None:
        when = _to_iso(now or datetime.now(timezone.utc))
        with self._lock:
            self._conn.execute(
                "UPDATE issue_state SET pending_gate = ?, updated_at = ? "
                "WHERE issue_id = ?",
                (gate_state, when, issue_id),
            )

    def set_internal_state(
        self,
        issue_id: str,
        state: str | None,
        now: datetime | None = None,
    ) -> None:
        when = _to_iso(now or datetime.now(timezone.utc))
        with self._lock:
            self._conn.execute(
                "UPDATE issue_state SET internal_state = ?, updated_at = ? "
                "WHERE issue_id = ?",
                (state, when, issue_id),
            )

    def bump_run(self, issue_id: str, gate_state: str) -> int:
        """Bump the per-(issue, gate) run counter and return the new value.

        Counter starts at 1 on creation, so a first bump yields 2 (matching
        the existing per-state run semantics where run 1 == first dispatch).
        """
        when = _to_iso(datetime.now(timezone.utc))
        with self._lock:
            cur = self._conn.execute(
                "SELECT run FROM gate_runs WHERE issue_id = ? AND gate_state = ?",
                (issue_id, gate_state),
            )
            row = cur.fetchone()
            if row is None:
                new = 2  # creation establishes run 1; first bump yields 2
                self._conn.execute(
                    "INSERT INTO gate_runs (issue_id, gate_state, run, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (issue_id, gate_state, new, when),
                )
            else:
                new = int(row["run"]) + 1
                self._conn.execute(
                    "UPDATE gate_runs SET run = ?, updated_at = ? "
                    "WHERE issue_id = ? AND gate_state = ?",
                    (new, when, issue_id, gate_state),
                )
            return new

    def set_session_id(
        self,
        issue_id: str,
        session_id: str | None,
        now: datetime | None = None,
    ) -> None:
        when = _to_iso(now or datetime.now(timezone.utc))
        with self._lock:
            self._conn.execute(
                "UPDATE issue_state SET last_session_id = ?, updated_at = ? "
                "WHERE issue_id = ?",
                (session_id, when, issue_id),
            )

    def mark_completed(self, issue_id: str, when: datetime) -> None:
        ts = _to_iso(when)
        with self._lock:
            self._conn.execute(
                "UPDATE issue_state SET last_completed_at = ?, updated_at = ? "
                "WHERE issue_id = ?",
                (ts, ts, issue_id),
            )

    def set_rework_context(
        self,
        issue_id: str,
        reason: str,
        detector: str,
        when: datetime,
    ) -> None:
        ts = _to_iso(when)
        with self._lock:
            self._conn.execute(
                "UPDATE issue_state SET "
                "  last_rework_reason = ?, "
                "  last_rework_detector = ?, "
                "  last_rework_at = ?, "
                "  updated_at = ? "
                "WHERE issue_id = ?",
                (reason, detector, ts, ts, issue_id),
            )

    def clear_rework_context(self, issue_id: str) -> None:
        now = _to_iso(datetime.now(timezone.utc))
        with self._lock:
            self._conn.execute(
                "UPDATE issue_state SET "
                "  last_rework_reason = NULL, "
                "  last_rework_detector = NULL, "
                "  last_rework_at = NULL, "
                "  updated_at = ? "
                "WHERE issue_id = ?",
                (now, issue_id),
            )

    # ── Transaction helper ─────────────────────────────────────────────────

    def transaction(self) -> _Txn:
        """Context manager that wraps a BEGIN/COMMIT around multiple writes.

        Use when a single logical transition needs to update both
        `issue_state` and `gate_runs` atomically (see spec §Write order).
        """
        return _Txn(self)


class _Txn:
    def __init__(self, store: StateStore):
        self._store = store

    def __enter__(self):
        self._store._lock.acquire()
        self._store._conn.execute("BEGIN")
        return self._store

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._store._conn.execute("COMMIT")
            else:
                self._store._conn.execute("ROLLBACK")
        finally:
            self._store._lock.release()
        return False
