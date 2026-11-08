"""Trusted scheduled input admission preserves exact current raw-read rights."""

from datetime import UTC, datetime, timedelta

import pytest

from moonmind.workflows.temporal.artifacts import TemporalArtifactStateError
from tests.artifact_access import access_for_test
from tests.unit.workflows.temporal import (
    test_artifact_evidence_boundaries as evidence_fixtures,
)

evidence_service = evidence_fixtures.evidence_service
put = evidence_fixtures.put

pytestmark = pytest.mark.asyncio


async def test_raw_delegation_cannot_revive_expired_selected_source(evidence_service):
    service, session = evidence_service
    artifact = await put(service)
    artifact.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    with pytest.raises(TemporalArtifactStateError, match="expired"):
        await service.delegate_read_access(
            artifact_ids=[artifact.artifact_id],
            workflow_id="scheduled-occurrence",
            access=access_for_test("owner"),
        )
    assert not await service._repository.list_links(artifact.artifact_id)


@pytest.mark.parametrize("placement", ["objective", "step"])
async def test_schedule_save_admits_only_selected_direct_inputs(
    evidence_service, placement
):
    from uuid import uuid4

    from api_service.services.recurring_workflows_service import (
        RecurringWorkflowsService,
    )
    from moonmind.workflows.temporal.artifacts import ArtifactAccessContext
    from moonmind.workflows.temporal.schedule_mapping import scheduled_input_scope

    service, session = evidence_service
    selected = await put(service)
    foreign = await put(service, b"Unselected source")
    definition_id = uuid4()
    target = {
        "workflowType": "MoonMind.UserWorkflow",
        "initialParameters": {
            "workflow": {
                "inputAttachments": [
                    {"artifactId": selected.artifact_id, "filename": "evidence.txt"}
                ]
            }
        },
    }
    if placement == "step":
        workflow = target["initialParameters"]["workflow"]
        workflow["steps"] = [
            {"id": "assess", "inputAttachments": workflow.pop("inputAttachments")}
        ]
    owner = RecurringWorkflowsService(session, artifact_service=service)
    root = await owner._retain_target_artifacts(
        target, definition_id=definition_id, access=access_for_test("owner")
    )
    assert root
    revision = ArtifactAccessContext.for_execution(
        scheduled_input_scope(definition_id, root)
    )
    assert (await service.read(artifact_id=selected.artifact_id, access=revision))[
        1
    ] == b"Useful reproduction"
    from moonmind.workflows.temporal.artifacts import TemporalArtifactAuthorizationError

    with pytest.raises(TemporalArtifactAuthorizationError):
        await service.read(artifact_id=foreign.artifact_id, access=revision)
    _, start = owner._workflow_bundle_for_target(
        definition_id=definition_id,
        name="Saved",
        owner_user_id=None,
        target_payload=target,
        input_artifact_root_id=root,
    )
    assert start["scheduled_input_artifact_ref"] == root


