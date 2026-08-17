"""Contract tests for the compiled Omnigent execution intent (issue #3706)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.schemas.omnigent_execution_intent import (
    EXECUTION_INTENT_PRODUCER_VERSION,
    EXECUTION_INTENT_SCHEMA_ID,
    EXECUTION_INTENT_SCHEMA_VERSION,
    CompiledOmnigentExecutionIntent,
    ExecutionIdentitySection,
    LaunchAuthoritySection,
    RemediationAuthoritySection,
    RuntimeSelectionSection,
    SessionContinuationSection,
    TimingFailureSection,
    WorkspaceAuthoritySection,
    resolve_execution_intent_compatibility,
)


def _intent(**overrides) -> CompiledOmnigentExecutionIntent:
    payload = dict(
        createdAt=datetime(2026, 1, 1, tzinfo=UTC),
        identity=ExecutionIdentitySection(
            workflowId="wf-1",
            stepExecutionId="wf-1:se:1",
            sessionSeed="seed-1",
            sourceKind="create",
        ),
        runtime=RuntimeSelectionSection(
            executionProfileRef="omnigent-codex@1",
            providerProfileId="pp-1",
            providerRuntime="codex_cli",
            harness="codex-native",
            model="gpt-5",
            effort="high",
        ),
        launch=LaunchAuthoritySection(
            launchPolicyRef="codex-on-demand@1",
            effectiveLaunchRef="omnigent-launch:sha256:deadbeef",
            effectiveLaunchDigest="omnigent-launch:sha256:deadbeef",
            hostMode="on_demand_docker",
            runtimeCapabilityRequirements=["gh", "git"],
        ),
        workspace=WorkspaceAuthoritySection(
            workspaceIntentDigest="sha256:ws",
            repository="https://github.com/acme/widgets.git",
            repositoryKind="github_https",
            operationClass="pull_request",
            workspaceLocatorKind="sandbox",
            workspaceAuthorityClass="mutation",
            publishMode="pr",
        ),
        session=SessionContinuationSection(
            sessionMode="fresh",
            initialTurnAttemptId="idem-1:turn:1",
            cleanupPolicy="remove",
        ),
        timing=TimingFailureSection(maxAttempts=1),
    )
    payload.update(overrides)
    return CompiledOmnigentExecutionIntent(**payload)


def test_stamps_schema_id_version_and_deterministic_digest() -> None:
    intent = _intent()
    assert intent.schema_id == EXECUTION_INTENT_SCHEMA_ID
    assert intent.schema_version == EXECUTION_INTENT_SCHEMA_VERSION
    assert intent.producer_version == EXECUTION_INTENT_PRODUCER_VERSION
    assert intent.intent_digest is not None
    assert intent.intent_digest.startswith("sha256:")
    assert intent.intent_digest == intent.compute_digest()


def test_digest_excludes_created_at_so_retries_reproduce_intent() -> None:
    first = _intent(createdAt=datetime(2026, 1, 1, tzinfo=UTC))
    later = _intent(createdAt=datetime(2031, 7, 30, 12, 0, tzinfo=UTC))
    assert first.intent_digest == later.intent_digest


def test_image_drift_changes_the_intent_digest() -> None:
    base = _intent()
    drifted = _intent(
        launch=LaunchAuthoritySection(
            launchPolicyRef="codex-on-demand@1",
            effectiveLaunchRef="omnigent-launch:sha256:cafebabe",
            effectiveLaunchDigest="omnigent-launch:sha256:cafebabe",
            hostMode="on_demand_docker",
            runtimeCapabilityRequirements=["gh", "git"],
        )
    )
    # A launch/image drift is not silently absorbed: the immutable digest changes.
    assert base.intent_digest != drifted.intent_digest


def test_capability_requirements_are_normalized_and_deduped() -> None:
    intent = _intent(
        launch=LaunchAuthoritySection(
            launchPolicyRef="codex-on-demand@1",
            effectiveLaunchRef="omnigent-launch:sha256:deadbeef",
            effectiveLaunchDigest="omnigent-launch:sha256:deadbeef",
            hostMode="on_demand_docker",
            runtimeCapabilityRequirements=["GH", "gh", " Git "],
        )
    )
    assert intent.launch.runtime_capability_requirements == ("gh", "git")


def test_intent_is_frozen_so_admitted_authority_cannot_drift() -> None:
    intent = _intent()
    with pytest.raises(ValidationError):
        intent.full_authority_proven = False
    with pytest.raises(ValidationError):
        intent.runtime.model = "downgraded"
    assert isinstance(intent.launch.runtime_capability_requirements, tuple)


def test_tampered_digest_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _intent(intentDigest="sha256:deadbeef")


def test_credential_shaped_value_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _intent(
            identity=ExecutionIdentitySection(
                workflowId="wf-1",
                stepExecutionId="wf-1:se:1",
                sessionSeed="seed-1",
                sourceKind="create",
                instructionRef="token=ghp_supersecretvalue",
            )
        )


def test_docker_authority_value_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _intent(
            workspace=WorkspaceAuthoritySection(
                workspaceIntentDigest="sha256:ws",
                repository="unix:///var/run/docker.sock",
                operationClass="read_only",
                workspaceLocatorKind="sandbox",
                workspaceAuthorityClass="read_only",
            )
        )


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _intent(unexpectedField="nope")


def test_evidence_redacts_local_source_and_exposes_only_refs() -> None:
    intent = _intent(
        workspace=WorkspaceAuthoritySection(
            workspaceIntentDigest="sha256:ws",
            repository="/work/agent_jobs/local/repo",
            repositoryKind="local",
            operationClass="read_only",
            workspaceLocatorKind="sandbox",
            workspaceAuthorityClass="read_only",
        )
    )
    evidence = intent.evidence()
    assert evidence["repository"] == "[local-source]"
    assert evidence["schemaId"] == EXECUTION_INTENT_SCHEMA_ID
    assert evidence["intentDigest"] == intent.intent_digest
    assert evidence["effectiveLaunchRef"] == "omnigent-launch:sha256:deadbeef"


def test_typed_remediation_controller_is_carried_and_digest_governed() -> None:
    remediation = RemediationAuthoritySection(
        loopId="loop-1",
        controllerDigest="sha256:controller",
        verifierOwner="verifier",
        remediatorOwner="remediator",
        hardMaxAttempts=5,
        restoreMode="cold_restore",
    )
    with_loop = _intent(remediation=remediation)
    without_loop = _intent()
    assert with_loop.remediation is not None
    assert with_loop.remediation.loop_id == "loop-1"
    # The typed controller is part of the governed digest, so a run that drops it
    # is not digest-equivalent to one that carries it.
    assert with_loop.intent_digest != without_loop.intent_digest
    assert with_loop.evidence()["remediationLoopId"] == "loop-1"


def test_compatibility_policy_admits_v1_and_fails_closed_on_unknown() -> None:
    document = _intent().model_dump(by_alias=True, mode="json")
    admit = resolve_execution_intent_compatibility(document, purpose="admission")
    assert admit.disposition == "admit"

    read = resolve_execution_intent_compatibility(
        document, purpose="historical_read"
    )
    assert read.disposition == "historical_read"

    unknown = {"schemaId": "moonmind.omnigent.compiled-execution-intent/v2"}
    assert (
        resolve_execution_intent_compatibility(
            unknown, purpose="admission"
        ).disposition
        == "reject"
    )
    # The same unknown version degrades to a bounded historical read, never a
    # fresh admission.
    assert (
        resolve_execution_intent_compatibility(
            unknown, purpose="historical_read"
        ).disposition
        == "historical_read"
    )
    # A structurally invalid document always fails closed.
    assert (
        resolve_execution_intent_compatibility(
            {"noSchema": True}, purpose="historical_read"
        ).disposition
        == "reject"
    )
