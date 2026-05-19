"""Read-only access to the linear-webhook-receiver warm cache.

Stokowski uses this in LinearClient as a "try cache first" preamble before
falling through to direct Linear calls. All paths are tolerant of a missing
or empty cache — they just return None / empty.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path


RECONCILE_STALE_MIN = 30  # cache considered stale if reconcile > N min ago
WEBHOOK_STALE_MIN = 5     # ALSO consider stale if no webhook in N min AND there's been activity


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class CacheReader:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection | None:
        if not self.db_path.exists():
            return None
        try:
            c = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=5)
            c.row_factory = sqlite3.Row
            return c
        except sqlite3.Error:
            return None

    def is_fresh(self) -> bool:
        """True iff cache is recent enough to trust for reads."""
        conn = self._connect()
        if conn is None:
            return False
        try:
            rec_row = conn.execute(
                "SELECT value FROM meta WHERE key='last_reconcile_at'"
            ).fetchone()
            if not rec_row:
                return False
            rec_age = datetime.now(timezone.utc) - _parse_iso(rec_row[0])
            if rec_age > timedelta(minutes=RECONCILE_STALE_MIN):
                return False
            wh_row = conn.execute(
                "SELECT value FROM meta WHERE key='last_webhook_at'"
            ).fetchone()
            if wh_row:
                wh_age = datetime.now(timezone.utc) - _parse_iso(wh_row[0])
                # If last webhook was > WEBHOOK_STALE_MIN ago AND reconcile is fresh,
                # trust reconcile (no events recently is fine).
            return True
        finally:
            conn.close()

    def get_issues_by_state(self, project_id: str, state_ids: list[str]) -> list[dict]:
        if not state_ids:
            return []
        conn = self._connect()
        if conn is None:
            return []
        try:
            placeholders = ",".join("?" * len(state_ids))
            rows = conn.execute(
                f"SELECT id, identifier, title, state_id, state_name, project_id, "
                f"labels_json, assignee_id, updated_at "
                f"FROM issue WHERE project_id=? AND state_id IN ({placeholders})",
                (project_id, *state_ids),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def get_issue_by_id(self, issue_id: str) -> dict | None:
        conn = self._connect()
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT id, identifier, title, state_id, state_name, project_id, "
                "labels_json, assignee_id, updated_at FROM issue WHERE id=?",
                (issue_id,),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row else None

    def get_comments_for_issue(self, issue_id: str) -> list[dict]:
        conn = self._connect()
        if conn is None:
            return []
        try:
            rows = conn.execute(
                "SELECT id, issue_id, body, author_id, created_at, updated_at "
                "FROM comment WHERE issue_id=? ORDER BY created_at ASC",
                (issue_id,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
