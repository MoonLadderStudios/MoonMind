from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.workflows.tasks.task_contract import (
    build_canonical_task_view,
    build_effective_task_skill_selectors,
    TaskExecutionSpec,
    TaskStepSpec,
)

def test_task_skills_accepts_valid_properties() -> None:
    """T001: Ensure task.skills structures successfully marshal."""
    raw_payload = {
        "repository": "test/repo",
        "instructions": "execute",
        "skills": {
            "sets": ["default", "python"],
            "include": [
                {"name": "test-skill", "version": "1.0.0"},
                {"name": "unversioned"},
            ],
            "exclude": ["legacy"],
            "materializationMode": "hybrid",
        },
    }
    
    spec = TaskExecutionSpec.model_validate(raw_payload)
    
    assert spec.skills is not None
    assert spec.skills.sets == ["default", "python"]
    assert len(spec.skills.include) == 2
    assert spec.skills.include[0].name == "test-skill"
    assert spec.skills.include[0].version == "1.0.0"
    assert spec.skills.include[1].name == "unversioned"
    assert spec.skills.include[1].version is None
    assert spec.skills.exclude == ["legacy"]
    assert spec.skills.materialization_mode == "hybrid"

def test_task_skills_rejects_invalid_values() -> None:
    """T001: Assert structure validation handles edge cases for skills."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="task.skills.sets must be a list"):
        TaskExecutionSpec.model_validate({
            "repository": "test/repo",
            "instructions": "foo",
            "skills": {"sets": "not-a-list"},
        })

    with pytest.raises(ValidationError, match="task.skills.include must be a list"):
        TaskExecutionSpec.model_validate({
            "repository": "test/repo",
            "instructions": "foo",
            "skills": {"include": "not-a-list"},
        })

    with pytest.raises(ValidationError, match="task.skills.exclude must be a list"):
        TaskExecutionSpec.model_validate({
            "repository": "test/repo",
            "instructions": "foo",
            "skills": {"exclude": "not-a-list"},
        })

    with pytest.raises(ValidationError, match="task\\.skills\\.materializationMode must be hybrid"):
        TaskExecutionSpec.model_validate({
            "repository": "test/repo",
            "instructions": "foo",
            "skills": {"materializationMode": "invalid"},
        })

def test_task_step_spec_with_step_skills() -> None:
    """T002: Ensure step.skills parses correctly on TaskStepSpec."""
    raw_payload = {
        "id": "step1",
        "skills": {
            "exclude": ["bad-skill"],
            "materializationMode": "none",
        }
    }
    
    spec = TaskStepSpec.model_validate(raw_payload)
    assert spec.skills is not None
    assert spec.skills.exclude == ["bad-skill"]
    assert spec.skills.materialization_mode == "none"

def test_effective_task_step_skills_apply_exclusions_without_mutating_task() -> None:
    task_skills = TaskExecutionSpec.model_validate(
        {
            "repository": "test/repo",
            "instructions": "execute",
            "skills": {
                "sets": ["default"],
                "include": [
                    {"name": "baseline", "version": "1.0.0"},
                    {"name": "remove-me", "version": "2.0.0"},
                ],
                "exclude": ["legacy"],
                "materializationMode": "hybrid",
            },
        }
    ).skills
    step_skills = TaskStepSpec.model_validate(
        {
            "id": "step1",
            "skills": {
                "sets": ["python"],
                "include": [{"name": "step-only"}],
                "exclude": ["remove-me"],
                "materializationMode": "none",
            },
        }
    ).skills

    effective = build_effective_task_skill_selectors(task_skills, step_skills)

    assert effective is not None
    assert effective.sets == ["default", "python"]
    assert [(item.name, item.version) for item in effective.include or []] == [
        ("baseline", "1.0.0"),
        ("step-only", None),
    ]
    assert effective.exclude == ["legacy", "remove-me"]
    assert effective.materialization_mode == "none"
    assert [item.name for item in task_skills.include or []] == [
        "baseline",
        "remove-me",
    ]

def test_effective_task_step_skills_returns_none_for_empty_intent() -> None:
    assert build_effective_task_skill_selectors(None, None) is None

def test_task_input_attachments_preserve_objective_and_step_targets() -> None:
    """MM-367: objective and step refs remain distinct canonical fields."""

    canonical = build_canonical_task_view(
        job_type="task",
        payload={
            "repository": "Moon/Mind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "Inspect the images.",
                "inputAttachments": [
                    {
                        "artifactId": "art-objective",
                        "filename": "same-name.png",
                        "contentType": "image/png",
                        "sizeBytes": 10,
                    }
                ],
                "steps": [
                    {
                        "instructions": "Inspect the step image.",
                        "inputAttachments": [
                            {
                                "artifactId": "art-step",
                                "filename": "same-name.png",
                                "contentType": "image/png",
                                "sizeBytes": 20,
                            }
                        ],
                    }
                ],
            },
        },
    )

    assert canonical["task"]["inputAttachments"] == [
        {
            "artifactId": "art-objective",
            "filename": "same-name.png",
            "contentType": "image/png",
            "sizeBytes": 10,
        }
    ]
    assert canonical["task"]["steps"][0]["inputAttachments"] == [
        {
            "artifactId": "art-step",
            "filename": "same-name.png",
            "contentType": "image/png",
            "sizeBytes": 20,
        }
    ]

def test_task_input_attachments_accept_field_name_keys() -> None:
    """MM-375: pre-validation preserves populate_by_name attachment payloads."""

    canonical = build_canonical_task_view(
        job_type="task",
        payload={
            "repository": "Moon/Mind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "Inspect the images.",
                "input_attachments": [
                    {
                        "artifact_id": "art-objective",
                        "filename": "objective.png",
                        "content_type": "image/png",
                        "size_bytes": 10,
                    }
                ],
                "steps": [
                    {
                        "id": "review-step",
                        "instructions": "Inspect the step image.",
                        "input_attachments": [
                            {
                                "artifact_id": "art-step",
                                "filename": "step.png",
                                "content_type": "image/png",
                                "size_bytes": 20,
                            }
                        ],
                    }
                ],
            },
        },
    )

    assert canonical["task"]["inputAttachments"] == [
        {
            "artifactId": "art-objective",
            "filename": "objective.png",
            "contentType": "image/png",
            "sizeBytes": 10,
        }
    ]
    assert canonical["task"]["steps"][0]["inputAttachments"] == [
        {
            "artifactId": "art-step",
            "filename": "step.png",
            "contentType": "image/png",
            "sizeBytes": 20,
        }
    ]

@pytest.mark.parametrize(
    "attachment",
    [
        {"filename": "missing-id.png", "contentType": "image/png", "sizeBytes": 10},
        {
            "artifactId": "art-inline",
            "filename": "inline.png",
            "contentType": "image/png",
            "sizeBytes": 10,
            "dataUrl": "data:image/png;base64,AAAA",
        },
        {
            "artifactId": "art-data-filename",
            "filename": "data:image/png;base64,AAAA",
            "contentType": "image/png",
            "sizeBytes": 10,
        },
    ],
)
def test_task_input_attachments_reject_incomplete_or_embedded_data(
    attachment: dict[str, object],
) -> None:
    """MM-367: refs stay compact and cannot carry inline image payloads."""

    with pytest.raises(ValidationError):
        TaskExecutionSpec.model_validate(
            {
                "instructions": "Inspect image.",
                "inputAttachments": [attachment],
            }
        )

def test_task_input_attachment_validation_error_carries_objective_diagnostic() -> None:
    """MM-375: validation failures expose target-aware diagnostic evidence."""

    with pytest.raises(ValidationError) as exc_info:
        TaskExecutionSpec.model_validate(
            {
                "instructions": "Inspect image.",
                "inputAttachments": [
                    {
                        "artifactId": "art-inline",
                        "filename": "inline.png",
                        "contentType": "image/png",
                        "sizeBytes": 10,
                        "dataUrl": "data:image/png;base64,AAAA",
                    }
                ],
            }
        )

    error = exc_info.value.errors()[0]["ctx"]["error"]
    assert error.diagnostic == {
        "event": "attachment_validation_failed",
        "status": "failed",
        "targetKind": "objective",
        "artifactId": "art-inline",
        "filename": "inline.png",
        "contentType": "image/png",
        "sizeBytes": 10,
        "error": "inputAttachments entries must not include embedded image data",
    }

def test_task_input_attachment_validation_diagnostic_accepts_field_names() -> None:
    """MM-375: field-name payloads still produce canonical diagnostic keys."""

    with pytest.raises(ValidationError) as exc_info:
        TaskExecutionSpec.model_validate(
            {
                "instructions": "Inspect image.",
                "input_attachments": [
                    {
                        "artifact_id": "art-inline",
                        "filename": "inline.png",
                        "content_type": "image/png",
                        "size_bytes": 10,
                        "data_url": "data:image/png;base64,AAAA",
                    }
                ],
            }
        )

    error = exc_info.value.errors()[0]["ctx"]["error"]
    assert error.diagnostic == {
        "event": "attachment_validation_failed",
        "status": "failed",
        "targetKind": "objective",
        "artifactId": "art-inline",
        "filename": "inline.png",
        "contentType": "image/png",
        "sizeBytes": 10,
        "error": "inputAttachments entries must not include embedded image data",
    }

def test_task_input_attachment_validation_error_carries_step_diagnostic() -> None:
    """MM-375: step validation failures identify the affected step target."""

    with pytest.raises(ValidationError) as exc_info:
        TaskExecutionSpec.model_validate(
            {
                "instructions": "Inspect image.",
                "steps": [
                    {
                        "id": "review-step",
                        "instructions": "Inspect step image.",
                        "inputAttachments": [
                            {
                                "artifactId": "art-step",
                                "filename": "step.png",
                                "contentType": "image/png",
                                "sizeBytes": 10,
                                "dataUrl": "data:image/png;base64,AAAA",
                            }
                        ],
                    }
                ],
            }
        )

    error = exc_info.value.errors()[0]["ctx"]["error"]
    assert error.diagnostic == {
        "event": "attachment_validation_failed",
        "status": "failed",
        "targetKind": "step",
        "stepRef": "review-step",
        "artifactId": "art-step",
        "filename": "step.png",
        "contentType": "image/png",
        "sizeBytes": 10,
        "error": "inputAttachments entries must not include embedded image data",
    }

def test_task_input_attachments_must_be_lists() -> None:
    """MM-367: canonical attachment fields are arrays."""

    with pytest.raises(ValidationError, match="task.inputAttachments must be a list"):
        TaskExecutionSpec.model_validate(
            {
                "instructions": "Inspect image.",
                "inputAttachments": {
                    "artifactId": "art-objective",
                    "filename": "objective.png",
                    "contentType": "image/png",
                    "sizeBytes": 10,
                },
            }
        )
