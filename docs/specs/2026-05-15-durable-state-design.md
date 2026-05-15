# Durable orchestrator state + Rework→label collapse

**Status:** Design approved 2026-05-15.
**Target branch:** `feature/durable-state` (worktree at `~/dev/tools/stokowski-durable-state`).
**Implementation target:** end of day 2026-05-15 with light smoke + cutover.

## Why

Stokowski's orchestrator state (`_pending_gates`, `_issue_state_runs`, `_issue_current_state`, `_last_session_ids`) lives in Python process memory. On restart it is reconstructed from Linear comments by `_rebuild_gates_from_linear()`. That recovery path is structurally fragile:

- The Linear GraphQL query `state: { name: { in: [..., "Rework"] } }` empirically returns zero Rework tickets even when Rework is populated. Reworked tickets are silently dropped from rebuild, the gate dispatch loop's primary path returns `None`, and the rework re-dispatch never fires. Workaround: manually move Rework → Todo, losing `--resume` session context.
- Even without that bug, the recovery is N+M Linear API calls (issues + comments per ticket) and pays a multi-second latency on every restart.
- The Rework Linear state is conceptually muddled — neither active nor a gate nor terminal. It exists only as a transient signal that Stokowski has decided to re-dispatch.

This design moves the durable subset of orchestrator state to a local SQLite store and collapses Rework from a Linear state to a `needs-rework` label on In Progress. Together they eliminate the rebuild-from-Linear path entirely, retire `poll-stuck-rework.py`, and fix the per-`(issue, gate)` run-counter aliasing.

## Goal

After cutover:

1. Stokowski restart fully recovers without parsing Linear comments. The dashboard accurately reflects in-flight tickets immediately on startup.
2. No ticket sits in Rework as a Linear state. Reworks are signaled by a `needs-rework` label on tickets still in `In Progress`.
3. Per-gate `max_rework` budgets are independent — 3 reworks at Awaiting CI does not consume Human Review's budget.
4. `poll-stuck-rework.py` is deleted. `poll-ci-status.py` and `poll-pr-conflicts.py` apply labels + post structured `stokowski:rework-trigger` markers instead of moving Linear state.
5. The 15-min stale-In-Progress rescue in `poll-ci-status.py` is deleted (orchestrator owns the transition on next tick based on durable state, not agent compliance).

## Non-goals

- Not consolidating `poll-ci-status` + `poll-pr-conflicts` into a single reconcile loop. Deferred.
- Not changing the `implement → await_ci_and_review → review_implementation → done` state machine shape. The Human Review gate stays.
- Not changing the slot/pool accounting model. Pool stays in-process.
- Not replacing Linear comments as audit history. `stokowski:state` and `stokowski:gate` markers continue to be posted; they are no longer parsed for recovery except during the one-time migration seed.
- Not auto-merging PRs. Manual merge stays.
- Not absorbing merge-conflict detection into the orchestrator's tick. Pollers stay separate processes.

## Deferred follow-ups (not in this commit)

- Orchestrator-driven In Progress → Awaiting CI transition when worker reports PR pushed. Retires another rescue path. Track in a follow-up Linear issue once durable state is stable.
- Selective rework (skip CI when Human Review asks for a 1-line rename). Risky; not worth it yet.
- Dropping the transient "Gate Approved" Linear state. Low-pain ceremony; revisit later.
- Consolidating `poll-ci-status` + `poll-pr-conflicts` into one reconcile loop.

## State partition

### Durable (SQLite-backed)

| Field | Why |
|---|---|
| `internal_state: issue_id → state_name` | Internal state machine state. Awaiting CI maps to multiple possible internal states across workflow configs. |
| `pending_gate: issue_id → gate_state_name` | The whole reason we are here. Today's rebuild silently loses Rework tickets. |
| `gate_runs: (issue_id, gate_state) → run_number` | Drives `max_rework`. Today's process-memory reset re-grants budget on restart. Now keyed per-gate so HR and Awaiting CI budgets are independent. |
| `last_session_id: issue_id → claude_session_id` | Enables `--resume` so the agent keeps prior reasoning across reworks. Losing this on restart is the main reason rework iterations regress. |
| `last_completed_at: issue_id → datetime` | Used for retry backoff calculation. |
| `last_rework_reason / detector / at` | Context for the next dispatch's prompt template: "you are reworking because of ci_fail / merge_conflict / reviewer_request_changes / etc." |

### Ephemeral (process memory only)

`running`, `_tasks`, `_child_pids`, `claimed`, `_slot_held`, `ConcurrencyPool`, `retry_attempts`, `completed`, `_last_issues`, `_queued`. These reflect *this process's* live work. Persisting them would lie after a restart.

## Schema

