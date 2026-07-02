from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.api.routers.checkpoint_branches import router
from api_service.db.base import get_async_session
from api_service.db.models import (
    Base,
    TemporalExecutionCanonicalRecord,
    TemporalWorkflowType,
)


@pytest_asyncio.fixture()
async def checkpoint_branch_api_session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/branches-api.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        session.add(
            TemporalExecutionCanonicalRecord(
                workflow_id="wf-api",
                run_id="run-api",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                entry="api",
            )
        )
        await session.commit()
        yield session

    await engine.dispose()


@pytest.fixture()
def checkpoint_branch_client(checkpoint_branch_api_session: AsyncSession):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_async_session] = lambda: checkpoint_branch_api_session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _create_payload(branch_id: str = "cbr-api") -> dict[str, object]:
    return {
        "branchId": branch_id,
        "source": {
            "workflowId": "wf-api",
            "runId": "run-api",
            "logicalStepId": "implement",
            "sourceExecutionOrdinal": 2,
            "checkpointBoundary": "after_execution",
            "checkpointRef": "artifact://checkpoint/after",
            "checkpointDigest": "sha256:checkpoint",
        },
        "label": "Try API branch",
        "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
        "runtimeContextPolicy": "fresh_agent_run",
        "instructionRef": "artifact://instructions/root",
        "instructionDigest": "sha256:root",
        "idempotencyKey": f"MM-1099:{branch_id}:create",
    }


def test_checkpoint_branch_api_create_continue_fork_archive_and_publish_ready(
    checkpoint_branch_client: TestClient,
) -> None:
    created = checkpoint_branch_client.post(
        "/api/executions/wf-api/checkpoint-branches",
        json=_create_payload(),
    )
    assert created.status_code == 201
    assert created.json()["branch"]["branchId"] == "cbr-api"
    assert (
        created.json()["branch"]["sourceCheckpointRef"]
        == "artifact://checkpoint/after"
    )
    assert (
        created.json()["turns"][0]["instructionRef"]
        == "artifact://instructions/root"
    )

    continued = checkpoint_branch_client.post(
        "/api/executions/wf-api/checkpoint-branches/cbr-api/continue",
        json={
            "instructionRef": "artifact://instructions/continue",
            "instructionDigest": "sha256:continue",
            "idempotencyKey": "MM-1099:cbr-api:continue",
            "createdStepExecutionId": "wf-api:run-branch:implement:execution:2",
        },
    )
    assert continued.status_code == 201
    parent_turn_id = continued.json()["branchTurnId"]

    forked = checkpoint_branch_client.post(
        "/api/executions/wf-api/checkpoint-branches/cbr-api/fork",
        json={
            "branchId": "cbr-api-child",
            "label": "Try child path",
            "parentTurnId": parent_turn_id,
            "instructionRef": "artifact://instructions/child",
            "instructionDigest": "sha256:child",
            "idempotencyKey": "MM-1099:cbr-api-child:create",
            "workspacePolicy": "continue_from_previous_execution",
            "runtimeContextPolicy": "fresh_agent_run",
        },
    )
    assert forked.status_code == 201
    assert forked.json()["branch"]["parentBranchId"] == "cbr-api"
    assert forked.json()["branch"]["parentTurnId"] == parent_turn_id

    archived = checkpoint_branch_client.post(
        "/api/executions/wf-api/checkpoint-branches/cbr-api-child/archive",
        json={"idempotencyKey": "MM-1099:cbr-api-child:archive"},
    )
    assert archived.status_code == 200
    assert archived.json()["state"] == "archived"

    publish_ready = checkpoint_branch_client.post(
        "/api/executions/wf-api/checkpoint-branches/cbr-api/publish-ready",
        json={
            "artifactRef": "artifact://publish/candidate",
            "idempotencyKey": "MM-1099:cbr-api:publish-ready",
        },
    )
    assert publish_ready.status_code == 200
    assert publish_ready.json()["state"] == "promotable"
    assert publish_ready.json()["promotedAt"] is None

    listed = checkpoint_branch_client.get(
        "/api/executions/wf-api/checkpoint-branches"
    )
    assert listed.status_code == 200
    ids = {item["branch"]["branchId"]: item for item in listed.json()["items"]}
    assert ids["cbr-api"]["branch"]["state"] == "promotable"
    assert ids["cbr-api-child"]["branch"]["state"] == "archived"
    assert any(
        artifact["artifactKind"] == "publish_ready"
        for artifact in ids["cbr-api"]["artifacts"]
    )


