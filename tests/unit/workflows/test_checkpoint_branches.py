from __future__ import annotations

from datetime import UTC, datetime

import pytest

from moonmind.workflows.checkpoint_branches import (
    CHECKPOINT_BRANCH_GIT_BINDING_CONTENT_TYPE,
    CHECKPOINT_BRANCH_TURN_CONTEXT_BUNDLE_CONTENT_TYPE,
    CHECKPOINT_BRANCH_WORKSPACE_RESTORE_CONTENT_TYPE,
    CheckpointBranchGitBindingError,
    CheckpointBranchContextBundleError,
    build_checkpoint_branch_turn_context_bundle,
    generate_checkpoint_branch_name,
    prepare_checkpoint_branch_git_binding,
)


def _binding_input(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "workflowId": "MM-1087 Workflow",
        "productBranchId": "cbr_MM-1090",
        "branchTurnId": "cbt_1",
        "sourceCheckpointRef": "artifact://checkpoint/root",
        "sourceCheckpointDigest": "sha256:checkpoint",
        "logicalStepId": "Implement MM-1090",
        "label": "Fix Git Isolation!",
        "repository": "MoonLadderStudios/MoonMind",
        "baseBranch": "feature/mm-1087-source",
        "baseCommit": "abc1234",
        "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
        "creationMode": "from_checkpoint_patch",
        "idempotencyKey": "MM-1090:MM-1087:checkpoint",
    }
    payload.update(overrides)
    return payload


def test_generated_checkpoint_branch_name_is_deterministic_and_sanitized() -> None:
    first = generate_checkpoint_branch_name(
        workflow_id="MM-1087 Workflow",
        logical_step_id="Implement MM-1090",
        checkpoint_ref="artifact://checkpoint/root",
        product_branch_id="cbr_MM-1090",
        label="Fix Git Isolation!",
        idempotency_key="MM-1090:MM-1087:checkpoint",
    )
    second = generate_checkpoint_branch_name(
        workflow_id="MM-1087 Workflow",
        logical_step_id="Implement MM-1090",
        checkpoint_ref="artifact://checkpoint/root",
        product_branch_id="cbr_MM-1090",
        label="Fix Git Isolation!",
        idempotency_key="MM-1090:MM-1087:checkpoint",
    )

    assert first == second
    assert first.startswith("mm/mm-1087-workflow/implement-mm-1090/cp-")
    assert first.endswith("/cbr_mm-1090-fix-git-isolation")
    assert " " not in first
    assert "!" not in first


def test_generated_checkpoint_branch_name_caps_long_components() -> None:
    branch = generate_checkpoint_branch_name(
        workflow_id="workflow-" + ("x" * 255),
        logical_step_id="step-" + ("y" * 255),
        checkpoint_ref="artifact://checkpoint/root",
        product_branch_id="cbr_MM-1090",
        label="Fix Git Isolation!",
        idempotency_key="MM-1090:MM-1087:checkpoint",
    )

    parts = branch.split("/")
    assert len(branch) <= 255
    assert len(parts[1]) == 40
    assert len(parts[2]) == 40


def test_prepare_binding_separates_product_branch_from_git_work_branch() -> None:
    created_at = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)

    result = prepare_checkpoint_branch_git_binding(
        _binding_input(),
        known_refs={"feature/mm-1087-source"},
        current_ref="feature/mm-1087-source",
        created_at=created_at,
    )

    binding = result.binding.model_dump(by_alias=True, mode="json")
    assert binding["productBranchId"] == "cbr_MM-1090"
    assert binding["workBranch"].startswith("mm/mm-1087-workflow/")
    assert binding["workBranch"] != binding["productBranchId"]
    assert binding["workspacePolicy"] == "apply_previous_execution_diff_to_clean_baseline"
    assert result.branch_metadata["workspacePolicy"] == binding["workspacePolicy"]
    assert result.branch_turn_metadata is not None
    assert result.branch_turn_metadata["workspacePolicy"] == binding["workspacePolicy"]
    assert (
        result.step_execution_manifest_branch["workspacePolicy"]
        == binding["workspacePolicy"]
    )
    assert result.diagnostics["workspacePolicy"] == binding["workspacePolicy"]