@pytest.mark.parametrize(
    "change",
    [
        "valid",
        "foreign",
        "revoked",
        "expired",
        "forged-root",
        "wrong-run",
        "revoked-retry",
        "corrupt-retry",
    ],
    ids=str,
)
async def test_actual_activity_admits_exact_saved_revision(
    evidence_service, monkeypatch, change
):
    from dataclasses import replace
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy import delete
    from temporalio.testing import ActivityEnvironment

    from api_service.db.models import TemporalArtifactLink
    from api_service.services.recurring_workflows_service import (
        RecurringWorkflowsService,
    )
    from moonmind.workflows.temporal.artifacts import (
        ArtifactAccessContext,
        TemporalArtifactActivities,
        TemporalArtifactError,
    )
    from moonmind.workflows.temporal.client import TemporalClientAdapter
    from moonmind.workflows.temporal.schedule_mapping import scheduled_input_scope

    service, session = evidence_service
    selected = await put(service)
    foreign = await put(service, b"Foreign")
    definition_id = uuid4()
    workflow_id = f"mm:{definition_id}-2026-11-08T12:00:00Z"
    target = {
        "workflowType": "MoonMind.UserWorkflow",
        "initialParameters": {
            "workflow": {"inputAttachments": [{"artifactId": selected.artifact_id}]}
        },
    }
    owner = RecurringWorkflowsService(session, artifact_service=service)
    root = await owner._retain_target_artifacts(
        target, definition_id=definition_id, access=access_for_test("owner")
    )
    _, start = owner._workflow_bundle_for_target(
        definition_id=definition_id,
        name="Saved",
        owner_user_id=None,
        target_payload=target,
        input_artifact_root_id=root,
    )

    # No current definition or recent_actions exist. Immutable start owns the
    # already launched revision even after replacement/deletion/history eviction.
    async def read_start(self, workflow, *, run_id):
        assert workflow == workflow_id and run_id == "actual-run"
        return start

    async def get_client(self):
        return SimpleNamespace(namespace="default")

    monkeypatch.setattr(
        TemporalClientAdapter, "read_scheduled_workflow_start_input", read_start
    )
    monkeypatch.setattr(TemporalClientAdapter, "get_client", get_client)
    ref = selected.artifact_id
    if change == "foreign":
        ref = foreign.artifact_id
    elif change == "revoked":
        await session.execute(
            delete(TemporalArtifactLink).where(
                TemporalArtifactLink.artifact_id == ref,
                TemporalArtifactLink.workflow_id
                == scheduled_input_scope(definition_id, root),
            )
        )
    elif change == "expired":
        selected.expires_at = datetime.now(UTC) - timedelta(days=1)
        (await service._repository.get_artifact(root)).expires_at = selected.expires_at
    elif change == "forged-root":
        start["scheduled_input_artifact_ref"] = foreign.artifact_id
    await session.commit()
    env = ActivityEnvironment()
    env.info = replace(
        env.info,
        namespace="default",
        workflow_id=workflow_id,
        workflow_run_id="actual-run",
    )
    payload = {
        "artifact_id": ref,
        "principal": "operator",
        "execution_ref": {
            "namespace": "default",
            "workflow_id": workflow_id,
            "run_id": "wrong-run" if change == "wrong-run" else "actual-run",
            "link_type": "input.attachment",
        },
    }
    activity = TemporalArtifactActivities(service)
    if change in {"revoked-retry", "corrupt-retry"}:
        await env.run(activity.artifact_link, **payload)
        if change == "revoked-retry":
            await session.execute(
                delete(TemporalArtifactLink).where(
                    TemporalArtifactLink.artifact_id == ref,
                    TemporalArtifactLink.workflow_id
                    == scheduled_input_scope(definition_id, root),
                )
            )
            await session.commit()
        else:
            service._store.write_bytes(
                selected.storage_key, b"Corrupt source", content_type="text/plain"
            )
        with pytest.raises(TemporalArtifactError):
            await env.run(activity.artifact_link, **payload)
        return
    if change == "valid":
        await env.run(activity.artifact_link, **payload)
        await env.run(activity.artifact_link, **payload)
        assert (
            await service.read(
                artifact_id=ref, access=ArtifactAccessContext.for_execution(workflow_id)
            )
        )[1] == b"Useful reproduction"
    else:
        with pytest.raises(TemporalArtifactError):
            await env.run(activity.artifact_link, **payload)
        assert not any(
            link.workflow_id == workflow_id
            for link in await service._repository.list_links(ref)
        )


