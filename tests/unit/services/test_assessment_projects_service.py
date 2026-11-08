"""Current metadata checks for saved assessment references."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api_service.services.assessment_projects import (
    assessment_project_gaps,
    validate_assessment_project_snapshot,
)
from moonmind.security.assessment_projects import resolve_assessment_project
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    TemporalArtifactAuthorizationError,
)


def snapshot(**fields):
    return resolve_assessment_project(
        {
            "projects": [
                {
                    "id": "system",
                    "name": "System",
                    "purpose": "Test",
                    "boundaries": "Local",
                    "environments": [{"id": "test", "name": "Test", **fields}],
                }
            ]
        },
        project_id="system",
        environment_id="test",
        version=2,
    )


def access():
    return ArtifactAccessContext.for_service("system:assessment-test")


@pytest.mark.asyncio
async def test_no_references_needs_no_database():
    value = snapshot(assets=["test"]).model_dump(mode="json", by_alias=True)
    assert (
        await validate_assessment_project_snapshot(
            value, session=None, access=access(), principal="system:assessment-test"
        )
        == value
    )


@pytest.mark.asyncio
async def test_missing_database_cannot_silently_accept_refs():
    value = snapshot(artifactRefs=["art_missing"]).model_dump(
        mode="json", by_alias=True
    )
    with pytest.raises(HTTPException) as error:
        await validate_assessment_project_snapshot(
            value, session=None, access=access(), principal="system:assessment-test"
        )
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "assessment_project_setup_gaps"


@pytest.mark.asyncio
async def test_foreign_artifact_denial_does_not_leak_exception_or_substitute(
    monkeypatch,
):
    artifact_service = SimpleNamespace(
        get_metadata=AsyncMock(
            side_effect=TemporalArtifactAuthorizationError("private source detail")
        )
    )
    monkeypatch.setattr(
        "api_service.services.assessment_projects.TemporalArtifactService",
        lambda *a, **k: artifact_service,
    )
    gaps = await assessment_project_gaps(
        snapshot(artifactRefs=["art_foreign"]),
        session=object(),
        access=access(),
        principal="system:assessment-test",
    )
    assert gaps == [
        {
            "field": "artifactRefs[0]",
            "code": "artifact_unavailable",
            "message": "Selected evidence is unavailable or not readable. Choose accessible retained evidence.",
        }
    ]
    assert "private source detail" not in str(gaps)
    artifact_service.get_metadata.assert_awaited_once_with(
        artifact_id="art_foreign", access=access()
    )


@pytest.mark.asyncio
async def test_revoked_secret_is_metadata_only_and_never_replaced(monkeypatch):
    check = AsyncMock(return_value={"valid": False, "status": "revoked"})
    monkeypatch.setattr(
        "api_service.services.assessment_projects.SecretsService.validate_secret_ref",
        check,
    )
    gaps = await assessment_project_gaps(
        snapshot(credentialRefs=["db://revoked"]),
        session="session",
        access=access(),
        principal="system:assessment-test",
    )
    assert gaps[0]["code"] == "credential_unavailable"
    assert gaps[0]["field"] == "credentialRefs[0]"
    check.assert_awaited_once_with("session", "revoked")


@pytest.mark.asyncio
async def test_snapshot_rejects_unknown_authority_and_secret_values():
    value = snapshot().model_dump(mode="json", by_alias=True)
    value["assessmentScope"] = {"grants": []}
    with pytest.raises(HTTPException) as error:
        await validate_assessment_project_snapshot(
            value, session=None, access=access(), principal="system:assessment-test"
        )
    assert error.value.status_code == 422
