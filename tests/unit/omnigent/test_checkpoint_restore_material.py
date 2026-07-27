from __future__ import annotations

from datetime import UTC, datetime

import pytest

from moonmind.omnigent.checkpoints import (
    OMNIGENT_CHECKPOINT_CONTENT_TYPE,
    OmnigentCheckpointManifest,
    RecoveryCapability,
    artifact_digest,
    materialize_cold_restore_inputs,
    recovery_capability_projection,
    validate_restore_material,
)
from moonmind.schemas.temporal_models import StepExecutionIdentityModel
from moonmind.workflows.temporal.step_checkpoints import build_step_checkpoint_payload


def _manifest(
    external: bytes = b"session",
    workspace: bytes = b"workspace",
) -> OmnigentCheckpointManifest:
    return OmnigentCheckpointManifest.model_validate(
        {
            "schemaVersion": "v1",
            "contentType": OMNIGENT_CHECKPOINT_CONTENT_TYPE,
            "workflowId": "wf",
            "runId": "run",
            "logicalStepId": "step",
            "stepExecutionId": "wf:run:step:execution:1",
            "attemptOrdinal": 1,
            "boundary": "after_execution",
            "session": {
                "externalStateRef": "artifact://session",
                "externalStateDigest": artifact_digest(external),
                "bridgeSessionId": "bridge",
                "omnigentSessionId": "session-id",
                "omnigentHostId": "host-id",
                "idempotencyKey": "idem",
                "lastCommittedEventCursor": "42",
                "firstMessageDigest": artifact_digest(b"first"),
                "terminalRef": "artifact://terminal",
                "terminalDigest": artifact_digest(b"terminal"),
                "diagnosticsRef": "artifact://diagnostics",
                "diagnosticsDigest": artifact_digest(b"diagnostics"),
                "resourceManifestRef": "artifact://resources",
                "resourceManifestDigest": artifact_digest(b"resources"),
                "captureManifestRef": "artifact://capture",
                "captureManifestDigest": artifact_digest(b"capture"),
            },
            "workspace": {
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": "replacement",
                    "relativePath": "repo",
                },
                "baselineCommit": "a" * 40,
                "checkpointRef": "artifact://workspace",
                "checkpointDigest": artifact_digest(workspace),
                "patchCapability": "git_patch_v1",
                "instructionRefs": ["artifact://instructions"],
                "contextRefs": ["artifact://context"],
                "sourceBranch": "main",
                "outputBranch": "feature/checkpoint",
                "publicationState": "unpublished",
            },
            "host": {
                "executionProfile": "omnigent",
                "launchPolicyRef": "artifact://launch-policy",
                "launchPolicyDigest": artifact_digest(b"launch-policy"),
                "effectiveLaunchRef": "omnigent-launch:sha256:" + "1" * 64,
                "providerProfileId": "profile",
                "providerProfileRef": "artifact://profile",
                "providerProfileDigest": artifact_digest(b"profile"),
                "providerLeaseRef": "artifact://provider-lease",
                "providerLeaseDigest": artifact_digest(b"provider-lease"),
                "hostBindingRef": "artifact://binding",
                "hostBindingDigest": artifact_digest(b"binding"),
                "hostLeaseRef": "artifact://host-lease",
                "hostLeaseDigest": artifact_digest(b"host-lease"),
                "endpointRef": "artifact://endpoint",
                "endpointDigest": artifact_digest(b"endpoint"),
            },
            "credentials": {"credentialGeneration": 3},
            "captureTime": datetime.now(UTC),
            "producerVersion": "test",
            "validation": {
                "status": "valid",
                "liveReattach": {"available": True},
                "workspaceColdRestore": {"available": True},
                "branchCreation": {"available": True},
            },
        }
    )


def _artifacts(**overrides: bytes) -> dict[str, bytes]:
    values = {
        "session": b"session",
        "workspace": b"workspace",
        "terminal": b"terminal",
        "diagnostics": b"diagnostics",
        "resources": b"resources",
        "capture": b"capture",
        "launch-policy": b"launch-policy",
        "profile": b"profile",
        "provider-lease": b"provider-lease",
        "binding": b"binding",
        "host-lease": b"host-lease",
        "endpoint": b"endpoint",
    }
    values.update(overrides)
    return {f"artifact://{name}": payload for name, payload in values.items()}


def test_restore_validation_checks_artifacts_and_projects_modes_independently() -> None:
    manifest = _manifest()
    result = validate_restore_material(
        manifest,
        workflow_id="wf",
        run_id="run",
        logical_step_id="step",
        provider_profile_id="profile",
        credential_generation=3,
        artifacts=_artifacts(),
        host_available=False,
        session_valid=False,
    )

    assert result.status == "degraded"
    assert result.live_reattach == RecoveryCapability(
        available=False, reason="host_unavailable"
    )
    assert result.workspace_cold_restore.available is True
    assert result.branch_creation.available is True


