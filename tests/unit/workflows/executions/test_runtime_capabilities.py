from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.workflows.executions.runtime_capabilities import (
    CAPABILITY_SET_VERSION,
    RUNTIME_EXECUTION_CAPABILITIES,
    RuntimeCapabilityError,
    RuntimeExecutionCapabilities,
    RuntimeExecutionCapabilityRegistry,
    resolve_runtime_execution_capabilities,
    validate_runtime_preflight,
)
from moonmind.workflows.temporal.checkpoint_policy import resolve_checkpoint_policy


def test_every_registered_runtime_descriptor_is_valid_and_versioned() -> None:
    assert RUNTIME_EXECUTION_CAPABILITIES.runtime_ids == (
        "claude_code", "codex_cli", "codex_cloud", "jules", "omnigent", "openclaw"
    )
    for runtime_id in RUNTIME_EXECUTION_CAPABILITIES.runtime_ids:
        descriptor = resolve_runtime_execution_capabilities(runtime_id)
        assert descriptor.capability_set_version == CAPABILITY_SET_VERSION
        assert len(descriptor.capability_digest) == 64
        assert descriptor == RuntimeExecutionCapabilities.model_validate(
            descriptor.model_dump(by_alias=True, mode="json")
        )


def test_alias_resolves_before_lookup_and_unknown_runtime_fails() -> None:
    assert resolve_runtime_execution_capabilities("codex").runtime_id == "codex_cli"
    assert resolve_runtime_execution_capabilities("jules_api").runtime_id == "jules"
    with pytest.raises(RuntimeCapabilityError, match="unknown agent runtime"):
        resolve_runtime_execution_capabilities("new_runtime")


def test_registry_rejects_duplicates() -> None:
    descriptor = resolve_runtime_execution_capabilities("jules")
    with pytest.raises(RuntimeCapabilityError, match="duplicate"):
        RuntimeExecutionCapabilityRegistry((descriptor, descriptor))


def test_contradictory_descriptor_claims_fail_validation() -> None:
    with pytest.raises(ValidationError, match="declared together"):
        RuntimeExecutionCapabilities(
            runtimeId="broken", workspaceAuthority="managed_runtime",
            checkpointCaptureKinds=("git_patch",),
            supportsSameSessionContinuation=False,
            postExecutionCheckpointCriticality="required",
        )


def test_workspace_authority_and_checkpoint_kind_are_jointly_validated() -> None:
    omnigent = resolve_runtime_execution_capabilities("omnigent")
    validate_runtime_preflight(
        omnigent,
        workspace_locator={"kind": "sandbox", "workspaceId": "ws_1"},
        checkpoint_kind="worktree_archive",
        restore_required=True,
        same_session_continuation=True,
    )
    with pytest.raises(RuntimeCapabilityError, match="not 'managed_runtime'"):
        validate_runtime_preflight(omnigent, workspace_authority="managed_runtime")
    with pytest.raises(RuntimeCapabilityError, match="cannot capture"):
        validate_runtime_preflight(omnigent, checkpoint_kind="git_patch")


def test_same_session_capability_is_enforced() -> None:
    with pytest.raises(RuntimeCapabilityError, match="same-session"):
        validate_runtime_preflight(
            resolve_runtime_execution_capabilities("claude_code"),
            same_session_continuation=True,
        )


def test_codex_declares_managed_capture_and_restore() -> None:
    codex = resolve_runtime_execution_capabilities("codex_cli")
    assert codex.checkpoint_capture_kinds == ("worktree_archive",)
    assert codex.checkpoint_capture_activity == (
        "agent_runtime.capture_workspace_checkpoint"
    )
    assert codex.checkpoint_restore_kinds == ("worktree_archive",)
    assert codex.checkpoint_restore_activity == (
        "agent_runtime.restore_workspace_checkpoint"
    )
    assert codex.checkpoint_boundary_support == {
        "before_execution": ("rerun_failed_step",)
    }


