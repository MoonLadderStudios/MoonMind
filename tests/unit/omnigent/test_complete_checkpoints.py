from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.omnigent.checkpoints import (
    OmnigentCheckpointManifest,
    build_cold_restore_inputs,
    materialize_cold_restore,
    validate_restore_material,
)


def _digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _manifest(**updates: object) -> OmnigentCheckpointManifest:
    payloads = {
        "artifact://external": b"external",
        "artifact://head": b"head",
        "artifact://checkpoint": b"checkpoint",
        "artifact://execution-profile": b"profile",
        "artifact://launch-policy": b"policy",
        "artifact://resources": b"resources",
        "artifact://capture": b"capture",
        "artifact://instructions": b"instructions",
        "artifact://context": b"context",
        "artifact://terminal": b"terminal",
        "artifact://diagnostics": b"diagnostics",
    }
    data = {
        "workflowId": "workflow-1",
        "runId": "run-1",
        "logicalStepId": "step-1",
        "stepExecutionId": "workflow-1:run-1:step-1:execution:1",
        "attemptOrdinal": 1,
        "boundary": "after_turn",
        "identity": {
            "providerProfileId": "profile-1",
            "credentialGeneration": 3,
            "providerLeaseRef": "lease-provider-1",
            "hostBindingRef": "host-binding-1",
            "hostLeaseRef": "lease-host-1",
            "endpointRef": "endpoint-1",
            "omnigentHostId": "host-1",
            "omnigentSessionId": "session-1",
            "bridgeSessionId": "bridge-1",
            "externalStateRef": "artifact://external",
            "idempotencyKey": "capture-1",
            "terminalRef": "artifact://terminal",
            "diagnosticsRef": "artifact://diagnostics",
            "effectiveLaunchRef": "omnigent-launch:sha256:" + "a" * 64,
        },
        "externalStateDigest": _digest(payloads["artifact://external"]),
        "executionProfileRef": "artifact://execution-profile",
        "launchPolicyRef": "artifact://launch-policy",
        "sourceEffectiveLaunchRef": "omnigent-launch:sha256:" + "a" * 64,
        "lastBridgeEventCursor": "event-8",
        "firstMessageIdentity": "message-1",
        "firstMessageDigest": "sha256:" + "b" * 64,
        "resourceManifestRef": "artifact://resources",
        "captureManifestRef": "artifact://capture",
        "patchCapability": "git_patch_v1",
        "workspaceLocator": {
            "kind": "sandbox",
            "workspaceId": "workspace-1",
            "relativePath": "repo",
        },
        "baselineCommit": "abc123",
        "headCommit": "def456",
        "headRef": "artifact://head",
        "headDigest": _digest(payloads["artifact://head"]),
        "checkpointRef": "artifact://checkpoint",
        "checkpointDigest": _digest(payloads["artifact://checkpoint"]),
        "instructionRefs": ["artifact://instructions"],
        "contextRefs": ["artifact://context"],
        "artifactDigests": {
            ref: _digest(payload) for ref, payload in payloads.items()
        },
        "sourceBranch": "main",
        "outputBranch": "issue-3509",
        "publicationState": "unpublished",
        "capturedAt": datetime.now(UTC),
        "producerVersion": "moonmind-test",
        "validationStatus": "valid",
        "liveReattach": {"available": False, "reasonCode": "host_unavailable"},
        "workspaceColdRestore": {"available": True},
        "branchCreation": {"available": True},
    }
    data.update(updates)
    return OmnigentCheckpointManifest.model_validate(data)


