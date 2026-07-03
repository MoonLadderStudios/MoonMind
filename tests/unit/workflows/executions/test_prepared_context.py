from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.memory.procedural import (
    EvidenceRun,
    FixPattern,
    extract_error_signature,
)
from moonmind.workflows.executions.prepared_context import (
    MINIMUM_BRANCH_ARTIFACT_NAMES,
    MINIMUM_BRANCH_TURN_ARTIFACT_NAMES,
    PreparedContextFailure,
    PreparedInputEntry,
    PreparedInputManifest,
    branch_turn_step_execution_manifest_projection,
    build_branch_turn_artifact_manifest,
    build_branch_turn_context_bundle,
    build_durable_retrieval_manifest_artifact,
    build_execution_context_bundle,
    build_memory_manifest,
    build_recovery_prepared_artifact_refs,
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


def test_execution_context_bundle_digest_is_stable_and_execution_scoped() -> None:
    manifest = build_prepared_input_manifest(_task_payload())
    context = select_step_prepared_context(manifest, logical_step_id="collect-evidence")

    first = build_execution_context_bundle(
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="collect-evidence",
        execution_ordinal=1,
        prepared_context=context,
        runtime_selection={"runtimeId": "codex_cli"},
    )
    duplicate = build_execution_context_bundle(
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="collect-evidence",
        execution_ordinal=1,
        prepared_context=context,
        runtime_selection={"runtimeId": "codex_cli"},
    )
    changed_execution = build_execution_context_bundle(
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="collect-evidence",
        execution_ordinal=2,
        prepared_context=context,
        runtime_selection={"runtimeId": "codex_cli"},
    )

    assert first.context_bundle_digest == duplicate.context_bundle_digest
    assert first.context_bundle_ref == (
        f"execution-context-bundle://{first.context_bundle_digest}"
    )
    assert first.context_bundle_digest != changed_execution.context_bundle_digest
    assert first.builder_version == "execution-context-builder-v1"
    projection = first.to_manifest_projection()
    assert projection["context"]["contextBundleDigest"] == first.context_bundle_digest
    assert "preparedInputRefs" not in projection["context"]


def test_execution_context_records_retrieval_and_memory_manifest_refs() -> None:
    retrieval = build_retrieval_manifest(
        {
            "query": "execution context bundle",
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
                "summary": "Remember failed execution evidence.",
            },
            {
                "proposalRef": "memory://proposal-2",
                "state": "rejected",
                "summary": "Rejected noisy suggestion.",
            },
        ]
    )
    bundle = build_execution_context_bundle(
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="collect-evidence",
        execution_ordinal=1,
        retrieval={
            "query": "execution context bundle",
            "indexVersion": "rag-index-1",
            "returnedRefs": ["artifact://doc-1"],
            "filters": {"source": "docs"},
            "compactSummaries": ["Relevant source design section."],
        },
        memory_proposals=[
            {
                "proposalRef": "memory://proposal-1",
                "state": "proposed",
                "summary": "Remember failed execution evidence.",
            },
            {
                "proposalRef": "memory://proposal-2",
                "state": "rejected",
                "summary": "Rejected noisy suggestion.",
            },
        ],
    )

    assert retrieval.query == "execution context bundle"
    assert retrieval.index_version == "rag-index-1"
    assert retrieval.returned_refs == ["artifact://doc-1"]
    assert retrieval.compact_summaries == ["Relevant source design section."]
    assert retrieval.retrieval_manifest_ref.startswith(
        "attempt-retrieval-manifest://sha256:"
    )
    assert memory.proposals[0].state == "proposed"
    assert memory.proposals[1].state == "rejected"
    assert memory.memory_manifest_ref.startswith("attempt-memory-manifest://sha256:")
    assert bundle.retrieval_manifest_ref.startswith(
        "artifact://retrieval-manifests/sha256:"
    )
    assert bundle.retrieval_manifest_ref != retrieval.retrieval_manifest_ref
    assert bundle.memory_manifest_ref == memory.memory_manifest_ref