def test_checkpoint_branch_api_repeated_operations_are_idempotent(
    checkpoint_branch_client: TestClient,
) -> None:
    create_payload = _create_payload("cbr-api-idempotent")

    created = checkpoint_branch_client.post(
        "/api/executions/wf-api/checkpoint-branches",
        json=create_payload,
    )
    duplicate_created = checkpoint_branch_client.post(
        "/api/executions/wf-api/checkpoint-branches",
        json=create_payload,
    )

    assert created.status_code == 201
    assert duplicate_created.status_code == 201
    assert (
        duplicate_created.json()["turns"][0]["branchTurnId"]
        == created.json()["turns"][0]["branchTurnId"]
    )

    continue_payload = {
        "instructionRef": "artifact://instructions/continue",
        "instructionDigest": "sha256:continue",
        "idempotencyKey": "MM-1099:cbr-api-idempotent:continue",
        "createdStepExecutionId": "wf-api:run-branch:implement:execution:2",
    }
    continued = checkpoint_branch_client.post(
        "/api/executions/wf-api/checkpoint-branches/cbr-api-idempotent/continue",
        json=continue_payload,
    )
    duplicate_continued = checkpoint_branch_client.post(
        "/api/executions/wf-api/checkpoint-branches/cbr-api-idempotent/continue",
        json=continue_payload,
    )

    assert continued.status_code == 201
    assert duplicate_continued.status_code == 201
    assert (
        duplicate_continued.json()["branchTurnId"]
        == continued.json()["branchTurnId"]
    )

    fork_payload = {
        "branchId": "cbr-api-idempotent-child",
        "label": "Try child path",
        "parentTurnId": continued.json()["branchTurnId"],
        "instructionRef": "artifact://instructions/child",
        "instructionDigest": "sha256:child",
        "idempotencyKey": "MM-1099:cbr-api-idempotent-child:create",
        "workspacePolicy": "continue_from_previous_execution",
        "runtimeContextPolicy": "fresh_agent_run",
    }
    forked = checkpoint_branch_client.post(
        "/api/executions/wf-api/checkpoint-branches/cbr-api-idempotent/fork",
        json=fork_payload,
    )
    duplicate_forked = checkpoint_branch_client.post(
        "/api/executions/wf-api/checkpoint-branches/cbr-api-idempotent/fork",
        json=fork_payload,
    )

    assert forked.status_code == 201
    assert duplicate_forked.status_code == 201
    assert (
        duplicate_forked.json()["turns"][0]["branchTurnId"]
        == forked.json()["turns"][0]["branchTurnId"]
    )

    archive_payload = {"idempotencyKey": "MM-1099:cbr-api-idempotent-child:archive"}
    archived = checkpoint_branch_client.post(
        "/api/executions/wf-api/checkpoint-branches/cbr-api-idempotent-child/archive",
        json=archive_payload,
    )
    duplicate_archived = checkpoint_branch_client.post(
        "/api/executions/wf-api/checkpoint-branches/cbr-api-idempotent-child/archive",
        json=archive_payload,
    )

    assert archived.status_code == 200
    assert duplicate_archived.status_code == 200
    assert duplicate_archived.json()["archivedAt"] == archived.json()["archivedAt"]

    publish_payload = {
        "artifactRef": "artifact://publish/idempotent",
        "idempotencyKey": "MM-1099:cbr-api-idempotent:publish-ready",
    }
    publish_ready = checkpoint_branch_client.post(
        "/api/executions/wf-api/checkpoint-branches/cbr-api-idempotent/publish-ready",
        json=publish_payload,
    )
    duplicate_publish_ready = checkpoint_branch_client.post(
        "/api/executions/wf-api/checkpoint-branches/cbr-api-idempotent/publish-ready",
        json=publish_payload,
    )

    assert publish_ready.status_code == 200
    assert duplicate_publish_ready.status_code == 200
    assert duplicate_publish_ready.json()["state"] == "promotable"

    listed = checkpoint_branch_client.get(
        "/api/executions/wf-api/checkpoint-branches"
    )
    branches = {item["branch"]["branchId"]: item for item in listed.json()["items"]}
    assert sorted(branches) == [
        "cbr-api-idempotent",
        "cbr-api-idempotent-child",
    ]
    assert len(branches["cbr-api-idempotent"]["turns"]) == 2
    assert len(branches["cbr-api-idempotent-child"]["turns"]) == 1
    publish_ready_artifacts = [
        artifact
        for artifact in branches["cbr-api-idempotent"]["artifacts"]
        if artifact["artifactKind"] == "publish_ready"
    ]
    assert len(publish_ready_artifacts) == 1
    assert publish_ready_artifacts[0]["artifactRef"] == "artifact://publish/idempotent"


