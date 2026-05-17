from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.task_proposals import (
    _get_service,
    _get_temporal_execution_service,
    router,
)
from api_service.auth_providers import get_current_user, get_current_user_optional
from moonmind.workflows.task_proposals.models import (
    TaskProposalOriginSource,
    TaskProposalReviewPriority,
    TaskProposalStatus,
)
from moonmind.workflows.task_proposals.service import TaskProposalStatusError

@pytest.fixture
def client() -> tuple[TestClient, AsyncMock, AsyncMock]:
    app = FastAPI()
    service = AsyncMock()
    execution_service = AsyncMock()
    execution_service.create_execution.return_value = SimpleNamespace(workflow_id="wf-abc-123")
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
        origin_external_id=None,
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

def test_create_proposal_accepts_workflow_origin(
    client: tuple[TestClient, AsyncMock, AsyncMock],
) -> None:
    test_client, service, _execution_service = client
    proposal = _build_proposal()
    proposal.origin_source = TaskProposalOriginSource.WORKFLOW
    service.create_proposal.return_value = proposal

    response = test_client.post(
        "/api/proposals",
        json={
            "title": "Add tests",
            "summary": "Ensure coverage",
            "category": "tests",
            "tags": ["auth"],
            "origin": {"source": "workflow", "id": str(uuid4())},
            "taskCreateRequest": {
                "type": "task",
                "priority": 0,
                "maxAttempts": 3,
                "payload": {"repository": "Moon/Repo"},
            },
        },
    )

    assert response.status_code == 201
    kwargs = service.create_proposal.await_args.kwargs
    assert kwargs["origin_source"] == TaskProposalOriginSource.WORKFLOW
    payload = response.json()
    assert payload["origin"]["source"] == "workflow"

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


def test_list_proposals_serializes_workflow_origin_id_from_external_id(
    client: tuple[TestClient, AsyncMock, AsyncMock],
) -> None:
    test_client, service, _execution_service = client
    proposal = _build_proposal()
    proposal.origin_source = TaskProposalOriginSource.WORKFLOW
    proposal.origin_id = None
    proposal.origin_external_id = "wf-123"
    proposal.origin_metadata = {"workflow_id": "wf-123", "trigger_repo": "Moon/Repo"}
    service.list_proposals.return_value = ([proposal], None)

    response = test_client.get("/api/proposals")

    assert response.status_code == 200
    origin = response.json()["items"][0]["origin"]
    assert origin["source"] == "workflow"
    assert origin["id"] == "wf-123"
    assert origin["metadata"]["workflow_id"] == "wf-123"

def test_list_proposals_serializes_delivery_record_fields(
    client: tuple[TestClient, AsyncMock, AsyncMock],
) -> None:
    test_client, service, _execution_service = client
    proposal = _build_proposal()
    proposal.provider = "jira"
    proposal.external_key = "MM-901"
    proposal.external_url = "https://jira.example/browse/MM-901"
    proposal.delivered_at = datetime(2026, 5, 7, 12, 30, tzinfo=UTC)
    proposal.last_synced_at = datetime(2026, 5, 7, 12, 45, tzinfo=UTC)
    proposal.task_snapshot_ref = "artifact://tasks/proposals/MM-901.json"
    proposal.provider_metadata = {"jira": {"project_key": "MM", "labels": ["moonmind"]}}
    proposal.resolved_policy = {"provider": "jira", "target": "project"}
    service.list_proposals.return_value = ([proposal], None)

    response = test_client.get("/api/proposals")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["provider"] == "jira"
    assert item["externalKey"] == "MM-901"
    assert item["externalUrl"] == "https://jira.example/browse/MM-901"
    assert item["taskSnapshotRef"] == "artifact://tasks/proposals/MM-901.json"
    assert item["providerMetadata"] == {
        "jira": {"project_key": "MM", "labels": ["moonmind"]}
    }
    assert item["resolvedPolicy"] == {"provider": "jira", "target": "project"}
    assert item["deliveredAt"] == "2026-05-07T12:30:00Z"
    assert item["lastSyncedAt"] == "2026-05-07T12:45:00Z"


