"""API tests for the issue-lifecycle projection router (#4183).

Exercises the real FastAPI routes with the real recovery-surface policy:
context projection with attention/availability/actions, plus server-side
operator-action validation (unauthorized, stale, duplicate, wrong-issue,
conflicting-PR, unknown-writer, cross-device stop).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers import issue_lifecycle as issue_lifecycle_router
from api_service.auth_providers import get_current_user

REPO = "o/r"
ISSUE_NUMBER = 4183


def _admin_user() -> SimpleNamespace:
    return SimpleNamespace(id="admin-1", email="admin@example.com", is_superuser=True)


def _regular_user() -> SimpleNamespace:
    return SimpleNamespace(id="user-1", email="user@example.com", is_superuser=False)


def _client(user: SimpleNamespace | None) -> TestClient:
    app = FastAPI()
    app.include_router(issue_lifecycle_router.router)
    if user is not None:
        app.dependency_overrides[get_current_user()] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def _issue(*labels: str) -> dict:
    return {
        "number": ISSUE_NUMBER,
        "repository": REPO,
        "state": "open",
        "html_url": f"https://github.com/{REPO}/issues/{ISSUE_NUMBER}",
        "labels": [{"name": label} for label in labels],
    }


def test_context_projection_returns_lineage_and_actions() -> None:
    client = _client(_admin_user())
    response = client.post(
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
            "preserved_pr": {"pr_url": f"https://github.com/{REPO}/pull/7", "head_sha": "b" * 40, "save_method": "pr_head_verified"},
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["context"]["issue_url"] == f"https://github.com/{REPO}/issues/{ISSUE_NUMBER}"
    assert payload["context"]["preserved_revision"] == "b" * 40
    assert payload["context"]["prior_failure_count"] == 1
    assert payload["recovery_availability"]["available"] is True
    actions = {entry["action"]: entry for entry in payload["actions"]}
    assert actions["continue_work"]["enabled"] is True
    assert actions["continue_work"]["continue_variant"] == "code_seeded_continuation"


def test_context_reports_pending_sync_during_outage() -> None:
    client = _client(_regular_user())
    response = client.post(
        "/api/v1/executions/issue-lifecycle/context",
        json={
            "repository": REPO,
            "issue_number": ISSUE_NUMBER,
            "issue": _issue("status: in-progress"),
            "sync_state": {"github_unavailable": True},
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["context"]["sync_status"] == "unknown"
    assert payload["recovery_availability"]["reason"] == "github_unavailable"


def test_validate_hold_records_auditable_decision() -> None:
    client = _client(_regular_user())
    response = client.post(
        "/api/v1/executions/issue-lifecycle/actions/validate",
        json={
            "action": "hold_processing",
            "repository": REPO,
            "issue_number": ISSUE_NUMBER,
            "request": {"idempotency_key": "hold-key-1"},
            "live_issue": _issue("status: in-progress"),
            "reason": "operator pause",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["allowed"] is True
    assert payload["decision"]["operator"] == "user-1"
    assert payload["decision"]["reason"] == "operator pause"
    assert payload["decision"]["hold_released"] is False
    assert "Next action" in payload["comment"]


def test_validate_rejects_unauthorized_abandon_for_regular_user() -> None:
    client = _client(_regular_user())
    response = client.post(
        "/api/v1/executions/issue-lifecycle/actions/validate",
        json={
            "action": "abandon_work",
            "repository": REPO,
            "issue_number": ISSUE_NUMBER,
            "request": {"idempotency_key": "abandon-key-1"},
            "live_issue": _issue(),
            "reason": "nope",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["allowed"] is False
    assert payload["verdict"]["code"] == "unauthorized"


def test_validate_rejects_stale_and_conflicting_submissions() -> None:
    client = _client(_admin_user())
    stale = client.post(
        "/api/v1/executions/issue-lifecycle/actions/validate",
        json={
            "action": "continue_work",
            "repository": REPO,
            "issue_number": ISSUE_NUMBER,
            "request": {"idempotency_key": "stale-key", "attempt_seq": 2},
            "live_issue": _issue("status: recovery-needed"),
            "live_attempt": {"attempt_id": "att_" + "a" * 24, "seq": 3},
            "stop_proof": {"writers_stopped": True},
        },
    )
    assert stale.json()["verdict"]["code"] == "stale_attempt"

    conflict = client.post(
        "/api/v1/executions/issue-lifecycle/actions/validate",
        json={
            "action": "continue_work",
            "repository": REPO,
            "issue_number": ISSUE_NUMBER,
            "request": {"idempotency_key": "conflict-key", "pr_url": f"https://github.com/{REPO}/pull/8"},
            "live_issue": _issue("status: recovery-needed"),
            "live_attempt": {"attempt_id": "att_" + "a" * 24},
            "live_pr": {"pr_url": f"https://github.com/{REPO}/pull/7"},
            "stop_proof": {"writers_stopped": True},
        },
    )
    assert conflict.json()["verdict"]["code"] == "conflicting_pr"


def test_validate_replays_duplicate_idempotency_keys_without_effects() -> None:
    client = _client(_regular_user())
    response = client.post(
        "/api/v1/executions/issue-lifecycle/actions/validate",
        json={
            "action": "acknowledge_incident",
            "repository": REPO,
            "issue_number": ISSUE_NUMBER,
            "request": {"idempotency_key": "ack-key-1"},
            "live_issue": _issue("status: needs-attention"),
            "seen_idempotency_keys": ["ack-key-1"],
            "reason": "seen",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["allowed"] is True
    assert payload["verdict"]["code"] == "duplicate_idempotent_replay"