@pytest.mark.parametrize(
    "change",
    [
        "continued",
        "copied-root",
        "parent",
        "wrong-definition",
        "wrong-type",
        "wrong-history",
    ],
)
async def test_original_start_owner_qualifies_scheduler_identity(monkeypatch, change):
    from types import SimpleNamespace
    from uuid import uuid4

    from temporalio.api.history.v1 import WorkflowExecutionStartedEventAttributes
    from temporalio.converter import DataConverter

    from moonmind.workflows.temporal.client import TemporalClientAdapter

    definition = uuid4()
    scheduled_id = f"mm:{definition}-2026-11-08T12:00:00Z"
    workflow_id = f"mm:{uuid4()}" if change == "copied-root" else scheduled_id
    start = {
        "workflow_type": "MoonMind.UserWorkflow",
        "scheduled_input_artifact_ref": "art_selected",
        "initial_parameters": {
            "system": {"recurrence": {"definitionId": str(definition)}}
        },
    }
    if change == "wrong-definition":
        start["initial_parameters"]["system"]["recurrence"]["definitionId"] = str(
            uuid4()
        )
    original = WorkflowExecutionStartedEventAttributes()
    original.workflow_type.name = (
        "MoonMind.AgentRun" if change == "wrong-type" else "MoonMind.UserWorkflow"
    )
    original.workflow_id = "mm:other" if change == "wrong-history" else workflow_id
    original.first_execution_run_id = "original-run"
    original.input.payloads.extend(await DataConverter.default.encode([start]))
    if change == "parent":
        original.parent_workflow_execution.workflow_id = "parent"
    continued = WorkflowExecutionStartedEventAttributes()
    continued.first_execution_run_id = "original-run"
    continued.input.payloads.extend(
        await DataConverter.default.encode(
            [{**start, "scheduled_input_artifact_ref": "art_mutated"}]
        )
    )
    reads = []

    async def attributes(self, workflow, *, run_id):
        assert workflow == workflow_id
        reads.append(run_id)
        return continued if run_id == "continued-run" else original

    async def client(self):
        return SimpleNamespace(data_converter=DataConverter.default)

    monkeypatch.setattr(
        TemporalClientAdapter, "_read_workflow_start_attributes", attributes
    )
    monkeypatch.setattr(TemporalClientAdapter, "get_client", client)
    adapter = TemporalClientAdapter()
    if change == "continued":
        admitted = await adapter.read_scheduled_workflow_start_input(
            workflow_id, run_id="continued-run"
        )
        assert admitted["scheduled_input_artifact_ref"] == "art_selected"
        assert reads == ["continued-run", "original-run"]
    else:
        with pytest.raises(ValueError):
            await adapter.read_scheduled_workflow_start_input(
                workflow_id, run_id="original-run"
            )


async def test_authored_top_level_revision_is_rejected():
    from api_service.services.recurring_workflows_service import (
        RecurringWorkflowValidationError,
        _normalize_target,
    )

    with pytest.raises(RecurringWorkflowValidationError, match="server-owned"):
        _normalize_target(
            {
                "workflowType": "MoonMind.UserWorkflow",
                "scheduled_input_artifact_ref": "art_copied",
            }
        )


@pytest.mark.parametrize("fault", ["timeout", "unavailable", "identity", "denied"])
async def test_registered_link_keeps_bounded_retries_for_transient_start_reads(
    evidence_service, monkeypatch, fault
):
    from dataclasses import replace
    from types import SimpleNamespace
    from uuid import uuid4

    from temporalio.exceptions import ApplicationError
    from temporalio.service import RPCError, RPCStatusCode
    from temporalio.testing import ActivityEnvironment

    from moonmind.workflows.temporal.activity_catalog import (
        TemporalActivityCatalog,
        build_default_activity_catalog,
    )
    from moonmind.workflows.temporal.activity_runtime import build_activity_bindings
    from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities
    from moonmind.workflows.temporal.client import TemporalClientAdapter

    service, _ = evidence_service
    workflow_id = f"mm:{uuid4()}-2026-11-08T12:00:00Z"

    async def client(self):
        return SimpleNamespace(namespace="default")

    async def read(self, workflow_id, *, run_id):
        if fault == "timeout":
            raise TimeoutError("synthetic transport detail")
        if fault == "identity":
            raise ValueError("synthetic malformed start")
        raise RPCError(
            "synthetic transport detail",
            RPCStatusCode.UNAVAILABLE
            if fault == "unavailable"
            else RPCStatusCode.PERMISSION_DENIED,
            b"",
        )

    monkeypatch.setattr(TemporalClientAdapter, "get_client", client)
    monkeypatch.setattr(
        TemporalClientAdapter, "read_scheduled_workflow_start_input", read
    )
    catalog = build_default_activity_catalog()
    selected = TemporalActivityCatalog(
        activities=tuple(
            d for d in catalog.activities if d.activity_type == "artifact.link"
        ),
        fleets=catalog.fleets,
    )
    (binding,) = build_activity_bindings(
        selected, artifact_activities=TemporalArtifactActivities(service)
    )
    env = ActivityEnvironment()
    env.info = replace(
        env.info, namespace="default", workflow_id=workflow_id, workflow_run_id="run"
    )
    with pytest.raises(ApplicationError) as failure:
        await env.run(
            binding.handler,
            {
                "artifact_id": "art_selected",
                "principal": "operator",
                "execution_ref": {
                    "namespace": "default",
                    "workflow_id": workflow_id,
                    "run_id": "run",
                    "link_type": "input.attachment",
                },
            },
        )
    assert failure.value.non_retryable is (fault in {"identity", "denied"})
    assert "synthetic" not in str(failure.value)