def test_execution_context_records_required_attempt_contract_fields() -> None:
    bundle = build_execution_context_bundle(
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="collect-evidence",
        execution_ordinal=2,
        reason="quality_gate_failed",
        task_input_snapshot_ref="artifact://task-input",
        plan_ref="artifact://plan",
        plan_digest="sha256:plan",
        workspace_policy="apply_previous_execution_diff_to_clean_baseline",
        workspace_baseline={"kind": "git_commit", "commit": "abc123"},
        checkpoint_refs={
            "before": "artifact://checkpoint-before",
            "after": "artifact://checkpoint-after",
        },
        prior_evidence_refs=["artifact://attempt-1-manifest"],
        quality_gate_profile="repo-default",
        policy_refs={
            "providerProfileRef": "provider-profile://default",
            "skillSourcePolicyRef": "skill-policy://resolver",
        },
    )

    dumped = bundle.model_dump(by_alias=True, exclude_none=True)

    assert dumped["taskInputSnapshotRef"] == "artifact://task-input"
    assert dumped["planRef"] == "artifact://plan"
    assert dumped["planDigest"] == "sha256:plan"
    assert dumped["workspacePolicy"] == (
        "apply_previous_execution_diff_to_clean_baseline"
    )
    assert dumped["workspaceBaseline"] == {
        "kind": "git_commit",
        "commit": "abc123",
    }
    assert dumped["checkpointRefs"] == {
        "before": "artifact://checkpoint-before",
        "after": "artifact://checkpoint-after",
    }
    assert dumped["priorEvidenceRefs"] == ["artifact://attempt-1-manifest"]
    assert dumped["qualityGateProfile"] == "repo-default"
    assert dumped["policyRefs"]["providerProfileRef"] == (
        "provider-profile://default"
    )
    assert bundle.context_bundle_digest.startswith("sha256:")
    projection = bundle.to_manifest_projection()
    assert projection["context"]["taskInputSnapshotRef"] == "artifact://task-input"
    assert projection["context"]["checkpointRefs"] == {
        "before": "artifact://checkpoint-before",
        "after": "artifact://checkpoint-after",
    }
    assert projection["context"]["priorEvidenceRefs"] == [
        "artifact://attempt-1-manifest"
    ]


def test_mm_1089_branch_turn_context_records_immutable_launch_evidence() -> None:
    bundle = build_branch_turn_context_bundle(
        workflow_id="workflow-1",
        run_id="run-branch-turn-1",
        logical_step_id="implement-story",
        execution_ordinal=3,
        branch_id="cbr_01",
        branch_turn_id="cbt_01",
        source_checkpoint={
            "workflowId": "workflow-1",
            "runId": "run-source",
            "logicalStepId": "implement-story",
            "sourceExecutionOrdinal": 2,
            "checkpointBoundary": "after_execution",
            "checkpointRef": "artifact://checkpoints/after-execution",
            "checkpointDigest": "sha256:checkpoint",
        },
        initial_instruction_ref="artifact://branch/initial-instructions",
        initial_instruction_digest="sha256:initial",
        instruction_artifact_ref="artifact://branch-turn/instructions",
        instruction_digest="sha256:turn",
        runtime_context_policy="fresh_agent_run",
        workspace_policy="apply_previous_execution_diff_to_clean_baseline",
        workspace_baseline={"kind": "git_patch", "patchRef": "artifact://patch"},
        prior_evidence_refs=["artifact://attempt-2-manifest"],
        bounded_summaries=["Previous attempt failed the unit gate."],
        runtime_selection={"runtimeId": "codex_cli", "model": "gpt-5"},
        policy_refs={"workspacePolicyRef": "artifact://policies/workspace"},
        git_work_branch="mm/workflow-1/implement-story/cbr-01",
    )

    dumped = bundle.model_dump(by_alias=True, exclude_none=True)

    assert dumped["reason"] == "checkpoint_branch"
    assert dumped["executionOrdinal"] == 3
    assert dumped["branch"]["branchId"] == "cbr_01"
    assert dumped["branch"]["branchTurnId"] == "cbt_01"
    assert dumped["branch"]["sourceCheckpoint"]["checkpointRef"] == (
        "artifact://checkpoints/after-execution"
    )
    assert dumped["branch"]["traceability"] == "MM-1089"
    assert dumped["instructionRefs"] == [
        "artifact://branch/initial-instructions",
        "artifact://branch-turn/instructions",
    ]
    assert dumped["instructionDigests"] == {
        "artifact://branch/initial-instructions": "sha256:initial",
        "artifact://branch-turn/instructions": "sha256:turn",
    }
    assert dumped["workspacePolicy"] == (
        "apply_previous_execution_diff_to_clean_baseline"
    )
    assert dumped["runtimeContextPolicy"] == "fresh_agent_run"
    assert dumped["checkpointRefs"]["source"] == (
        "artifact://checkpoints/after-execution"
    )
    assert bundle.context_bundle_ref == (
        f"execution-context-bundle://{bundle.context_bundle_digest}"
    )
    projection = bundle.to_manifest_projection()["context"]
    assert projection["branch"]["branchTurnId"] == "cbt_01"
    assert projection["runtimeContextPolicy"] == "fresh_agent_run"
    assert branch_turn_step_execution_manifest_projection(bundle) == {
        "branch": {
            "branchId": "cbr_01",
            "branchTurnId": "cbt_01",
            "rootCheckpointRef": "artifact://checkpoints/after-execution",
            "gitWorkBranch": "mm/workflow-1/implement-story/cbr-01",
        }
    }


