"""Historical ManifestIngest reads and mutation denial — MoonLadderStudios/MoonMind#4189.

The retired ``MoonMind.ManifestIngest`` type has no registration, worker, or
launch path, but its old-release rows must stay readable through the generic
execution list/detail surfaces. These tests drive the real executions router
and the real ``TemporalExecutionService`` over a populated database (no
``describe_execution`` mock) and prove that restoring reads does not make the
historical row controllable.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionCloseStatus,
    TemporalExecutionOwnerType,
    TemporalWorkflowType,
)

# Adapter calls a read may make while Temporal no longer knows the run.
_READ_ADAPTER_CALLS = {"describe_workflow"}


@asynccontextmanager
async def _historical_app(tmp_path: Path, *, owner_id: str | None, **record_fields):
    from api_service.api.routers import temporal_artifacts
    from api_service.api.routers.executions import (
        _get_service,
        get_temporal_client,
        router,
    )
    from api_service.auth_providers import get_current_user, get_current_user_optional
    from api_service.db.base import get_async_session
    from api_service.db.models import Base
    from moonmind.workflows.temporal import (
        LocalTemporalArtifactStore,
        TemporalArtifactRepository,
        TemporalArtifactService,
    )
    from moonmind.workflows.temporal.service import TemporalExecutionService

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/historical_manifest_4189.db", future=True
    )
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with factory() as session:
            fields = {
                "workflow_id": f"mm:historical-manifest:{uuid4().hex[:8]}",
                "run_id": uuid4().hex,
                "namespace": "default",
                "workflow_type": TemporalWorkflowType.MANIFEST_INGEST,
                "owner_id": owner_id,
                "owner_type": TemporalExecutionOwnerType.USER,
                "state": MoonMindWorkflowState.COMPLETED,
                "close_status": TemporalExecutionCloseStatus.COMPLETED,
                "entry": "manifest",
                "memo": {"title": "Nightly manifest", "summary": "Manifest completed."},
                "manifest_ref": "art_manifest_compile_1",
                "plan_ref": "art_manifest_plan_1",
                "parameters": {"action": "run", "options": {}},
                "started_at": datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
                "closed_at": datetime(2026, 9, 1, 12, 30, tzinfo=UTC),
            }
            fields.update(record_fields)
            record = TemporalExecutionCanonicalRecord(**fields)
            session.add(record)
            await session.commit()
            await session.refresh(record)

            adapter = MagicMock()
            adapter.describe_workflow = AsyncMock(
                side_effect=RuntimeError("retired history is not live in Temporal")
            )
            service = TemporalExecutionService(session, client_adapter=adapter)

            artifacts = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(str(tmp_path / "artifacts")),
            )

            app = FastAPI()
            app.include_router(router)
            app.include_router(temporal_artifacts.router)
            principal = {"user": None}
            app.dependency_overrides[_get_service] = lambda: service
            app.dependency_overrides[
                temporal_artifacts._get_temporal_artifact_service
            ] = lambda: artifacts
            app.dependency_overrides[get_async_session] = lambda: session
            app.dependency_overrides[get_temporal_client] = _RetiredTemporalClient
            app.dependency_overrides[get_current_user()] = lambda: principal["user"]
            app.dependency_overrides[get_current_user_optional()] = (
                lambda: principal["user"]
            )

            def act_as(user_id: str, *, is_superuser: bool = False) -> None:
                principal["user"] = SimpleNamespace(
                    id=user_id,
                    email="operator@example.com",
                    is_active=True,
                    is_superuser=is_superuser,
                    roles=[],
                )

            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                yield SimpleNamespace(
                    client=client,
                    record=record,
                    adapter=adapter,
                    artifacts=artifacts,
                    session=session,
                    act_as=act_as,
                )
    finally:
        await engine.dispose()


def _mutating_adapter_calls(adapter: MagicMock) -> list[str]:
    return [
        name
        for name, _args, _kwargs in adapter.mock_calls
        if name.split(".")[0] not in _READ_ADAPTER_CALLS
        and not name.startswith("__")
    ]


class _RetiredTemporalClient:
    """Temporal no longer has the retired run; authoritative reads fall back."""

    def get_workflow_handle(self, workflow_id: str, **_kwargs):
        raise RuntimeError(f"{workflow_id} is not live in Temporal")


@pytest.mark.asyncio
async def test_owner_reads_historical_manifest_run_through_generic_list_and_detail(
    tmp_path: Path,
) -> None:
    owner = str(uuid4())
    async with _historical_app(tmp_path, owner_id=owner) as ctx:
        ctx.act_as(owner)
        record = ctx.record

        listed = await ctx.client.get("/api/executions", params={"entry": "manifest"})
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert body["count"] == 1
        [item] = body["items"]
        assert item["workflowId"] == record.workflow_id
        assert item["workflowType"] == "MoonMind.ManifestIngest"

        detail = await ctx.client.get(f"/api/executions/{record.workflow_id}")
        assert detail.status_code == 200, detail.text
        payload = detail.json()
        assert payload["workflowId"] == record.workflow_id
        assert payload["runId"] == record.run_id
        assert payload["workflowType"] == "MoonMind.ManifestIngest"
        assert payload["entry"] == "manifest"
        assert payload["state"] == "completed"
        assert payload["closeStatus"] == "completed"
        assert payload["planArtifactRef"] == "art_manifest_plan_1"
        assert payload["ownerId"] == owner
        # Historical display must not imply launchability.
        actions = payload["actions"]
        for capability in (
            "canRerun",
            "canEditForRerun",
            "canUpdateInputs",
            "canFailedStepResume",
            "canCancel",
            "canPause",
            "canResume",
        ):
            assert actions.get(capability) in (False, None), capability

        assert _mutating_adapter_calls(ctx.adapter) == []


@pytest.mark.asyncio
async def test_degraded_historical_row_does_not_acquire_system_ownership(
    tmp_path: Path,
) -> None:
    async with _historical_app(
        tmp_path,
        owner_id=None,
        memo={},
        close_status=None,
        state=MoonMindWorkflowState.FAILED,
        plan_ref=None,
    ) as ctx:
        ctx.act_as(str(uuid4()), is_superuser=True)

        detail = await ctx.client.get(f"/api/executions/{ctx.record.workflow_id}")
        assert detail.status_code == 200, detail.text
        payload = detail.json()
        assert payload["workflowType"] == "MoonMind.ManifestIngest"
        # No recorded owner is reported as unavailable, not as system-owned.
        assert payload["ownerType"] == "user"
        assert payload["ownerId"] == ""
        # A degraded row is never presented as a successful completion.
        assert payload["state"] == "failed"
        assert payload["closeStatus"] != "completed"
        assert payload["planArtifactRef"] is None


@pytest.mark.asyncio
async def test_historical_compile_input_is_read_from_stored_evidence(
    tmp_path: Path,
) -> None:
    # ``manifest_ref`` was the compile/summary input of the parent run. It is
    # read back verbatim from the stored row, without the deleted compiler.
    async with _historical_app(tmp_path, owner_id=str(uuid4())) as ctx:
        ctx.act_as(str(uuid4()), is_superuser=True)

        detail = await ctx.client.get(f"/api/executions/{ctx.record.workflow_id}")

        assert detail.status_code == 200, detail.text
        payload = detail.json()
        assert payload["manifestArtifactRef"] == "art_manifest_compile_1"
        assert payload["planArtifactRef"] == "art_manifest_plan_1"
        assert payload["phase"] is None
        assert payload["paused"] is None


@pytest.mark.asyncio
async def test_historical_node_execution_lineage_stays_on_the_child_run(
    tmp_path: Path,
) -> None:
    # ``manifestArtifactRef`` was node-execution lineage carried by child
    # UserWorkflow runs. The child stays an ordinary, readable UserWorkflow
    # and keeps that lineage; it is not reclassified as a Manifest run.
    parent_id = f"mm:historical-manifest:{uuid4().hex[:8]}"
    lineage = {
        "manifestIngestWorkflowId": parent_id,
        "manifestIngestRunId": "run-parent-1",
        "manifestArtifactRef": "art_manifest_nodes_1",
        "nodeId": "node-a",
    }
    owner = str(uuid4())
    async with _historical_app(
        tmp_path,
        owner_id=owner,
        workflow_id=f"{parent_id}:run-parent-1:node-a",
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        entry="user_workflow",
        manifest_ref=None,
        input_ref="art_manifest_nodes_1",
        parameters=lineage,
    ) as ctx:
        ctx.act_as(owner)

        detail = await ctx.client.get(f"/api/executions/{ctx.record.workflow_id}")

        assert detail.status_code == 200, detail.text
        payload = detail.json()
        assert payload["workflowType"] == "MoonMind.UserWorkflow"
        assert payload["entry"] == "user_workflow"
        assert payload["inputArtifactRef"] == "art_manifest_nodes_1"
        assert {
            key: payload["inputParameters"].get(key) for key in lineage
        } == lineage


@pytest.mark.asyncio
async def test_historical_manifest_run_artifacts_stay_readable(
    tmp_path: Path,
) -> None:
    from moonmind.workflows.temporal.artifacts import ExecutionRef

    owner = str(uuid4())
    async with _historical_app(tmp_path, owner_id=owner) as ctx:
        ctx.act_as(owner)
        record = ctx.record
        artifact, _upload = await ctx.artifacts.create(
            principal=owner,
            content_type="application/json",
            link=ExecutionRef(
                namespace=record.namespace,
                workflow_id=record.workflow_id,
                run_id=record.run_id,
                link_type="output.summary",
            ),
        )
        await ctx.artifacts.write_complete(
            artifact_id=artifact.artifact_id,
            principal=owner,
            payload=b'{"phase": "completed"}',
        )

        listed = await ctx.client.get(
            f"/api/executions/{record.namespace}/{record.workflow_id}/"
            f"{record.run_id}/artifacts"
        )
        assert listed.status_code == 200, listed.text
        assert [item["artifact_id"] for item in listed.json()["artifacts"]] == [
            artifact.artifact_id
        ]

        downloaded = await ctx.client.get(
            f"/api/artifacts/{artifact.artifact_id}/download"
        )
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content == b'{"phase": "completed"}'
        assert _mutating_adapter_calls(ctx.adapter) == []


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("update", {"updateName": "UpdateInputs", "parametersPatch": {"x": 1}}),
        ("update", {"updateName": "SetTitle", "title": "renamed"}),
        ("update", {"updateName": "RequestRerun"}),
        ("signal", {"signalName": "Pause"}),
        ("signal", {"signalName": "Resume"}),
        ("cancel", {"reason": "retire"}),
        ("cancel", {"reason": "retire", "graceful": False}),
        ("rerun", None),
        ("reschedule", {"scheduledFor": "2030-01-01T00:00:00Z"}),
        ("continue", {}),
        ("recover", {}),
        ("recover-from-failed-step", {}),
        ("retry-publication", {}),
        ("checkpoint-branches", {"checkpointRef": "art_checkpoint_1"}),
    ],
)
@pytest.mark.asyncio
async def test_historical_manifest_run_rejects_every_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    body: dict | None,
) -> None:
    from moonmind.config.settings import settings

    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    owner = str(uuid4())
    async with _historical_app(
        tmp_path,
        owner_id=owner,
        # An open historical row is the riskiest case: a new release must not
        # signal, cancel, or update a run it has no worker for.
        state=MoonMindWorkflowState.EXECUTING,
        close_status=None,
        closed_at=None,
    ) as ctx:
        ctx.act_as(owner)
        record = ctx.record

        response = await ctx.client.post(
            f"/api/executions/{record.workflow_id}/{path}", json=body
        )

        assert response.status_code in (404, 409, 422), response.text
        assert _mutating_adapter_calls(ctx.adapter) == []
        await ctx.session.refresh(record)
        assert record.workflow_type is TemporalWorkflowType.MANIFEST_INGEST
        assert record.state is MoonMindWorkflowState.EXECUTING
        assert record.rerun_count == 0
