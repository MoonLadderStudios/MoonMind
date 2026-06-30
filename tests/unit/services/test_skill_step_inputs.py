from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.services.skill_step_inputs import validate_skill_step_inputs


class _ArtifactMetadataSession:
    def __init__(self, metadata: dict[str, object] | None) -> None:
        self._artifact = (
            SimpleNamespace(metadata_json=metadata) if metadata is not None else None
        )
        self.get = AsyncMock(return_value=self._artifact)


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
        "input_contract_digest": "sha256:contract",
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
                "contentRef 'missing_art_skill'."
            ),
            "code": "content_evidence_not_found",
            "recoverable": True,
        }
    ]