def test_branch_turn_manifest_projection_includes_prepared_workspace_baseline() -> None:
    workspace_baseline = {
        "repository": "MoonLadderStudios/MoonMind",
        "baseBranch": "feature/mm-1101-source",
        "baseCommit": "abc1234",
        "resolvedBaseCommit": "abc1234",
        "workBranch": "mm/workflow-1/implement-story/cbr-01",
        "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
        "creationMode": "from_checkpoint_patch",
        "sourceCheckpointRef": "artifact://checkpoints/after-execution",
        "productBranchId": "cbr_01",
        "branchTurnId": "cbt_01",
        "idempotencyKey": "mm-1101:create",
    }
    bundle = build_branch_turn_context_bundle(
        workflow_id="workflow-1",
        run_id="run-branch-turn-1",
        logical_step_id="implement-story",
        execution_ordinal=3,
        branch_id="cbr_01",
        branch_turn_id="cbt_01",
        source_checkpoint={
            "workflowId": "workflow-1",
            "runId": "run-source",
            "checkpointBoundary": "after_execution",
            "checkpointRef": "artifact://checkpoints/after-execution",
        },
        instruction_artifact_ref="artifact://branch-turn/instructions",
        instruction_digest="sha256:turn",
        runtime_context_policy="fresh_agent_run",
        workspace_policy="apply_previous_execution_diff_to_clean_baseline",
        workspace_baseline=workspace_baseline,
        git_work_branch="mm/workflow-1/implement-story/cbr-01",
    )

    projection = branch_turn_step_execution_manifest_projection(bundle)

    assert projection["branch"]["workspacePolicy"] == (
        "apply_previous_execution_diff_to_clean_baseline"
    )
    assert projection["branch"]["creationMode"] == "from_checkpoint_patch"
    assert projection["branch"]["repository"] == "MoonLadderStudios/MoonMind"
    assert projection["branch"]["baseBranch"] == "feature/mm-1101-source"
    assert projection["branch"]["baseCommit"] == "abc1234"
    assert projection["branch"]["resolvedBaseCommit"] == "abc1234"
    assert projection["branch"]["workspaceBaseline"] == workspace_baseline