def test_checkpoint_branch_api_lists_and_reads_inactive_evidence_by_default(
    checkpoint_branch_client: TestClient,
) -> None:
    for branch_id in ("cbr-active", "cbr-archived"):
        assert checkpoint_branch_client.post(
            "/api/executions/wf-api/checkpoint-branches",
            json=_create_payload(branch_id),
        ).status_code == 201
    assert checkpoint_branch_client.post(
        "/api/executions/wf-api/checkpoint-branches/cbr-archived/archive",
        json={"idempotencyKey": "MM-1099:cbr-archived:archive"},
    ).status_code == 200

    listed = checkpoint_branch_client.get(
        "/api/executions/wf-api/checkpoint-branches"
    )
    active_only = checkpoint_branch_client.get(
        "/api/executions/wf-api/checkpoint-branches?activeOnly=true"
    )
    archived_read = checkpoint_branch_client.get(
        "/api/executions/wf-api/checkpoint-branches/cbr-archived"
    )

    assert listed.status_code == 200
    assert {item["branch"]["branchId"] for item in listed.json()["items"]} == {
        "cbr-active",
        "cbr-archived",
    }
    assert active_only.status_code == 200
    assert [item["branch"]["branchId"] for item in active_only.json()["items"]] == [
        "cbr-active"
    ]
    assert archived_read.status_code == 200
    assert archived_read.json()["branch"]["state"] == "archived"
    assert archived_read.json()["turns"]


def test_checkpoint_branch_api_publish_ready_does_not_promote_or_publish(
    checkpoint_branch_client: TestClient,
) -> None:
    assert checkpoint_branch_client.post(
        "/api/executions/wf-api/checkpoint-branches",
        json=_create_payload("cbr-publish-ready"),
    ).status_code == 201

    response = checkpoint_branch_client.post(
        "/api/executions/wf-api/checkpoint-branches/cbr-publish-ready/publish-ready",
        json={
            "artifactRef": "artifact://publish/candidate",
            "idempotencyKey": "MM-1099:cbr-publish-ready:publish-ready",
        },
    )
    read = checkpoint_branch_client.get(
        "/api/executions/wf-api/checkpoint-branches/cbr-publish-ready"
    )

    assert response.status_code == 200
    assert response.json()["state"] == "promotable"
    assert response.json()["promotedAt"] is None
    assert read.json()["branch"]["state"] == "promotable"
    assert read.json()["branch"]["promotedAt"] is None
    assert any(
        artifact["artifactKind"] == "publish_ready"
        for artifact in read.json()["artifacts"]
    )


def test_checkpoint_branch_api_rejects_missing_source_identity(
    checkpoint_branch_client: TestClient,
) -> None:
    payload = _create_payload()
    payload["source"] = {"workflowId": "wf-api", "runId": "run-api"}

    response = checkpoint_branch_client.post(
        "/api/executions/wf-api/checkpoint-branches",
        json=payload,
    )

    assert response.status_code == 422


def test_checkpoint_branch_api_scopes_reads_to_workflow(
    checkpoint_branch_client: TestClient,
) -> None:
    assert checkpoint_branch_client.post(
        "/api/executions/wf-api/checkpoint-branches",
        json=_create_payload("cbr-scope"),
    ).status_code == 201

    response = checkpoint_branch_client.get(
        "/api/executions/wf-other/checkpoint-branches/cbr-scope"
    )

    assert response.status_code == 404
