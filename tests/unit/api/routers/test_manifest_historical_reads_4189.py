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
from unittest.mock import AsyncMock, MagicMock, patch
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
    from api_service.api.routers import executions, temporal_artifacts
    from api_service.api.routers.executions import (
        _get_service,
        get_temporal_client,
        get_temporal_client_adapter,
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
            # Routes that reach Temporal directly (reschedule) or through the
            # adapter dependency (retry-publication) share the recorded adapter.
            app.dependency_overrides[get_temporal_client_adapter] = lambda: adapter
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
            with patch.object(
                executions, "get_temporal_client_adapter", lambda: adapter
            ):
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


async def _row_counts(session) -> dict[str, int]:
    """Row count of every table: any durable mutation side effect shows up."""
    from sqlalchemy import func, select

    from api_service.db.models import Base

    return {
        table.name: int(
            (await session.execute(select(func.count()).select_from(table))).scalar_one()
        )
        for table in Base.metadata.sorted_tables
    }


def _recover_body(record) -> dict:
    from moonmind.schemas.workflow_recovery_models import (
        deterministic_recovery_creation_key,
    )
    from moonmind.workflows.executions.runtime_capabilities import (
        resolve_runtime_execution_capabilities,
    )

    digest = "sha256:historical-checkpoint"
    return {
        "target": {
            "kind": "failed_step",
            "logicalStepId": "implement",
            "sourceStepExecutionId": "step-execution-1",
        },
        "source": {
            "workflowId": record.workflow_id,
            "runId": record.run_id,
            "planRef": "artifact://plan/source",
            "planDigest": "sha256:plan",
            "taskInputSnapshotRef": "artifact://snapshot/source",
        },
        "checkpoint": {
            "ref": "artifact://checkpoint/source",
            "boundary": "before_execution",
            "kind": "worktree_archive",
            "digest": digest,
            "validationRef": "artifact://checkpoint-validation",
            "sourceWorkspaceRef": "workspace://source",
        },
        "continuation": {"phase": "rerun_failed_step"},
        "capabilitySnapshot": resolve_runtime_execution_capabilities(
            "omnigent"
        ).model_dump(by_alias=True, mode="json"),
        "preservedStepRefs": [],
        "sideEffectDispositionRef": "artifact://side-effects",
        "sideEffectSafe": True,
        "destination": {
            "workflowId": "mm:historical-recovery-destination",
            "creationKey": deterministic_recovery_creation_key(
                record.workflow_id,
                record.run_id,
                "failed_step",
                digest,
                "rerun_failed_step",
            ),
            "runtimeId": "omnigent",
            "executionProfileRef": "provider-profile:primary",
            "workspaceReservationId": "workspace-reservation:destination",
        },
    }


# Each body passes route validation and each row state meets the route's own
# precondition for an ordinary run, so only the historical denial can reject
# the request. An open row is used where a live control would signal Temporal.
_OPEN = MoonMindWorkflowState.EXECUTING
_FAILED = MoonMindWorkflowState.FAILED
_MUTATIONS = [
    ("update", _OPEN, lambda r: {"updateName": "UpdateInputs", "parametersPatch": {"x": 1}}),
    ("update", _OPEN, lambda r: {"updateName": "SetTitle", "title": "renamed"}),
    ("update", _OPEN, lambda r: {"updateName": "RequestRerun"}),
    ("signal", _OPEN, lambda r: {"signalName": "Pause"}),
    ("signal", _OPEN, lambda r: {"signalName": "Resume"}),
    ("cancel", _OPEN, lambda r: {"reason": "retire"}),
    ("cancel", _OPEN, lambda r: {"reason": "retire", "graceful": False}),
    ("reschedule", MoonMindWorkflowState.SCHEDULED, lambda r: {"scheduledFor": "2030-01-01T00:00:00Z"}),
    ("rerun", _FAILED, lambda r: None),
    ("continue", _FAILED, lambda r: {"idempotencyKey": "historical-continue"}),
    ("recover", _FAILED, _recover_body),
    ("recover-from-failed-step", _FAILED, lambda r: {"idempotencyKey": "historical-recover"}),
    (
        "recover-from-selected-step",
        _FAILED,
        lambda r: {
            "idempotencyKey": "historical-resume",
            "sourceWorkflowId": r.workflow_id,
            "sourceRunId": r.run_id,
            "selectedStartStepId": "step-1",
        },
    ),
    ("retry-publication", _FAILED, lambda r: None),
    (
        "checkpoint-branches",
        _FAILED,
        lambda r: {
            "source": {
                "runId": r.run_id,
                "logicalStepId": "implement",
                "executionOrdinal": 1,
                "checkpointBoundary": "after_execution",
                "checkpointRef": "artifact://checkpoints/after-implement",
                "checkpointDigest": "sha256:checkpointdigest",
            },
            "label": "Historical branch",
            "instructions": {"text": "Continue from the checkpoint."},
            "workspacePolicy": "continue_from_previous_execution",
            "idempotencyKey": "historical-branch",
        },
    ),
]


@pytest.mark.parametrize(
    ("path", "state", "body"),
    _MUTATIONS,
    ids=[f"{path}-{index}" for index, (path, _s, _b) in enumerate(_MUTATIONS)],
)
@pytest.mark.asyncio
async def test_historical_manifest_run_rejects_every_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    state: MoonMindWorkflowState,
    body,
) -> None:
    from api_service.db.models import TemporalExecutionRecord
    from moonmind.config.settings import settings

    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "submit_enabled", True)
    owner = str(uuid4())
    terminal = state is _FAILED
    async with _historical_app(
        tmp_path,
        owner_id=owner,
        state=state,
        close_status=TemporalExecutionCloseStatus.FAILED if terminal else None,
        closed_at=datetime(2026, 9, 1, 12, 30, tzinfo=UTC) if terminal else None,
    ) as ctx:
        ctx.act_as(owner)
        record = ctx.record
        # A read materializes the projection row; count only mutation effects.
        read = await ctx.client.get(f"/api/executions/{record.workflow_id}")
        assert read.status_code == 200, read.text
        before = await _row_counts(ctx.session)

        response = await ctx.client.post(
            f"/api/executions/{record.workflow_id}/{path}", json=body(record)
        )

        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "execution_historical", detail
        assert "historical MoonMind.ManifestIngest execution" in detail["message"]
        assert _mutating_adapter_calls(ctx.adapter) == []
        await ctx.session.refresh(record)
        assert record.workflow_type is TemporalWorkflowType.MANIFEST_INGEST
        assert record.state is state
        assert record.rerun_count == 0
        assert await _row_counts(ctx.session) == before
        projection = await ctx.session.get(TemporalExecutionRecord, record.workflow_id)
        assert projection is None or projection.scheduled_for is None


