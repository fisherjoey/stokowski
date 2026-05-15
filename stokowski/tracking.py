"""State machine tracking via structured Linear comments."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("stokowski.tracking")

STATE_PATTERN = re.compile(r"<!-- stokowski:state ({.*?}) -->")
GATE_PATTERN = re.compile(r"<!-- stokowski:gate ({.*?}) -->")
REWORK_TRIGGER_PATTERN = re.compile(r"<!-- stokowski:rework-trigger ({.*?}) -->")


def make_state_comment(state: str, run: int = 1) -> str:
    """Build a structured state-tracking comment."""
    payload = {
        "state": state,
        "run": run,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    machine = f"<!-- stokowski:state {json.dumps(payload)} -->"
    human = f"**[Stokowski]** Entering state: **{state}** (run {run})"
    return f"{machine}\n\n{human}"


def make_gate_comment(
    state: str,
    status: str,
    prompt: str = "",
    rework_to: str | None = None,
    run: int = 1,
) -> str:
    """Build a structured gate-tracking comment."""
    payload: dict[str, Any] = {
        "state": state,
        "status": status,
        "run": run,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if rework_to:
        payload["rework_to"] = rework_to

    machine = f"<!-- stokowski:gate {json.dumps(payload)} -->"

    if status == "waiting":
        human = f"**[Stokowski]** Awaiting human review: **{state}**"
        if prompt:
            human += f" — {prompt}"
    elif status == "approved":
        human = f"**[Stokowski]** Gate **{state}** approved."
    elif status == "rework":
        human = (
            f"**[Stokowski]** Rework requested at **{state}**. "
            f"Returning to: **{rework_to}**"
        )
        if run > 1:
            human += f" (run {run})"
    elif status == "escalated":
        human = (
            f"**[Stokowski]** Max rework exceeded at **{state}**. "
            f"Escalating for human intervention."
        )
    else:
        human = f"**[Stokowski]** Gate **{state}** status: {status}"

    return f"{machine}\n\n{human}"


def parse_latest_tracking(comments: list[dict]) -> dict[str, Any] | None:
    """Parse comments (oldest-first) to find the latest state or gate tracking entry.

    Returns a dict with keys:
        - "type": "state" or "gate"
        - Plus all fields from the JSON payload

    Returns None if no tracking comments found.
    """
    latest: dict[str, Any] | None = None

    for comment in comments:
        body = comment.get("body", "")

        state_match = STATE_PATTERN.search(body)
        if state_match:
            try:
                data = json.loads(state_match.group(1))
                data["type"] = "state"
                latest = data
            except json.JSONDecodeError:
                pass

        gate_match = GATE_PATTERN.search(body)
        if gate_match:
            try:
                data = json.loads(gate_match.group(1))
                data["type"] = "gate"
                latest = data
            except json.JSONDecodeError:
                pass

    return latest


def parse_latest_rework_trigger(comments: list[dict]) -> dict[str, Any] | None:
    """Return the most-recent stokowski:rework-trigger payload, or None.

    Pollers (poll-ci-status, poll-pr-conflicts) post these markers when they
    apply the `needs-rework` label. The orchestrator reads the latest one on
    pickup to extract reason + detector for the dispatch prompt context.

    Payload shape: {"reason": str, "detector": str, "pr_number"?: int}.
    The returned dict is the parsed JSON unchanged.
    """
    latest: dict[str, Any] | None = None
    for comment in comments:
        body = comment.get("body", "")
        match = REWORK_TRIGGER_PATTERN.search(body)
        if match:
            try:
                latest = json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
    return latest


def make_rework_trigger_comment(
    reason: str,
    detector: str,
    pr_number: int | None = None,
    note: str | None = None,
) -> str:
    """Build a rework-trigger marker comment for pollers to post.

    Pollers in synced-sport use raw GraphQL and embed the marker directly;
    this helper exists so the marker format stays in lockstep with the
    parser and is easy to update in one place.
    """
    payload: dict[str, Any] = {
        "reason": reason,
        "detector": detector,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if pr_number is not None:
        payload["pr_number"] = pr_number
    machine = f"<!-- stokowski:rework-trigger {json.dumps(payload)} -->"
    human = note or (
        f"**[Stokowski]** Rework triggered: `{reason}` "
        f"(detected by `{detector}`)."
    )
    return f"{machine}\n\n{human}"


def get_last_tracking_timestamp(comments: list[dict]) -> str | None:
    """Find the timestamp of the latest tracking comment."""
    latest_ts: str | None = None

    for comment in comments:
        body = comment.get("body", "")
        for pattern in (STATE_PATTERN, GATE_PATTERN):
            match = pattern.search(body)
            if match:
                try:
                    data = json.loads(match.group(1))
                    ts = data.get("timestamp")
                    if ts:
                        latest_ts = ts
                except json.JSONDecodeError:
                    pass

    return latest_ts


def get_comments_since(
    comments: list[dict], since_timestamp: str | None
) -> list[dict]:
    """Filter comments to only those after a given timestamp.

    Returns comments that are NOT stokowski tracking comments and
    were created after the given timestamp.
    """
    result = []
    since_dt = None
    if since_timestamp:
        try:
            since_dt = datetime.fromisoformat(
                since_timestamp.replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            pass

    for comment in comments:
        body = comment.get("body", "")
        if "<!-- stokowski:" in body:
            continue

        if since_dt:
            created = comment.get("createdAt", "")
            if created:
                try:
                    created_dt = datetime.fromisoformat(
                        created.replace("Z", "+00:00")
                    )
                    if created_dt <= since_dt:
                        continue
                except (ValueError, AttributeError):
                    pass

        result.append(comment)

    return result
