from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.workflows.tasks.prepared_context import (
    PreparedContextFailure,
    PreparedInputEntry,
    PreparedInputManifest,
    build_prepared_input_manifest,
    select_step_prepared_context,
)


def _task_payload() -> dict[str, object]:
    return {
        "inputAttachments": [
            {
                "artifactId": "artifact-objective",
                "filename": "objective.png",
                "contentType": "image/png",
                "sizeBytes": 42,
            }
        ],
        "steps": [
            {
                "id": "collect-evidence",
                "inputAttachments": [
                    {
                        "artifactId": "artifact-step-1",
                        "filename": "evidence.txt",
                        "contentType": "text/plain",
                        "sizeBytes": 17,
                    }
                ],
            },
            {
                "id": "write-report",
                "inputAttachments": [
                    {
                        "artifactId": "artifact-step-2",
                        "filename": "report-notes.txt",
                        "contentType": "text/plain",
                        "sizeBytes": 19,
                    }
                ],
            },
        ],
    }


def test_prepared_manifest_preserves_objective_and_step_targets() -> None:
    manifest = build_prepared_input_manifest(_task_payload())

    assert isinstance(manifest, PreparedInputManifest)
    assert manifest.manifest_ref.startswith("prepared-context-manifest://")
    assert [entry.target_kind for entry in manifest.entries] == [
        "objective",
        "step",
        "step",
    ]
    assert manifest.entries[0].raw_input_ref == "artifact://artifact-objective"
    assert manifest.entries[0].derived_context_ref == (
        "prepared-context://objective/artifact-objective"
    )
    assert manifest.entries[1].step_ref == "collect-evidence"
    assert manifest.entries[2].step_ref == "write-report"


def test_prepared_manifest_preserves_zero_byte_size() -> None:
    manifest = build_prepared_input_manifest(
        {
            "inputAttachments": [
                {
                    "artifactId": "empty-file",
                    "sizeBytes": 0,
                    "size": 42,
                }
            ]
        }
    )

    assert manifest.entries[0].size_bytes == 0


def test_prepared_models_reject_missing_step_binding_and_embedded_content() -> None:
    with pytest.raises(ValidationError, match="stepRef"):
        PreparedInputEntry.model_validate(
            {
                "artifactId": "artifact-step",
                "targetKind": "step",
                "rawInputRef": "artifact://artifact-step",
            }
        )

    with pytest.raises(ValueError, match="inline attachment content"):
        build_prepared_input_manifest(
            {
                "inputAttachments": [
                    {
                        "artifactId": "artifact-objective",
                        "filename": "objective.png",
                        "contentType": "image/png",
                        "dataUrl": "data:image/png;base64,AAAA",
                    }
                ]
            }
        )

    with pytest.raises(ValueError, match="inline attachment content"):
        build_prepared_input_manifest(
            {
                "inputAttachments": [
                    {
                        "artifactId": "artifact-objective",
                        "filename": "has-space-in-data-url.txt",
                        "url": "data:image/png;base64,AAAA remaining-safe-text",
                    }
                ]
            }
        )

    with pytest.raises(ValueError, match="generated markdown"):
        build_prepared_input_manifest(
            {
                "steps": [
                    {
                        "id": "collect-evidence",
                        "inputAttachments": [
                            {
                                "artifactId": "artifact-step-1",
                                "markdown": "![image](data:image/png;base64,AAAA)",
                            }
                        ],
                    }
                ]
            }
        )


def test_select_step_context_includes_objective_and_current_step_only() -> None:
    manifest = build_prepared_input_manifest(_task_payload())

    context = select_step_prepared_context(
        manifest,
        logical_step_id="collect-evidence",
    )

    assert context.logical_step_id == "collect-evidence"
    assert context.objective_context_refs == [
        "prepared-context://objective/artifact-objective"
    ]
    assert context.step_context_refs == [
        "prepared-context://steps/collect-evidence/artifact-step-1"
    ]
    assert context.raw_input_refs == [
        "artifact://artifact-objective",
        "artifact://artifact-step-1",
    ]
    assert "artifact-step-2" not in str(context.model_dump(by_alias=True))


def test_step_context_metadata_is_bounded_and_target_aware() -> None:
    manifest = build_prepared_input_manifest(_task_payload())
    context = select_step_prepared_context(manifest, logical_step_id="collect-evidence")

    metadata = context.to_metadata()

    assert metadata["manifestRef"] == manifest.manifest_ref
    assert metadata["logicalStepId"] == "collect-evidence"
    assert metadata["targetCounts"] == {"objective": 1, "step": 1}
    assert metadata["inputRefs"] == [
        "prepared-context://objective/artifact-objective",
        "prepared-context://steps/collect-evidence/artifact-step-1",
        "artifact://artifact-objective",
        "artifact://artifact-step-1",
    ]
    assert "data:image" not in str(metadata)


def test_prepare_failure_payload_is_bounded() -> None:
    failure = PreparedContextFailure.from_exception(
        RuntimeError("data:image/png;base64,AAAA " + "x" * 400),
        logical_step_id="collect-evidence",
        manifest_ref="prepared-context-manifest://task-inputs",
    )

    dumped = failure.model_dump(by_alias=True)

    assert dumped["logicalStepId"] == "collect-evidence"
    assert dumped["manifestRef"] == "prepared-context-manifest://task-inputs"
    assert dumped["reason"] == "RuntimeError"
    assert "data:image" not in dumped["message"]
    assert len(dumped["message"]) <= 180