def test_mm_1089_branch_turn_artifact_manifest_names_minimum_evidence() -> None:
    bundle = build_branch_turn_context_bundle(
        workflow_id="workflow-1",
        run_id="run-branch-turn-1",
        logical_step_id="implement-story",
        execution_ordinal=3,
        branch_id="cbr_01",
        branch_turn_id="cbt_01",
        source_checkpoint={
            "workflowId": "workflow-1",
            "runId": "run-source",
            "checkpointBoundary": "after_execution",
            "checkpointRef": "artifact://checkpoints/after-execution",
        },
        instruction_artifact_ref="artifact://branch-turn/instructions",
        instruction_digest="sha256:turn",
        runtime_context_policy="reuse_session_new_epoch",
        workspace_policy="continue_from_previous_execution",
    )

    manifest = build_branch_turn_artifact_manifest(
        branch_id="cbr_01",
        branch_turn_id="cbt_01",
        context_bundle=bundle,
    )

    names = [artifact["name"] for artifact in manifest["artifacts"]]
    assert names == [
        *MINIMUM_BRANCH_ARTIFACT_NAMES,
        *MINIMUM_BRANCH_TURN_ARTIFACT_NAMES,
    ]
    assert manifest["traceability"] == "MM-1089"
    assert manifest["contextBundleRef"] == bundle.context_bundle_ref
    assert manifest["artifactManifestDigest"].startswith("sha256:")
    instruction_entry = next(
        artifact
        for artifact in manifest["artifacts"]
        if artifact["name"] == "input.branch_turn.instructions.md"
    )
    assert instruction_entry["artifactRefStatus"] == "planned"
    assert "artifactRef" not in instruction_entry


def test_mm_1089_branch_turn_artifact_manifest_uses_real_refs_when_available() -> None:
    bundle = build_branch_turn_context_bundle(
        workflow_id="workflow-1",
        run_id="run-branch-turn-1",
        logical_step_id="implement-story",
        execution_ordinal=3,
        branch_id="cbr_01",
        branch_turn_id="cbt_01",
        source_checkpoint={
            "workflowId": "workflow-1",
            "runId": "run-source",
            "checkpointBoundary": "after_execution",
            "checkpointRef": "art_checkpoint",
        },
        instruction_artifact_ref="art_instruction",
        instruction_digest="sha256:turn",
        runtime_context_policy="reuse_session_new_epoch",
        workspace_policy="continue_from_previous_execution",
    )

    manifest = build_branch_turn_artifact_manifest(
        branch_id="cbr_01",
        branch_turn_id="cbt_01",
        context_bundle=bundle,
        artifact_refs={
            "input.branch.root_checkpoint.json": "art_checkpoint",
            "input.branch_turn.instructions.md": "art_instruction",
        },
    )

    by_name = {artifact["name"]: artifact for artifact in manifest["artifacts"]}
    assert by_name["input.branch.root_checkpoint.json"]["artifactRef"] == (
        "art_checkpoint"
    )
    assert by_name["input.branch.root_checkpoint.json"]["artifactRefStatus"] == (
        "persisted"
    )
    assert by_name["input.branch_turn.instructions.md"]["artifactRef"] == (
        "art_instruction"
    )
    assert by_name["runtime.branch_turn.agent_result.json"][
        "artifactRefStatus"
    ] == "planned"


