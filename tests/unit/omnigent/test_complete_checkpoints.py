from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from api_service.api.routers.executions import _checkpoint_recovery_projection
from moonmind.omnigent.checkpoints import (
    OmnigentCheckpointManifest,
    assemble_checkpoint_manifest,
    build_cold_restore_inputs,
    materialize_cold_restore,
    validate_restore_material,
)
from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
)
from moonmind.schemas.agent_runtime_models import AgentRunResult


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


def _capture() -> dict[str, object]:
    capture = _manifest().model_dump(by_alias=True, mode="json")
    for key in (
        "workflowId",
        "runId",
        "logicalStepId",
        "stepExecutionId",
        "attemptOrdinal",
        "boundary",
        "capturedAt",
        "artifactDigests",
        "externalStateDigest",
        "headDigest",
        "checkpointDigest",
        "diffDigest",
    ):
        capture.pop(key, None)
    return capture


def test_capture_assembler_derives_lineage_boundary_and_digests() -> None:
    artifacts = {
        ref: payload
        for ref, payload in {
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
        }.items()
    }
    manifest = assemble_checkpoint_manifest(
        _capture(),
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="step-1",
        step_execution_id="workflow-1:run-1:step-1:execution:1",
        attempt_ordinal=1,
        boundary="after_turn",
        captured_at=datetime.now(UTC),
        artifact_reader=artifacts.__getitem__,
    )
    assert manifest.schema_version == "v2"
    assert manifest.external_state_digest == _digest(b"external")
    assert manifest.checkpoint_digest == _digest(b"checkpoint")
    assert set(manifest.artifact_digests) == set(artifacts)


def test_api_projection_fails_closed_without_current_restore_validation() -> None:
    projection = _checkpoint_recovery_projection(
        {
            "omnigentCheckpoint": _manifest().model_dump(
                by_alias=True, mode="json"
            )
        }
    )

    assert projection is not None
    assert projection["valid"] is False
    assert projection["reasonCode"] == "current_validation_unavailable"
    assert projection["validatedRefs"] == []
    assert projection["liveReattach"] == {
        "available": False,
        "reasonCode": "current_validation_unavailable",
        "message": "current restore authority has not been validated",
    }


def test_api_projection_prefers_current_restore_validation() -> None:
    current = validate_restore_material(
        _manifest(),
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="step-1",
        provider_profile_id="profile-1",
        credential_generation=3,
        repository_baseline="abc123",
        step_execution_id="workflow-1:run-1:step-1:execution:1",
        attempt_ordinal=1,
        boundary="after_turn",
        artifact_reader=lambda ref: {
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
        }[ref],
    )
    projection = _checkpoint_recovery_projection(
        {
            "omnigentCheckpoint": _manifest().model_dump(
                by_alias=True, mode="json"
            ),
            "omnigentRestoreValidation": current.model_dump(
                by_alias=True, mode="json"
            ),
        }
    )

    assert projection is not None
    assert projection["valid"] is True
    assert projection["validatedRefs"]


def test_current_restore_validation_is_persisted_in_runtime_result() -> None:
    validation = validate_restore_material(
        _manifest(),
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="step-1",
        provider_profile_id="profile-1",
        credential_generation=3,
        repository_baseline="abc123",
        step_execution_id="workflow-1:run-1:step-1:execution:1",
        attempt_ordinal=1,
        boundary="after_turn",
        artifact_reader=lambda ref: {
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
        }[ref],
    )
    result = OmnigentProfileBoundExecutionCoordinator._with_restore_validation(
        AgentRunResult(summary="restored", metadata={"providerName": "omnigent"}),
        validation,
    )

    assert result.metadata["providerName"] == "omnigent"
    assert result.metadata["omnigentRestoreValidation"]["valid"] is True


@pytest.mark.parametrize("schema", ["v1", "v3"])
def test_capture_assembler_rejects_old_or_unknown_schema(schema: str) -> None:
    capture = _capture()
    capture["schemaVersion"] = schema
    with pytest.raises(ValueError, match="unsupported"):
        assemble_checkpoint_manifest(
            capture,
            workflow_id="workflow-1",
            run_id="run-1",
            logical_step_id="step-1",
            step_execution_id="workflow-1:run-1:step-1:execution:1",
            attempt_ordinal=1,
            boundary="after_turn",
            captured_at=datetime.now(UTC),
            artifact_reader=lambda _ref: b"",
        )


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
    ("kwargs", "reason"),
    [
        ({"current_host_lease_ref": "wrong"}, "host_lease_mismatch"),
        ({"expected_first_message_identity": "wrong"}, "first_message_mismatch"),
        ({"expected_first_message_digest": "sha256:" + "c" * 64}, "first_message_mismatch"),
        ({"expected_bridge_event_cursor": "wrong"}, "event_cursor_mismatch"),
        ({"supported_patch_capabilities": ("archive_v1",)}, "unsupported_patch"),
    ],
)
def test_restore_rejects_stale_session_and_patch_evidence(
    kwargs: dict[str, object], reason: str
) -> None:
    artifacts = {
        ref: payload
        for ref, payload in {
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
        }.items()
    }
    arguments: dict[str, object] = {
        "workflow_id": "workflow-1",
        "run_id": "run-1",
        "logical_step_id": "step-1",
        "step_execution_id": "workflow-1:run-1:step-1:execution:1",
        "attempt_ordinal": 1,
        "boundary": "after_turn",
        "expected_first_message_identity": "message-1",
        "expected_first_message_digest": "sha256:" + "b" * 64,
        "expected_bridge_event_cursor": "event-8",
        "provider_profile_id": "profile-1",
        "credential_generation": 3,
        "artifact_reader": artifacts.__getitem__,
        "current_provider_lease_ref": "lease-provider-1",
        "current_host_lease_ref": "lease-host-1",
        "host_registered": True,
        "session_valid": True,
    }
    arguments.update(kwargs)
    result = validate_restore_material(_manifest(), **arguments)
    if reason == "host_lease_mismatch":
        assert result.valid
        assert result.live_reattach.reason_code == reason
    else:
        assert not result.valid
        assert result.reason_code == reason


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
