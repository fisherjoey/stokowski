"""Shared test helpers — fake LinearClient and an in-memory Orchestrator builder."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from stokowski.config import (
    AgentConfig,
    ClaudeConfig,
    HooksConfig,
    LinearStatesConfig,
    PollingConfig,
    PromptsConfig,
    ProjectConfig,
    ServerConfig,
    ServiceConfig,
    StateConfig,
    TrackerConfig,
    WorkflowDefinition,
    WorkspaceConfig,
)
from stokowski.models import Issue
from stokowski.orchestrator import Orchestrator
from stokowski.storage import StateStore
from stokowski.tracking import make_rework_trigger_comment


# ── Fake Linear client ───────────────────────────────────────────────────────

@dataclass
class FakeLinearClient:
    """In-memory stand-in for LinearClient.

    Holds a list of Issues + per-issue comments + per-issue labels, records
    every mutation so tests can assert on what the orchestrator did, and lets
    tests mutate Linear state directly (simulating human moves or poller
    label applications).
    """
    issues: dict[str, Issue] = field(default_factory=dict)
    comments: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    label_writes: list[tuple[str, str, str]] = field(default_factory=list)  # (op, issue_id, label)
    state_writes: list[tuple[str, str]] = field(default_factory=list)  # (issue_id, new_state)
    comment_writes: list[tuple[str, str]] = field(default_factory=list)  # (issue_id, body)

    def seed(
        self,
        issue_id: str,
        identifier: str,
        state: str,
        labels: list[str] | None = None,
        title: str = "",
        priority: int | None = None,
    ) -> Issue:
        issue = Issue(
            id=issue_id,
            identifier=identifier,
            title=title or identifier,
            state=state,
            labels=list(labels or []),
            priority=priority,
        )
        self.issues[issue_id] = issue
        self.comments.setdefault(issue_id, [])
        return issue

    def add_comment(self, issue_id: str, body: str, created_at: datetime | None = None):
        when = (created_at or datetime.now(timezone.utc)).isoformat()
        self.comments.setdefault(issue_id, []).append(
            {"id": f"c{len(self.comments[issue_id])}", "body": body, "createdAt": when}
        )

    def set_label(self, issue_id: str, label: str, present: bool = True):
        issue = self.issues[issue_id]
        labels = list(issue.labels)
        if present and label not in labels:
            labels.append(label)
        if not present and label in labels:
            labels.remove(label)
        issue.labels = labels

    # ── LinearClient surface ─────────────────────────────────────────────

    async def close(self):
        pass

    async def fetch_candidate_issues(self, project_slug, states):
        wanted = {s.strip().lower() for s in states}
        return [i for i in self.issues.values() if i.state.strip().lower() in wanted]

    async def fetch_issue_states_by_ids(self, ids):
        return {i: self.issues[i].state for i in ids if i in self.issues}

    async def fetch_issues_by_states(self, project_slug, states):
        return await self.fetch_candidate_issues(project_slug, states)

    async def fetch_comments(self, issue_id):
        return list(self.comments.get(issue_id, []))

    async def post_comment(self, issue_id, body) -> bool:
        self.add_comment(issue_id, body)
        self.comment_writes.append((issue_id, body))
        return True

    async def update_issue_state(self, issue_id, state_name) -> bool:
        if issue_id in self.issues:
            self.issues[issue_id].state = state_name
            self.state_writes.append((issue_id, state_name))
            return True
        return False

    async def add_label_by_name(self, issue_id, label_name) -> bool:
        self.set_label(issue_id, label_name, present=True)
        self.label_writes.append(("add", issue_id, label_name))
        return True

    async def remove_label_by_name(self, issue_id, label_name) -> bool:
        self.set_label(issue_id, label_name, present=False)
        self.label_writes.append(("remove", issue_id, label_name))
        return True


# ── Minimal Orchestrator construction ────────────────────────────────────────

def make_test_config(tmp_path: Path) -> ServiceConfig:
    """Build a minimal but realistic ServiceConfig mirroring synced-sport."""
    states = {
        "implement": StateConfig(
            name="implement",
            type="agent",
            linear_state="active",
            transitions={"complete": "await_ci_and_review"},
        ),
        "await_ci_and_review": StateConfig(
            name="await_ci_and_review",
            type="gate",
            linear_state="awaiting_ci",
            rework_to="implement",
            max_rework=3,
            transitions={"approve": "review_implementation"},
        ),
        "review_implementation": StateConfig(
            name="review_implementation",
            type="gate",
            linear_state="review",
            rework_to="implement",
            max_rework=2,
            transitions={"approve": "done"},
        ),
        "done": StateConfig(
            name="done",
            type="terminal",
            linear_state="terminal",
            transitions={},
        ),
    }
    project = ProjectConfig(
        name="testproj",
        tracker=TrackerConfig(
            kind="linear",
            endpoint="http://linear.test",
            api_key="test",
            project_slug="proj-123",
        ),
        workspace=WorkspaceConfig(root=str(tmp_path / "ws")),
        hooks=HooksConfig(),
        prompts=PromptsConfig(),
        states=states,
        linear_states=LinearStatesConfig(),
        claude=ClaudeConfig(),
        workflow_dir=tmp_path,
    )
    return ServiceConfig(
        tracker=project.tracker,
        polling=PollingConfig(),
        workspace=project.workspace,
        hooks=project.hooks,
        claude=project.claude,
        agent=AgentConfig(),
        server=ServerConfig(),
        linear_states=project.linear_states,
        prompts=project.prompts,
        states=states,
        projects=[project],
        workflow_dir=tmp_path,
    )


def make_test_orchestrator(
    tmp_path: Path,
    store: StateStore | None = None,
    fake_linear: FakeLinearClient | None = None,
) -> Orchestrator:
    """Construct an Orchestrator wired to in-memory deps, bypassing workflow.yaml."""
    cfg = make_test_config(tmp_path)
    orch = Orchestrator(
        workflow_path=tmp_path / "workflow.yaml",
        project_name="testproj",
        store=store or StateStore(tmp_path / "state.db"),
    )
    orch.workflow = WorkflowDefinition(config=cfg, prompt_template="")
    orch.project = cfg.projects[0]
    orch._linear = fake_linear or FakeLinearClient()
    return orch


def trigger_marker(reason: str, detector: str, pr_number: int | None = None) -> str:
    return make_rework_trigger_comment(reason, detector, pr_number)