def test_mm_1089_branch_turn_context_accepts_typed_source_state() -> None:
    bundle = build_branch_turn_context_bundle(
        workflow_id="workflow-1",
        run_id="run-branch-turn-1",
        logical_step_id="implement-story",
        execution_ordinal=3,
        branch_id="cbr_01",
        branch_turn_id="cbt_01",
        source_checkpoint={
            "workflowId": "workflow-1",
            "runId": "run-source",
            "sourceStateKind": "provider_session",
            "sourceStateRef": "provider://session/1",
            "sourceStateDigest": "sha256:provider-state",
        },
        instruction_artifact_ref="art_instruction",
        instruction_digest="sha256:turn",
        runtime_context_policy="external_provider_continuation",
        workspace_policy="continue_from_previous_execution",
    )

    branch = bundle.model_dump(by_alias=True, exclude_none=True)["branch"]
    assert branch["sourceStateKind"] == "provider_session"
    assert branch["sourceStateRef"] == "provider://session/1"
    assert "rootCheckpointRef" not in branch
    assert branch_turn_step_execution_manifest_projection(bundle) == {
        "branch": {
            "branchId": "cbr_01",
            "branchTurnId": "cbt_01",
            "sourceStateKind": "provider_session",
            "sourceStateRef": "provider://session/1",
            "sourceStateDigest": "sha256:provider-state",
        }
    }


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        (
            {"instruction_artifact_ref": "inline instruction text"},
            "instruction_artifact_ref must be an artifact ref",
        ),
        (
            {"instruction_digest": "not-a-digest"},
            "instruction_digest must be a sha256 digest",
        ),
        (
            {"source_checkpoint": {"checkpointRef": "artifact://checkpoint"}},
            "source_checkpoint.workflowId",
        ),
        (
            {
                "source_checkpoint": {
                    "workflowId": "workflow-1",
                    "runId": "run-source",
                    "checkpointBoundary": "after_execution",
                    "checkpointRef": "artifact://checkpoint",
                    "providerPayload": {"messages": ["raw"]},
                }
            },
            "provider payload",
        ),
    ],
)
def test_mm_1089_branch_turn_context_rejects_unsafe_or_incomplete_inputs(
    kwargs: dict[str, object],
    match: str,
) -> None:
    base = {
        "workflow_id": "workflow-1",
        "run_id": "run-branch-turn-1",
        "logical_step_id": "implement-story",
        "execution_ordinal": 3,
        "branch_id": "cbr_01",
        "branch_turn_id": "cbt_01",
        "source_checkpoint": {
            "workflowId": "workflow-1",
            "runId": "run-source",
            "checkpointBoundary": "after_execution",
            "checkpointRef": "artifact://checkpoints/after-execution",
        },
        "instruction_artifact_ref": "artifact://branch-turn/instructions",
        "instruction_digest": "sha256:turn",
        "runtime_context_policy": "fresh_agent_run",
        "workspace_policy": "continue_from_previous_execution",
    }
    base.update(kwargs)

    with pytest.raises(ValueError, match=match):
        build_branch_turn_context_bundle(**base)


def test_with_retrieval_manifest_ref_recomputes_digest() -> None:
    bundle = build_execution_context_bundle(
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="collect-evidence",
        execution_ordinal=1,
        retrieval={
            "status": "captured",
            "query": "execution context",
            "returnedRefs": ["artifact://doc-1"],
        },
    )

    assert bundle.retrieval_manifest_ref is not None
    swapped = bundle.with_retrieval_manifest_ref("art_retrieval_persisted")

    assert swapped.retrieval_manifest_ref == "art_retrieval_persisted"
    # retrievalManifestRef participates in the digest, so the digest-addressed
    # refs must change and stay self-consistent rather than going stale.
    assert swapped.context_bundle_digest != bundle.context_bundle_digest
    assert swapped.context_bundle_ref == (
        f"execution-context-bundle://{swapped.context_bundle_digest}"
    )
    assert swapped.to_manifest_projection()["context"]["contextBundleDigest"] == (
        swapped.context_bundle_digest
    )
    # Every other field is preserved unchanged.
    assert swapped.logical_step_id == bundle.logical_step_id
    assert swapped.runtime_selection == bundle.runtime_selection
    # Re-applying the same ref is idempotent.
    assert swapped.with_retrieval_manifest_ref(
        "art_retrieval_persisted"
    ).context_bundle_digest == swapped.context_bundle_digest


def test_retrieval_manifest_records_explicit_statuses() -> None:
    captured = build_retrieval_manifest(
        {
            "status": "captured",
            "query": "execution context bundle",
            "returnedRefs": ["artifact://doc-1"],
            "excludedRefs": ["artifact://doc-secret"],
        }
    )
    skipped = build_retrieval_manifest({"status": "skipped"})
    unavailable = build_retrieval_manifest(
        {"status": "unavailable", "selector": {"reason": "index_offline"}}
    )

    assert captured.status == "captured"
    assert captured.excluded_refs == ["artifact://doc-secret"]
    assert skipped.status == "skipped"
    assert skipped.retrieval_manifest_ref.startswith(
        "attempt-retrieval-manifest://sha256:"
    )
    assert unavailable.status == "unavailable"


