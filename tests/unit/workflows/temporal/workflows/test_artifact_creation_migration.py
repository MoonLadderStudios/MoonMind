"""Retained create/write Activities keep verified producer authority after update."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from api_service.db import models
from moonmind.workflows.temporal import worker_runtime
from moonmind.workflows.temporal.activity_catalog import (
    ARTIFACTS_TASK_QUEUE,
    WORKFLOW_TASK_QUEUE,
    TemporalActivityCatalog,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import build_activity_bindings
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    TemporalArtifactActivities,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
    build_artifact_ref,
)
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from tests.unit.omnigent.test_admitted_input_authority import artifacts  # noqa: F401
from tests.unit.omnigent.test_admitted_input_authority import auth_mode  # noqa: F401

pytestmark = pytest.mark.asyncio


@workflow.defn(name="Fixture.ArtifactCreationMigration")
class _MigrationWorkflow:
    def __init__(self):
        self.created = None
        self.proceed = False

    @workflow.query
    def created_ref(self):
        return self.created

    @workflow.signal
    def resume(self):
        self.proceed = True

    @workflow.run
    async def run(self, request: dict) -> bytes:
        principal = "workflow:" + workflow.info().workflow_id
        created = await workflow.execute_activity(
            "artifact.create",
            {
                "principal": principal,
                "content_type": "text/plain",
                "redaction_level": request["redaction"],
            },
            task_queue=ARTIFACTS_TASK_QUEUE,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
        self.created = created[0]["artifact_id"]
        await workflow.wait_condition(lambda: self.proceed)
        target = request.get("target") or self.created
        await workflow.execute_activity(
            "artifact.write_complete",
            {
                "artifact_id": target,
                "principal": principal,
                "payload": "resumed bytes",
                "content_type": "text/plain",
            },
            task_queue=ARTIFACTS_TASK_QUEUE,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
        return await workflow.execute_activity(
            "artifact.read",
            {"artifact_ref": target},
            task_queue=ARTIFACTS_TASK_QUEUE,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )


@asynccontextmanager
async def _workers(client, handlers):
    async with Worker(
        client,
        task_queue=WORKFLOW_TASK_QUEUE,
        workflows=[_MigrationWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
        max_cached_workflows=0,
    ):
        async with Worker(client, task_queue=ARTIFACTS_TASK_QUEUE, activities=handlers):
            yield


@pytest.mark.parametrize(
    "fault",
    [
        "ordinary",
        "restricted",
        "complete_ordinary",
        "complete_restricted",
        "unrecorded_ref",
        "wrong_namespace",
        "quarantine",
        "current_restricted",
    ],
)
async def test_retained_artifact_creation_is_verified_before_write(
    artifacts, tmp_path, monkeypatch, fault  # noqa: F811
):
    monkeypatch.setattr(worker_runtime, "get_async_session_context", artifacts)
    catalog = build_default_activity_catalog()
    selected = TemporalActivityCatalog(
        activities=tuple(
            a
            for a in catalog.activities
            if a.activity_type
            in {"artifact.create", "artifact.write_complete", "artifact.read"}
        ),
        fleets=catalog.fleets,
    )
    handlers = {
        b.activity_type: b.handler
        for b in build_activity_bindings(
            selected,
            artifact_activities=TemporalArtifactActivities(
                worker_runtime._ArtifactServiceProxy()
            ),
        )
    }

    @activity.defn(name="artifact.create")
    async def legacy_create(request: dict):
        async with artifacts() as session:
            service = TemporalArtifactService(TemporalArtifactRepository(session))
            row, upload = await service.create(
                principal=request["principal"],
                content_type=request["content_type"],
                redaction_level=models.TemporalArtifactRedactionLevel(
                    request["redaction_level"]
                ),
            )
            if fault.startswith("complete_"):
                await service.write_complete(
                    artifact_id=row.artifact_id,
                    principal=request["principal"],
                    payload=b"resumed bytes",
                )
            return build_artifact_ref(row), upload

    workflow_id = "migration-" + fault
    target = None
    if fault == "unrecorded_ref":
        async with artifacts() as session:
            row, _ = await TemporalArtifactService(
                TemporalArtifactRepository(session)
            ).create(principal="workflow:" + workflow_id, content_type="text/plain")
            target = row.artifact_id
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
    ) as env:
        first = (
            handlers["artifact.create"]
            if fault == "current_restricted"
            else legacy_create
        )
        async with _workers(env.client, [first]):
            handle = await env.client.start_workflow(
                _MigrationWorkflow.run,
                {
                    "redaction": "restricted" if "restricted" in fault else "none",
                    "target": target,
                },
                id=workflow_id,
                task_queue=WORKFLOW_TASK_QUEUE,
            )
            for _ in range(100):
                created = await handle.query(_MigrationWorkflow.created_ref)
                if created:
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("creation did not complete before the bounded restart")
        async with artifacts() as session:
            repository = TemporalArtifactRepository(session)
            row = await repository.get_artifact(created)
            provenance = (row.artifact_id, row.created_by_principal)
            if fault != "current_restricted":
                assert await repository.list_links(created) == []
            if fault == "quarantine":
                row.metadata_json = {"quarantine": True}
                await session.commit()
        if fault == "wrong_namespace":
            monkeypatch.setattr(
                activity, "client", lambda: SimpleNamespace(namespace="foreign")
            )
        async with _workers(
            env.client, [handlers["artifact.write_complete"], handlers["artifact.read"]]
        ):
            await handle.signal(_MigrationWorkflow.resume)
            if fault in {"unrecorded_ref", "wrong_namespace", "quarantine"}:
                with pytest.raises(WorkflowFailureError):
                    await handle.result()
            else:
                assert await handle.result() == b"resumed bytes"
        async with artifacts() as session:
            repository = TemporalArtifactRepository(session)
            row = await repository.get_artifact(created)
            assert (row.artifact_id, row.created_by_principal) == provenance
            service = TemporalArtifactService(repository)
            if fault in {
                "ordinary",
                "restricted",
                "complete_ordinary",
                "complete_restricted",
                "current_restricted",
            }:
                access = ArtifactAccessContext.for_execution(workflow_id)
                assert not access.is_operator and not access.operator_recovery
                assert (await service.read(artifact_id=created, access=access))[
                    1
                ] == b"resumed bytes"
                with pytest.raises(TemporalArtifactAuthorizationError):
                    await service.read(
                        artifact_id=created,
                        access=ArtifactAccessContext.for_execution("unrelated"),
                    )
            else:
                assert await repository.list_links(target or created) == []
