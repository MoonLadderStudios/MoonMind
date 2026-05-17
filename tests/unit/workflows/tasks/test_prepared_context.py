from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.workflows.tasks.prepared_context import (
    PreparedContextFailure,
    PreparedInputEntry,
    PreparedInputManifest,
    build_attempt_context_bundle,
    build_memory_manifest,
    build_resume_prepared_artifact_refs,
    build_prepared_input_manifest,
    build_retrieval_manifest,
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


def test_prepared_manifest_rejects_step_attachment_without_stable_step_ref() -> None:
    with pytest.raises(ValueError, match="stable stepRef"):
        build_prepared_input_manifest(
            {
                "steps": [
                    {
                        "instructions": "No stable step identity",
                        "inputAttachments": [{"artifactId": "artifact-step"}],
                    }
                ]
            }
        )


def test_prepared_manifest_allows_anonymous_steps_without_attachments() -> None:
    manifest = build_prepared_input_manifest(
        {
            "inputAttachments": [{"artifactId": "objective-artifact"}],
            "steps": [
                {"instructions": "No attachments on this anonymous step"},
                {
                    "id": "attached-step",
                    "inputAttachments": [{"artifactId": "step-artifact"}],
                },
            ],
        }
    )

    assert [(entry.artifact_id, entry.step_ref) for entry in manifest.entries] == [
        ("objective-artifact", None),
        ("step-artifact", "attached-step"),
    ]


def test_prepared_manifest_bindings_survive_reorder_and_text_edits() -> None:
    original = {
        "steps": [
            {
                "id": "first-step",
                "instructions": "Original text",
                "inputAttachments": [{"artifactId": "first-artifact"}],
            },
            {
                "id": "second-step",
                "instructions": "Original text",
                "inputAttachments": [{"artifactId": "second-artifact"}],
            },
        ]
    }
    reordered = {
        "steps": [
            {
                "id": "second-step",
                "instructions": "Edited text",
                "inputAttachments": [{"artifactId": "second-artifact"}],
            },
            {
                "id": "first-step",
                "instructions": "Edited text",
                "inputAttachments": [{"artifactId": "first-artifact"}],
            },
        ]
    }

    original_manifest = build_prepared_input_manifest(original)
    reordered_manifest = build_prepared_input_manifest(reordered)

    original_bindings = {
        entry.artifact_id: entry.step_ref for entry in original_manifest.entries
    }
    reordered_bindings = {
        entry.artifact_id: entry.step_ref for entry in reordered_manifest.entries
    }

    assert reordered_bindings == original_bindings


def test_prepared_manifest_entries_include_stable_workspace_status_metadata() -> None:
    manifest = build_prepared_input_manifest(_task_payload())

    dumped = manifest.model_dump(by_alias=True)

    assert dumped["entries"][0]["workspacePath"] == (
        ".moonmind/inputs/objective/artifact-objective-objective.png"
    )
    assert dumped["entries"][0]["status"] == "prepared"
    assert dumped["entries"][1]["workspacePath"] == (
        ".moonmind/inputs/steps/collect-evidence/artifact-step-1-evidence.txt"
    )
    assert dumped["entries"][1]["status"] == "prepared"
    assert "data:image" not in str(dumped)
    assert "base64" not in str(dumped)


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


def test_select_step_context_excludes_sibling_refs_from_shared_workspace() -> None:
    manifest = PreparedInputManifest.model_validate(
        {
            "manifestRef": "prepared-context-manifest://task-inputs",
            "entries": [
                {
                    "artifactId": "objective-image",
                    "targetKind": "objective",
                    "rawInputRef": "artifact://objective-image",
                    "derivedContextRef": "prepared-context://objective/objective-image",
                    "workspacePath": ".moonmind/inputs/shared/objective-image.png",
                },
                {
                    "artifactId": "collect-image",
                    "targetKind": "step",
                    "stepRef": "collect-evidence",
                    "rawInputRef": "artifact://collect-image",
                    "derivedContextRef": (
                        "prepared-context://steps/collect-evidence/collect-image"
                    ),
                    "workspacePath": ".moonmind/inputs/shared/image.png",
                },
                {
                    "artifactId": "report-image",
                    "targetKind": "step",
                    "stepRef": "write-report",
                    "rawInputRef": "artifact://report-image",
                    "derivedContextRef": (
                        "prepared-context://steps/write-report/report-image"
                    ),
                    "workspacePath": ".moonmind/inputs/shared/image.png",
                },
            ],
        }
    )

    context = select_step_prepared_context(
        manifest,
        logical_step_id="collect-evidence",
    )
    dumped = context.model_dump(by_alias=True)

    assert context.objective_context_refs == [
        "prepared-context://objective/objective-image"
    ]
    assert context.step_context_refs == [
        "prepared-context://steps/collect-evidence/collect-image"
    ]
    assert "report-image" not in str(dumped)
    assert "write-report" not in str(dumped)


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


def test_attempt_context_bundle_digest_is_stable_and_attempt_scoped() -> None:
    manifest = build_prepared_input_manifest(_task_payload())
    context = select_step_prepared_context(manifest, logical_step_id="collect-evidence")

    first = build_attempt_context_bundle(
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="collect-evidence",
        attempt=1,
        prepared_context=context,
        runtime_selection={"runtimeId": "codex_cli"},
    )
    duplicate = build_attempt_context_bundle(
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="collect-evidence",
        attempt=1,
        prepared_context=context,
        runtime_selection={"runtimeId": "codex_cli"},
    )
    changed_attempt = build_attempt_context_bundle(
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="collect-evidence",
        attempt=2,
        prepared_context=context,
        runtime_selection={"runtimeId": "codex_cli"},
    )

    assert first.context_bundle_digest == duplicate.context_bundle_digest
    assert first.context_bundle_ref == (
        f"attempt-context-bundle://{first.context_bundle_digest}"
    )
    assert first.context_bundle_digest != changed_attempt.context_bundle_digest
    assert first.builder_version == "attempt-context-builder-v1"
    projection = first.to_manifest_projection()
    assert projection["context"]["contextBundleDigest"] == first.context_bundle_digest
    assert "preparedInputRefs" not in projection["context"]


def test_attempt_context_records_retrieval_and_memory_manifest_refs() -> None:
    retrieval = build_retrieval_manifest(
        {
            "query": "attempt context bundle",
            "indexVersion": "rag-index-1",
            "returnedRefs": ["artifact://doc-1"],
            "filters": {"source": "docs"},
            "compactSummaries": ["Relevant source design section."],
        }
    )
    memory = build_memory_manifest(
        [
            {
                "proposalRef": "memory://proposal-1",
                "state": "proposed",
                "summary": "Remember failed attempt evidence.",
            },
            {
                "proposalRef": "memory://proposal-2",
                "state": "rejected",
                "summary": "Rejected noisy suggestion.",
            },
        ]
    )
    bundle = build_attempt_context_bundle(
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="collect-evidence",
        attempt=1,
        retrieval={
            "query": "attempt context bundle",
            "indexVersion": "rag-index-1",
            "returnedRefs": ["artifact://doc-1"],
            "filters": {"source": "docs"},
            "compactSummaries": ["Relevant source design section."],
        },
        memory_proposals=[
            {
                "proposalRef": "memory://proposal-1",
                "state": "proposed",
                "summary": "Remember failed attempt evidence.",
            },
            {
                "proposalRef": "memory://proposal-2",
                "state": "rejected",
                "summary": "Rejected noisy suggestion.",
            },
        ],
    )

    assert retrieval.query == "attempt context bundle"
    assert retrieval.index_version == "rag-index-1"
    assert retrieval.returned_refs == ["artifact://doc-1"]
    assert retrieval.compact_summaries == ["Relevant source design section."]
    assert retrieval.retrieval_manifest_ref.startswith(
        "attempt-retrieval-manifest://sha256:"
    )
    assert memory.proposals[0].state == "proposed"
    assert memory.proposals[1].state == "rejected"
    assert memory.memory_manifest_ref.startswith("attempt-memory-manifest://sha256:")
    assert bundle.retrieval_manifest_ref == retrieval.retrieval_manifest_ref
    assert bundle.memory_manifest_ref == memory.memory_manifest_ref


def test_attempt_context_rejects_secretish_values_and_unknown_memory_states() -> None:
    with pytest.raises(ValueError, match="raw secret material"):
        build_prepared_input_manifest(
            {
                "inputAttachments": [
                    {
                        "artifactId": "token=password=unsafe",
                    }
                ]
            }
        )

    with pytest.raises(ValueError, match="raw secret material"):
        build_attempt_context_bundle(
            workflow_id="workflow-1",
            run_id="run-1",
            logical_step_id="collect-evidence",
            retrieval={
                "query": "token=unsafe",
                "indexVersion": "rag-index-1",
            },
        )

    with pytest.raises(ValidationError, match="Input should be"):
        build_memory_manifest(
            [
                {
                    "proposalRef": "memory://proposal-1",
                    "state": "auto_promoted",
                }
            ]
        )

    with pytest.raises(ValueError, match="policyRef"):
        build_memory_manifest(
            [
                {
                    "proposalRef": "memory://proposal-1",
                    "state": "applied_to_repo",
                }
            ]
        )


def test_resume_prepared_artifact_refs_are_compact_and_deduped() -> None:
    manifest = build_prepared_input_manifest(_task_payload())

    refs = build_resume_prepared_artifact_refs(manifest)

    assert refs == [
        "prepared-context://objective/artifact-objective",
        "artifact://artifact-objective",
        "prepared-context://steps/collect-evidence/artifact-step-1",
        "artifact://artifact-step-1",
        "prepared-context://steps/write-report/artifact-step-2",
        "artifact://artifact-step-2",
    ]
    assert "data:image" not in str(refs)


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
