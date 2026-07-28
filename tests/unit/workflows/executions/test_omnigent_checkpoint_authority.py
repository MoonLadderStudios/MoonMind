import pytest

from moonmind.workflows.executions.omnigent_checkpoint_authority import (
    OmnigentCheckpointAuthorityError,
    compile_omnigent_checkpoint_execution,
)


def _workspace() -> dict[str, object]:
    checkpoint_ref = "artifact://omnigent/state/1"
    return {
        "workspaceCheckpoint": {
            "kind": "external_state_ref",
            "externalStateRef": checkpoint_ref,
            "idempotencyKey": "source-attempt",
            "omnigentSessionId": "session-1",
            "providerProfileId": "codex",
            "credentialGeneration": 3,
            "providerLeaseRef": "provider-lease-1",
            "hostBindingRef": "binding-1",
            "hostLeaseRef": "host-lease-1",
            "endpointRef": "endpoint-1",
            "omnigentHostId": "host-1",
            "bridgeSessionId": "bridge-1",
        },
        "candidateWorkspace": {
            "loopId": "loop-1",
            "attemptOrdinal": 2,
            "headRef": "artifact://head/2",
            "headDigest": "sha256:" + "a" * 64,
            "checkpointRef": checkpoint_ref,
            "checkpointDigest": "sha256:" + "b" * 64,
        },
    }


def test_compiler_defaults_to_fail_closed_cold_restore_authority() -> None:
    execution = compile_omnigent_checkpoint_execution(
        recovery_workspace=_workspace(),
        validation_ref="artifact://validation/1",
    )

    assert execution.action == "resume"
    assert execution.checkpoint.provider_profile_id == "codex"
    assert execution.provider_lease is None
    assert execution.host_lease is None
    assert execution.host_registered is False
    assert execution.session_valid is False


def test_compiler_accepts_controller_resolved_live_authority() -> None:
    execution = compile_omnigent_checkpoint_execution(
        recovery_workspace=_workspace(),
        validation_ref="artifact://validation/1",
        current_authority={
            "currentCredentialGeneration": 3,
            "providerLease": {
                "active": True,
                "leaseId": "provider-lease-1",
            },
            "hostLease": {
                "status": "assigned",
                "leaseId": "host-lease-1",
                "credentialGeneration": 3,
            },
            "hostRegistered": True,
            "sessionValid": True,
            "firstMessageConsistent": True,
            "eventCursorValid": True,
        },
    )

    assert execution.provider_lease == {
        "active": True,
        "leaseId": "provider-lease-1",
    }
    assert execution.host_registered is True


def test_compiler_rejects_mismatched_checkpoint_authority() -> None:
    workspace = _workspace()
    workspace["candidateWorkspace"]["checkpointRef"] = "artifact://other"  # type: ignore[index]

    with pytest.raises(
        OmnigentCheckpointAuthorityError,
        match="resume_unavailable:checkpoint_identity_mismatch",
    ):
        compile_omnigent_checkpoint_execution(
            recovery_workspace=workspace,
            validation_ref="artifact://validation/1",
        )
