from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from moonmind.services.skill_step_inputs import validate_skill_step_inputs


class _ArtifactMetadataSession:
    def __init__(
        self,
        metadata: dict[str, object] | None,
        *,
        artifact_id: str = "art_skill",
        skill_slug: str = "issue-implement",
        content_digest: str = "sha256:skill",
    ) -> None:
        self._artifact_id = artifact_id
        self._artifact = (
            SimpleNamespace(artifact_id=artifact_id, metadata_json=metadata)
            if metadata is not None
            else None
        )
        self._definition = SimpleNamespace(
            slug=skill_slug,
            artifact_ref=artifact_id,
            content_digest=content_digest,
        )
        self.get = AsyncMock(side_effect=self._get)
        self.execute = AsyncMock(return_value=self._execute_result(self._definition))

    async def _get(self, _model, artifact_id: str):
        if artifact_id != self._artifact_id:
            return None
        return self._artifact

    @staticmethod
    def _execute_result(definition):
        result = MagicMock()
        result.scalars.return_value.first.return_value = definition
        return result


def _parameters(skill_payload: dict[str, object]) -> dict[str, object]:
    return {
        "repository": "Moon/Mind",
        "workflow": {
            "git": {"branch": "feature/mm-1052"},
            "steps": [
                {
                    "id": "step-1",
                    "type": "skill",
                    "skill": skill_payload,
                }
            ],
        },
    }


def _metadata() -> dict[str, object]:
    return {
        "input_contract": {
            "input_schema": {
                "type": "object",
                "required": ["issue"],
                "properties": {
                    "issue": {"type": "string"},
                    "repository": {
                        "type": "string",
                        "x-moonmind-context-default": "repository",
                    },
                    "branch": {
                        "type": "string",
                        "x-moonmind-context-default": "branch",
                        "default": "main",
                    },
                    "dryRun": {"type": "boolean"},
                },
            },
            "ui_schema": {
                "issue": {"required": False},
                "uiOnlyRequired": {"widget": "text"},
            },
            "defaults": {"dryRun": True},
            "contract_digest": "sha256:contract",
        },
    }


@pytest.mark.asyncio
async def test_validate_skill_step_inputs_applies_defaults_and_evidence() -> None:
    result = await validate_skill_step_inputs(
        initial_parameters=_parameters(
            {
                "name": "issue-implement",
                "contentRef": "art_skill",
                "contentDigest": "sha256:skill",
                "inputs": {"issue": "MM-1052"},
            }
        ),
        session=_ArtifactMetadataSession(_metadata()),
    )

    assert result.valid
    skill = result.parameters["workflow"]["steps"][0]["skill"]
    assert skill["name"] == "issue-implement"
    assert skill["contentRef"] == "art_skill"
    assert skill["contentDigest"] == "sha256:skill"
    assert skill["inputContractDigest"] == "sha256:contract"
    assert skill["inputs"] == {
        "issue": "MM-1052",
        "repository": "Moon/Mind",
        "branch": "feature/mm-1052",
        "dryRun": True,
    }
    assert "args" not in skill


@pytest.mark.asyncio
async def test_validate_skill_step_inputs_uses_input_schema_not_ui_schema() -> None:
    result = await validate_skill_step_inputs(
        initial_parameters=_parameters(
            {
                "name": "issue-implement",
                "contentRef": "art_skill",
                "contentDigest": "sha256:skill",
                "inputs": {},
            }
        ),
        session=_ArtifactMetadataSession(_metadata()),
    )

    assert not result.valid
    assert [error.as_dict() for error in result.errors] == [
        {
            "path": "steps[0].skill.inputs.issue",
            "message": "issue is required.",
            "code": "required",
            "recoverable": True,
        }
    ]


@pytest.mark.asyncio
async def test_validate_skill_step_inputs_normalizes_legacy_args() -> None:
    result = await validate_skill_step_inputs(
        initial_parameters=_parameters(
            {
                "id": "issue-implement",
                "contentRef": "art_skill",
                "contentDigest": "sha256:skill",
                "args": {"issue": "MM-1052"},
            }
        ),
        session=_ArtifactMetadataSession(_metadata()),
    )

    assert result.valid
    skill = result.parameters["workflow"]["steps"][0]["skill"]
    assert skill["name"] == "issue-implement"
    assert "id" not in skill
    assert "args" not in skill
    assert skill["inputs"]["issue"] == "MM-1052"


@pytest.mark.asyncio
async def test_validate_skill_step_inputs_resolves_digest_only_deployment_skill() -> None:
    result = await validate_skill_step_inputs(
        initial_parameters=_parameters(
            {
                "name": "issue-implement",
                "inputContractDigest": "sha256:client-contract",
                "inputs": {"issue": "MM-1052"},
            }
        ),
        session=_ArtifactMetadataSession(_metadata()),
    )

    assert result.valid
    skill = result.parameters["workflow"]["steps"][0]["skill"]
    assert skill["contentRef"] == "art_skill"
    assert skill["contentDigest"] == "sha256:skill"
    assert skill["inputContractDigest"] == "sha256:contract"


@pytest.mark.asyncio
async def test_validate_skill_step_inputs_replaces_client_supplied_evidence() -> None:
    session = _ArtifactMetadataSession(_metadata())
    result = await validate_skill_step_inputs(
        initial_parameters=_parameters(
            {
                "name": "issue-implement",
                "contentRef": "art_bogus",
                "contentDigest": "sha256:bogus",
                "inputs": {"issue": "MM-1052"},
            }
        ),
        session=session,
    )

    assert result.valid
    skill = result.parameters["workflow"]["steps"][0]["skill"]
    assert skill["contentRef"] == "art_skill"
    assert skill["contentDigest"] == "sha256:skill"
    assert skill["inputContractDigest"] == "sha256:contract"
    session.get.assert_awaited_once()
    assert session.get.await_args.args[1] == "art_skill"


@pytest.mark.asyncio
async def test_validate_skill_step_inputs_rejects_missing_content_evidence() -> None:
    result = await validate_skill_step_inputs(
        initial_parameters=_parameters(
            {
                "name": "issue-implement",
                "contentRef": "missing_art_skill",
                "contentDigest": "sha256:skill",
                "inputs": {"issue": "MM-1052"},
            }
        ),
        session=_ArtifactMetadataSession(None),
    )

    assert not result.valid
    assert [error.as_dict() for error in result.errors] == [
        {
            "path": "steps[0].skill.inputs",
            "message": (
                "Selected Skill content evidence could not be loaded for "
                "contentRef 'art_skill'."
            ),
            "code": "content_evidence_not_found",
            "recoverable": True,
        }
    ]