def test_prepare_binding_accepts_normalized_protected_base_ref() -> None:
    result = prepare_checkpoint_branch_git_binding(
        _binding_input(baseBranch="main"),
        known_refs={"refs/heads/main"},
        current_ref="main",
    )

    assert result.binding.base_branch == "main"


def test_prepare_binding_emits_workspace_restore_and_git_binding_artifacts() -> None:
    result = prepare_checkpoint_branch_git_binding(
        _binding_input(),
        known_refs={"feature/mm-1087-source"},
        current_ref="feature/mm-1087-source",
        created_at=datetime(2026, 7, 2, 12, 0, tzinfo=UTC),
    )

    assert (
        result.workspace_restore_payload["contentType"]
        == CHECKPOINT_BRANCH_WORKSPACE_RESTORE_CONTENT_TYPE
    )
    assert (
        result.git_binding_payload["contentType"]
        == CHECKPOINT_BRANCH_GIT_BINDING_CONTENT_TYPE
    )
    assert result.workspace_restore_payload["productBranchId"] == "cbr_MM-1090"
    assert result.git_binding_payload["productBranchId"] == "cbr_MM-1090"
    assert (
        result.diagnostics["evidence"]["workspaceRestoreArtifact"]
        == "runtime.branch.workspace_restore.json"
    )
    assert (
        result.diagnostics["evidence"]["gitBindingArtifact"]
        == "runtime.branch.git_binding.json"
    )


@pytest.mark.parametrize(
    ("overrides", "known_refs", "current_ref", "failure_code"),
    [
        (
            {"requestedWorkBranch": "main"},
            {"feature/mm-1087-source"},
            "feature/mm-1087-source",
            "protected_branch_ref",
        ),
        ({}, {"feature/mm-1087-source"}, "HEAD", "detached_head"),
        (
            {"baseBranch": "missing"},
            {"feature/mm-1087-source"},
            "feature/mm-1087-source",
            "unknown_ref",
        ),
        ({}, set(), "feature/mm-1087-source", "unknown_ref"),
        (
            {"requestedWorkBranch": "mm/MM 1087/not-sanitized"},
            {"feature/mm-1087-source"},
            "feature/mm-1087-source",
            "protected_branch_ref",
        ),
        (
            {"requestedWorkBranch": ".foo/bar"},
            {"feature/mm-1087-source"},
            "feature/mm-1087-source",
            "protected_branch_ref",
        ),
        (
            {"requestedWorkBranch": "mm/foo.lock/bar"},
            {"feature/mm-1087-source"},
            "feature/mm-1087-source",
            "protected_branch_ref",
        ),
        (
            {"requestedWorkBranch": "mm/foo."},
            {"feature/mm-1087-source"},
            "feature/mm-1087-source",
            "protected_branch_ref",
        ),
        (
            {"requestedWorkBranch": "mm/" + ("x" * 253)},
            {"feature/mm-1087-source"},
            "feature/mm-1087-source",
            "protected_branch_ref",
        ),
        ({"branchTurnId": None}, {"feature/mm-1087-source"}, "main", "invalid_binding"),
    ],
)
def test_prepare_binding_fails_closed_for_unsafe_refs(
    overrides: dict[str, object],
    known_refs: set[str],
    current_ref: str,
    failure_code: str,
) -> None:
    with pytest.raises(CheckpointBranchGitBindingError) as exc_info:
        prepare_checkpoint_branch_git_binding(
            _binding_input(**overrides),
            known_refs=known_refs,
            current_ref=current_ref,
        )

    assert exc_info.value.failure_code == failure_code


def test_prepare_binding_reuses_matching_collision_idempotently() -> None:
    work_branch = "mm/mm-1087/implement/cp-12345678/cbr-mm-1090"

    result = prepare_checkpoint_branch_git_binding(
        _binding_input(requestedWorkBranch=work_branch),
        known_refs={"feature/mm-1087-source"},
        current_ref="feature/mm-1087-source",
        existing_bindings_by_work_branch={
            work_branch: {
                "productBranchId": "cbr_MM-1090",
                "repository": "moonladderstudios/moonmind",
            }
        },
    )

    assert result.binding.work_branch == work_branch


