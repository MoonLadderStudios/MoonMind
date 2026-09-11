"""Hermetic integration coverage for the issue-lifecycle surface (#4183).

Exercises the real ``/api/v1/executions/issue-lifecycle`` HTTP boundary
(real FastAPI router + real recovery-surface policy) with a fake GitHub
write boundary, so no network or credentials are required:

* pending synchronization during a GitHub outage is reported as
  pending/unknown, never as a false successful remote update;
* the cancellation/hold flow records an auditable decision, publishes it
  through the authorized GitHub write boundary, and keeps admission
  blocked until an explicit resolution;
* a competing-PR submission is rejected server-side (conflicting_pr);
* prior failed-attempt history is preserved across a later launch.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers import issue_lifecycle as issue_lifecycle_router
from api_service.auth_providers import get_current_user

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

REPO = "o/r"
ISSUE_NUMBER = 4183


class _FakeGithubService:
    """Hermetic stand-in for the authorized GitHub write boundary."""

    def __init__(self, result: dict | None = None) -> None:
        self.calls: list[dict] = []
        self.result = result or {
            "ok": True,
            "reasonCode": "created",
            "summary": "Created issue comment 101.",
            "commentId": 101,
        }

    async def create_issue_comment(self, *, repo: str, issue_number: int, body: str) -> dict:
        self.calls.append({"repo": repo, "issue_number": issue_number, "body": body})
        return dict(self.result)


def _issue(*labels: str) -> dict:
    return {
        "number": ISSUE_NUMBER,
        "repository": REPO,
        "state": "open",
        "html_url": f"https://github.com/{REPO}/issues/{ISSUE_NUMBER}",
        "labels": [{"name": label} for label in labels],
    }


def _client(user: SimpleNamespace, github_service: _FakeGithubService) -> TestClient:
    app = FastAPI()
    app.include_router(issue_lifecycle_router.router)
    app.dependency_overrides[get_current_user()] = lambda: user
    app.dependency_overrides[issue_lifecycle_router.get_github_service] = lambda: github_service
    return TestClient(app, raise_server_exceptions=False)


def _admin() -> SimpleNamespace:
    return SimpleNamespace(id="admin-1", email="admin@example.com", is_superuser=True)


def _regular() -> SimpleNamespace:
    return SimpleNamespace(id="user-1", email="user@example.com", is_superuser=False)


def test_pending_synchronization_during_github_outage_is_unknown_not_success() -> None:
    """Outage honesty across the real context + submit boundary."""
    fake = _FakeGithubService()
    client = _client(_regular(), fake)

    context = client.post(
        "/api/v1/executions/issue-lifecycle/context",
        json={
            "repository": REPO,
            "issue_number": ISSUE_NUMBER,
            "issue": _issue("status: in-progress"),
            "sync_state": {"github_unavailable": True},
        },
    )
    assert context.status_code == 200, context.text
    projection = context.json()
    assert projection["context"]["sync_status"] == "unknown"
    assert projection["recovery_availability"]["reason"] == "github_unavailable"
    assert projection["recovery_availability"]["available"] is False

    # A lost publication response stays pending: honestly unknown, never a
    # false successful remote update.
    flaky = _FakeGithubService(
        result={"ok": False, "reasonCode": "outcome_unknown", "summary": "lost response"}
    )
    outage_client = _client(_regular(), flaky)
    submitted = outage_client.post(
        "/api/v1/executions/issue-lifecycle/actions/submit",
        json={
            "action": "hold_processing",
            "repository": REPO,
            "issue_number": ISSUE_NUMBER,
            "request": {"idempotency_key": "int-outage-hold-1"},
            "live_issue": _issue("status: in-progress"),
            "reason": "operator pause during outage",
        },
    )
    assert submitted.status_code == 200, submitted.text
    payload = submitted.json()
    assert payload["allowed"] is True
    assert payload["publication"]["published"] is False
    assert payload["publication"]["status"] == "pending"
    assert payload["publication"]["reason"] == "outcome_unknown"


def test_cancellation_hold_flow_publishes_auditable_decision_and_stays_blocked() -> None:
    """Hold records identity/reason, publishes via GitHub, keeps the block."""
    fake = _FakeGithubService()
    client = _client(_regular(), fake)

    submitted = client.post(
        "/api/v1/executions/issue-lifecycle/actions/submit",
        json={
            "action": "hold_processing",
            "repository": REPO,
            "issue_number": ISSUE_NUMBER,
            "request": {"idempotency_key": "int-hold-1"},
            "live_issue": _issue("status: in-progress"),
            "reason": "operator investigating",
        },
    )
    assert submitted.status_code == 200, submitted.text
    payload = submitted.json()
    assert payload["allowed"] is True
    assert payload["decision"]["operator"] == "user-1"
    assert payload["decision"]["reason"] == "operator investigating"
    assert payload["decision"]["hold_released"] is False
    assert payload["publication"]["status"] == "published"
    assert payload["publication"]["commentId"] == 101
    # The published GitHub comment carries the auditable decision so another
    # deployment can observe it without a second result store.
    assert len(fake.calls) == 1
    assert "Next action" in fake.calls[0]["body"]
    assert "user-1" in fake.calls[0]["body"]

    # The hold keeps recovery blocked: acknowledgment-equivalent state does
    # not release admission until an explicit resolution.
    held = client.post(
        "/api/v1/executions/issue-lifecycle/context",
        json={
            "repository": REPO,
            "issue_number": ISSUE_NUMBER,
            "issue": _issue("status: needs-attention"),
            "operator_hold": {"active": True, "acknowledged": True, "reason": "operator investigating"},
        },
    )
    assert held.status_code == 200, held.text
    held_payload = held.json()
    assert held_payload["recovery_availability"]["available"] is False
    assert held_payload["recovery_availability"]["reason"] == "operator_hold"


def test_competing_pr_conflict_is_rejected_by_the_server() -> None:
    """Stale/conflicting submissions fail server-side, not just in the UI."""
    fake = _FakeGithubService()
    client = _client(_admin(), fake)

    submitted = client.post(
        "/api/v1/executions/issue-lifecycle/actions/submit",
        json={
            "action": "continue_work",
            "repository": REPO,
            "issue_number": ISSUE_NUMBER,
            "request": {"idempotency_key": "int-conflict-1", "pr_url": f"https://github.com/{REPO}/pull/8"},
            "live_issue": _issue("status: recovery-needed"),
            "live_attempt": {"attempt_id": "att_" + "a" * 24},
            "live_pr": {"pr_url": f"https://github.com/{REPO}/pull/7"},
            "stop_proof": {"writers_stopped": True},
        },
    )
    assert submitted.status_code == 200, submitted.text
    payload = submitted.json()
    assert payload["allowed"] is False
    assert payload["verdict"]["code"] == "conflicting_pr"
    # Rejected submissions publish nothing.
    assert fake.calls == []


def test_prior_failed_attempt_history_survives_a_later_launch() -> None:
    """A later launch does not erase prior failure evidence."""
    fake = _FakeGithubService()
    client = _client(_admin(), fake)

    first = client.post(
        "/api/v1/executions/issue-lifecycle/context",
        json={
            "repository": REPO,
            "issue_number": ISSUE_NUMBER,
            "issue": _issue("status: recovery-needed"),
            "current_attempt": {
                "attempt_id": "att_" + "a" * 24,
                "deployment_id": "dep-origin",
                "remaining_requirements": ["finish repair"],
            },
            "predecessor_attempts": [{"attempt_id": "att_" + "0" * 24, "result": "failed"}],
            "preserved_pr": {
                "pr_url": f"https://github.com/{REPO}/pull/7",
                "head_sha": "b" * 40,
                "save_method": "pr_head_verified",
            },
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["context"]["prior_failure_count"] == 1

    later = client.post(
        "/api/v1/executions/issue-lifecycle/context",
        json={
            "repository": REPO,
            "issue_number": ISSUE_NUMBER,
            "issue": _issue("status: in-progress"),
            "current_attempt": {
                "attempt_id": "att_" + "c" * 24,
                "deployment_id": "dep-origin",
                "remaining_requirements": ["finish repair"],
            },
            "predecessor_attempts": [
                {"attempt_id": "att_" + "0" * 24, "result": "failed"},
                {"attempt_id": "att_" + "a" * 24, "result": "failed"},
            ],
            "preserved_pr": {
                "pr_url": f"https://github.com/{REPO}/pull/7",
                "head_sha": "b" * 40,
                "save_method": "pr_head_verified",
            },
        },
    )
    assert later.status_code == 200, later.text
    later_payload = later.json()
    assert later_payload["context"]["prior_failure_count"] == 2
    assert len(later_payload["context"]["failure_history"]) == 2
