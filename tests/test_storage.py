"""StateStore: CRUD, schema migration, per-gate counter independence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stokowski.storage import IssueState, StateStore


def test_schema_initialised_to_version_1(tmp_path: Path):
    store = StateStore(tmp_path / "state.db")
    try:
        cur = store._conn.execute("SELECT version FROM schema_version")
        rows = [r["version"] for r in cur.fetchall()]
        assert rows == [1]
    finally:
        store.close()


def test_schema_idempotent_on_reopen(tmp_path: Path):
    db = tmp_path / "state.db"
    StateStore(db).close()
    StateStore(db).close()  # must not raise on re-open
    s = StateStore(db)
    try:
        cur = s._conn.execute("SELECT version FROM schema_version")
        row = cur.fetchone()
        assert row["version"] == 1
    finally:
        s.close()


def test_upsert_and_get_issue(tmp_store: StateStore, now: datetime):
    tmp_store.upsert_issue(
        issue_id="ID1",
        issue_identifier="SYN-1",
        project_name="proj",
        internal_state="implement",
        pending_gate=None,
        now=now,
    )
    got = tmp_store.get_issue("ID1")
    assert got is not None
    assert got.issue_id == "ID1"
    assert got.issue_identifier == "SYN-1"
    assert got.project_name == "proj"
    assert got.internal_state == "implement"
    assert got.pending_gate is None
    assert got.created_at == now
    assert got.updated_at == now


def test_get_issue_missing_returns_none(tmp_store: StateStore):
    assert tmp_store.get_issue("nope") is None


def test_upsert_preserves_created_at_updates_updated_at(
    tmp_store: StateStore, now: datetime
):
    tmp_store.upsert_issue(
        issue_id="ID1",
        issue_identifier="SYN-1",
        project_name="p",
        internal_state="implement",
        pending_gate=None,
        now=now,
    )
    later = now + timedelta(hours=1)
    tmp_store.upsert_issue(
        issue_id="ID1",
        issue_identifier="SYN-1",
        project_name="p",
        internal_state="await_ci_and_review",
        pending_gate="await_ci_and_review",
        now=later,
    )
    got = tmp_store.get_issue("ID1")
    assert got.internal_state == "await_ci_and_review"
    assert got.pending_gate == "await_ci_and_review"
    assert got.created_at == now  # preserved
    assert got.updated_at == later  # bumped


def test_set_pending_gate(tmp_store: StateStore, now: datetime):
    tmp_store.upsert_issue(
        "ID1", "SYN-1", "p", "implement", None, now=now
    )
    tmp_store.set_pending_gate("ID1", "await_ci_and_review", now=now)
    assert tmp_store.get_issue("ID1").pending_gate == "await_ci_and_review"
    tmp_store.set_pending_gate("ID1", None, now=now)
    assert tmp_store.get_issue("ID1").pending_gate is None


def test_set_internal_state(tmp_store: StateStore, now: datetime):
    tmp_store.upsert_issue("ID1", "SYN-1", "p", "implement", None, now=now)
    tmp_store.set_internal_state("ID1", "review_implementation", now=now)
    assert tmp_store.get_issue("ID1").internal_state == "review_implementation"


def test_set_session_id_persists_for_resume(tmp_store: StateStore, now: datetime):
    tmp_store.upsert_issue("ID1", "SYN-1", "p", "implement", None, now=now)
    tmp_store.set_session_id("ID1", "claude-session-uuid-abc", now=now)
    assert tmp_store.get_issue("ID1").last_session_id == "claude-session-uuid-abc"
    tmp_store.set_session_id("ID1", None, now=now)
    assert tmp_store.get_issue("ID1").last_session_id is None


def test_mark_completed(tmp_store: StateStore, now: datetime):
    tmp_store.upsert_issue("ID1", "SYN-1", "p", "implement", None, now=now)
    later = now + timedelta(minutes=5)
    tmp_store.mark_completed("ID1", later)
    assert tmp_store.get_issue("ID1").last_completed_at == later


def test_set_and_clear_rework_context(tmp_store: StateStore, now: datetime):
    tmp_store.upsert_issue("ID1", "SYN-1", "p", "implement", None, now=now)
    tmp_store.set_rework_context(
        "ID1",
        reason="ci_fail",
        detector="poll-ci-status",
        when=now,
    )
    s = tmp_store.get_issue("ID1")
    assert s.last_rework_reason == "ci_fail"
    assert s.last_rework_detector == "poll-ci-status"
    assert s.last_rework_at == now
    tmp_store.clear_rework_context("ID1")
    s = tmp_store.get_issue("ID1")
    assert s.last_rework_reason is None
    assert s.last_rework_detector is None
    assert s.last_rework_at is None


def test_gate_runs_default_to_one_when_unset(tmp_store: StateStore):
    assert tmp_store.get_run("ID1", "await_ci_and_review") == 1


def test_bump_run_increments_returned_value(tmp_store: StateStore):
    assert tmp_store.bump_run("ID1", "await_ci_and_review") == 2
    assert tmp_store.bump_run("ID1", "await_ci_and_review") == 3
    assert tmp_store.get_run("ID1", "await_ci_and_review") == 3


def test_per_gate_counters_are_independent(tmp_store: StateStore):
    """3 reworks at Awaiting CI must NOT consume Human Review's budget."""
    for _ in range(3):
        tmp_store.bump_run("ID1", "await_ci_and_review")
    assert tmp_store.get_run("ID1", "await_ci_and_review") == 4
    # Human Review counter untouched.
    assert tmp_store.get_run("ID1", "review_implementation") == 1
    assert tmp_store.bump_run("ID1", "review_implementation") == 2
    # Awaiting CI still independent.
    assert tmp_store.get_run("ID1", "await_ci_and_review") == 4


