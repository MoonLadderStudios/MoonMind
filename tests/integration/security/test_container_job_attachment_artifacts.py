"""Attachment inputs and outputs through the real artifact authority (MoonMind#5036).

An owner-readable exported-evidence artifact is admitted, captured read-only,
and the report written to the job-owned output root is published with the
production publisher. After container removal and storage cleanup the report
remains readable by its owner through the container-job and artifact APIs,
while a sibling owner can neither submit the other owner's artifact as input
nor read the job or its report.
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock

import pytest

from api_service.services.container_jobs import (
    ContainerJobNotFoundError,
    ContainerJobService,
    owner_artifact_principal,
)
from moonmind.schemas.container_job_models import (
    AuxiliaryOutcome,
    ContainerJobActivityRequest,
    ContainerJobSubmitRequest,
    OwnerIdentity,
)
from moonmind.workflows.temporal.artifacts import TemporalArtifactAuthorizationError
from moonmind.workflows.temporal.container_job_attachments import storage_for
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from moonmind.workflows.temporal.worker_runtime import (
    _container_job_artifact_reader,
    _container_job_evidence_publisher,
)
from tests.integration.security import test_recurring_assessments as recurring

environment = recurring.environment

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
OWNER = OwnerIdentity(principalType="user", principalId="operator")
SIBLING = OwnerIdentity(principalType="user", principalId="sibling")


async def _exported(service, payload: bytes, owner: OwnerIdentity) -> str:
    principal = owner_artifact_principal(owner)
    artifact, _ = await service.create(
        principal=principal, content_type="application/json"
    )
    await service.write_complete(
        artifact_id=artifact.artifact_id, principal=principal, payload=payload
    )
    return artifact.artifact_id


def _submission(artifact_ref: str, key: str) -> ContainerJobSubmitRequest:
    return ContainerJobSubmitRequest.model_validate(
        {
            "idempotencyKey": key,
            "source": {"source": "workflow", "workflowId": "assessment"},
            "spec": {
                "image": "python:3.13",
                "workspaceRef": {"kind": "sandbox", "workspaceId": "run-1"},
                "inputAttachments": [{"name": "evidence", "artifactRef": artifact_ref}],
                "command": ["assess"],
                "resources": {"cpuMillis": 1000, "memoryMiB": 512},
                "outputs": [{"name": "report", "relativePath": "report.json"}],
            },
        }
    )


async def test_owned_report_outlives_cleanup_and_siblings_are_refused(
    environment, tmp_path
):
    service = environment.service
    exported = await _exported(service, b'{"accounts":["a"]}', OWNER)
    foreign = await _exported(service, b'{"accounts":["s"]}', SIBLING)
    jobs = ContainerJobService(
        environment.session, temporal=AsyncMock(), artifacts=service
    )
    with pytest.raises(ValueError, match="not readable"):
        await jobs.submit(owner=OWNER, request=_submission(foreign, "foreign"))

    submission = _submission(exported, "owned")
    accepted = await jobs.submit(owner=OWNER, request=submission)
    workspace = tmp_path / "temporal_sandbox" / "run-1" / "repo"
    workspace.mkdir(parents=True)

    async def daemon(argv):
        if argv[0] == "inspect":
            return 1, b"", b"no such container"
        return 0, b"assessment finished\n", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=daemon,
        evidence_publisher=_container_job_evidence_publisher(service),
        artifact_reader=_container_job_artifact_reader(service),
    )
    request = ContainerJobActivityRequest(
        jobId=accepted.job_id,
        owner=OWNER,
        ownershipToken=f"{accepted.job_id}:v1",
        request=submission,
        resolvedWorkspaceRef=str(workspace),
        resolvedImageRef="sha256:" + "a" * 64,
    )
    await backend.create_container(request)
    storage = storage_for(tmp_path, accepted.job_id)
    assert (storage.inputs / "evidence" / "content").read_bytes() == (
        b'{"accounts":["a"]}'
    )
    (storage.outputs / "report.json").write_bytes(b'{"findings":1}')
    request.container_ref = "fixture-container"
    request.terminal_state = "succeeded"
    request.exit_code = 0
    evidence = await backend.publish_evidence(request)
    request.publication = AuxiliaryOutcome(state="succeeded")
    await backend.remove_container(request)
    cleaned = await backend.cleanup(request)
    assert cleaned.cleanup_succeeded is True
    assert not storage.root.exists()

    record = await jobs.repository.get_for_owner(owner=OWNER, job_id=accepted.job_id)
    record.logs_ref, record.artifacts_ref = evidence.logs_ref, evidence.artifacts_ref
    record.state = "succeeded"
    await environment.session.flush()
    page = await jobs.artifacts(owner=OWNER, job_id=accepted.job_id)
    report = next(item for item in page.artifacts if item.name == "report")
    _artifact, payload = await service.read(
        artifact_id=report.artifact_ref, principal=owner_artifact_principal(OWNER)
    )
    assert payload == b'{"findings":1}'
    assert report.sha256 == hashlib.sha256(payload).hexdigest()

    with pytest.raises(ContainerJobNotFoundError):
        await jobs.artifacts(owner=SIBLING, job_id=accepted.job_id)
    with pytest.raises(TemporalArtifactAuthorizationError):
        await service.read(
            artifact_id=report.artifact_ref,
            principal=owner_artifact_principal(SIBLING),
        )