def test_prepare_binding_rejects_work_branch_that_already_exists_as_repo_ref() -> None:
    with pytest.raises(CheckpointBranchGitBindingError) as exc_info:
        prepare_checkpoint_branch_git_binding(
            _binding_input(requestedWorkBranch="feature/existing-work"),
            known_refs={"feature/mm-1087-source", "refs/heads/feature/existing-work"},
            current_ref="feature/mm-1087-source",
        )

    assert exc_info.value.failure_code == "git_branch_collision"


def test_prepare_binding_rejects_mismatched_collision() -> None:
    work_branch = "mm/mm-1087/implement/cp-12345678/cbr-mm-1090"

    with pytest.raises(CheckpointBranchGitBindingError) as exc_info:
        prepare_checkpoint_branch_git_binding(
            _binding_input(requestedWorkBranch=work_branch),
            known_refs={"feature/mm-1087-source"},
            current_ref="feature/mm-1087-source",
            existing_bindings_by_work_branch={
                work_branch: {
                    "productBranchId": "cbr_other",
                    "repository": "MoonLadderStudios/MoonMind",
                }
            },
        )

    assert exc_info.value.failure_code == "git_branch_collision"


def test_build_branch_turn_context_bundle_includes_required_refs_only() -> None:
    bundle = build_checkpoint_branch_turn_context_bundle(
        {
            "workflowId": "wf-1",
            "runId": "run-branch",
            "logicalStepId": "implement",
            "executionOrdinal": 3,
            "branch": {
                "branchId": "cbr-1",
                "branchTurnId": "cbt-1",
                "sourceCheckpointRef": "artifact://checkpoint/after",
                "parentBranchId": None,
                "parentTurnId": None,
                "gitWorkBranch": "mm/wf-1/implement/cbr-1",
            },
            "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
            "workspaceBaseline": {
                "kind": "git_patch",
                "baseCommit": "abc123",
                "patchRef": "artifact://patches/1",
            },
            "instructionRefs": ["artifact://instructions/1"],
            "instructionDigests": ["sha256:instructions"],
            "priorEvidenceRefs": ["artifact://manifest/previous"],
            "boundedSummaries": [{"label": "prior failure", "summary": "gate failed"}],
            "builderMetadata": {
                "version": "test-builder-v1",
                "digest": "sha256:builder",
            },
        }
    )

    assert bundle["contentType"] == CHECKPOINT_BRANCH_TURN_CONTEXT_BUNDLE_CONTENT_TYPE
    assert bundle["reason"] == "checkpoint_branch"
    assert bundle["branch"]["branchId"] == "cbr-1"
    assert bundle["branch"]["branchTurnId"] == "cbt-1"
    assert bundle["instructionRefs"] == ["artifact://instructions/1"]
    assert bundle["priorEvidenceRefs"] == ["artifact://manifest/previous"]
    assert "rawLogs" not in bundle
    assert "providerPayload" not in bundle


@pytest.mark.parametrize(
    "forbidden_key",
    ["rawLogs", "rawDiff", "providerPayload", "credentials", "secret"],
)
def test_build_branch_turn_context_bundle_rejects_forbidden_raw_content(
    forbidden_key: str,
) -> None:
    payload = {
        "workflowId": "wf-1",
        "runId": "run-branch",
        "branch": {
            "branchId": "cbr-1",
            "branchTurnId": "cbt-1",
            "sourceCheckpointRef": "artifact://checkpoint/after",
        },
        "workspacePolicy": "continue_from_previous_execution",
        "workspaceBaseline": {"kind": "git_ref", "ref": "main"},
        "instructionRefs": ["artifact://instructions/1"],
        "priorEvidenceRefs": [],
        "builderMetadata": {"version": "test-builder-v1", "digest": "sha256:builder"},
        forbidden_key: "do not persist this",
    }

    with pytest.raises(CheckpointBranchContextBundleError, match=forbidden_key):
        build_checkpoint_branch_turn_context_bundle(payload)
