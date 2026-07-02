"""Unit coverage for MM-1091 checkpoint branch API paths."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.api.routers.executions import _get_service, router
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import (
    Base,
    MoonMindWorkflowState,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionOwnerType,
    TemporalWorkflowType,
    WorkflowCheckpointBranch,
    WorkflowCheckpointBranchOperation,
)


def _override_user_dependencies(app: FastAPI, user: SimpleNamespace) -> None:
    user_dependencies = {
        dep.call
        for route in router.routes
        if route.dependant is not None
        for dep in route.dependant.dependencies
        if getattr(dep.call, "__name__", "") == "_current_user_fallback"
    }
    if not user_dependencies:
        user_dependencies = {get_current_user()}

    def _current_user() -> SimpleNamespace:
        return user

    for dependency in user_dependencies:
        app.dependency_overrides[dependency] = _current_user


@pytest_asyncio.fixture
async def checkpoint_branch_client(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/checkpoint-branches.db"
    )
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    user = SimpleNamespace(
        id=uuid4(),
        email="checkpoint-branches@example.com",
        is_superuser=True,
        roles=[],
    )
    now = datetime.now(UTC)
    record = TemporalExecutionCanonicalRecord(
        workflow_id="mm:wf-branch",
        run_id="run-branch",
        namespace="default",
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        owner_id=str(user.id),
        owner_type=TemporalExecutionOwnerType.USER,
        state=MoonMindWorkflowState.EXECUTING,
        entry="run",
        search_attributes={"mm_owner_id": str(user.id), "mm_owner_type": "user"},
        memo={
            "stepCheckpointRef": "artifact://checkpoints/after-implement",
            "latest_temporal_run_id": "run-branch",
        },
        parameters={
            "steps": [
                {
                    "logicalStepId": "implement",
                    "executionOrdinal": 2,
                    "checkpointRefsByBoundary": {
                        "after_execution": "artifact://checkpoints/after-implement"
                    },
                    "checkpointRef": "artifact://checkpoints/after-implement",
                    "checkpointDigest": "sha256:checkpointdigest",
                }
            ]
        },
        artifact_refs=[],
        created_at=now,
        updated_at=now,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        session.add(record)
        await session.commit()

    app = FastAPI()
    app.include_router(router)
    service = SimpleNamespace(describe_execution=AsyncMock(return_value=record))
    app.dependency_overrides[_get_service] = lambda: service

    async def _session_override():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = _session_override
    _override_user_dependencies(app, user)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest_asyncio.fixture
async def checkpoint_branch_denied_client(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/checkpoint-branches-denied.db"
    )
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    owner_id = uuid4()
    user = SimpleNamespace(
        id=uuid4(),
        email="not-the-owner@example.com",
        is_superuser=False,
        roles=[],
    )
    now = datetime.now(UTC)
    record = TemporalExecutionCanonicalRecord(
        workflow_id="mm:wf-branch",
        run_id="run-branch",
        namespace="default",
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        owner_id=str(owner_id),
        owner_type=TemporalExecutionOwnerType.USER,
        state=MoonMindWorkflowState.EXECUTING,
        entry="run",
        search_attributes={"mm_owner_id": str(owner_id), "mm_owner_type": "user"},
        memo={"stepCheckpointRef": "artifact://checkpoints/after-implement"},
        parameters={},
        artifact_refs=[],
        created_at=now,
        updated_at=now,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        session.add(record)
        await session.commit()

    app = FastAPI()
    app.include_router(router)
    service = SimpleNamespace(describe_execution=AsyncMock(return_value=record))
    app.dependency_overrides[_get_service] = lambda: service

    async def _session_override():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = _session_override
    _override_user_dependencies(app, user)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client

    app.dependency_overrides.clear()
    await engine.dispose()


def _create_payload(idempotency_key: str = "mm-1091:create") -> dict[str, object]:
    return {
        "source": {
            "runId": "run-branch",
            "logicalStepId": "implement",
            "executionOrdinal": 2,
            "checkpointBoundary": "after_execution",
            "checkpointRef": "artifact://checkpoints/after-implement",
            "checkpointDigest": "sha256:checkpointdigest",
        },
        "label": "MM-1091 branch",
        "instructions": {"text": "Continue from the checkpoint."},
        "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
        "runtimeContextPolicy": "fresh_agent_run",
        "publishMode": "none",
        "idempotencyKey": idempotency_key,
    }


@pytest.mark.asyncio
async def test_checkpoint_branch_api_lists_creates_details_turns_and_is_idempotent(
    checkpoint_branch_client: AsyncClient,
) -> None:
    checkpoints = await checkpoint_branch_client.get(
        "/api/executions/mm:wf-branch/checkpoints"
    )
    assert checkpoints.status_code == 200
    assert checkpoints.json()["items"][0]["checkpointRef"] == (
        "artifact://checkpoints/after-implement"
    )

    first = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload(),
    )
    second = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload(),
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["branchId"] == second.json()["branchId"]
    branch_id = first.json()["branchId"]

    detail = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}"
    )
    turns = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/turns"
    )
    branches = await checkpoint_branch_client.get(
        "/api/executions/mm:wf-branch/checkpoint-branches"
    )

    assert detail.status_code == 200
    assert turns.status_code == 200
    assert branches.status_code == 200
    assert detail.json()["branchId"] == branch_id
    assert len(turns.json()["items"]) == 1
    assert branches.json()["items"][0]["branchId"] == branch_id


@pytest.mark.asyncio
async def test_checkpoint_branch_publish_does_not_promote_and_archive_hides_active(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1091:create-publish"),
    )
    branch_id = created.json()["branchId"]

    published = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/publish",
        json={
            "mode": "pull_request",
            "repository": "Moon/Mind",
            "baseBranch": "main",
            "headBranch": "mm/mm-1091/checkpoint-branch",
            "provider": "github",
            "idempotencyKey": "mm-1091:publish",
        },
    )
    assert published.status_code == 200
    assert published.json()["publishStatus"] == "published"
    assert published.json()["state"] == "published"
    assert published.json()["promotedAt"] is None

    archived = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/archive",
        json={"reason": "No longer active", "idempotencyKey": "mm-1091:archive"},
    )
    active = await checkpoint_branch_client.get(
        "/api/executions/mm:wf-branch/checkpoint-branches"
    )
    all_branches = await checkpoint_branch_client.get(
        "/api/executions/mm:wf-branch/checkpoint-branches?active=false"
    )

    assert archived.status_code == 200
    assert archived.json()["state"] == "archived"
    assert active.json()["items"] == []
    assert all_branches.json()["items"][0]["branchId"] == branch_id


@pytest.mark.asyncio
async def test_checkpoint_branch_continue_fork_and_compare_are_typed_and_idempotent(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1091:create-branching"),
    )
    branch_id = created.json()["branchId"]

    continue_payload = {
        "label": "Continued branch",
        "instructions": {"text": "Continue this branch."},
        "workspacePolicy": "continue_from_previous_execution",
        "runtimeContextPolicy": "reuse_session_new_epoch",
        "idempotencyKey": "mm-1091:continue",
    }
    first_continue = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/continue",
        json=continue_payload,
    )
    second_continue = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/continue",
        json=continue_payload,
    )
    assert first_continue.status_code == 201
    assert second_continue.status_code == 201
    assert first_continue.json()["branchTurnId"] == second_continue.json()["branchTurnId"]

    fork_payload = {
        "label": "Forked branch",
        "instructions": {"text": "Fork this branch."},
        "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
        "runtimeContextPolicy": "fresh_agent_run",
        "idempotencyKey": "mm-1091:fork",
    }
    first_fork = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/fork",
        json=fork_payload,
    )
    second_fork = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/fork",
        json=fork_payload,
    )
    assert first_fork.status_code == 201
    assert second_fork.status_code == 201
    fork_id = first_fork.json()["branchId"]
    assert fork_id == second_fork.json()["branchId"]
    assert first_fork.json()["parentBranchId"] == branch_id

    first_compare = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/compare",
        params={"against": fork_id},
    )
    second_compare = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/compare",
        params={"against": fork_id},
    )
    assert first_compare.status_code == 200
    assert second_compare.status_code == 200
    comparison = first_compare.json()
    assert comparison["branchId"] == branch_id
    assert comparison["againstBranchId"] == fork_id
    assert comparison["summaryRef"].startswith("artifact://checkpoint-branch-comparisons/")
    assert comparison["summaryRef"] == second_compare.json()["summaryRef"]
    assert comparison["comparisonRecord"]["recordType"] == "checkpoint_branch_comparison"
    assert comparison["comparisonRecord"]["evidenceRefs"]["branchCheckpointRef"] == (
        "artifact://checkpoints/after-implement"
    )


@pytest.mark.asyncio
async def test_checkpoint_branch_promotion_requires_head_gate_side_effects_and_approval(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1091:create-promote"),
    )
    branch_id = created.json()["branchId"]

    missing_approval = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/promote",
        json={
            "expectedHeadStepExecutionId": "mm:wf-branch:run:implement:execution:2",
            "gateEvidence": {"verdict": "passed", "artifactRef": "artifact://gate"},
            "sideEffectDisposition": {"status": "isolated"},
            "policyRequiresApproval": True,
            "idempotencyKey": "mm-1091:promote-missing-approval",
        },
    )
    assert missing_approval.status_code == 409
    assert missing_approval.json()["detail"]["code"] == "approval_required"

    promoted = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/promote",
        json={
            "expectedHeadStepExecutionId": "mm:wf-branch:run:implement:execution:2",
            "gateEvidence": {"verdict": "passed", "artifactRef": "artifact://gate"},
            "sideEffectDisposition": {"status": "isolated"},
            "approvalEvidence": {"artifactRef": "artifact://approval"},
            "policyRequiresApproval": True,
            "idempotencyKey": "mm-1091:promote",
        },
    )
    assert promoted.status_code == 200
    assert promoted.json()["state"] == "promoted"
    assert promoted.json()["currentHeadStepExecutionId"] == (
        "mm:wf-branch:run:implement:execution:2"
    )

    head_mismatch = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/promote",
        json={
            "expectedHeadStepExecutionId": "mm:wf-branch:run:implement:execution:3",
            "gateEvidence": {"verdict": "passed", "artifactRef": "artifact://gate"},
            "sideEffectDisposition": {"status": "isolated"},
            "approvalEvidence": {"artifactRef": "artifact://approval"},
            "policyRequiresApproval": True,
            "idempotencyKey": "mm-1091:promote-head-mismatch",
        },
    )
    assert head_mismatch.status_code == 409
    assert head_mismatch.json()["detail"]["code"] == "checkpoint_head_mismatch"


@pytest.mark.asyncio
async def test_checkpoint_branch_api_fails_closed_for_invalid_source_provider_budget_and_refs(
    checkpoint_branch_client: AsyncClient,
) -> None:
    invalid_source = _create_payload("mm-1091:invalid-source")
    invalid_source["source"] = {
        **invalid_source["source"],  # type: ignore[index]
        "checkpointRef": "artifact://checkpoints/missing",
    }
    source_response = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=invalid_source,
    )
    assert source_response.status_code == 409
    assert source_response.json()["detail"]["code"] == "checkpoint_invalid"

    digest_mismatch = _create_payload("mm-1091:digest")
    digest_mismatch["source"] = {
        **digest_mismatch["source"],  # type: ignore[index]
        "checkpointDigest": "sha256:not-the-checkpoint-digest",
    }
    digest_response = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=digest_mismatch,
    )
    assert digest_response.status_code == 409
    assert digest_response.json()["detail"]["code"] == "checkpoint_digest_mismatch"

    workspace_policy_response = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json={
            **_create_payload("mm-1091:workspace-policy"),
            "workspacePolicy": "continue_from_previous_execution",
            "publishMode": "branch",
            "gitWorkBranch": "mm/mm-1091/workspace-policy",
        },
    )
    assert workspace_policy_response.status_code == 409
    assert (
        workspace_policy_response.json()["detail"]["code"]
        == "workspace_policy_incompatible"
    )

    conflict_payload = _create_payload("mm-1091:idempotency-conflict")
    first_conflict = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=conflict_payload,
    )
    second_conflict = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json={**conflict_payload, "label": "Same idempotency key, different body"},
    )
    assert first_conflict.status_code == 201
    assert second_conflict.status_code == 409
    assert second_conflict.json()["detail"]["code"] == "idempotency_key_conflict"

    provider_response = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json={
            **_create_payload("mm-1091:provider"),
            "runtimeContextPolicy": "external_provider_continuation",
        },
    )
    assert provider_response.status_code == 409
    assert (
        provider_response.json()["detail"]["code"]
        == "provider_continuation_unsupported"
    )

    budget_response = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json={**_create_payload("mm-1091:budget"), "maxBudgetUsd": 0},
    )
    assert budget_response.status_code == 409
    assert budget_response.json()["detail"]["code"] == "budget_exhausted"

    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1091:create-protected-ref"),
    )
    protected_ref = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{created.json()['branchId']}/publish",
        json={
            "mode": "branch",
            "repository": "Moon/Mind",
            "baseBranch": "main",
            "headBranch": "main",
            "provider": "github",
            "idempotencyKey": "mm-1091:protected-ref",
        },
    )
    assert protected_ref.status_code == 409
    assert protected_ref.json()["detail"]["code"] == "protected_branch_ref"

    unsupported_provider = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{created.json()['branchId']}/publish",
        json={
            "mode": "pull_request",
            "repository": "Moon/Mind",
            "baseBranch": "main",
            "headBranch": "mm/mm-1091/gitlab",
            "provider": "gitlab",
            "idempotencyKey": "mm-1091:unsupported-provider",
        },
    )
    assert unsupported_provider.status_code == 409
    assert unsupported_provider.json()["detail"]["code"] == (
        "provider_continuation_unsupported"
    )

    bad_gate = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{created.json()['branchId']}/promote",
        json={
            "expectedHeadStepExecutionId": "mm:wf-branch:run:implement:execution:2",
            "gateEvidence": {"verdict": "failed", "artifactRef": "artifact://gate"},
            "sideEffectDisposition": {"status": "isolated"},
            "idempotencyKey": "mm-1091:bad-gate",
        },
    )
    assert bad_gate.status_code == 409
    assert bad_gate.json()["detail"]["reason"] == "gate_evidence_not_passing"

    unsafe_side_effects = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{created.json()['branchId']}/promote",
        json={
            "expectedHeadStepExecutionId": "mm:wf-branch:run:implement:execution:2",
            "gateEvidence": {"verdict": "passed", "artifactRef": "artifact://gate"},
            "sideEffectDisposition": {"status": "unsafe"},
            "idempotencyKey": "mm-1091:unsafe-side-effects",
        },
    )
    assert unsafe_side_effects.status_code == 409
    assert unsafe_side_effects.json()["detail"]["reason"] == (
        "side_effect_disposition_required"
    )


@pytest.mark.asyncio
async def test_checkpoint_branch_api_fails_closed_for_non_owner(
    checkpoint_branch_denied_client: AsyncClient,
) -> None:
    response = await checkpoint_branch_denied_client.get(
        "/api/executions/mm:wf-branch/checkpoints"
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "execution_not_found"


@pytest.mark.asyncio
async def test_checkpoint_branch_compare_records_operation_payload(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1091:create-compare-ledger"),
    )
    forked = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{created.json()['branchId']}/fork",
        json={
            "label": "Forked branch",
            "instructions": {"text": "Fork for comparison."},
            "idempotencyKey": "mm-1091:fork-compare-ledger",
        },
    )
    compared = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{created.json()['branchId']}/compare",
        params={"against": forked.json()["branchId"]},
    )
    assert compared.status_code == 200

    # The compare path is a read API, but the comparison record itself is durable
    # evidence referenced by clients and stored in the operation ledger.
    operation = None
    async for session in checkpoint_branch_client._transport.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        result = await session.execute(
            select(WorkflowCheckpointBranchOperation).where(
                WorkflowCheckpointBranchOperation.operation
                == "checkpoint_branch.compare"
            )
        )
        operation = result.scalar_one()
    assert operation is not None
    assert operation.response_payload["summaryRef"] == compared.json()["summaryRef"]


@pytest.mark.asyncio
async def test_checkpoint_branch_compare_refreshes_when_branch_head_changes(
    checkpoint_branch_client: AsyncClient,
) -> None:
    created = await checkpoint_branch_client.post(
        "/api/executions/mm:wf-branch/checkpoint-branches",
        json=_create_payload("mm-1091:create-compare-refresh"),
    )
    forked = await checkpoint_branch_client.post(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{created.json()['branchId']}/fork",
        json={
            "label": "Forked branch",
            "instructions": {"text": "Fork for comparison refresh."},
            "idempotencyKey": "mm-1091:fork-compare-refresh",
        },
    )
    branch_id = created.json()["branchId"]
    fork_id = forked.json()["branchId"]

    first_compare = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/compare",
        params={"against": fork_id},
    )
    assert first_compare.status_code == 200

    async for session in checkpoint_branch_client._transport.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        result = await session.execute(
            select(WorkflowCheckpointBranch).where(
                WorkflowCheckpointBranch.branch_id == branch_id
            )
        )
        branch = result.scalar_one()
        branch.current_head_checkpoint_ref = "artifact://checkpoints/after-review"
        branch.current_head_commit = "review-head"
        await session.commit()

    second_compare = await checkpoint_branch_client.get(
        f"/api/executions/mm:wf-branch/checkpoint-branches/{branch_id}/compare",
        params={"against": fork_id},
    )
    assert second_compare.status_code == 200
    assert second_compare.json()["summaryRef"] != first_compare.json()["summaryRef"]
    assert second_compare.json()["comparisonRecord"]["summary"]["branchHeadCommit"] == (
        "review-head"
    )
    assert second_compare.json()["comparisonRecord"]["evidenceRefs"][
        "branchCheckpointRef"
    ] == "artifact://checkpoints/after-review"

    async for session in checkpoint_branch_client._transport.app.dependency_overrides[  # type: ignore[attr-defined]
        get_async_session
    ]():
        result = await session.execute(
            select(WorkflowCheckpointBranchOperation).where(
                WorkflowCheckpointBranchOperation.operation
                == "checkpoint_branch.compare"
            )
        )
        operations = result.scalars().all()
    assert len(operations) == 2
