#!/usr/bin/env python3
"""
Poll open Stokowski PRs for merge conflicts and reflect status in Linear.

For each open PR labelled `symphony`:
  - If `mergeable == CONFLICTING`:
      * Add the `merge-conflict` AND `needs-rework` labels to the linked
        Linear issue.
      * Post a `stokowski:rework-trigger` marker comment (once per PR) so
        the orchestrator picks up the rework with the correct reason on
        its next reconcile tick. Linear state is NOT changed — the
        orchestrator owns that move.
  - If `mergeable == MERGEABLE` and the issue currently has the
    `merge-conflict` label: remove it (the conflict was resolved). Leave
    `needs-rework` alone — the orchestrator clears that on pickup.
  - Anything else (UNKNOWN, in-progress check): skip — let the next poll re-check.

Linkage strategy: extract SYN-XXX from the PR title or body. Stokowski's
implement prompt and Linear's GitHub attachment system both produce PRs with
the issue identifier in the title or body, so this is reliable.

Idempotent: safe to run on a timer.

Env:
  LINEAR_API_KEY               required
  GH_TOKEN or `gh auth status` already authed
  REPO                         optional, defaults to SyncedTech/synced-sport
  LINEAR_TEAM_ID               optional, Linear team that owns the workflow labels
  LINEAR_LABEL_MERGE_CONFLICT  optional, ID of the merge-conflict label

Usage:
  stokowski-poll-pr-conflicts            # one shot (console script)
  stokowski-poll-pr-conflicts --verbose  # log every PR checked
  python -m stokowski.pollers.poll_pr_conflicts  # via python -m
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _pr_helpers

LINEAR_API_KEY = os.environ.get("LINEAR_API_KEY")
if not LINEAR_API_KEY:
    print("error: LINEAR_API_KEY not set", file=sys.stderr)
    sys.exit(2)

REPO = os.environ.get("REPO", "SyncedTech/synced-sport")
TEAM_ID = os.environ.get("LINEAR_TEAM_ID", "82bdad05-fcb3-4fc8-b873-49056aa672d3")
MERGE_CONFLICT_LABEL_ID = os.environ.get(
    "LINEAR_LABEL_MERGE_CONFLICT", "13d03f23-81f9-45f3-8506-386891ad8db3"
)
NEEDS_REWORK_LABEL_NAME = "needs-rework"
SYMPHONY_LABEL = "symphony"
COMMENT_MARKER = "<!-- poll-pr-conflicts -->"
REWORK_TRIGGER_MARKER_PREFIX = "<!-- stokowski:rework-trigger"

VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv

SYN_RE = re.compile(r"\bSYN-(\d+)\b", re.IGNORECASE)


def log(msg):
    if VERBOSE:
        print(msg, file=sys.stderr)


def collapse_mergeable(current, new):
    """Merge two mergeable states into a single ticket-level decision.

    CONFLICTING wins, UNKNOWN beats MERGEABLE, MERGEABLE only when every
    linked PR is mergeable. Used to aggregate per-PR states to the Linear
    ticket level so the merge-conflict label doesn't oscillate when one
    SYN-id has multiple linked PRs (see SYN-906).
    """
    if current is None:
        return new
    if "CONFLICTING" in (current, new):
        return "CONFLICTING"
    if "UNKNOWN" in (current, new):
        return "UNKNOWN"
    return "MERGEABLE"


def gql(query, variables=None):
    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={"Authorization": LINEAR_API_KEY, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.loads(r.read())
    if "errors" in body:
        raise RuntimeError(f"Linear API error: {body['errors']}")
    return body["data"]


def find_syn_id(pr):
    """Extract first SYN-NNN from title, then body, then branch name."""
    for source in (pr.get("title", ""), pr.get("body") or "", pr.get("headRefName", "")):
        m = SYN_RE.search(source)
        if m:
            return f"SYN-{m.group(1)}"
    return None


def fetch_issue(identifier):
    data = gql(
        """
        query($id: String!) {
          issue(id: $id) {
            id
            identifier
            state { name }
            labels { nodes { id name } }
          }
        }
        """,
        {"id": identifier},
    )
    return data["issue"]


def has_label(issue, label_id):
    return any(l["id"] == label_id for l in issue["labels"]["nodes"])


def add_label(issue, label_id):
    existing = [l["id"] for l in issue["labels"]["nodes"]]
    if label_id in existing:
        return False
    new_labels = existing + [label_id]
    gql(
        """
        mutation($id: String!, $labels: [String!]!) {
          issueUpdate(id: $id, input: { labelIds: $labels }) { success }
        }
        """,
        {"id": issue["id"], "labels": new_labels},
    )
    return True


def remove_label(issue, label_id):
    existing = [l["id"] for l in issue["labels"]["nodes"]]
    if label_id not in existing:
        return False
    new_labels = [l for l in existing if l != label_id]
    gql(
        """
        mutation($id: String!, $labels: [String!]!) {
          issueUpdate(id: $id, input: { labelIds: $labels }) { success }
        }
        """,
        {"id": issue["id"], "labels": new_labels},
    )
    return True


def has_marker_comment(issue_id):
    data = gql(
        """
        query($id: String!) {
          issue(id: $id) { comments(first: 50) { nodes { body } } }
        }
        """,
        {"id": issue_id},
    )
    return any(COMMENT_MARKER in c["body"] for c in data["issue"]["comments"]["nodes"])


def post_comment(issue_id, body):
    gql(
        """
        mutation($id: String!, $body: String!) {
          commentCreate(input: { issueId: $id, body: $body }) { success }
        }
        """,
        {"id": issue_id, "body": body},
    )


def fetch_needs_rework_label_id():
    """Look up the needs-rework label ID once per run.

    Linear's GraphQL doesn't let us update labels by name on issueUpdate —
    everything is keyed by ID — so we resolve the name here and cache it.
    """
    data = gql(
        """
        query($team: String!) {
          team(id: $team) {
            labels { nodes { id name } }
          }
        }
        """,
        {"team": TEAM_ID},
    )
    for l in (data.get("team", {}) or {}).get("labels", {}).get("nodes", []):
        if l.get("name", "").strip().lower() == NEEDS_REWORK_LABEL_NAME:
            return l["id"]
    return None


def post_rework_trigger(issue_id, reason, detector, pr_number):
    """Post the structured marker the orchestrator parses on reconcile."""
    import datetime
    payload = {
        "reason": reason,
        "detector": detector,
        "pr_number": pr_number,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    body = (
        f"<!-- stokowski:rework-trigger {json.dumps(payload)} -->\n\n"
        f"**[Stokowski]** Rework triggered: `{reason}` "
        f"(detected by `{detector}`, PR #{pr_number})."
    )
    post_comment(issue_id, body)


def has_trigger_marker_for_pr(issue_id, pr_number):
    """Check whether we already posted a rework-trigger for this PR.

    Trigger markers carry the PR number in their JSON payload; we treat
    one-per-PR as the idempotency unit (matches the prior per-PR label
    behaviour and avoids re-firing on every poll while the conflict
    persists).
    """
    data = gql(
        """
        query($id: String!) {
          issue(id: $id) { comments(first: 100) { nodes { body } } }
        }
        """,
        {"id": issue_id},
    )
    for c in data["issue"]["comments"]["nodes"]:
        body = c.get("body", "")
        if REWORK_TRIGGER_MARKER_PREFIX in body and f'"pr_number": {pr_number}' in body:
            return True
    return False


def aggregate_by_syn(prs):
    """Group PRs by SYN-id and collapse their mergeable states.

    The merge-conflict label and comment live on the Linear ticket, so when
    Linear's GitHub integration links two PRs to the same SYN-id, we must
    decide at the ticket level — not per-PR. Without this, iterating PRs in
    arbitrary order causes the label to flap between flagged and cleared
    every poll. (SYN-906.)

    Returns ({syn_id: {"mergeable": str, "conflicting_prs": [pr, ...]}},
             [pr_number, ...]) — the second value is PRs with no SYN-id.
    """
    by_syn = {}
    skipped_no_syn = []
    for pr in prs:
        syn_id = find_syn_id(pr)
        mergeable = pr.get("mergeable", "UNKNOWN")
        if not syn_id:
            skipped_no_syn.append(pr["number"])
            continue
        bucket = by_syn.setdefault(syn_id, {"mergeable": None, "conflicting_prs": []})
        bucket["mergeable"] = collapse_mergeable(bucket["mergeable"], mergeable)
        if mergeable == "CONFLICTING":
            bucket["conflicting_prs"].append(pr)
    return by_syn, skipped_no_syn


def main():
    prs = _pr_helpers.get_symphony_prs(REPO)
    log(f"found {len(prs)} open symphony PR(s) in {REPO}")

    for pr in prs:
        log(f"  PR #{pr['number']:>4} {pr.get('mergeable', 'UNKNOWN'):<12} "
            f"{find_syn_id(pr) or '(no SYN)':<10} {pr['title'][:60]}")

    by_syn, skipped_no_syn = aggregate_by_syn(prs)

    needs_rework_label_id = fetch_needs_rework_label_id()
    if needs_rework_label_id is None:
        print("warning: needs-rework label not found on team; cannot signal rework",
              file=sys.stderr)

    summary = {"flagged": [], "triggered": [], "cleared": [], "skipped_unknown": []}

    for syn_id, bucket in by_syn.items():
        mergeable = bucket["mergeable"]
        if mergeable == "UNKNOWN":
            summary["skipped_unknown"].append(syn_id)
            continue

        try:
            issue = fetch_issue(syn_id)
        except Exception as e:
            log(f"    ! failed to fetch {syn_id}: {e}")
            continue
        if issue is None:
            log(f"    ! {syn_id} not found in Linear")
            continue

        if mergeable == "CONFLICTING":
            conflicting = bucket["conflicting_prs"]
            newly_flagged = not has_label(issue, MERGE_CONFLICT_LABEL_ID)
            if newly_flagged:
                add_label(issue, MERGE_CONFLICT_LABEL_ID)
                summary["flagged"].append(syn_id)
                if not has_marker_comment(issue["id"]):
                    pr_links = ", ".join(f"[PR #{p['number']}]({p['url']})" for p in conflicting)
                    branch_word = "branches" if len(conflicting) > 1 else "branch"
                    post_comment(
                        issue["id"],
                        f"{COMMENT_MARKER}\n"
                        f"⚠️ **Merge conflict detected** in {pr_links}.\n\n"
                        f"The {branch_word} needs a rebase onto `develop` before it can merge.",
                    )
            # Apply needs-rework + post trigger marker (once per PR) on every
            # tick where the conflict persists and the orchestrator hasn't
            # picked it up yet. The label is the durable signal; the trigger
            # marker carries the reason/detector to feed the rework dispatch
            # prompt. Idempotent: orchestrator strips the label on pickup,
            # so this re-applies only after the orchestrator handed it back.
            if needs_rework_label_id is not None:
                primary_pr = conflicting[0]
                added_label = add_label(issue, needs_rework_label_id)
                already_triggered = has_trigger_marker_for_pr(
                    issue["id"], primary_pr["number"]
                )
                if added_label or not already_triggered:
                    if not already_triggered:
                        post_rework_trigger(
                            issue["id"],
                            reason="merge_conflict",
                            detector="poll-pr-conflicts",
                            pr_number=primary_pr["number"],
                        )
                    summary["triggered"].append(
                        f"{syn_id} (PR #{primary_pr['number']})"
                    )
        elif mergeable == "MERGEABLE":
            if has_label(issue, MERGE_CONFLICT_LABEL_ID):
                remove_label(issue, MERGE_CONFLICT_LABEL_ID)
                summary["cleared"].append(syn_id)

    if summary["flagged"]:
        print(f"flagged conflicts on: {', '.join(summary['flagged'])}")
    if summary["triggered"]:
        print(f"rework triggered: {', '.join(summary['triggered'])}")
    if summary["cleared"]:
        print(f"cleared resolved conflicts on: {', '.join(summary['cleared'])}")
    if VERBOSE:
        if skipped_no_syn:
            print(f"skipped (no SYN-id in title/body/branch): PR #{', #'.join(map(str, skipped_no_syn))}", file=sys.stderr)
        if summary["skipped_unknown"]:
            print(f"skipped (mergeable=UNKNOWN, will retry next poll): {', '.join(summary['skipped_unknown'])}", file=sys.stderr)


def cli():
    """Console script entry point — wraps main() with rate-limit guard."""
    try:
        from stokowski.pollers._rate_limit_guard import run_with_rate_limit_guard
    except ImportError:
        from _rate_limit_guard import run_with_rate_limit_guard
    run_with_rate_limit_guard(main)


if __name__ == "__main__":
    cli()
