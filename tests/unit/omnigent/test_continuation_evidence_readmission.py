"""Lost continuation links recover only from the server's exact copy admission."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import pytest_asyncio

from api_service.db import models
from api_service.services.linked_continuation import (
    SqlLinkedContinuationRepository,
    compute_request_digest,
)
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    LocalTemporalArtifactStore,
    TemporalArtifactActivities,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactStateError,
)
from temporalio.testing import ActivityEnvironment
from tests.unit.workflows.temporal.test_artifacts import temporal_db

WORKFLOW = "mm:continuation"
RUN = "destination-run"
NAMESPACE = "default"
SOURCE_REF = "artifact://omnigent/source/expired-provider-evidence"
PAYLOAD = b"previously authorized evidence\n"


async def _link(state, *, execution=None, principal="operator"):
    environment = ActivityEnvironment()
    environment.info = replace(
        environment.info,
        workflow_id=WORKFLOW,
        workflow_run_id=RUN,
        workflow_namespace=NAMESPACE,
        namespace=NAMESPACE,
    )
    return await environment.run(
        state.activities.artifact_link,
        artifact_id=state.artifact.artifact_id,
        principal=principal,
        execution_ref=execution
        or {
            "namespace": NAMESPACE,
            "workflow_id": WORKFLOW,
            "run_id": RUN,
            "link_type": "input.attachment",
        },
    )


@pytest_asyncio.fixture
async def admission(tmp_path):
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "blobs"),
        )
        artifact, _ = await service.create(
            principal="operator",
            content_type="text/plain",
            metadata_json={
                "artifact_class": "linked_continuation_source_evidence",
                "source_ref": SOURCE_REF,
            },
        )
        await service.write_complete(
            artifact_id=artifact.artifact_id, principal="operator", payload=PAYLOAD
        )
        copy = {
            "artifactId": artifact.artifact_id,
            "sha256": artifact.sha256,
            "sizeBytes": len(PAYLOAD),
            "contentType": "text/plain",
            "filename": "proof.txt",
            "sourceRef": SOURCE_REF,
        }
        canonical = models.TemporalExecutionCanonicalRecord(
            workflow_id=WORKFLOW,
            run_id=RUN,
            namespace=NAMESPACE,
            create_idempotency_key="continue:"
            + compute_request_digest(
                {
                    "sourceWorkflowId": "mm:source",
                    "sourceRunId": "source-run",
                    "idempotencyKey": "continue-once",
                }
            ),
            workflow_type=models.TemporalWorkflowType.USER_WORKFLOW,
            entry="user_workflow",
            owner_id="operator",
            owner_type=models.TemporalExecutionOwnerType.USER,
            state=models.MoonMindWorkflowState.PLANNING,
            parameters={
                "workflow": {
                    "inputAttachments": [
                        {
                            "artifactId": artifact.artifact_id,
                            "filename": "proof.txt",
                            "contentType": "text/plain",
                            "sizeBytes": len(PAYLOAD),
                        }
                    ]
                }
            },
        )
        receipt = models.WorkflowLinkedContinuationRecord(
            source_workflow_id="mm:source",
            source_run_id="source-run",
            idempotency_key="continue-once",
            request_digest="a" * 64,
            destination_workflow_id=WORKFLOW,
            destination_run_id=RUN,
            pinned_source_refs={
                "selectedSourceArtifactRefs": [SOURCE_REF],
                "evidenceCopyAdmission": {
                    "schemaVersion": 1,
                    "namespace": NAMESPACE,
                    "copies": [copy],
                },
            },
        )
        session.add_all([canonical, receipt])
        await session.commit()
        yield SimpleNamespace(
            sessions=sessions,
            service=service,
            activities=TemporalArtifactActivities(service),
            session=session,
            artifact=artifact,
            canonical=canonical,
            receipt=receipt,
        )


@pytest.mark.asyncio
async def test_exact_admitted_copy_recovers_without_original_source(admission):
    # No original source bytes/record exist. The durable copy must outlive them.
    await _link(admission)
    await _link(admission)
    _, payload = await admission.service.read(
        artifact_id=admission.artifact.artifact_id,
        access=ArtifactAccessContext.for_execution(WORKFLOW),
    )
    assert payload == PAYLOAD
    with pytest.raises(TemporalArtifactAuthorizationError):
        await admission.service.read(
            artifact_id=admission.artifact.artifact_id,
            access=ArtifactAccessContext.for_execution("mm:unrelated"),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_id", "mm:foreign"),
        ("namespace", "foreign"),
        ("run_id", "foreign-run"),
        ("link_type", "output"),
        ("link_type", "input.delegated_raw"),
    ],
)
async def test_activity_cannot_change_admitted_link_target(admission, field, value):
    execution = {
        "namespace": NAMESPACE,
        "workflow_id": WORKFLOW,
        "run_id": RUN,
        "link_type": "input.attachment",
    }
    execution[field] = value
    with pytest.raises(TemporalArtifactAuthorizationError):
        await _link(admission, execution=execution)
    assert (
        await admission.service._repository.list_links(admission.artifact.artifact_id)
        == []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "missing_receipt",
        "foreign_receipt",
        "missing_canonical",
        "foreign_canonical_namespace",
        "foreign_receipt_run",
        "foreign_canonical_run",
        "wrong_create_identity",
        "missing_proof",
        "authored_proof_only",
        "wrong_namespace",
        "wrong_version",
        "wrong_artifact",
        "wrong_digest",
        "wrong_size",
        "wrong_type",
        "wrong_source",
        "unselected_source",
        "forged_metadata",
        "restricted",
        "quarantined",
        "incomplete",
        "corrupt_bytes",
    ],
)
async def test_forged_or_unavailable_admission_cannot_grant_access(admission, fault):
    receipt = admission.receipt
    proof = deepcopy(receipt.pinned_source_refs)
    copy = proof["evidenceCopyAdmission"]["copies"][0]
    if fault == "missing_receipt":
        await admission.session.delete(receipt)
    elif fault == "foreign_receipt":
        receipt.destination_workflow_id = "mm:foreign"
    elif fault == "missing_canonical":
        await admission.session.delete(admission.canonical)
    elif fault == "foreign_canonical_namespace":
        admission.canonical.namespace = "foreign"
    elif fault == "foreign_receipt_run":
        receipt.destination_run_id = "foreign-run"
    elif fault == "foreign_canonical_run":
        admission.canonical.run_id = "foreign-run"
    elif fault == "wrong_create_identity":
        admission.canonical.create_idempotency_key = "foreign-create"
    elif fault == "authored_proof_only":
        admission.canonical.parameters = {
            **admission.canonical.parameters,
            "evidenceCopyAdmission": deepcopy(proof["evidenceCopyAdmission"]),
            "continuationSource": deepcopy(proof),
        }
        proof.pop("evidenceCopyAdmission")
    elif fault == "missing_proof":
        proof.pop("evidenceCopyAdmission")
    elif fault == "wrong_namespace":
        proof["evidenceCopyAdmission"]["namespace"] = "foreign"
    elif fault == "wrong_version":
        proof["evidenceCopyAdmission"]["schemaVersion"] = 999
    elif fault == "wrong_artifact":
        copy["artifactId"] = "art_unrelated"
    elif fault == "wrong_digest":
        copy["sha256"] = "0" * 64
    elif fault == "wrong_size":
        copy["sizeBytes"] += 1
    elif fault == "wrong_type":
        copy["contentType"] = "application/json"
    elif fault == "wrong_source":
        copy["sourceRef"] = "artifact://omnigent/foreign"
    elif fault == "unselected_source":
        proof["selectedSourceArtifactRefs"] = []
    elif fault == "forged_metadata":
        admission.artifact.metadata_json = {
            "artifact_class": "linked_continuation_source_evidence",
            "source_ref": "artifact://omnigent/foreign",
        }
    elif fault == "restricted":
        admission.artifact.redaction_level = (
            models.TemporalArtifactRedactionLevel.RESTRICTED
        )
    elif fault == "quarantined":
        admission.artifact.metadata_json = {
            **admission.artifact.metadata_json,
            "quarantine": True,
        }
    elif fault == "corrupt_bytes":
        admission.service._store.read_path(admission.artifact.storage_key).write_bytes(
            b"forged payload with no metadata change"
        )
    elif fault == "incomplete":
        admission.artifact.status = models.TemporalArtifactStatus.PENDING_UPLOAD
    receipt.pinned_source_refs = proof
    await admission.session.commit()
    expected_error = (
        TemporalArtifactStateError
        if fault == "quarantined"
        else TemporalArtifactAuthorizationError
    )
    with pytest.raises(expected_error):
        await _link(admission)
    assert (
        await admission.service._repository.list_links(admission.artifact.artifact_id)
        == []
    )


@pytest.mark.asyncio
async def test_first_dispatch_recovers_before_start_acknowledgement(admission):
    admission.receipt.destination_run_id = None
    admission.canonical.run_id = "provisional-run"
    admission.canonical.memo = {
        "temporalStart": {
            "state": "pending",
            "firstRequestedAt": datetime.now(UTC).isoformat(),
            "taskQueue": "mm.workflow.user",
            "scheduledFor": None,
        }
    }
    await admission.session.commit()
    await _link(admission)
    _, payload = await admission.service.read(
        artifact_id=admission.artifact.artifact_id,
        access=ArtifactAccessContext.for_execution(WORKFLOW),
    )
    assert payload == PAYLOAD


@pytest.mark.asyncio
async def test_late_materializer_reuses_committed_winner_copy_set(admission):
    original = deepcopy(admission.receipt.pinned_source_refs)
    loser, _ = await admission.service.create(
        principal="operator",
        content_type="text/plain",
        metadata_json={
            "artifact_class": "linked_continuation_source_evidence",
            "source_ref": SOURCE_REF,
        },
    )
    await admission.service.write_complete(
        artifact_id=loser.artifact_id,
        principal="operator",
        payload=PAYLOAD,
    )
    # A second request finished materializing after a winner recorded admission.
    # Independent sessions exercise rereading, not identity-map proof overwrites.
    async with admission.sessions() as session:
        receipt = await session.get(
            models.WorkflowLinkedContinuationRecord, admission.receipt.id
        )
        result = await SqlLinkedContinuationRepository(session).record_evidence_copies(
            receipt,
            namespace=NAMESPACE,
            attachments=[
                {
                    "artifactId": loser.artifact_id,
                    "filename": "loser.txt",
                    "contentType": "text/plain",
                    "sizeBytes": len(PAYLOAD),
                }
            ],
        )
        assert result[0]["artifactId"] == admission.artifact.artifact_id
        assert receipt.pinned_source_refs == original
    await _link(admission)
    with pytest.raises(TemporalArtifactAuthorizationError):
        await admission.service.read(
            artifact_id=loser.artifact_id,
            access=ArtifactAccessContext.for_execution(WORKFLOW),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "flag_only",
        "no_attempt",
        "malformed_attempt",
        "no_queue",
        "confirmed_run",
        "closed",
        "terminal",
    ],
)
async def test_pending_flag_cannot_replace_durable_start_intent(admission, fault):
    admission.receipt.destination_run_id = None
    admission.canonical.run_id = "unconfirmed-provisional-run"
    intent = {
        "state": "pending",
        "firstRequestedAt": datetime.now(UTC).isoformat(),
        "taskQueue": "mm.workflow.user",
        "scheduledFor": None,
    }
    if fault == "flag_only":
        intent = {"state": "pending"}
    elif fault == "no_attempt":
        intent["firstRequestedAt"] = None
    elif fault == "malformed_attempt":
        intent["firstRequestedAt"] = "not-a-time"
    elif fault == "no_queue":
        intent["taskQueue"] = ""
    elif fault == "confirmed_run":
        intent["runId"] = "already-confirmed-other-run"
    elif fault == "closed":
        admission.canonical.close_status = models.TemporalExecutionCloseStatus.COMPLETED
    elif fault == "terminal":
        admission.canonical.state = models.MoonMindWorkflowState.COMPLETED
    admission.canonical.memo = {"temporalStart": intent}
    await admission.session.commit()
    with pytest.raises(TemporalArtifactAuthorizationError):
        await _link(admission)
    assert (
        await admission.service._repository.list_links(admission.artifact.artifact_id)
        == []
    )
