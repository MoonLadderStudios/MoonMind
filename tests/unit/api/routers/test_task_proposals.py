from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.task_proposals import _get_service, _get_temporal_execution_service, router
from api_service.auth_providers import get_current_user, get_current_user_optional
from moonmind.workflows.task_proposals.models import (
    TaskProposalOriginSource,
    TaskProposalReviewPriority,
    TaskProposalStatus,
)


@pytest.fixture
def client() -> tuple[TestClient, AsyncMock, AsyncMock]:
    app = FastAPI()
    service = AsyncMock()
    execution_service = AsyncMock()
    app.include_router(router)

    async def _service_override():
        return service

    async def _execution_service_override():
        return execution_service

    app.dependency_overrides[_get_service] = _service_override
    app.dependency_overrides[_get_temporal_execution_service] = _execution_service_override

    mock_user = SimpleNamespace(id=uuid4(), email="user@example.com", is_active=True)

    async def _user_override():
        return mock_user

    app.dependency_overrides[get_current_user()] = _user_override
    app.dependency_overrides[get_current_user_optional()] = _user_override

    with TestClient(app) as test_client:
        yield test_client, service, execution_service


def _build_proposal() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        status=TaskProposalStatus.OPEN,
        title="Add tests",
        summary="Follow-up",
        category="tests",
        tags=["tests"],
        repository="Moon/Repo",
        dedup_key="moon/repo:add-tests",
        dedup_hash="abcd1234",
        review_priority=TaskProposalReviewPriority.NORMAL,
        priority_override_reason=None,
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
        promoted_at=None,
        promoted_by_user_id=None,
        decided_by_user_id=None,
        decision_note=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        origin_source=TaskProposalOriginSource.QUEUE,
        origin_id=uuid4(),
        origin_metadata={},
        task_create_request={"payload": {"repository": "Moon/Repo"}},
        similar=[],
    )


def test_create_proposal_with_user_auth(client: tuple[TestClient, AsyncMock, AsyncMock]) -> None:
    test_client, service, _execution_service = client
    proposal = _build_proposal()
    service.create_proposal.return_value = proposal

    response = test_client.post(
        "/api/proposals",
        json={
            "title": "Add tests",
            "summary": "Ensure coverage",
            "category": "tests",
            "tags": ["auth"],
            "reviewPriority": "high",
            "origin": {"source": "queue", "id": str(uuid4())},
            "taskCreateRequest": {
                "type": "task",
                "priority": 0,
                "maxAttempts": 3,
                "payload": {"repository": "Moon/Repo"},
            },
        },
    )

    assert response.status_code == 201
    service.create_proposal.assert_awaited()
    kwargs = service.create_proposal.await_args.kwargs
    assert kwargs["review_priority"] == "high"
    payload = response.json()
    assert payload["repository"] == "Moon/Repo"




def test_list_proposals_supports_filters(client: tuple[TestClient, AsyncMock, AsyncMock]) -> None:
    test_client, service, _execution_service = client
    proposal = _build_proposal()
    service.list_proposals.return_value = ([proposal], None)
    origin_id = uuid4()

    response = test_client.get(
        "/api/proposals",
        params={
            "status": "open",
            "repository": "Moon/Repo",
            "category": "tests",
            "originSource": "queue",
            "originId": str(origin_id),
        },
    )

    assert response.status_code == 200
    service.list_proposals.assert_awaited()
    kwargs = service.list_proposals.await_args.kwargs
    assert kwargs["origin_source"] == TaskProposalOriginSource.QUEUE
    assert kwargs["origin_id"] == origin_id
    payload = response.json()
    assert payload["items"]


def test_promote_proposal_returns_proposal(
    client: tuple[TestClient, AsyncMock, AsyncMock],
) -> None:
    test_client, service, execution_service = client
    proposal = _build_proposal()
    final_request = {
        "payload": {
            "repository": "Moon/Repo",
            "task": {"instructions": "do something"}
        }
    }
    service.promote_proposal.return_value = (proposal, final_request)

    response = test_client.post(
        f"/api/proposals/{proposal.id}/promote",
        json={"priority": 5},
    )

    assert response.status_code == 200
    body = response.json()
    assert "proposal" in body
    assert body["proposal"]["title"] == "Add tests"
    execution_service.create_execution.assert_awaited_once()
    call_kwargs = execution_service.create_execution.await_args.kwargs
    assert call_kwargs["idempotency_key"] == f"proposal-promote-{proposal.id}"
    assert call_kwargs["initial_parameters"] == final_request["payload"]
    assert call_kwargs["title"] == "do something"