def test_durable_retrieval_manifest_artifact_preserves_captured_status() -> None:
    artifact = build_durable_retrieval_manifest_artifact(
        {
            "status": "captured",
            "query": "execution context bundle",
            "indexVersion": "rag-index-1",
            "returnedRefs": ["artifact://doc-1"],
            "filters": {"source": "docs"},
            "excludedRefs": ["artifact://doc-secret"],
            "compactSummaries": ["Relevant source design section."],
        }
    )

    payload = artifact["payload"]

    assert artifact["artifactRef"] == payload["retrievalManifestRef"]
    assert artifact["contentType"] == "application/json"
    assert artifact["metadata"]["artifact_kind"] == "retrieval_manifest"
    assert payload["status"] == "captured"
    assert payload["query"] == "execution context bundle"
    assert payload["indexVersion"] == "rag-index-1"
    assert payload["returnedRefs"] == ["artifact://doc-1"]
    assert payload["excludedRefs"] == ["artifact://doc-secret"]
    assert payload["compactSummaries"] == ["Relevant source design section."]
    assert payload["retrievalManifestDigest"].startswith("sha256:")
    assert payload["retrievalManifestRef"].startswith(
        "artifact://retrieval-manifests/sha256:"
    )


def test_durable_retrieval_manifest_artifact_preserves_absent_statuses() -> None:
    skipped = build_durable_retrieval_manifest_artifact({"status": "skipped"})
    unavailable = build_durable_retrieval_manifest_artifact(
        {"status": "unavailable", "selector": {"reason": "index_offline"}}
    )

    assert skipped["payload"]["status"] == "skipped"
    assert skipped["payload"]["returnedRefs"] == []
    assert unavailable["payload"]["status"] == "unavailable"
    assert unavailable["payload"]["selector"] == {"reason": "index_offline"}
    assert skipped["artifactRef"] != unavailable["artifactRef"]


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("runtime_selection", {"token": "ghp_secret"}, "raw secret"),
        ("workspace_baseline", {"log": "line\n" * 600}, "large log"),
        (
            "workspace_baseline",
            {"diff": "diff --git a/a b/a\n@@ -1 +1 @@"},
            "raw diff",
        ),
        (
            "policy_refs",
            {"providerPayload": {"messages": ["raw"]}},
            "provider payload",
        ),
    ],
)
def test_execution_context_rejects_unsafe_payloads(
    field: str,
    value: object,
    match: str,
) -> None:
    kwargs = {
        "workflow_id": "workflow-1",
        "run_id": "run-1",
        "logical_step_id": "collect-evidence",
        "execution_ordinal": 1,
        field: value,
    }

    with pytest.raises(ValueError, match=match):
        build_execution_context_bundle(**kwargs)


@pytest.mark.parametrize(
    ("retrieval", "match"),
    [
        ({"query": "ghp_secret"}, "raw secret"),
        (
            {
                "status": "captured",
                "query": "ok",
                "filters": {"log": "line\n" * 600},
            },
            "large log",
        ),
        (
            {
                "status": "captured",
                "query": "ok",
                "filters": {"diff": "diff --git a/a b/a\n@@ -1 +1 @@"},
            },
            "raw diff",
        ),
        (
            {
                "status": "captured",
                "query": "ok",
                "selector": {"providerPayload": {"messages": ["raw"]}},
            },
            "provider payload",
        ),
    ],
)
def test_retrieval_manifest_rejects_unsafe_payloads(
    retrieval: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        build_retrieval_manifest(retrieval)


def test_execution_context_records_budgeted_memory_context_ref() -> None:
    bundle = build_execution_context_bundle(
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="collect-evidence",
        execution_ordinal=1,
        memory_context={
            "tokenBudget": 32,
            "candidates": [
                {
                    "text": "Prefer the proven fix pattern for this error.",
                    "source": "fix-pattern://signature-1",
                    "plane": "history",
                    "trustClass": "derived",
                    "provenance": {
                        "workflowId": "workflow-previous",
                        "artifactRefs": ["artifact://fix-pattern-1"],
                    },
                    "recency": "2026-06-01T12:00:00Z",
                    "tokenCost": 10,
                }
            ],
        },
    )

    assert bundle.memory_context_ref is not None
    assert bundle.memory_context_ref.startswith("memory-context-pack://sha256:")
    assert bundle.to_manifest_projection()["context"]["memoryContextRef"] == (
        bundle.memory_context_ref
    )


def test_execution_context_uses_configured_default_memory_context_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, int] = {}

    def fake_build_memory_context_pack(
        candidates: object,
        *,
        token_budget: int,
    ) -> object:
        observed["token_budget"] = token_budget

        class Pack:
            memory_context_ref = "memory-context-pack://sha256:configured"

        return Pack()

    monkeypatch.setattr(
        "moonmind.workflows.executions.prepared_context.settings.workflow."
        "memory_context_budget_tokens",
        1234,
    )
    monkeypatch.setattr(
        "moonmind.workflows.executions.prepared_context.build_memory_context_pack",
        fake_build_memory_context_pack,
    )

    bundle = build_execution_context_bundle(
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="collect-evidence",
        execution_ordinal=1,
        memory_context={"candidates": []},
    )

    assert observed == {"token_budget": 1234}
    assert bundle.memory_context_ref == "memory-context-pack://sha256:configured"