def test_complete_manifest_validates_and_builds_clean_restore() -> None:
    manifest = _manifest()
    artifacts = {
        "artifact://external": b"external",
        "artifact://head": b"head",
        "artifact://checkpoint": b"checkpoint",
        "artifact://execution-profile": b"profile",
        "artifact://launch-policy": b"policy",
        "artifact://resources": b"resources",
        "artifact://capture": b"capture",
        "artifact://instructions": b"instructions",
        "artifact://context": b"context",
        "artifact://terminal": b"terminal",
        "artifact://diagnostics": b"diagnostics",
    }
    result = validate_restore_material(
        manifest,
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="step-1",
        provider_profile_id="profile-1",
        credential_generation=3,
        repository_baseline="abc123",
        step_execution_id="workflow-1:run-1:step-1:execution:1",
        attempt_ordinal=1,
        boundary="after_turn",
        artifact_reader=artifacts.__getitem__,
    )
    assert result.valid
    assert not result.live_reattach.available
    assert result.workspace_cold_restore.available

    restore = build_cold_restore_inputs(
        manifest,
        result,
        destination_workspace_locator={
            "kind": "sandbox",
            "workspaceId": "workspace-2",
            "relativePath": "repo",
        },
        new_effective_launch_ref="omnigent-launch:sha256:" + "c" * 64,
    )
    assert restore["baselineCommit"] == "abc123"
    assert restore["externalStateRef"] == "artifact://external"
    assert restore["credentialGeneration"] == 3

    calls: list[tuple[object, ...]] = []
    launched = materialize_cold_restore(
        manifest,
        result,
        destination_workspace_locator=restore["destinationWorkspaceLocator"],
        new_effective_launch_ref=restore["effectiveLaunchRef"],
        checkout_baseline=lambda locator, baseline: calls.append(
            ("checkout", locator, baseline)
        ),
        apply_workspace_artifact=lambda locator, ref, patch: calls.append(
            ("apply", locator, ref, patch)
        ),
        restore_immutable_refs=lambda locator, instructions, context: calls.append(
            ("refs", locator, list(instructions), list(context))
        ),
        launch_fresh_session=lambda inputs: ("launched", inputs["externalStateRef"]),
    )
    assert calls[0][0] == "checkout"
    assert calls[1][0:2] == ("apply", restore["destinationWorkspaceLocator"])
    assert launched == ("launched", "artifact://external")


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"workflow_id": "wrong"}, "lineage_mismatch"),
        ({"provider_profile_id": "wrong"}, "profile_mismatch"),
        ({"credential_generation": 4}, "credential_generation_mismatch"),
        ({"repository_baseline": "wrong"}, "baseline_mismatch"),
        ({"repository_head": "wrong"}, "head_mismatch"),
        ({"step_execution_id": "wrong"}, "step_execution_mismatch"),
        ({"attempt_ordinal": 2}, "attempt_mismatch"),
        ({"boundary": "terminal"}, "boundary_mismatch"),
    ],
)
def test_restore_validation_fails_closed(kwargs: dict[str, object], reason: str) -> None:
    arguments = {
        "workflow_id": "workflow-1",
        "run_id": "run-1",
        "logical_step_id": "step-1",
        "provider_profile_id": "profile-1",
        "credential_generation": 3,
        "repository_baseline": "abc123",
        "step_execution_id": "workflow-1:run-1:step-1:execution:1",
        "attempt_ordinal": 1,
        "boundary": "after_turn",
        "artifact_reader": {
            "artifact://external": b"external",
            "artifact://head": b"head",
            "artifact://checkpoint": b"checkpoint",
            "artifact://execution-profile": b"profile",
            "artifact://launch-policy": b"policy",
            "artifact://resources": b"resources",
            "artifact://capture": b"capture",
            "artifact://instructions": b"instructions",
            "artifact://context": b"context",
            "artifact://terminal": b"terminal",
            "artifact://diagnostics": b"diagnostics",
        }.__getitem__,
    }
    arguments.update(kwargs)
    result = validate_restore_material(_manifest(), **arguments)
    assert not result.valid
    assert result.reason_code == reason


def test_digest_mismatch_and_unresolvable_artifact_are_bounded() -> None:
    bad = validate_restore_material(
        _manifest(),
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="step-1",
        provider_profile_id="profile-1",
        credential_generation=3,
        step_execution_id="workflow-1:run-1:step-1:execution:1",
        attempt_ordinal=1,
        boundary="after_turn",
        artifact_reader=lambda _ref: b"wrong",
    )
    assert bad.reason_code == "digest_mismatch"

    missing = validate_restore_material(
        _manifest(),
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="step-1",
        provider_profile_id="profile-1",
        credential_generation=3,
        step_execution_id="workflow-1:run-1:step-1:execution:1",
        attempt_ordinal=1,
        boundary="after_turn",
        artifact_reader=lambda _ref: (_ for _ in ()).throw(KeyError()),
    )
    assert missing.reason_code == "artifact_unresolvable"


def test_every_artifact_evidence_class_requires_a_pinned_digest() -> None:
    manifest = _manifest()
    digests = dict(manifest.artifact_digests)
    digests.pop("artifact://execution-profile")
    with pytest.raises(ValidationError, match="artifactDigests"):
        _manifest(artifactDigests=digests)


def test_restore_recomputes_independent_capabilities_and_identity_checks() -> None:
    artifacts = {
        "artifact://external": b"external",
        "artifact://head": b"head",
        "artifact://checkpoint": b"checkpoint",
        "artifact://execution-profile": b"profile",
        "artifact://launch-policy": b"policy",
        "artifact://resources": b"resources",
        "artifact://capture": b"capture",
        "artifact://instructions": b"instructions",
        "artifact://context": b"context",
        "artifact://terminal": b"terminal",
        "artifact://diagnostics": b"diagnostics",
    }
    result = validate_restore_material(
        _manifest(),
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="step-1",
        step_execution_id="workflow-1:run-1:step-1:execution:1",
        attempt_ordinal=1,
        boundary="after_turn",
        expected_first_message_identity="message-1",
        expected_first_message_digest="sha256:" + "b" * 64,
        expected_bridge_event_cursor="event-8",
        provider_profile_id="profile-1",
        credential_generation=3,
        artifact_reader=artifacts.__getitem__,
        current_provider_lease_ref="lease-provider-1",
        current_host_lease_ref="lease-host-1",
        host_registered=True,
        session_valid=True,
        capacity_available=False,
    )
    assert result.valid
    assert result.live_reattach.available
    assert not result.workspace_cold_restore.available
    assert result.workspace_cold_restore.reason_code == "capacity_unavailable"
    assert result.capacity_blocked
    assert set(result.validated_refs) == set(artifacts)


@pytest.mark.parametrize(
    "update",
    [
        {"workspaceLocator": {"kind": "sandbox", "workspacePath": "/tmp/repo"}},
        {"executionProfileRef": "https://provider.example/session/1"},
        {"contextRefs": ["token=secret"]},
    ],
)
def test_manifest_rejects_non_authoritative_or_secret_material(
    update: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _manifest(**update)