def test_per_issue_counters_are_independent(tmp_store: StateStore):
    tmp_store.bump_run("ID1", "await_ci_and_review")
    tmp_store.bump_run("ID1", "await_ci_and_review")
    assert tmp_store.get_run("ID1", "await_ci_and_review") == 3
    assert tmp_store.get_run("ID2", "await_ci_and_review") == 1


def test_list_active_filters_by_project_and_excludes_completed(
    tmp_store: StateStore, now: datetime
):
    tmp_store.upsert_issue("A", "SYN-1", "synced-sport", "implement", None, now=now)
    tmp_store.upsert_issue(
        "B", "SYN-2", "synced-sport", "implement", "await_ci_and_review", now=now
    )
    tmp_store.upsert_issue("C", "SYN-3", "other", "implement", None, now=now)
    # Terminal — internal_state cleared.
    tmp_store.upsert_issue("D", "SYN-4", "synced-sport", None, None, now=now)

    active = tmp_store.list_active("synced-sport")
    ids = {s.issue_id for s in active}
    assert ids == {"A", "B"}


def test_iter_pending_gates_returns_only_parked(
    tmp_store: StateStore, now: datetime
):
    tmp_store.upsert_issue("A", "SYN-1", "p", "implement", None, now=now)
    tmp_store.upsert_issue("B", "SYN-2", "p", "await_ci_and_review", "await_ci_and_review", now=now)
    tmp_store.upsert_issue("C", "SYN-3", "p", "review_implementation", "review_implementation", now=now)

    gates = tmp_store.iter_pending_gates("p")
    rows = {s.issue_id: s.pending_gate for s in gates}
    assert rows == {"B": "await_ci_and_review", "C": "review_implementation"}


def test_round_trip_datetime_preserves_utc(tmp_store: StateStore):
    when = datetime(2026, 5, 15, 23, 59, 59, tzinfo=timezone.utc)
    tmp_store.upsert_issue("ID1", "SYN-1", "p", "implement", None, now=when)
    tmp_store.set_rework_context("ID1", "ci_fail", "poll-ci-status", when=when)
    s = tmp_store.get_issue("ID1")
    assert s.created_at == when
    assert s.updated_at == when
    assert s.last_rework_at == when