def test_promote_proposal_uses_first_non_empty_instruction_line_for_title(
    client: tuple[TestClient, AsyncMock, AsyncMock],
) -> None:
    test_client, service, execution_service = client
    proposal = _build_proposal()
    final_request = {
        "payload": {
            "repository": "Moon/Repo",
            "task": {
                "instructions": "\n\n  First line with spaces   \nSecond line\nThird line"
            },
        }
    }
    service.promote_proposal.return_value = (proposal, final_request)

    response = test_client.post(
        f"/api/proposals/{proposal.id}/promote",
        json={"priority": 5},
    )

    assert response.status_code == 200
    call_kwargs = execution_service.create_execution.await_args.kwargs
    assert call_kwargs["title"] == "First line with spaces"


def test_promote_proposal_accepts_override_payload(
    client: tuple[TestClient, AsyncMock, AsyncMock],
) -> None:
    test_client, service, execution_service = client
    proposal = _build_proposal()
    final_request = {
        "payload": {
            "repository": "Moon/Repo",
            "task": {"instructions": "edit"}
        }
    }
    service.promote_proposal.return_value = (proposal, final_request)

    response = test_client.post(
        f"/api/proposals/{proposal.id}/promote",
        json={
            "taskCreateRequestOverride": {
                "type": "task",
                "priority": 0,
                "maxAttempts": 3,
                "payload": {
                    "repository": "Moon/Repo",
                    "task": {"instructions": "edit"},
                },
            }
        },
    )

    assert response.status_code == 200
    kwargs = service.promote_proposal.await_args.kwargs
    assert (
        kwargs["task_create_request_override"]["payload"]["task"]["instructions"]
        == "edit"
    )
    call_kwargs = execution_service.create_execution.await_args.kwargs
    assert call_kwargs["title"] == "edit"


def test_dismiss_proposal_returns_payload(client: tuple[TestClient, AsyncMock, AsyncMock]) -> None:
    test_client, service, _execution_service = client
    proposal = _build_proposal()
    proposal.status = TaskProposalStatus.DISMISSED
    service.dismiss_proposal.return_value = proposal

    response = test_client.post(
        f"/api/proposals/{proposal.id}/dismiss",
        json={"note": "later"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "dismissed"


def test_get_proposal_includes_similar(client: tuple[TestClient, AsyncMock, AsyncMock]) -> None:
    test_client, service, _execution_service = client
    proposal = _build_proposal()
    similar = _build_proposal()
    service.get_proposal.return_value = proposal
    service.get_similar_proposals.return_value = [similar]

    response = test_client.get(f"/api/proposals/{proposal.id}")

    assert response.status_code == 200
    service.get_similar_proposals.assert_awaited()
    body = response.json()
    assert body["similar"]


def test_update_priority_endpoint(client: tuple[TestClient, AsyncMock, AsyncMock]) -> None:
    test_client, service, _execution_service = client
    proposal = _build_proposal()
    service.update_review_priority.return_value = proposal

    response = test_client.post(
        f"/api/proposals/{proposal.id}/priority",
        json={"priority": "high"},
    )

    assert response.status_code == 200
    service.update_review_priority.assert_awaited()


def test_promote_proposal_with_runtime_mode_shortcut(
    client: tuple[TestClient, AsyncMock, AsyncMock],
) -> None:
    """runtimeMode shortcut builds a task_create_request_override for the service."""
    test_client, service, execution_service = client
    proposal = _build_proposal()
    proposal.task_create_request = {
        "type": "task",
        "priority": 0,
        "maxAttempts": 3,
        "payload": {
            "repository": "Moon/Repo",
            "task": {
                "instructions": "do stuff",
                "runtime": {"mode": "codex"},
            },
        },
    }
    final_request = {
        "payload": {
            "repository": "Moon/Repo",
            "task": {
                "instructions": "do stuff",
                "runtime": {"mode": "gemini_cli"}
            }
        }
    }
    service.get_proposal.return_value = proposal
    service.promote_proposal.return_value = (proposal, final_request)

    response = test_client.post(
        f"/api/proposals/{proposal.id}/promote",
        json={"runtimeMode": "gemini_cli"},
    )

    assert response.status_code == 200
    kwargs = service.promote_proposal.await_args.kwargs
    override = kwargs["task_create_request_override"]
    assert override is not None
    assert override["payload"]["task"]["runtime"]["mode"] == "gemini_cli"
    assert override["payload"]["repository"] == "Moon/Repo"
    execution_service.create_execution.assert_awaited_once()
    call_kwargs = execution_service.create_execution.await_args.kwargs
    assert call_kwargs["idempotency_key"] == f"proposal-promote-{proposal.id}"
    assert call_kwargs["title"] == "do stuff"