def test_canonical_step_checkpoint_writer_embeds_complete_manifest() -> None:
    payload = build_step_checkpoint_payload(
        identity=StepExecutionIdentityModel(
            workflowId="wf",
            runId="run",
            logicalStepId="step",
            executionOrdinal=1,
        ),
        boundary="after_execution",
        task_input_snapshot_ref="artifact://input",
        workspace={
            "kind": "external_state_ref",
            "externalStateRef": "artifact://session",
        },
        created_at=datetime.now(UTC),
        plan_ref="artifact://plan",
        omnigent_manifest=_manifest().model_dump(by_alias=True, mode="json"),
    )

    assert payload["omnigentManifest"]["contentType"] == (
        OMNIGENT_CHECKPOINT_CONTENT_TYPE
    )
    assert payload["omnigentManifest"]["attemptOrdinal"] == 1


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"run_id": "other"}, "lineage_mismatch"),
        ({"provider_profile_id": "other"}, "provider_profile_mismatch"),
        ({"credential_generation": 4}, "credential_generation_stale"),
    ],
)
def test_restore_validation_fails_closed(overrides: dict[str, object], reason: str) -> None:
    arguments: dict[str, object] = {
        "workflow_id": "wf",
        "run_id": "run",
        "logical_step_id": "step",
        "provider_profile_id": "profile",
        "credential_generation": 3,
        "artifacts": _artifacts(),
    }
    arguments.update(overrides)
    result = validate_restore_material(_manifest(), **arguments)  # type: ignore[arg-type]

    assert result.workspace_cold_restore.available is False
    assert result.workspace_cold_restore.reason == reason
    assert result.branch_creation.available is False


def test_digest_mismatch_and_unresolved_refs_are_not_resumable() -> None:
    result = validate_restore_material(
        _manifest(),
        workflow_id="wf",
        run_id="run",
        logical_step_id="step",
        provider_profile_id="profile",
        credential_generation=3,
        artifacts=_artifacts(session=b"wrong"),
    )

    assert result.status == "invalid"
    assert result.workspace_cold_restore.reason == "artifact_digest_mismatch"


def test_cold_restore_inputs_are_path_free_and_pin_source_policy() -> None:
    manifest = _manifest()
    validation = validate_restore_material(
        manifest,
        workflow_id="wf",
        run_id="run",
        logical_step_id="step",
        provider_profile_id="profile",
        credential_generation=3,
        artifacts=_artifacts(),
    )
    payload = materialize_cold_restore_inputs(
        manifest,
        validation,
        replacement_workspace_locator={
            "kind": "managed_runtime",
            "runtimeId": "omnigent",
            "agentRunId": "replacement",
            "relativePath": ".",
        },
        effective_launch_ref="omnigent-launch:sha256:" + "2" * 64,
    )

    assert payload["baselineCommit"] == "a" * 40
    assert payload["externalStateRef"] == "artifact://session"
    assert payload["sourceLaunchPolicyRef"] == "artifact://launch-policy"
    assert "workspacePath" not in payload

    projection = recovery_capability_projection(
        manifest,
        validation,
        capacity_ready=False,
        readiness_reason="profile_capacity_exhausted",
    )
    assert projection["liveSessionReattach"]["reason"] == "host_unavailable"
    assert projection["workspaceColdRestore"]["available"] is True
    assert projection["branchCreation"]["available"] is True
    assert projection["capacityBlockingReason"] == "profile_capacity_exhausted"
    assert projection["artifactEvidence"]["workspaceCheckpointDigest"] == artifact_digest(
        b"workspace"
    )


@pytest.mark.parametrize(
    "unsafe",
    [
        {"oauthHome": "/root/.codex"},
        {"credentialBody": "secret"},
        {"workspacePath": "/tmp/repo"},
        {"endpoint": "https://provider.example/session/native-id"},
    ],
)
def test_manifest_rejects_host_local_or_credential_authority(
    unsafe: dict[str, str],
) -> None:
    payload = _manifest().model_dump(by_alias=True, mode="json")
    payload["credentials"].update(unsafe)

    with pytest.raises(
        ValueError,
        match="not durable checkpoint authority|provider-native or host-local authority",
    ):
        OmnigentCheckpointManifest.model_validate(payload)


def test_valid_manifest_requires_complete_host_independent_evidence() -> None:
    payload = _manifest().model_dump(by_alias=True, mode="json")
    del payload["host"]["providerLeaseRef"]

    with pytest.raises(ValueError, match="providerLeaseRef"):
        OmnigentCheckpointManifest.model_validate(payload)
