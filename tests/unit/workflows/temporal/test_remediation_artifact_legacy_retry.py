"""Retained lifecycle evidence survives a provenance-hardening upgrade."""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from api_service.db.models import (
    TemporalArtifactLink,
    TemporalArtifactRedactionLevel,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionRemediationLink,
)
from moonmind.workflows.temporal import (
    ExecutionRef,
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.remediation_context import (
    RemediationContextError,
    RemediationLifecyclePublisher,
)
from tests.unit.workflows.temporal.test_remediation_context import temporal_db


async def _record(session, workflow_id, run_id):
    record = TemporalExecutionCanonicalRecord(
        workflow_id=workflow_id,
        run_id=run_id,
        namespace="moonmind",
        workflow_type="MoonMind.UserWorkflow",
        entry="run",
        artifact_refs=[],
    )
    session.add(record)
    await session.commit()
    return record


async def _legacy_artifact(
    service, record, *, principal, metadata=None,
    artifact_type="remediation.verification",
    name="reports/remediation_verification-action-1.json",
):
    artifact, _ = await service.create(
        principal=principal,
        content_type="application/json",
        link=ExecutionRef(
            namespace=record.namespace,
            workflow_id=record.workflow_id,
            run_id=record.run_id,
            link_type=artifact_type,
            label=name,
            created_by_activity_type="remediation.lifecycle.publish",
        ),
        metadata_json={
            "artifact_type": artifact_type,
            "name": name,
            "schemaVersion": "v1",
            "verificationOutcome": "still_failed",
            **(metadata or {}),
        },
        redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
    )
    return await service.write_complete(
        artifact_id=artifact.artifact_id,
        principal=principal,
        payload=b'{"outcome":"still_failed"}',
        content_type="application/json",
    )


async def _retry(session, service, record):
    return await RemediationLifecyclePublisher(
        session=session, artifact_service=service
    ).publish_json_artifact(
        remediation_workflow_id=record.workflow_id,
        artifact_type="remediation.verification",
        name="reports/remediation_verification-action-1.json",
        payload={"outcome": "verified_resolved"},
        extra_metadata={"verificationOutcome": "verified_resolved"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "principal", ["service:remediation-lifecycle", "service:remediation-tools"]
)
async def test_retry_reconciles_confirmed_legacy_service_artifact(tmp_path, principal):
    async with temporal_db(tmp_path) as session:
        record = await _record(session, "remediation", "original-run")
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        legacy = await _legacy_artifact(service, record, principal=principal)
        original_sha256 = legacy.sha256
        original_metadata = dict(legacy.metadata_json)
        # The upload was confirmed before the execution projection committed.
        assert record.artifact_refs == []
        retried = await _retry(session, service, record)
        assert retried.artifact_id == legacy.artifact_id
        assert retried.created_by_principal == principal
        assert retried.sha256 == original_sha256
        assert retried.metadata_json == original_metadata
        assert retried.metadata_json["verificationOutcome"] == "still_failed"
        assert legacy.artifact_id in record.artifact_refs
        _, content = await service.read(artifact_id=legacy.artifact_id, principal=principal)
        assert json.loads(content) == {"outcome": "still_failed"}
        assert (await _retry(session, service, record)).artifact_id == legacy.artifact_id
        links = (await session.execute(select(TemporalArtifactLink).where(
            TemporalArtifactLink.link_type == "remediation.verification"
        ))).scalars().all()
        assert [link.artifact_id for link in links] == [legacy.artifact_id]


@pytest.mark.asyncio
async def test_retry_reconciles_actor_artifact_only_from_approval_owner_ref(tmp_path):
    async with temporal_db(tmp_path) as session:
        target = await _record(session, "target", "target-run")
        record = await _record(session, "remediation", "original-run")
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        legacy = await _legacy_artifact(service, record, principal="operator")
        session.add(TemporalExecutionRemediationLink(
            remediation_workflow_id=record.workflow_id,
            remediation_run_id=record.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            mode="snapshot",
            authority_mode="approval_gated",
            approval_state={"artifactRefs": {"verification": legacy.artifact_id}},
        ))
        await session.commit()
        assert (await _retry(session, service, record)).artifact_id == legacy.artifact_id
        assert legacy.created_by_principal == "operator"


@pytest.mark.asyncio
async def test_retry_reuses_legacy_target_annotation_and_supplemental_link(tmp_path):
    async with temporal_db(tmp_path) as session:
        target = await _record(session, "target", "target-run")
        record = await _record(session, "remediation", "original-run")
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        name = "annotations/remediation_target-action-1.json"
        legacy = await _legacy_artifact(
            service, record, principal="service:remediation-tools",
            artifact_type="remediation.target_annotation", name=name,
            metadata={"targetWorkflowId": target.workflow_id, "targetRunId": target.run_id},
        )
        await service.link_artifact(
            artifact_id=legacy.artifact_id,
            principal="operator",
            execution_ref=ExecutionRef(
                namespace=target.namespace,
                workflow_id=target.workflow_id,
                run_id=target.run_id,
                link_type="remediation.target_annotation",
                label=name,
                created_by_activity_type="remediation.target.annotation.publish",
            ),
        )
        retried = await RemediationLifecyclePublisher(
            session=session, artifact_service=service
        ).publish_target_annotation(
            remediation_workflow_id=record.workflow_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            name=name,
            payload={"outcome": "verified_resolved"},
        )
        assert retried.artifact_id == legacy.artifact_id
        links = (await session.execute(select(TemporalArtifactLink).where(
            TemporalArtifactLink.artifact_id == legacy.artifact_id
        ))).scalars().all()
        assert len(links) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["other-workflow", "prior-run"])
async def test_retry_rejects_relinked_legacy_service_artifact(tmp_path, origin):
    async with temporal_db(tmp_path) as session:
        record = await _record(session, "remediation", "current-run")
        other = await _record(session, "other", "other-run")
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        if origin == "prior-run":
            other = SimpleNamespace(
                namespace=record.namespace,
                workflow_id=record.workflow_id,
                run_id="prior-run",
            )
        legacy = await _legacy_artifact(
            service, other, principal="service:remediation-tools"
        )
        await service.link_artifact(
            artifact_id=legacy.artifact_id,
            principal="operator",
            execution_ref=ExecutionRef(
                namespace="moonmind",
                workflow_id="remediation",
                run_id="current-run",
                link_type="remediation.verification",
                label="reports/remediation_verification-action-1.json",
                created_by_activity_type="remediation.lifecycle.publish",
            ),
        )
        published = await _retry(session, service, record)
        assert published.artifact_id != legacy.artifact_id
        assert "workflowId" not in legacy.metadata_json
        links = (await session.execute(select(TemporalArtifactLink).where(
            TemporalArtifactLink.artifact_id == legacy.artifact_id
        ))).scalars().all()
        assert len(links) == 2


@pytest.mark.asyncio
async def test_retry_preserves_unverifiable_actor_evidence_without_competing_output(tmp_path):
    async with temporal_db(tmp_path) as session:
        record = await _record(session, "remediation", "original-run")
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        forged = await _legacy_artifact(service, record, principal="operator")
        # Generic refs can contain imported/provider evidence; they are not a
        # lifecycle producer's durable acknowledgement.
        record.artifact_refs = [forged.artifact_id]
        await session.commit()
        with pytest.raises(RemediationContextError, match="producer cannot be verified"):
            await _retry(session, service, record)
        links = (await session.execute(select(TemporalArtifactLink).where(
            TemporalArtifactLink.link_type == "remediation.verification"
        ))).scalars().all()
        assert [link.artifact_id for link in links] == [forged.artifact_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("run_id", ["unrelated-run", "original-run"])
async def test_retry_does_not_backfill_partial_or_conflicting_legacy_identity(tmp_path, run_id):
    async with temporal_db(tmp_path) as session:
        record = await _record(session, "remediation", "original-run")
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        legacy = await _legacy_artifact(
            service, record, principal="service:remediation-tools",
            metadata={"runId": run_id},
        )
        assert (await _retry(session, service, record)).artifact_id != legacy.artifact_id
        assert legacy.metadata_json["runId"] == run_id