def test_list_proposals_serializes_review_delivery_state(
    client: tuple[TestClient, AsyncMock, AsyncMock],
) -> None:
    test_client, service, _execution_service = client
    proposal = _build_proposal()
    proposal.provider = "github"
    proposal.external_key = "42"
    proposal.external_url = "https://github.example/Moon/Repo/issues/42"
    proposal.delivered_at = datetime(2026, 5, 7, 12, 30, tzinfo=UTC)
    proposal.last_synced_at = datetime(2026, 5, 7, 12, 45, tzinfo=UTC)
    proposal.task_snapshot_ref = "artifact://tasks/proposals/42.json"
    proposal.provider_metadata = {
        "delivery": {
            "status": "delivered",
            "storedSnapshotNotice": True,
            "created": True,
        }
    }
    proposal.resolved_policy = {"provider": "github", "target": "project"}
    service.list_proposals.return_value = ([proposal], None)

    response = test_client.get("/api/proposals")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["reviewDelivery"] == {
        "provider": "github",
        "status": "delivered",
        "externalKey": "42",
        "externalUrl": "https://github.example/Moon/Repo/issues/42",
        "deliveredAt": "2026-05-07T12:30:00Z",
        "lastSyncedAt": "2026-05-07T12:45:00Z",
        "taskSnapshotRef": "artifact://tasks/proposals/42.json",
        "storedSnapshotNotice": True,
        "created": True,
    }

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
    assert body["promotedExecutionId"] == "wf-abc-123"
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

def test_promote_proposal_rejects_task_create_request_override(
    client: tuple[TestClient, AsyncMock, AsyncMock],
) -> None:
    test_client, service, execution_service = client
    proposal = _build_proposal()

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

    assert response.status_code == 422
    service.promote_proposal.assert_not_awaited()
    execution_service.create_execution.assert_not_awaited()

