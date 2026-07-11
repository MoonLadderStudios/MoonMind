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
        workspace_locator={"kind": "external_state", "artifactRef": "art_1"},
        checkpoint_kind="external_state_ref",
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
    replacement = RuntimeExecutionCapabilities.model_validate(
        {**snapshot, "checkpointCaptureKinds": ["future_kind"]}
    )

    recorded = RuntimeExecutionCapabilities.model_validate(snapshot)

    assert recorded.checkpoint_capture_kinds == ("external_state_ref",)
    assert replacement.checkpoint_capture_kinds == ("future_kind",)
