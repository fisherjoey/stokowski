"""Reconcile rule coverage (spec §Reconcile rule decision table)."""

from __future__ import annotations

import pytest

from ._helpers import (
    FakeLinearClient,
    make_test_orchestrator,
    trigger_marker,
)


pytestmark = pytest.mark.asyncio


async def test_seeds_from_linear_on_empty_db(tmp_path):
    """AC6: empty state.db seeds rows for every non-terminal Linear ticket."""
    fake = FakeLinearClient()
    fake.seed("ID1", "SYN-1", "In Progress")
    fake.seed("ID2", "SYN-2", "Awaiting CI")
    fake.seed("ID3", "SYN-3", "Human Review")

    orch = make_test_orchestrator(tmp_path, fake_linear=fake)
    await orch._reconcile_from_storage(initial=True)

    project = "testproj"
    rows = orch.store.list_active(project)
    ids = {r.issue_id for r in rows}
    assert ids == {"ID1", "ID2", "ID3"}

    # Gate Linear states result in pending_gate set on the corresponding
    # internal state. SYN-1 is in active so no pending_gate.
    by_id = {r.issue_id: r for r in rows}
    assert by_id["ID1"].pending_gate is None
    assert by_id["ID2"].pending_gate == "await_ci_and_review"
    assert by_id["ID3"].pending_gate == "review_implementation"


async def test_seeds_run_counter_from_gate_tracking_comment(tmp_path):
    """A reworked ticket's run number is recovered from its latest gate comment."""
    fake = FakeLinearClient()
    fake.seed("ID1", "SYN-1", "Awaiting CI")
    # Simulate two prior rework iterations.
    fake.add_comment(
        "ID1",
        '<!-- stokowski:gate {"state": "await_ci_and_review", "status": "waiting", "run": 3} -->'
    )

    orch = make_test_orchestrator(tmp_path, fake_linear=fake)
    await orch._reconcile_from_storage(initial=True)

    assert orch.store.get_run("ID1", "await_ci_and_review") == 3


async def test_subsequent_startup_skips_seed_and_logs_divergences(tmp_path, caplog):
    """AC6: second startup does not re-seed; logs divergence count."""
    fake = FakeLinearClient()
    fake.seed("ID1", "SYN-1", "In Progress")

    orch = make_test_orchestrator(tmp_path, fake_linear=fake)
    await orch._reconcile_from_storage(initial=True)
    # DB now has a row; clear caches and reconcile again.
    orch._issue_current_state.clear()
    orch._pending_gates.clear()

    import logging
    with caplog.at_level(logging.INFO, logger="stokowski"):
        await orch._reconcile_from_storage(initial=True)
    msgs = [r.getMessage() for r in caplog.records]
    assert not any("seeded" in m for m in msgs)
    assert any("divergence" in m for m in msgs)


async def test_terminal_linear_state_clears_pending_gate(tmp_path):
    """Reconcile rule §4: externally terminalised → wipe SQLite parking."""
    fake = FakeLinearClient()
    orch = make_test_orchestrator(tmp_path, fake_linear=fake)
    # Pre-seed SQLite as if we parked at gate, but Linear has the ticket done.
    orch.store.upsert_issue(
        "ID1", "SYN-1", "testproj",
        internal_state="await_ci_and_review",
        pending_gate="await_ci_and_review",
    )
    # Linear has no record (terminal — not in non-terminal fetch).
    await orch._reconcile_from_storage(initial=False)

    row = orch.store.get_issue("ID1")
    assert row.pending_gate is None
    assert row.internal_state is None


async def test_linear_in_gate_state_overrides_sqlite_on_divergence(tmp_path):
    """Reconcile rule §5: when SQLite and Linear disagree, Linear wins."""
    fake = FakeLinearClient()
    fake.seed("ID1", "SYN-1", "Human Review")  # Linear says review gate.

    orch = make_test_orchestrator(tmp_path, fake_linear=fake)
    # SQLite says we're at the Awaiting CI gate (mismatch).
    orch.store.upsert_issue(
        "ID1", "SYN-1", "testproj",
        internal_state="await_ci_and_review",
        pending_gate="await_ci_and_review",
    )

    await orch._reconcile_from_storage(initial=False)

    row = orch.store.get_issue("ID1")
    assert row.pending_gate == "review_implementation"
    assert row.internal_state == "review_implementation"


async def test_linear_active_but_sqlite_parked_clears_pending_gate(tmp_path):
    """Reconcile rule §6 implicit: ticket back to In Progress without label → clear gate."""
    fake = FakeLinearClient()
    fake.seed("ID1", "SYN-1", "In Progress")  # Linear says active.

    orch = make_test_orchestrator(tmp_path, fake_linear=fake)
    orch.store.upsert_issue(
        "ID1", "SYN-1", "testproj",
        internal_state="await_ci_and_review",
        pending_gate="await_ci_and_review",
    )

    await orch._reconcile_from_storage(initial=False)

    row = orch.store.get_issue("ID1")
    assert row.pending_gate is None
    # Should land on the gate's rework_to so the dispatcher picks it up cleanly.
    assert row.internal_state == "implement"


async def test_needs_rework_label_triggers_rework_pickup(tmp_path):
    """Reconcile rule §6: needs-rework on a parked ticket → rework pickup."""
    fake = FakeLinearClient()
    fake.seed("ID1", "SYN-1", "Awaiting CI", labels=["needs-rework"])
    fake.add_comment("ID1", trigger_marker("ci_fail", "poll-ci-status", pr_number=42))

    orch = make_test_orchestrator(tmp_path, fake_linear=fake)
    orch.store.upsert_issue(
        "ID1", "SYN-1", "testproj",
        internal_state="await_ci_and_review",
        pending_gate="await_ci_and_review",
    )

    await orch._reconcile_from_storage(initial=False)

    # Pickup side effects: label removed, ticket back to In Progress,
    # pending_gate cleared, run bumped, rework context persisted.
    assert "needs-rework" not in fake.issues["ID1"].labels
    assert fake.issues["ID1"].state == "In Progress"
    row = orch.store.get_issue("ID1")
    assert row.pending_gate is None
    assert row.internal_state == "implement"
    assert orch.store.get_run("ID1", "await_ci_and_review") == 2
    assert row.last_rework_reason == "ci_fail"
    assert row.last_rework_detector == "poll-ci-status"


async def test_new_ticket_creates_no_row_on_reconcile_pass(tmp_path):
    """Brand-new tickets are created on the dispatch path, not the reconcile path.

    Reconcile only fixes existing divergences; a Linear ticket with no
    SQLite row is left alone, to be seeded when the dispatch loop calls
    `_resolve_current_state` on it.
    """
    fake = FakeLinearClient()
    fake.seed("ID1", "SYN-1", "In Progress")

    orch = make_test_orchestrator(tmp_path, fake_linear=fake)
    await orch._reconcile_from_storage(initial=False)

    assert orch.store.get_issue("ID1") is None