@pytest.mark.parametrize(
    ("workflow_type", "entry", "expected_status", "signals"),
    [
        (TemporalWorkflowType.MANIFEST_INGEST, "manifest", 409, 0),
        # Control: the same request still reschedules an ordinary run.
        (TemporalWorkflowType.USER_WORKFLOW, "user_workflow", 202, 1),
    ],
)
@pytest.mark.asyncio
async def test_reschedule_reaches_temporal_only_for_ordinary_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    workflow_type: TemporalWorkflowType,
    entry: str,
    expected_status: int,
    signals: int,
) -> None:
    from moonmind.config.settings import settings

    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    owner = str(uuid4())
    async with _historical_app(
        tmp_path,
        owner_id=owner,
        workflow_type=workflow_type,
        entry=entry,
        state=MoonMindWorkflowState.SCHEDULED,
        close_status=None,
        closed_at=None,
    ) as ctx:
        ctx.act_as(owner)
        ctx.adapter.send_reschedule_signal = AsyncMock(return_value=None)

        response = await ctx.client.post(
            f"/api/executions/{ctx.record.workflow_id}/reschedule",
            json={"scheduledFor": "2030-01-01T00:00:00Z"},
        )

        assert response.status_code == expected_status, response.text
        assert ctx.adapter.send_reschedule_signal.await_count == signals


@pytest.mark.parametrize(
    "subpath",
    ["checkpoints", "checkpoint-branches", "continuations", "remediations"],
)
@pytest.mark.asyncio
async def test_historical_manifest_run_subresource_reads_are_not_denied(
    tmp_path: Path,
    subpath: str,
) -> None:
    owner = str(uuid4())
    async with _historical_app(tmp_path, owner_id=owner) as ctx:
        ctx.act_as(owner)

        response = await ctx.client.get(
            f"/api/executions/{ctx.record.workflow_id}/{subpath}"
        )

        assert response.status_code == 200, response.text
        assert _mutating_adapter_calls(ctx.adapter) == []


@pytest.mark.parametrize(
    "state",
    [MoonMindWorkflowState.SCHEDULED, MoonMindWorkflowState.EXECUTING],
)
@pytest.mark.asyncio
async def test_open_historical_manifest_run_offers_no_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: MoonMindWorkflowState,
) -> None:
    from moonmind.config.settings import settings

    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    owner = str(uuid4())
    async with _historical_app(
        tmp_path, owner_id=owner, state=state, close_status=None, closed_at=None
    ) as ctx:
        ctx.act_as(owner)

        detail = await ctx.client.get(f"/api/executions/{ctx.record.workflow_id}")

        assert detail.status_code == 200, detail.text
        actions = detail.json()["actions"]
        offered = {
            name for name, value in actions.items()
            if name.startswith("can") and value is True
        }
        assert offered == set()
        assert actions["disabledReasons"]["canCancel"] == "historical_workflow_type"
