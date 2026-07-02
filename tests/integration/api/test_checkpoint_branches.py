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

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


@pytest_asyncio.fixture()
async def checkpoint_branch_integration_session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/branches-int.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        session.add(
            TemporalExecutionCanonicalRecord(
                workflow_id="wf-int",
                run_id="run-int",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                entry="api",
            )
        )
        await session.commit()
        yield session

    await engine.dispose()


@pytest.fixture()
def checkpoint_branch_integration_client(
    checkpoint_branch_integration_session: AsyncSession,
):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_async_session] = (
        lambda: checkpoint_branch_integration_session
    )
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _create_payload(branch_id: str = "cbr-int") -> dict[str, object]:
    return {
        "branchId": branch_id,
        "source": {
            "workflowId": "wf-int",
            "runId": "run-int",
            "logicalStepId": "implement",
            "sourceExecutionOrdinal": 2,
            "checkpointBoundary": "after_execution",
            "checkpointRef": "artifact://checkpoint/after",
            "checkpointDigest": "sha256:checkpoint",
        },
        "label": "Try integration branch",
        "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
        "runtimeContextPolicy": "fresh_agent_run",
        "instructionRef": f"artifact://instructions/{branch_id}/root",
        "instructionDigest": f"sha256:{branch_id}:root",
        "idempotencyKey": f"MM-1099:{branch_id}:create",
    }


def test_checkpoint_branch_graph_complete_api_workflow(
    checkpoint_branch_integration_client: TestClient,
) -> None:
    created = checkpoint_branch_integration_client.post(
        "/api/executions/wf-int/checkpoint-branches",
        json=_create_payload(),
    )
    duplicate_created = checkpoint_branch_integration_client.post(
        "/api/executions/wf-int/checkpoint-branches",
        json=_create_payload(),
    )
    assert created.status_code == 201
    assert duplicate_created.status_code == 201
    assert (
        duplicate_created.json()["turns"][0]["branchTurnId"]
        == created.json()["turns"][0]["branchTurnId"]
    )
    assert created.json()["branch"]["sourceCheckpointDigest"] == "sha256:checkpoint"

    continued = checkpoint_branch_integration_client.post(
        "/api/executions/wf-int/checkpoint-branches/cbr-int/continue",
        json={
            "instructionRef": "artifact://instructions/continue",
            "instructionDigest": "sha256:continue",
            "idempotencyKey": "MM-1099:cbr-int:continue",
            "createdStepExecutionId": "wf-int:run-branch:implement:execution:2",
        },
    )
    duplicate_continued = checkpoint_branch_integration_client.post(
        "/api/executions/wf-int/checkpoint-branches/cbr-int/continue",
        json={
            "instructionRef": "artifact://instructions/continue",
            "instructionDigest": "sha256:continue",
            "idempotencyKey": "MM-1099:cbr-int:continue",
            "createdStepExecutionId": "wf-int:run-branch:implement:execution:2",
        },
    )
    assert continued.status_code == 201
    assert duplicate_continued.status_code == 201
    assert (
        duplicate_continued.json()["branchTurnId"]
        == continued.json()["branchTurnId"]
    )

    forked = checkpoint_branch_integration_client.post(
        "/api/executions/wf-int/checkpoint-branches/cbr-int/fork",
        json={
            "branchId": "cbr-int-child",
            "label": "Try child path",
            "parentTurnId": continued.json()["branchTurnId"],
            "instructionRef": "artifact://instructions/child",
            "instructionDigest": "sha256:child",
            "idempotencyKey": "MM-1099:cbr-int-child:create",
            "workspacePolicy": "continue_from_previous_execution",
            "runtimeContextPolicy": "fresh_agent_run",
            "createdStepExecutionId": "wf-int:run-branch:implement:execution:3",
        },
    )
    assert forked.status_code == 201
    assert forked.json()["branch"]["parentBranchId"] == "cbr-int"
    assert forked.json()["branch"]["parentTurnId"] == continued.json()["branchTurnId"]

    archived = checkpoint_branch_integration_client.post(
        "/api/executions/wf-int/checkpoint-branches/cbr-int-child/archive",
        json={"idempotencyKey": "MM-1099:cbr-int-child:archive"},
    )
    publish_ready = checkpoint_branch_integration_client.post(
        "/api/executions/wf-int/checkpoint-branches/cbr-int/publish-ready",
        json={
            "artifactRef": "artifact://publish/candidate",
            "idempotencyKey": "MM-1099:cbr-int:publish-ready",
        },
    )
    assert archived.status_code == 200
    assert publish_ready.status_code == 200
    assert publish_ready.json()["state"] == "promotable"
    assert publish_ready.json()["promotedAt"] is None

    listed = checkpoint_branch_integration_client.get(
        "/api/executions/wf-int/checkpoint-branches"
    )
    active_only = checkpoint_branch_integration_client.get(
        "/api/executions/wf-int/checkpoint-branches?activeOnly=true"
    )
    child_read = checkpoint_branch_integration_client.get(
        "/api/executions/wf-int/checkpoint-branches/cbr-int-child"
    )
    parent_read = checkpoint_branch_integration_client.get(
        "/api/executions/wf-int/checkpoint-branches/cbr-int"
    )

    assert listed.status_code == 200
    branches = {item["branch"]["branchId"]: item for item in listed.json()["items"]}
    assert set(branches) == {"cbr-int", "cbr-int-child"}
    assert branches["cbr-int"]["branch"]["state"] == "promotable"
    assert branches["cbr-int-child"]["branch"]["state"] == "archived"
    assert (
        branches["cbr-int"]["branch"]["sourceCheckpointRef"]
        == "artifact://checkpoint/after"
    )
    assert (
        branches["cbr-int"]["branch"]["sourceCheckpointDigest"]
        == "sha256:checkpoint"
    )
    assert branches["cbr-int-child"]["branch"]["parentBranchId"] == "cbr-int"
    assert (
        branches["cbr-int-child"]["branch"]["parentTurnId"]
        == continued.json()["branchTurnId"]
    )
    assert any(
        artifact["artifactKind"] == "publish_ready"
        for artifact in branches["cbr-int"]["artifacts"]
    )
    assert all(
        turn["createdStepExecutionId"]
        not in {turn["branchId"], turn["branchTurnId"]}
        for item in branches.values()
        for turn in item["turns"]
    )
    assert [item["branch"]["branchId"] for item in active_only.json()["items"]] == [
        "cbr-int"
    ]
    assert child_read.status_code == 200
    assert child_read.json()["branch"]["state"] == "archived"
    assert child_read.json()["turns"]
    assert parent_read.status_code == 200
    assert parent_read.json()["branch"]["promotedAt"] is None
