"""Core domain models."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime


def _parse_datetime(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


@dataclass
class BlockerRef:
    id: str | None = None
    identifier: str | None = None
    state: str | None = None


@dataclass
class Issue:
    id: str
    identifier: str
    title: str
    description: str | None = None
    priority: int | None = None
    state: str = ""
    branch_name: str | None = None
    url: str | None = None
    labels: list[str] = field(default_factory=list)
    blocked_by: list[BlockerRef] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_cache_row(cls, row: dict) -> Issue:
        """Construct an Issue from a cache DB row dict (as returned by CacheReader)."""
        raw_labels = row.get("labels_json") or "[]"
        labels = json.loads(raw_labels) if isinstance(raw_labels, str) else raw_labels
        return cls(
            id=row["id"],
            identifier=row["identifier"],
            title=row["title"],
            state=row.get("state_name") or "",
            labels=[lbl.lower() for lbl in labels],
            updated_at=_parse_datetime(row.get("updated_at")),
        )


@dataclass
class RunAttempt:
    issue_id: str
    issue_identifier: str
    attempt: int | None = None
    workspace_path: str = ""
    started_at: datetime | None = None
    status: str = "pending"
    session_id: str | None = None
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    turn_count: int = 0
    last_event_at: datetime | None = None
    last_event: str | None = None
    last_message: str = ""
    completed_at: datetime | None = None
    state_name: str | None = None       # current internal state machine state
    # HEAD SHA of the agent's workspace branch when the worker was spawned.
    # Used by _on_worker_exit to detect whether the agent actually committed:
    # if the post-exit HEAD == this value, the agent ran but didn't make any
    # commits — we treat that as "no real work happened" and stay in the
    # current state instead of transitioning forward. Keeps Rework "sticky".
    head_sha_at_dispatch: str | None = None


@dataclass
class RetryEntry:
    issue_id: str
    identifier: str
    attempt: int = 1
    due_at_ms: float = 0
    error: str | None = None