def test_v1_restore_snapshot_does_not_require_v2_artifact_contract() -> None:
    snapshot = RuntimeExecutionCapabilities(
        capabilitySetVersion="runtime-execution-capabilities-v1",
        runtimeId="codex_cli", runtimeFamily="managed_cli",
        workspaceAuthority="managed_runtime",
        checkpointRestoreKinds=("worktree_archive",),
        checkpointRestoreActivity="agent_runtime.restore_workspace_checkpoint",
        supportsSameSessionContinuation=True,
        postExecutionCheckpointCriticality="recoverability_only",
    )
    assert snapshot.checkpoint_artifact_contract_version is None


def test_codex_after_execution_selects_its_supported_archive_kind() -> None:
    capabilities = resolve_runtime_execution_capabilities("codex_cli")

    policy = resolve_checkpoint_policy(
        boundary="after_execution", capabilities=capabilities
    )

    assert policy.checkpoint_kind == "worktree_archive"


def test_checkpoint_matrix_never_defaults_external_runtime_to_local_capture() -> None:
    for runtime_id in RUNTIME_EXECUTION_CAPABILITIES.runtime_ids:
        capabilities = resolve_runtime_execution_capabilities(runtime_id)
        for boundary in ("before_execution", "after_execution", "before_publication"):
            policy = resolve_checkpoint_policy(boundary=boundary, capabilities=capabilities)
            if (
                capabilities.workspace_authority == "external_provider"
                and not capabilities.checkpoint_capture_kinds
            ):
                assert policy.capture_activity != "workspace.capture_checkpoint"
            if policy.checkpoint_kind is not None:
                assert policy.checkpoint_kind in capabilities.checkpoint_capture_kinds


def test_recorded_capability_snapshot_is_independent_of_registry_changes() -> None:
    snapshot = resolve_runtime_execution_capabilities("omnigent").model_dump(
        by_alias=True, mode="json"
    )
    replacement_payload = {**snapshot, "checkpointCaptureKinds": ["future_kind"]}
    replacement_payload["workspaceState"] = {
        **snapshot["workspaceState"], "checkpointKinds": ["future_kind"]
    }
    replacement = RuntimeExecutionCapabilities.model_validate(replacement_payload)

    recorded = RuntimeExecutionCapabilities.model_validate(snapshot)

    assert recorded.checkpoint_capture_kinds == ("worktree_archive",)
    assert replacement.checkpoint_capture_kinds == ("future_kind",)


def test_omnigent_freezes_three_independent_ownership_planes() -> None:
    capability = resolve_runtime_execution_capabilities("omnigent")

    assert capability.agent_identity.model_dump(by_alias=True) == {
        "agentKind": "external", "agentId": "omnigent", "harness": "codex-native"
    }
    assert capability.session_state.authority == "external_provider"
    assert capability.session_state.checkpoint_kinds == ("external_state_ref",)
    assert capability.workspace_state.authority == "moonmind_sandbox"
    assert capability.workspace_state.locator_kinds == ("sandbox",)
    assert capability.workspace_state.restore_activity == "workspace.apply_checkpoint"
    assert capability.host_realization.workspace_mount_target == "/workspaces/run"
    assert capability.host_realization.require_mode(
        "on_demand_docker", repository_mutation=True, github_credentials=True
    ).mode == "on_demand_docker"
    with pytest.raises(RuntimeCapabilityError, match="does not isolate repository mutation"):
        capability.host_realization.require_mode(
            "static_compose", repository_mutation=True
        )


def test_v3_rejects_contradictory_flattened_workspace_claim() -> None:
    snapshot = resolve_runtime_execution_capabilities("omnigent").model_dump(by_alias=True)
    snapshot["workspaceAuthority"] = "external_provider"
    with pytest.raises(ValidationError, match="contradicts workspace state"):
        RuntimeExecutionCapabilities.model_validate(snapshot)