SQLite at `~/.local/share/stokowski/state.db`. Single connection per orchestrator process, WAL mode, single writer.

```sql
CREATE TABLE issue_state (
  issue_id              TEXT PRIMARY KEY,         -- Linear's stable ID
  issue_identifier      TEXT NOT NULL,            -- SYN-1234 (denormalized for ops)
  project_name          TEXT NOT NULL,            -- workflow.yaml project name
  internal_state        TEXT,                     -- 'implement', 'await_ci_and_review', etc.
  pending_gate          TEXT,                     -- gate state name if parked at a gate; NULL otherwise
  last_session_id       TEXT,                     -- Claude session_id for --resume
  last_completed_at     TEXT,                     -- ISO 8601
  last_rework_reason    TEXT,                     -- 'ci_fail' | 'reviewer_request_changes' | 'reviewer_approve_with_minors' | 'merge_conflict' | 'awaiting_ci_timeout' | NULL
  last_rework_detector  TEXT,                     -- 'poll-ci-status' | 'poll-pr-conflicts' | NULL
  last_rework_at        TEXT,                     -- ISO 8601 | NULL
  created_at            TEXT NOT NULL,
  updated_at            TEXT NOT NULL
);

CREATE TABLE gate_runs (
  issue_id    TEXT NOT NULL,
  gate_state  TEXT NOT NULL,
  run         INTEGER NOT NULL DEFAULT 1,
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (issue_id, gate_state),
  FOREIGN KEY (issue_id) REFERENCES issue_state(issue_id)
);

CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
INSERT INTO schema_version VALUES (1);
```

**Retention:** Terminal tickets stay in `issue_state` forever (rows are ~200 bytes; thousands of tickets = MB territory). Provides audit history and prevents re-entry races.

## Authority model

| Surface | Authoritative for |
|---|---|
| **Linear** | Ticket state. Labels (`symphony`, `needs-rework`, `merge-conflict`). |
| **SQLite** | Internal state-machine state, pending_gate, per-gate run counters, last_session_id, last_completed_at, last_rework_* context. |
| **Process memory** | Live workers (`running`, `_tasks`), claimed set, slot accounting, retry timers. |

### Reconcile rule (startup + every tick)

**Inputs:** (a) non-terminal Linear tickets in the project, and (b) SQLite rows with `pending_gate IS NOT NULL` (catches tickets that went terminal externally without going through `_transition`).

For each ticket in the union:

1. Read Linear state + labels.
2. Read SQLite row by `issue_id`.
3. If SQLite row missing → new to us. Set `internal_state := entry state`, `pending_gate := NULL`. Insert row.
4. If Linear state is terminal but SQLite has `pending_gate IS NOT NULL` (externally-terminalized):
   - Clear `pending_gate` and `internal_state` in SQLite. Log.
5. If SQLite `pending_gate` and Linear state disagree on which gate (or whether we are at a gate):
   - Re-read Linear once to rule out transient races.
   - If still divergent: **Linear wins.** Update SQLite to match. Log the divergence.
6. If Linear has `needs-rework` label AND ticket is In Progress:
   - Read most recent `stokowski:rework-trigger` comment for reason.
   - Persist `last_rework_reason / detector / at`.
   - Bump `gate_runs(issue_id, pending_gate)`. If `run > max_rework`: escalate (move to Human Review + `rework-escalated` label) and remove `needs-rework`.
   - Otherwise: dispatch implement worker with reason in prompt context. Remove `needs-rework` label so the trigger is single-shot.
7. Else: SQLite is authoritative; orchestrator resumes normal dispatch.

### Write order — durable before observable

Every transition:

1. `BEGIN` SQLite transaction
2. Update `issue_state` + `gate_runs` rows
3. `COMMIT`
4. Post Linear comment (audit marker)
5. Move Linear state OR apply/remove label

A crash between (3) and (4): restart re-posts the comment idempotently (comments are checked for presence before re-post). A crash between (4) and (5): reconcile in step 4 above catches and applies the Linear-side change.

## Touchpoints

### New module: `stokowski/storage.py`

~200 lines. Owns SQLite connection + schema/migrations + CRUD:

```python
class StateStore:
    def __init__(self, db_path: Path) -> None
    def close(self) -> None

    # Reads
    def get_issue(self, issue_id: str) -> IssueState | None
    def list_active(self, project_name: str) -> list[IssueState]
    def get_run(self, issue_id: str, gate_state: str) -> int

    # Writes (each a single transaction)
    def upsert_issue(self, ...) -> None
    def set_pending_gate(self, issue_id: str, gate_state: str | None) -> None
    def set_internal_state(self, issue_id: str, state: str | None) -> None
    def bump_run(self, issue_id: str, gate_state: str) -> int
    def set_session_id(self, issue_id: str, session_id: str | None) -> None
    def mark_completed(self, issue_id: str, when: datetime) -> None
    def set_rework_context(self, issue_id: str, reason: str, detector: str, when: datetime) -> None
    def clear_rework_context(self, issue_id: str) -> None
```