def test_execution_context_rejects_zero_memory_context_budget() -> None:
    with pytest.raises(ValueError, match="token_budget must be positive"):
        build_execution_context_bundle(
            workflow_id="workflow-1",
            run_id="run-1",
            logical_step_id="collect-evidence",
            memory_context={
                "tokenBudget": 0,
                "candidates": [],
            },
        )


def test_execution_context_projects_fix_patterns_into_memory_manifest() -> None:
    signature = extract_error_signature("RuntimeError: missing qdrant collection")
    assert signature is not None
    fix_pattern = FixPattern.from_successful_run(
        signature=signature,
        summary="Create the Qdrant collection before indexing.",
        steps=["Run namespace bootstrap before the retrieval write."],
        evidence=EvidenceRun(workflowId="workflow-1", outcome="succeeded"),
    )
    expected_memory = build_memory_manifest(
        [
            {
                "proposalRef": fix_pattern.pattern_ref,
                "state": "accepted_for_run_context",
                "summary": (
                    "Create the Qdrant collection before indexing. Steps: "
                    "Run namespace bootstrap before the retrieval write."
                ),
            }
        ]
    )

    bundle = build_execution_context_bundle(
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="collect-evidence",
        fix_patterns=[fix_pattern.model_dump(by_alias=True)],
    )

    assert bundle.memory_manifest_ref == expected_memory.memory_manifest_ref


def test_retrieval_manifest_accepts_documented_retrieved_refs_key() -> None:
    retrieval = build_retrieval_manifest(
        {
            "retrievedRefs": ["artifact://doc-1", "artifact://doc-2"],
        }
    )

    assert retrieval.returned_refs == ["artifact://doc-1", "artifact://doc-2"]
    assert retrieval.retrieval_manifest_ref.startswith(
        "attempt-retrieval-manifest://sha256:"
    )


def test_retrieval_manifest_allows_literal_assignment_search_terms() -> None:
    retrieval = build_retrieval_manifest(
        {
            "query": "find `password=` assignments and token= examples",
            "indexVersion": "rag-index-1",
        }
    )

    assert retrieval.query == "find `password=` assignments and token= examples"


def test_execution_context_rejects_secretish_values_and_unknown_memory_states() -> None:
    with pytest.raises(ValueError, match="raw secret material"):
        build_prepared_input_manifest(
            {
                "inputAttachments": [
                    {
                        "artifactId": "ghp_unsafe",
                    }
                ]
            }
        )

    with pytest.raises(ValueError, match="raw secret material"):
        build_execution_context_bundle(
            workflow_id="workflow-1",
            run_id="run-1",
            logical_step_id="collect-evidence",
            retrieval={
                "query": "ghp_unsafe",
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


def test_recovery_prepared_artifact_refs_are_compact_and_deduped() -> None:
    manifest = build_prepared_input_manifest(_task_payload())

    refs = build_recovery_prepared_artifact_refs(manifest)

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
