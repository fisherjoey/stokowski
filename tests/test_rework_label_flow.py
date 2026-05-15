"""Label-driven rework flow (AC4, AC11).

Covers:
  - needs-rework label detection on a gate-parked ticket
  - reason / detector extraction from stokowski:rework-trigger marker
  - dispatch with rework_reason threaded into prompt context
  - label removal so the trigger is single-shot (double-dispatch prevention)
  - per-gate run counter independence (AC3)
  - max_rework escalation
"""

from __future__ import annotations

import pytest

from stokowski.models import Issue
from stokowski.prompt import build_lifecycle_section, build_template_context

from ._helpers import FakeLinearClient, make_test_orchestrator, trigger_marker


pytestmark = pytest.mark.asyncio


def _park_at_gate(orch, issue_id: str, identifier: str, gate: str):
    orch.store.upsert_issue(
        issue_id, identifier, "testproj",
        internal_state=gate, pending_gate=gate,
    )


async def test_pickup_dispatches_with_reason_from_trigger(tmp_path):
    fake = FakeLinearClient()
    fake.seed("ID1", "SYN-1", "Awaiting CI", labels=["needs-rework"])
    fake.add_comment("ID1", trigger_marker("ci_fail", "poll-ci-status", pr_number=42))

    orch = make_test_orchestrator(tmp_path, fake_linear=fake)
    _park_at_gate(orch, "ID1", "SYN-1", "await_ci_and_review")

    await orch._handle_rework_pickup(fake.issues["ID1"], orch.store.get_issue("ID1"))

    row = orch.store.get_issue("ID1")
    assert row.last_rework_reason == "ci_fail"
    assert row.last_rework_detector == "poll-ci-status"
    assert row.internal_state == "implement"
    assert row.pending_gate is None


async def test_pickup_removes_needs_rework_label(tmp_path):
    """The trigger must be single-shot; otherwise we dispatch every tick."""
    fake = FakeLinearClient()
    fake.seed("ID1", "SYN-1", "Awaiting CI", labels=["needs-rework", "symphony"])
    fake.add_comment("ID1", trigger_marker("ci_fail", "poll-ci-status"))

    orch = make_test_orchestrator(tmp_path, fake_linear=fake)
    _park_at_gate(orch, "ID1", "SYN-1", "await_ci_and_review")

    await orch._handle_rework_pickup(fake.issues["ID1"], orch.store.get_issue("ID1"))

    assert "needs-rework" not in fake.issues["ID1"].labels
    assert "symphony" in fake.issues["ID1"].labels  # untouched


async def test_pickup_moves_ticket_to_active_linear_state(tmp_path):
    fake = FakeLinearClient()
    fake.seed("ID1", "SYN-1", "Awaiting CI", labels=["needs-rework"])
    orch = make_test_orchestrator(tmp_path, fake_linear=fake)
    _park_at_gate(orch, "ID1", "SYN-1", "await_ci_and_review")

    await orch._handle_rework_pickup(fake.issues["ID1"], orch.store.get_issue("ID1"))

    assert fake.issues["ID1"].state == "In Progress"


async def test_double_dispatch_prevented_by_idempotent_label_removal(tmp_path):
    """Running pickup twice in a row must not double-bump the run counter."""
    fake = FakeLinearClient()
    fake.seed("ID1", "SYN-1", "Awaiting CI", labels=["needs-rework"])
    fake.add_comment("ID1", trigger_marker("ci_fail", "poll-ci-status"))

    orch = make_test_orchestrator(tmp_path, fake_linear=fake)
    _park_at_gate(orch, "ID1", "SYN-1", "await_ci_and_review")

    await orch._handle_rework_pickup(fake.issues["ID1"], orch.store.get_issue("ID1"))
    # Second reconcile sees no label → no rework pickup fires. We exercise the
    # reconcile path explicitly to simulate the next tick.
    await orch._reconcile_from_storage(initial=False)

    assert orch.store.get_run("ID1", "await_ci_and_review") == 2


async def test_per_gate_counters_remain_independent(tmp_path):
    """3 reworks at Awaiting CI must not consume Human Review's budget (AC3)."""
    fake = FakeLinearClient()
    fake.seed("ID1", "SYN-1", "Awaiting CI", labels=["needs-rework"])
    fake.add_comment("ID1", trigger_marker("ci_fail", "poll-ci-status"))

    orch = make_test_orchestrator(tmp_path, fake_linear=fake)
    _park_at_gate(orch, "ID1", "SYN-1", "await_ci_and_review")
    await orch._handle_rework_pickup(fake.issues["ID1"], orch.store.get_issue("ID1"))

    # Sim agent finishing implement → reaches Human Review gate.
    fake.issues["ID1"].state = "Human Review"
    fake.issues["ID1"].labels = ["needs-rework"]
    fake.add_comment(
        "ID1", trigger_marker("reviewer_request_changes", "poll-ci-status")
    )
    orch.store.set_pending_gate("ID1", "review_implementation")
    orch.store.set_internal_state("ID1", "review_implementation")

    await orch._handle_rework_pickup(fake.issues["ID1"], orch.store.get_issue("ID1"))

    # Awaiting CI run is still 2 (its previous bump); Human Review is at 2 now.
    assert orch.store.get_run("ID1", "await_ci_and_review") == 2
    assert orch.store.get_run("ID1", "review_implementation") == 2


async def test_max_rework_escalates_when_ceiling_hit(tmp_path):
    """When run == max_rework, escalate to Human Review + rework-escalated label."""
    fake = FakeLinearClient()
    fake.seed("ID1", "SYN-1", "Awaiting CI", labels=["needs-rework"])
    fake.add_comment("ID1", trigger_marker("ci_fail", "poll-ci-status"))

    orch = make_test_orchestrator(tmp_path, fake_linear=fake)
    _park_at_gate(orch, "ID1", "SYN-1", "await_ci_and_review")
    # max_rework=3 in test config. Pre-bump twice → next attempt blows the ceiling.
    orch.store.bump_run("ID1", "await_ci_and_review")  # → 2
    orch.store.bump_run("ID1", "await_ci_and_review")  # → 3

    await orch._handle_rework_pickup(fake.issues["ID1"], orch.store.get_issue("ID1"))

    assert fake.issues["ID1"].state == "Human Review"
    assert "rework-escalated" in fake.issues["ID1"].labels
    assert "needs-rework" not in fake.issues["ID1"].labels


async def test_rework_reason_renders_into_prompt_lifecycle(tmp_path):
    """AC11: lifecycle prompt template surfaces rework_reason when present."""
    from stokowski.config import LinearStatesConfig, StateConfig

    issue = Issue(id="ID1", identifier="SYN-1", title="t", state="In Progress")
    state_cfg = StateConfig(
        name="implement",
        type="agent",
        transitions={"complete": "await_ci_and_review"},
    )
    section = build_lifecycle_section(
        issue=issue,
        state_name="implement",
        state_cfg=state_cfg,
        linear_states=LinearStatesConfig(),
        run=2,
        is_rework=True,
        recent_comments=[],
        rework_reason="ci_fail",
    )
    assert "Rework" in section
    assert "ci_fail" in section


@pytest.mark.asyncio(loop_scope="function")
async def test_template_context_carries_rework_reason():
    issue = Issue(id="ID1", identifier="SYN-1", title="t", state="In Progress")
    ctx = build_template_context(
        issue=issue,
        state_name="implement",
        run=2,
        attempt=1,
        last_run_at="2026-05-15T00:00:00+00:00",
        rework_reason="merge_conflict",
    )
    assert ctx["rework_reason"] == "merge_conflict"