`sqlite3` from stdlib, accessed from asyncio via `loop.run_in_executor` (or `aiosqlite` if simpler). Single connection acquired in `__init__`, closed in `close`.

### `stokowski/orchestrator.py`

| Method | Change |
|---|---|
| `__init__` | Construct `self.store = StateStore(...)`. Keep in-memory dicts as read caches populated from the store. |
| `start` / `stop` | Open/close the store. Replace `_rebuild_gates_from_linear()` call with `_reconcile_from_storage()` (new, ~40 lines). |
| `_resolve_current_state` | First read from `store.get_issue(id)`. Comment-parsing only on missing row (new ticket). |
| `_enter_gate` | Write `set_pending_gate` + `set_internal_state` + (if first time at this gate) `bump_run`, all in one transaction. Then post Linear comment + move Linear state. |
| `_transition` | For state moves: `set_internal_state`. For terminal: clear `pending_gate` + `internal_state`. |
| `_handle_gate_responses` — rework path | **Biggest change.** Read gate context from store. Bump run counter. Apply `needs-rework` label + leave ticket in In Progress (do NOT move to Rework Linear state). |
| `_dispatch` / `_run_worker` | Read `last_session_id` from store. Persist new session_id when worker reports it. |
| After worker exits | `store.mark_completed`. |

### Methods retired

- `_rebuild_gates_from_linear` — entire method deleted.
- `_evict_terminal_gates` — replaced by reconcile-on-tick which handles terminal states naturally.

### Lifecycle prompt template

`prompts/implement.md` (or the lifecycle section assembled in `prompt.py`) receives a new variable `rework_reason` (one of the enum values or `null`). The template branches on this to give the agent appropriately-scoped context. Default rendering when not a rework: unchanged from today.

## Poller changes (in `~/dev/synced/sport/synced-sport/.claude/scripts/`)

### New poller contract

- Pollers never write to SQLite (single-writer invariant: only the orchestrator writes).
- Pollers never change Linear state (only the orchestrator does, on rework decision).
- On detecting a rework trigger:
  1. Apply `needs-rework` label to the Linear ticket.
  2. Post `<!-- stokowski:rework-trigger {"reason": "...", "detector": "...", "pr_number": N} -->` marker.

### `poll-ci-status.py`

| Before | After |
|---|---|
| CI fail → move to Rework state + comment | Apply `needs-rework` + post rework-trigger with `reason: ci_fail` |
| Reviewer REQUEST_CHANGES → Rework | Apply `needs-rework` + post rework-trigger with `reason: reviewer_request_changes` |
| Reviewer APPROVE with unresolved minors/importants → Rework | Apply `needs-rework` + post rework-trigger with `reason: reviewer_approve_with_minors` |
| 60-min stale Awaiting CI → Rework | Apply `needs-rework` + post rework-trigger with `reason: awaiting_ci_timeout` |
| 15-min stale In Progress rescue (with PR) → Awaiting CI | **Retired.** Orchestrator owns this transition based on durable state on next tick. |
| Ghost closed PR resolution → Done / Cancelled | **Unchanged.** Real terminal transitions. |

### `poll-pr-conflicts.py`

| Before | After |
|---|---|
| Detect CONFLICTING → add `merge-conflict` label + move {HR, Gate Approved, Awaiting CI} → Rework + one-time `<!-- poll-pr-conflicts -->` marker | Add `merge-conflict` AND `needs-rework` labels. Do NOT move Linear state. Post `<!-- stokowski:rework-trigger {"reason": "merge_conflict", "detector": "poll-pr-conflicts", "pr_number": N} -->`. |
| MERGEABLE → remove `merge-conflict` label | Remove `merge-conflict` label only (do not clear `needs-rework`; orchestrator clears it on dispatch pickup) |

### `poll-stuck-rework.py`

Deleted entirely. Disable + remove the systemd timer + service.

## Worktree mechanics

```
cd ~/dev/tools/stokowski

# 1. Commit dashboard WIP as a clean checkpoint
git add stokowski/linear.py stokowski/models.py stokowski/web.py
git commit -m "feat(dashboard): activity feed events + PR chips on agent rows"

# 2. Create the worktree at that commit
git worktree add ../stokowski-durable-state -b feature/durable-state HEAD
```

After this, `feature/durable-state` starts from current HEAD + dashboard WIP. Production stokowski continues running from `~/dev/tools/stokowski` unchanged. Iteration on durable state happens in `~/dev/tools/stokowski-durable-state`.

