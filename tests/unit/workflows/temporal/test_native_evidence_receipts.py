"""Native cleanup reuses only complete, current-access, job-owned artifact bytes."""

import hashlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio import activity

from api_service.db.models import TemporalArtifactStatus
from api_service.services import assessment_authorizations
from moonmind.workflows.temporal import worker_runtime


@pytest.fixture
def receipt_setup(monkeypatch):
    request = SimpleNamespace(
        job_id="container-job:one",
        ownership_token="owned-token",
        owner=SimpleNamespace(principal_type="workflow", principal_id="owner"),
        request=object(),
    )
    name = request.job_id + "-native-egress-final.json"
    info = SimpleNamespace(
        workflow_namespace="test",
        workflow_id="container-job:one",
        workflow_run_id="run-one",
    )
    monkeypatch.setattr(activity, "info", lambda: info)
    monkeypatch.setattr(activity, "in_activity", lambda: True)

    @asynccontextmanager
    async def session():
        yield object()

    monkeypatch.setattr(worker_runtime, "get_async_session_context", session)
    admission = AsyncMock(return_value=object())
    monkeypatch.setattr(worker_runtime, "_container_job_admitted_record", admission)
    validator = AsyncMock()
    monkeypatch.setattr(
        assessment_authorizations, "validate_job_assessment_authority", validator
    )
    body = json.dumps(
        {"coverage": "partial", "nativeNetwork": {"attempts": 1}}
    ).encode()
    artifact = SimpleNamespace(
        artifact_id="art_one",
        status=TemporalArtifactStatus.COMPLETE,
        created_by_principal="workflow:owner",
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        metadata_json={
            "artifact_type": "container_job.evidence",
            "name": name,
            "container_job_id": request.job_id,
            "ownership_digest": hashlib.sha256(
                request.ownership_token.encode()
            ).hexdigest(),
        },
    )
    service = SimpleNamespace(
        list_for_execution=AsyncMock(return_value=[artifact]),
        read=AsyncMock(return_value=(artifact, body)),
    )
    return request, name, artifact, body, service, admission, validator


@pytest.mark.asyncio
async def test_receipt_read_uses_actual_activity_and_saved_job_authority(receipt_setup):
    request, name, _artifact, body, service, admission, validator = receipt_setup
    assert await worker_runtime._container_job_evidence_reconciler(service)(
        request, name
    ) == ("art_one", body)
    admission.assert_awaited_once()
    validator.assert_awaited_once()
    assert validator.await_args.kwargs["live"] is False
    query = service.list_for_execution.await_args.kwargs
    assert (query["namespace"], query["workflow_id"], query["run_id"]) == (
        "test",
        "container-job:one",
        "run-one",
    )
    assert query["access"].candidates == frozenset({"workflow:container-job:one"})
    assert service.read.await_args.kwargs["access"] is query["access"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mismatch", ["job", "name", "ownership", "owner", "incomplete"]
)
async def test_unrelated_receipt_never_grants_cleanup(receipt_setup, mismatch):
    request, name, artifact, _body, service, _admission, _validator = receipt_setup
    if mismatch == "owner":
        artifact.created_by_principal = "workflow:other"
    elif mismatch == "incomplete":
        artifact.status = TemporalArtifactStatus.PENDING_UPLOAD
    else:
        artifact.metadata_json[
            {
                "job": "container_job_id",
                "name": "name",
                "ownership": "ownership_digest",
            }[mismatch]
        ] = "other"
    assert (
        await worker_runtime._container_job_evidence_reconciler(service)(request, name)
        is None
    )
    service.read.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["denied", "corrupt", "oversized", "admission", "conflicting"]
)
async def test_unavailable_or_conflicting_receipt_fails_closed(receipt_setup, failure):
    request, name, artifact, body, service, admission, _validator = receipt_setup
    if failure == "denied":
        service.read.side_effect = RuntimeError(
            "current access denied or artifact expired"
        )
    elif failure == "corrupt":
        artifact.sha256 = "0" * 64
    elif failure == "oversized":
        artifact.size_bytes = 1048577
    elif failure == "admission":
        admission.side_effect = RuntimeError("saved job admission mismatch")
    else:
        other_body = json.dumps(
            {"coverage": "partial", "nativeNetwork": {"attempts": 2}}
        ).encode()
        other = SimpleNamespace(
            **{
                **vars(artifact),
                "artifact_id": "art_two",
                "size_bytes": len(other_body),
                "sha256": hashlib.sha256(other_body).hexdigest(),
            }
        )
        service.list_for_execution.return_value = [artifact, other]
        service.read.side_effect = [(artifact, body), (other, other_body)]
    with pytest.raises(RuntimeError):
        await worker_runtime._container_job_evidence_reconciler(service)(request, name)


@pytest.mark.asyncio
async def test_equivalent_duplicate_receipts_reconcile_deterministically(receipt_setup):
    request, name, artifact, body, service, _admission, _validator = receipt_setup
    other = SimpleNamespace(**{**vars(artifact), "artifact_id": "art_zero"})
    service.list_for_execution.return_value = [other, artifact]
    service.read.side_effect = [(other, body), (artifact, body)]
    assert await worker_runtime._container_job_evidence_reconciler(service)(
        request, name
    ) == ("art_one", body)