def test_promote_proposal_rejects_invalid_state(
    client: tuple[TestClient, AsyncMock, AsyncMock],
) -> None:
    test_client, service, _execution_service = client
    proposal = _build_proposal()
    service.promote_proposal.side_effect = TaskProposalStatusError("invalid state")

    response = test_client.post(
        f"/api/proposals/{proposal.id}/promote",
        json={"priority": 5},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["detail"]["code"] == "invalid_state"

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

def test_get_proposal_preview_includes_preset_provenance(
    client: tuple[TestClient, AsyncMock, AsyncMock],
) -> None:
    test_client, service, _execution_service = client
    proposal = _build_proposal()
    proposal.task_create_request = {
        "type": "task",
        "payload": {
            "repository": "Moon/Repo",
            "task": {
                "instructions": "Add regression coverage",
                "authoredPresets": [
                    {
                        "presetId": "runtime-quality-followup",
                        "presetVersion": "2026-04-17",
                        "includePath": ["root", "regression-coverage"],
                    }
                ],
                "steps": [
                    {
                        "title": "Add regression coverage",
                        "source": {
                            "kind": "preset-derived",
                            "presetId": "runtime-quality-followup",
                            "presetVersion": "2026-04-17",
                            "includePath": ["root", "regression-coverage"],
                            "originalStepId": "add-regression-test",
                        },
                    }
                ],
            },
        },
    }
    service.get_proposal.return_value = proposal
    service.get_similar_proposals.return_value = []

    response = test_client.get(f"/api/proposals/{proposal.id}")

    assert response.status_code == 200
    preview = response.json()["taskPreview"]
    assert preview["presetProvenance"] == "preserved-binding"
    assert preview["authoredPresetCount"] == 1
    assert preview["stepSourceKinds"] == ["preset-derived"]
    assert preview["presetSourceMetadata"] == [
        {
            "kind": "preset-derived",
            "presetId": "runtime-quality-followup",
            "presetVersion": "2026-04-17",
            "includePath": ["root", "regression-coverage"],
            "originalStepId": "add-regression-test",
        }
    ]


def test_get_proposal_preview_includes_operator_outcome_fields(
    client: tuple[TestClient, AsyncMock, AsyncMock],
) -> None:
    test_client, service, _execution_service = client
    proposal = _build_proposal()
    proposal.provider = "jira"
    proposal.external_key = "MM-901"
    proposal.external_url = "https://jira.example/browse/MM-901"
    proposal.delivered_at = datetime(2026, 5, 7, 12, 30, tzinfo=UTC)
    proposal.last_synced_at = datetime(2026, 5, 7, 12, 45, tzinfo=UTC)
    proposal.task_snapshot_ref = "artifact://tasks/proposals/MM-901.json"
    proposal.provider_metadata = {
        "delivery": {
            "status": "updated",
            "storedSnapshotNotice": True,
            "created": False,
            "duplicateSource": "existing-open-issue",
        },
        "providerDecisions": [
            {
                "providerEventId": "evt-promote",
                "accepted": True,
                "decision": "promote",
                "promotedExecutionId": "wf-promoted-1",
                "resultingExternalState": "promoted",
            }
        ],
    }
    proposal.task_create_request = {
        "type": "task",
        "priority": 1,
        "maxAttempts": 3,
        "payload": {
            "repository": "Moon/Repo",
            "task": {
                "instructions": "Add regression coverage",
                "runtime": {"mode": "codex"},
                "publish": {"mode": "pr"},
                "skills": ["moonspec-implement"],
                "authoredPresets": [{"presetId": "runtime-quality-followup"}],
            },
        },
    }
    service.get_proposal.return_value = proposal
    service.get_similar_proposals.return_value = []

    response = test_client.get(f"/api/proposals/{proposal.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["reviewDelivery"]["status"] == "updated"
    assert payload["reviewDelivery"]["lastSyncedAt"] == "2026-05-07T12:45:00Z"
    assert payload["reviewDelivery"]["duplicateSource"] == "existing-open-issue"
    assert payload["taskPreview"]["runtimeMode"] == "codex"
    assert payload["taskPreview"]["publishMode"] == "pr"
    assert payload["taskPreview"]["priority"] == 1
    assert payload["taskPreview"]["maxAttempts"] == 3
    assert payload["taskPreview"]["taskSkills"] == ["moonspec-implement"]
    assert payload["promotionResult"] == {
        "promotedExecutionId": "wf-promoted-1",
        "promotedExecutionUrl": "/tasks/temporal/wf-promoted-1",
        "providerEventId": "evt-promote",
        "resultingExternalState": "promoted",
    }

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
    """runtimeMode shortcut is passed as a bounded promotion control."""
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
    service.promote_proposal.return_value = (proposal, final_request)

    response = test_client.post(
        f"/api/proposals/{proposal.id}/promote",
        json={"runtimeMode": "gemini_cli"},
    )

    assert response.status_code == 200
    kwargs = service.promote_proposal.await_args.kwargs
    assert kwargs["runtime_mode_override"] == "gemini_cli"
    execution_service.create_execution.assert_awaited_once()
    call_kwargs = execution_service.create_execution.await_args.kwargs
    assert call_kwargs["idempotency_key"] == f"proposal-promote-{proposal.id}"


def test_provider_decision_rejects_unverified_event_before_run(
    client: tuple[TestClient, AsyncMock, AsyncMock],
) -> None:
    test_client, service, execution_service = client
    proposal = _build_proposal()
    proposal.provider = "github"
    proposal.external_key = "42"
    service.record_provider_decision_event.return_value = SimpleNamespace(
        accepted=False,
        decision=None,
        note=None,
        actor="reviewer",
        provider_event_id="evt-unverified",
        reason="provider_auth_failed",
        priority=None,
        defer_until=None,
        runtime_mode=None,
        external_state="ignored",
        promoted_execution_id=None,
    )
    service.get_proposal.return_value = proposal

    response = test_client.post(
        f"/api/proposals/{proposal.id}/provider-decision",
        json={
            "provider": "github",
            "externalKey": "42",
            "providerEventId": "evt-unverified",
            "actor": "reviewer",
            "action": "promote",
            "authenticity": {"verified": False, "method": "signature"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted"] is False
    assert payload["reason"] == "provider_auth_failed"
    assert payload["promotedExecutionId"] is None
    service.record_provider_decision_event.assert_awaited_once()
    service.promote_proposal.assert_not_awaited()
    execution_service.create_execution.assert_not_awaited()


def test_provider_decision_promotes_through_canonical_execution_path(
    client: tuple[TestClient, AsyncMock, AsyncMock],
) -> None:
    test_client, service, execution_service = client
    proposal = _build_proposal()
    proposal.provider = "jira"
    proposal.external_key = "MM-599"
    final_request = {
        "payload": {
            "repository": "Moon/Repo",
            "task": {
                "instructions": "Implement MM-599\nIgnore edited issue text",
                "authoredPresets": [{"presetId": "runtime-quality-followup"}],
                "steps": [
                    {
                        "type": "skill",
                        "source": {"kind": "preset-derived"},
                    }
                ],
            },
        }
    }
    service.record_provider_decision_event.return_value = SimpleNamespace(
        accepted=True,
        decision="promote",
        note="ready",
        actor="reviewer",
        provider_event_id="evt-promote",
        reason=None,
        priority=None,
        defer_until=None,
        runtime_mode="codex",
        external_state="promoted",
        promoted_execution_id=None,
    )
    service.promote_proposal.return_value = (proposal, final_request)
    service.attach_provider_decision_execution.return_value = proposal

    response = test_client.post(
        f"/api/proposals/{proposal.id}/provider-decision",
        json={
            "provider": "jira",
            "externalKey": "MM-599",
            "providerEventId": "evt-promote",
            "actor": "reviewer",
            "body": "Do not run this edited body\n/moonmind promote --runtime codex",
            "authenticity": {"verified": True, "method": "signature"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted"] is True
    assert payload["promotedExecutionId"] == "wf-abc-123"
    service.promote_proposal.assert_awaited_once()
    assert service.promote_proposal.await_args.kwargs["runtime_mode_override"] == "codex"
    execution_service.create_execution.assert_awaited_once()
    call_kwargs = execution_service.create_execution.await_args.kwargs
    assert call_kwargs["idempotency_key"] == f"proposal-provider-{proposal.id}-evt-promote"
    assert call_kwargs["initial_parameters"] == final_request["payload"]
    service.attach_provider_decision_execution.assert_awaited_once_with(
        proposal_id=proposal.id,
        provider_event_id="evt-promote",
        promoted_execution_id="wf-abc-123",
    )


def test_provider_decision_recovery_inspects_delivery_history(
    client: tuple[TestClient, AsyncMock, AsyncMock],
) -> None:
    test_client, service, _execution_service = client
    proposal = _build_proposal()
    proposal.provider = "github"
    proposal.external_key = "42"
    proposal.external_url = "https://github.example/Moon/Repo/issues/42"
    proposal.provider_metadata = {
        "delivery": {"status": "delivered", "storedSnapshotNotice": True},
        "providerDecisions": [
            {
                "providerEventId": "evt-promote",
                "decision": "promote",
                "promotedExecutionId": "wf-abc-123",
            }
        ],
    }
    service.get_proposal.return_value = proposal

    response = test_client.get(f"/api/proposals/{proposal.id}/delivery")

    assert response.status_code == 200
    payload = response.json()
    assert payload["reviewDelivery"]["status"] == "delivered"
    assert payload["providerMetadata"]["providerDecisions"][0]["promotedExecutionId"] == (
        "wf-abc-123"
    )


def test_redeliver_proposal_endpoint_returns_recovery_record(
    client: tuple[TestClient, AsyncMock, AsyncMock],
) -> None:
    test_client, service, _execution_service = client
    proposal = _build_proposal()
    proposal.provider = "github"
    proposal.external_key = "42"
    proposal.external_url = "https://github.example/Moon/Repo/issues/42"
    proposal.provider_metadata = {
        "delivery": {
            "status": "delivered",
            "storedSnapshotNotice": True,
            "created": False,
        }
    }
    service.redeliver_proposal.return_value = proposal

    response = test_client.post(f"/api/proposals/{proposal.id}/redeliver")

    assert response.status_code == 200
    service.redeliver_proposal.assert_awaited_once_with(proposal_id=proposal.id)
    assert response.json()["reviewDelivery"]["status"] == "delivered"


def test_sync_proposal_delivery_endpoint_returns_recovery_record(
    client: tuple[TestClient, AsyncMock, AsyncMock],
) -> None:
    test_client, service, _execution_service = client
    proposal = _build_proposal()
    proposal.provider = "jira"
    proposal.external_key = "MM-901"
    proposal.external_url = "https://jira.example/browse/MM-901"
    proposal.last_synced_at = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    proposal.provider_metadata = {
        "delivery": {"status": "delivered", "storedSnapshotNotice": True},
        "sync": {"status": "inspected"},
    }
    service.sync_proposal_delivery.return_value = proposal

    response = test_client.post(f"/api/proposals/{proposal.id}/sync")

    assert response.status_code == 200
    service.sync_proposal_delivery.assert_awaited_once_with(proposal_id=proposal.id)
    payload = response.json()
    assert payload["reviewDelivery"]["lastSyncedAt"] == "2026-05-17T12:00:00Z"
    assert payload["providerMetadata"]["sync"]["status"] == "inspected"