## Acceptance criteria

Implementation complete when ALL of these hold:

```json
{
  "criteria": [
    {
      "id": "AC1",
      "description": "stokowski/storage.py exists with StateStore class covering the public API in §Touchpoints",
      "verified": false
    },
    {
      "id": "AC2",
      "description": "tests/test_storage.py passes — covers CRUD, schema migration, per-gate run counter independence",
      "verified": false
    },
    {
      "id": "AC3",
      "description": "tests/test_reconcile.py passes — covers each branch of the §Reconcile rule decision table",
      "verified": false
    },
    {
      "id": "AC4",
      "description": "tests/test_rework_label_flow.py passes — covers needs-rework detection, reason extraction, dispatch with reason in prompt, label removal, double-dispatch prevention",
      "verified": false
    },
    {
      "id": "AC5",
      "description": "_rebuild_gates_from_linear and _evict_terminal_gates are deleted from stokowski/orchestrator.py",
      "verified": false
    },
    {
      "id": "AC6",
      "description": "Orchestrator startup against an empty state.db seeds rows for every non-terminal Linear ticket and logs a count of seeded rows. Subsequent startups skip the seed and log a divergence count instead",
      "verified": false
    },
    {
      "id": "AC7",
      "description": "Dry-run against production Linear (STOKOWSKI_DRY_RUN=1) produces a state.db whose row count and pending_gate distribution match production's current in-flight set as reported by /api/v1/state",
      "verified": false
    },
    {
      "id": "AC8",
      "description": "poll-ci-status.py: applies needs-rework label + posts stokowski:rework-trigger marker on CI fail / REQUEST_CHANGES / APPROVE-with-unresolved / 60-min stale; does NOT move Linear state to Rework on any of these; 15-min stale-In-Progress rescue path is removed",
      "verified": false
    },
    {
      "id": "AC9",
      "description": "poll-pr-conflicts.py: applies both needs-rework and merge-conflict labels on CONFLICTING; does NOT move Linear state to Rework; posts stokowski:rework-trigger marker with reason=merge_conflict",
      "verified": false
    },
    {
      "id": "AC10",
      "description": "poll-stuck-rework.py and test_poll_stuck_rework.py are deleted from synced-sport/.claude/scripts/. The systemd user units at ~/.config/systemd/user/poll-stuck-rework.{service,timer} are stopped, disabled, and removed (this is host-local; not in the repo)",
      "verified": false
    },
    {
      "id": "AC11",
      "description": "Lifecycle prompt template accepts a rework_reason variable; agent dispatch passes the SQLite-stored reason when present",
      "verified": false
    }
  ]
}
```

## Smoke test + cutover

**Smoke (15-30 min):**

1. `pip install -e .` in the worktree; `python -c "from stokowski.storage import StateStore"` to catch wiring errors.
2. Run pytest: `pytest tests/test_storage.py tests/test_reconcile.py tests/test_rework_label_flow.py`. All pass.
3. `STOKOWSKI_DRY_RUN=1 python -m stokowski --port 7879` for one tick (~30s). Inspect `~/.local/share/stokowski/state.db` and dry-run log.
4. Diff dry-run's `list_active` output vs production's `/api/v1/state` running+gates. Row count matches; any extra rows in SQLite are the Rework-rebuild bug's missing tickets — that's a win.

**Cutover (5 min):**

```
systemctl --user stop stokowski

# Edit ~/.config/systemd/user/stokowski.service:
#   WorkingDirectory=%h/dev/tools/stokowski-durable-state
# (or symlink swap if preferred)

systemctl --user daemon-reload
systemctl --user start stokowski
journalctl --user -u stokowski -f
```

**First-tick expectations:**

- `reconcile: seeded N tickets from Linear` on first start.
- Subsequent ticks: `reconcile: 0 divergences` (or a small number if humans moved tickets during the swap).
- Next CI signal causes a `needs-rework` label application, not a Rework state move.

**Rollback if anything looks off:**

```
systemctl --user stop stokowski
# Revert the unit's WorkingDirectory edit
systemctl --user daemon-reload
systemctl --user start stokowski
```

The state.db can stay. If we re-cutover later, it picks up where it left off.

## Effort estimate

| Step | Time |
|---|---|
| `storage.py` module — schema, migrations, CRUD, tests | 2-3h |
| Orchestrator integration — reconcile loop, replace rebuild path, rework-pickup | 2-3h |
| Poller updates — adapt poll-ci-status + poll-pr-conflicts, delete poll-stuck-rework | 1h |
| Test suite — storage + reconcile + rework flow | 1-2h |
| Migration smoke + cutover | 1h |
| **Total** | **7-10h** (half-day to full-day) |
