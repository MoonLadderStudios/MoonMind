"""Evidence readers preserve current grants, lifecycle and safe representations."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from temporalio.testing import ActivityEnvironment

from api_service.db.models import TemporalArtifactRedactionLevel
from moonmind.workflows.skills.security_assessment import AssessmentExecution
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    ArtifactDependency,
    LocalTemporalArtifactStore,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactStateError,
    TemporalArtifactValidationError,
)
from tests.artifact_access import access_for_test
from tests.unit.workflows.temporal.test_artifacts import temporal_db

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def evidence_service(tmp_path):
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "bytes"),
        )
        yield service, session


async def put(service, payload=b"Useful reproduction", content_type="text/plain"):
    artifact, _ = await service.create(
        principal="owner",
        content_type=content_type,
        redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
    )
    await service.write_complete(
        artifact_id=artifact.artifact_id,
        principal="owner",
        payload=payload,
    )
    return artifact


async def test_assessment_writer_retains_real_producer_raw_grant_and_retry(
    evidence_service,
):
    service, _ = evidence_service
    environment = ActivityEnvironment()
    environment.info = replace(
        environment.info,
        namespace="default",
        workflow_id="assessment",
        workflow_run_id="run",
    )

    async def produce():
        execution = AssessmentExecution(
            service,
            principal="workflow:assessment",
            context={
                "namespace": "default",
                "workflow_id": "assessment",
                "run_id": "run",
                "node_id": "assess",
            },
            access=ArtifactAccessContext.for_activity(
                principal="system:security-assessment"
            ),
        )
        ref = await execution.write(
            b'{"safe":"retained"}', kind="security.scan", key="fixture"
        )
        await execution.seal(ref, lambda: _empty_dependencies())
        assert await execution.saved(kind="security.scan", key="fixture") == (
            ref,
            {"safe": "retained"},
        )
        return ref

    ref = await environment.run(produce)
    artifact, _, _, policy = await service.get_metadata(
        artifact_id=ref,
        access=access_for_test("workflow:assessment"),
    )
    assert artifact.redaction_level is TemporalArtifactRedactionLevel.RESTRICTED
    assert policy.raw_access_allowed
    assert policy.preview_artifact_ref is not None
    metadata_only = access_for_test("workflow:other")._with_verified_inputs([ref])
    _, _, _, other_policy = await service.get_metadata(
        artifact_id=ref, access=metadata_only
    )
    assert not other_policy.raw_access_allowed
    with pytest.raises(TemporalArtifactAuthorizationError):
        await service.read(artifact_id=ref, access=metadata_only)


async def _empty_dependencies():
    return []


@pytest.mark.parametrize(
    "surface",
    ["read", "read_path", "read_chunks", "presign_download", "compute_preview"],
)
async def test_expired_evidence_rejected_before_use_claim_or_storage(
    evidence_service, monkeypatch, surface
):
    service, session = evidence_service
    artifact = await put(service)
    artifact.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    async def unexpected_claim(**kwargs):
        raise AssertionError("Expired evidence acquired a new use claim")

    monkeypatch.setattr(service, "_begin_consumer_use", unexpected_claim)
    with pytest.raises(TemporalArtifactStateError, match="expired"):
        await getattr(service, surface)(
            artifact_id=artifact.artifact_id, access=access_for_test("owner")
        )


@pytest.mark.parametrize("protection", ["pin", "dependency", "claim"])
async def test_retained_evidence_remains_readable_after_own_expiry(
    evidence_service, protection
):
    service, session = evidence_service
    artifact = await put(service)
    if protection == "pin":
        await service.pin(
            artifact_id=artifact.artifact_id,
            principal="owner",
            reason="Retained evidence",
        )
    elif protection == "dependency":
        root = await put(service, b"Retained report")
        await service.record_verified_dependencies(
            root_artifact_id=root.artifact_id,
            dependencies=[
                ArtifactDependency(
                    artifact.artifact_id, artifact.sha256, True, "security.evidence"
                )
            ],
            access=access_for_test("owner"),
        )
        await service.pin(
            artifact_id=root.artifact_id, principal="owner", reason="Retained report"
        )
    else:
        await service.acquire_saved_work_use(
            artifact_id=artifact.artifact_id,
            principal="owner",
            access=access_for_test("owner"),
            request_id="existing-use",
            operation_kind="restore",
        )
    artifact.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    _, body = await service.read(
        artifact_id=artifact.artifact_id, access=access_for_test("owner")
    )
    assert body == b"Useful reproduction"
    _, preview = await service.read(
        artifact_id=artifact.metadata_json["preview_artifact_id"],
        access=access_for_test("owner"),
    )
    assert preview == b"Useful reproduction"


async def test_restricted_preview_transforms_json_without_destroying_reproduction(
    evidence_service,
):
    service, _ = evidence_service
    payload = {
        "password": "synthetic-preview-password",
        "message": "Bearer synthetic-preview-bearer alice.fixture@example.test",
        "fullName": "Synthetic Private Person",
        "phoneNumber": "+1-202-555-0111",
        "path": "src/example.py",
        "line": 42,
        "ruleId": "fixture-rule",
    }
    artifact = await put(service, json.dumps(payload).encode(), "application/json")
    ref = await service.compute_preview(
        artifact_id=artifact.artifact_id, access=access_for_test("owner")
    )
    _, preview = await service.read(
        artifact_id=ref.artifact_id, access=access_for_test("owner")
    )
    for forbidden in (
        "synthetic-preview-password",
        "synthetic-preview-bearer",
        "alice.fixture@example.test",
        "Synthetic Private Person",
        "+1-202-555-0111",
    ):
        assert forbidden.encode() not in preview
    assert (
        b"src/example.py" in preview and b"fixture-rule" in preview and b"42" in preview
    )
    assert artifact.sha256 == hashlib.sha256(json.dumps(payload).encode()).hexdigest()


@pytest.mark.parametrize(
    "payload,content_type",
    [
        (b'{"password":"synthetic-malformed-secret"', "application/json"),
        (b"\xffsynthetic-malformed-secret", "text/plain"),
        (b"synthetic-malformed-secret", "application/octet-stream"),
    ],
    ids=["malformed-json", "invalid-text", "opaque-content"],
)
async def test_unsafe_preview_is_withheld_without_payload_in_diagnostic(
    evidence_service, payload, content_type
):
    service, _ = evidence_service
    artifact = await put(service, payload, content_type)
    ref = await service.compute_preview(
        artifact_id=artifact.artifact_id, access=access_for_test("owner")
    )
    _, preview = await service.read(
        artifact_id=ref.artifact_id, access=access_for_test("owner")
    )
    assert b"synthetic-malformed-secret" not in preview
    assert b"withheld" in preview.lower()


@pytest.mark.parametrize("failure", ["expired", "revoked", "stale", "cycle"])
@pytest.mark.parametrize(
    "surface", ["read", "read_path", "read_chunks", "presign_download"]
)
async def test_direct_preview_cannot_bypass_source_authority(
    evidence_service, failure, surface
):
    service, session = evidence_service
    source = await put(service)
    preview_ref = await service.compute_preview(
        artifact_id=source.artifact_id, access=access_for_test("owner")
    )
    preview = await service._repository.get_artifact(preview_ref.artifact_id)
    if failure == "expired":
        source.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif failure == "revoked":
        source.created_by_principal = "new-owner"
    elif failure == "stale":
        preview.metadata_json = {
            k: v for k, v in preview.metadata_json.items() if k != "transformation"
        }
    else:
        source.metadata_json = {
            **source.metadata_json,
            "preview_of": preview.artifact_id,
        }
    await session.commit()
    with pytest.raises(
        (TemporalArtifactAuthorizationError, TemporalArtifactStateError)
    ):
        await getattr(service, surface)(
            artifact_id=preview.artifact_id, access=access_for_test("owner")
        )


async def test_generated_preview_uses_source_metadata_grant_without_raw_grant(
    evidence_service,
):
    service, _ = evidence_service
    source = await put(service, b"token=synthetic-preview-token\nUseful reproduction")
    metadata_only = access_for_test("workflow:review")._with_verified_inputs(
        [source.artifact_id]
    )
    _, _, _, policy = await service.get_metadata(
        artifact_id=source.artifact_id, access=metadata_only
    )
    assert not policy.raw_access_allowed
    _, safe = await service.read(
        artifact_id=policy.default_read_ref.artifact_id, access=metadata_only
    )
    assert b"Useful reproduction" in safe and b"synthetic-preview-token" not in safe
    with pytest.raises(TemporalArtifactAuthorizationError):
        await service.read(artifact_id=source.artifact_id, access=metadata_only)


async def test_stale_preview_is_regenerated_without_raw_fallback(evidence_service):
    service, session = evidence_service
    source = await put(service, b"contact alice.fixture@example.test")
    old_ref = source.metadata_json["preview_artifact_id"]
    preview = await service._repository.get_artifact(old_ref)
    preview.metadata_json = {
        k: v for k, v in preview.metadata_json.items() if k != "transformation"
    }
    await session.commit()
    _, _, _, policy = await service.get_metadata(
        artifact_id=source.artifact_id, access=access_for_test("operator")
    )
    assert not policy.raw_access_allowed and policy.preview_artifact_ref is None
    with pytest.raises(TemporalArtifactAuthorizationError):
        await service.read(
            artifact_id=policy.default_read_ref.artifact_id,
            access=access_for_test("operator"),
        )
    ref = await service.compute_preview(
        artifact_id=source.artifact_id, access=access_for_test("owner")
    )
    assert ref.artifact_id != old_ref
    _, safe = await service.read(
        artifact_id=ref.artifact_id, access=access_for_test("owner")
    )
    assert b"alice.fixture@example.test" not in safe


@pytest.mark.parametrize("reserved", ["preview_of", "preview_artifact_id"])
async def test_callers_cannot_forge_preview_relationships(evidence_service, reserved):
    service, _ = evidence_service
    with pytest.raises(TemporalArtifactValidationError, match="reserved"):
        await service.create(
            principal="owner",
            content_type="text/plain",
            metadata_json={reserved: "art_foreign"},
        )


async def test_oversized_preview_preserves_native_evidence_and_safe_diagnostic(
    evidence_service,
):
    service, _ = evidence_service
    payload = b"synthetic-oversized-evidence" + b" " * (4 * 1024 * 1024)
    source = await put(service, payload)
    _, native = await service.read(
        artifact_id=source.artifact_id, access=access_for_test("owner")
    )
    assert native == payload
    _, preview = await service.read(
        artifact_id=source.metadata_json["preview_artifact_id"],
        access=access_for_test("owner"),
    )
    assert (
        b"withheld" in preview.lower()
        and b"synthetic-oversized-evidence" not in preview
    )


async def test_quarantined_source_keeps_only_authorized_transformed_diagnostics(
    evidence_service,
):
    service, session = evidence_service
    source = await put(service, b'token=synthetic-quarantine-value\nUseful reproduction')
    source.metadata_json = {**source.metadata_json, "quarantine": True}
    await session.commit()
    ref = await service.compute_preview(
        artifact_id=source.artifact_id, access=access_for_test("owner")
    )
    for surface in ("read", "read_path", "read_chunks", "presign_download"):
        with pytest.raises(TemporalArtifactStateError, match="QUARANTINED"):
            await getattr(service, surface)(
                artifact_id=source.artifact_id, access=access_for_test("owner")
            )
    _, _, _, policy = await service.get_metadata(
        artifact_id=source.artifact_id, access=access_for_test("owner")
    )
    assert not policy.raw_access_allowed and policy.default_read_ref == ref
    _, safe = await service.read(
        artifact_id=ref.artifact_id, access=access_for_test("owner")
    )
    assert b"Useful reproduction" in safe and b"synthetic-quarantine-value" not in safe
    with pytest.raises(TemporalArtifactAuthorizationError):
        await service.read(
            artifact_id=ref.artifact_id, access=access_for_test("workflow:unrelated")
        )
    assert source.metadata_json["quarantine"] is True
    assert source.deleted_at is None and source.hard_deleted_at is None


@pytest.mark.parametrize("content_type", ["text/plain", "application/json"])
async def test_actual_previews_remove_nested_credentials_but_keep_locations(
    evidence_service, content_type
):
    service, _ = evidence_service
    evidence = {
        "apiKey": "synthetic-private-value",
        "message": 'settings={"password": "synthetic-private-value"}',
        "url": "https://svc:synthetic-private-value@127.0.0.1/reproduce?apiKey=synthetic-private-value&findingId=abc123",
        "path": "src/example.py",
        "line": 42,
    }
    raw = json.dumps(evidence).encode()
    source = await put(service, raw, content_type)
    _, safe = await service.read(
        artifact_id=source.metadata_json["preview_artifact_id"],
        access=access_for_test("owner"),
    )
    assert b"synthetic-private-value" not in safe
    assert b"src/example.py" in safe and b"42" in safe
    assert b"127.0.0.1/reproduce" in safe and b"findingId=abc123" in safe
    _, native = await service.read(
        artifact_id=source.artifact_id, access=access_for_test("owner")
    )
    assert native == raw and source.sha256 == hashlib.sha256(raw).hexdigest()


async def test_legacy_assessment_retry_preserves_recorded_access_without_new_grants(
    evidence_service,
):
    service, _ = evidence_service
    context = {
        "namespace": "default", "workflow_id": "assessment", "run_id": "run",
        "node_id": "assess",
    }
    source, _ = await service.create(
        principal="workflow:assessment",
        content_type="application/json",
        metadata_json={"sensitivity": "restricted", "subject": "legacy-fixture"},
        link={**{key: context[key] for key in ("namespace", "workflow_id", "run_id")}, "link_type": "security.scan"},
    )
    raw = b'{"safe":"retained legacy work"}'
    await service.write_complete(
        artifact_id=source.artifact_id, principal="workflow:assessment", payload=raw
    )
    execution = AssessmentExecution(
        service, principal="workflow:assessment", context=context,
        access=access_for_test("workflow:assessment"),
    )
    assert await execution.saved(kind="security.scan", key="legacy-fixture") == (
        source.artifact_id, {"safe": "retained legacy work"}
    )
    assert source.redaction_level is TemporalArtifactRedactionLevel.NONE
    assert not source.metadata_json.get("preview_artifact_id")
    assert source.sha256 == hashlib.sha256(raw).hexdigest()
    links = await service._repository.list_links(source.artifact_id)
    assert [link.link_type for link in links] == ["security.scan"]