@pytest.mark.parametrize("state", ["ordinary", "restricted", "expired", "foreign"])
async def test_legacy_native_selected_input_backfill_preserves_current_rights(
    evidence_service, monkeypatch, state
):
    from dataclasses import replace
    from types import SimpleNamespace
    from uuid import uuid4

    from temporalio.testing import ActivityEnvironment

    from api_service.db.models import (
        RecurringWorkflowDefinition,
        TemporalArtifactRedactionLevel,
    )
    from api_service.services.recurring_workflows_service import (
        RecurringWorkflowsService,
        RecurringWorkflowValidationError,
    )
    from moonmind.workflows.temporal.artifacts import (
        ArtifactAccessContext,
        TemporalArtifactActivities,
        TemporalArtifactAuthorizationError,
    )
    from moonmind.workflows.temporal.client import TemporalClientAdapter

    service, session = evidence_service
    selected = await put(service)
    if state != "restricted":
        selected.redaction_level = TemporalArtifactRedactionLevel.NONE
    foreign = await put(service, b"Foreign input")
    definition_id = uuid4()
    target = {
        "workflowType": "MoonMind.UserWorkflow",
        "initialParameters": {
            "workflow": {
                "steps": [
                    {
                        "id": "assess",
                        "inputAttachments": [{"artifactId": selected.artifact_id}],
                    }
                ]
            }
        },
    }
    definition = RecurringWorkflowDefinition(
        id=definition_id,
        name="Retained native schedule",
        cron="0 6 * * *",
        timezone="UTC",
        target=target,
    )
    session.add(definition)
    if state == "expired":
        selected.expires_at = datetime.now(UTC) - timedelta(days=1)
    await session.commit()
    assert definition.input_artifact_root_id is None
    owner = RecurringWorkflowsService(session, artifact_service=service)
    if state == "foreign":
        with pytest.raises(RecurringWorkflowValidationError):
            await owner._retain_target_artifacts(
                {"inputArtifactRef": foreign.artifact_id}, definition_id=definition_id
            )
        return
    if state in {"restricted", "expired"}:
        with pytest.raises(RecurringWorkflowValidationError):
            await owner._retain_frozen_schedule_skill_inputs(definition)
        assert definition.input_artifact_root_id is None
        assert not await service._repository.list_links(selected.artifact_id)
        return
    assert await owner._retain_frozen_schedule_skill_inputs(definition)
    await session.commit()
    _, start = owner._workflow_bundle_for_definition(definition)
    workflow_id = f"mm:{definition_id}-2026-11-08T12:00:00Z"

    async def client(self):
        return SimpleNamespace(namespace="default")

    async def original_start(self, workflow, *, run_id):
        assert workflow == workflow_id and run_id == "run"
        return start

    monkeypatch.setattr(TemporalClientAdapter, "get_client", client)
    monkeypatch.setattr(
        TemporalClientAdapter, "read_scheduled_workflow_start_input", original_start
    )
    env = ActivityEnvironment()
    env.info = replace(
        env.info, namespace="default", workflow_id=workflow_id, workflow_run_id="run"
    )
    execution = {
        "namespace": "default",
        "workflow_id": workflow_id,
        "run_id": "run",
        "link_type": "input.attachment",
    }
    await env.run(
        TemporalArtifactActivities(service).artifact_link,
        artifact_id=selected.artifact_id,
        principal="operator",
        execution_ref=execution,
    )
    assert (
        await service.read(
            artifact_id=selected.artifact_id,
            access=ArtifactAccessContext.for_execution(workflow_id),
        )
    )[1] == b"Useful reproduction"
    with pytest.raises(TemporalArtifactAuthorizationError):
        await env.run(
            TemporalArtifactActivities(service).artifact_link,
            artifact_id=foreign.artifact_id,
            principal="operator",
            execution_ref=execution,
        )
