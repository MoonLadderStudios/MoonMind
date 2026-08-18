"""Tests for the compiled Omnigent execution intent (issue #3706).

Covers the strict versioned contract, the admission compiler, the migration
adapter, the unknown-version compatibility policy, cross-surface preservation,
and the specific incident replays the issue calls out (#3684 remediation
annotation loss, repository readiness race, image-policy drift, and continuation
with incomplete capability authority).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.omnigent.execution_intent import (
    ExecutionIntentCompilationError,
    ResolvedExecutionAuthority,
    compile_execution_intent,
    derive_execution_intent_from_request,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.omnigent_execution_intent import (
    EXECUTION_INTENT_CONTRADICTORY_AUTHORITY,
    EXECUTION_INTENT_MAX_PAYLOAD_BYTES,
    EXECUTION_INTENT_PAYLOAD_TOO_LARGE,
    EXECUTION_INTENT_SCHEMA,
    EXECUTION_INTENT_UNSAFE_INPUT,
    AuthorityProvenance,
    CompiledOmnigentExecutionIntent,
    ExecutionIntentSchemaPolicy,
    RemediationCheckpointPolicy,
    RepositoryOperationClass,
    SessionMode,
    classify_execution_intent_schema,
)

_FIXED_CREATED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _resolved(**overrides) -> ResolvedExecutionAuthority:
    base = dict(
        workflowId="wf-1",
        runId="run-1",
        logicalStepId="ls-1",
        stepExecutionId="wf-1:run-1:step:1",
        agentRunId="ar-1",
        canonicalSessionSeed="seed-1",
        taskInputSnapshotRef="artifact://task-input",
        taskInputSnapshotDigest="sha256:taskinput",
        instructionRef="artifact://instruction",
        instructionDigest="sha256:instruction",
        executionProfileRef="profile://omni",
        executionProfileVersion="7",
        agentProfileRef="agent://p",
        agentProfileDigest="sha256:agentprofile",
        providerProfileRef="provider://pp",
        providerProfileId="pp-1",
        credentialGeneration="gen-3",
        providerRuntime="claude-code",
        providerHarness="omnigent-host",
        compatibilityProfile="compat://v1",
        allowedModels=(),
        defaultModel="claude-opus-4-8",
        defaultEffort="high",
        launchPolicyRef="policy://launch",
        launchPolicyDigest="sha256:launchpolicy",
        effectiveLaunchSnapshotRef="artifact://launch-snap",
        effectiveLaunchSnapshotDigest="sha256:launchsnap",
        hostMode="oauth",
        serverImageDigest="sha256:server",
        uiImageDigest="sha256:ui",
        hostImageDigest="sha256:host",
        networkPolicyRef="policy://network",
        egressPolicyRef="policy://egress",
        runtimeCapabilities=("http", "sse", "websocket"),
        compatibilityManifestRef="artifact://compat-manifest",
        buildManifestRef="artifact://build-manifest",
        repositoryProvider="github",
        repository="https://github.com/acme/widgets.git",
        connectionRef="connection://gh",
        baseBranch="main",
        checkoutCommit="abc123",
        workspaceLocator={
            "kind": "sandbox",
            "workspaceId": "ws-1",
            "relativePath": "repo",
        },
        workspaceAuthorityClass="sandbox",
        allowedOperationClasses=(),
        initialTurnAttemptId="ta-1",
        firstMessageMarkerPolicy="require_marker",
        allowedContinuationKinds=("same_session",),
        chatBindingPolicy="bind_on_first_turn",
        terminalEvidenceContract="provider_terminal_snapshot",
        cleanupPolicy="release_after_evidence",
        historicalReadPolicy="allow",
        remediationLoopPermitted=True,
        verifierOwner="verifier://default",
        remediatorOwner="remediator://default",
        checkpointBranchBehavior="branch_per_immutable_change",
        immutableDimensions=("profile", "image", "branch"),
        executionDeadlineSeconds=3600,
        noProgressTimeoutSeconds=600,
        observationCadenceSeconds=10,
        reconcileCadenceSeconds=30,
        retryClasses=("transient", "provider"),
        maxAttempts=3,
        cancellationPolicy="cooperative",
        requiredEvidence=("provider_terminal_snapshot",),
        cleanupLeaseReleaseOrder=("cleanup", "lease_release"),
    )
    base.update(overrides)
    return ResolvedExecutionAuthority(**base)


def _request(*, parameters=None, workspace_spec=None, **overrides) -> AgentExecutionRequest:
    base = dict(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr-1",
        idempotencyKey="idem-1",
    )
    base.update(overrides)
    if parameters is not None:
        base["parameters"] = parameters
    if workspace_spec is not None:
        base["workspaceSpec"] = workspace_spec
    return AgentExecutionRequest(**base)


def _compile(request=None, resolved=None, **resolved_overrides):
    return compile_execution_intent(
        request or _request(),
        resolved=resolved or _resolved(**resolved_overrides),
        created_at=_FIXED_CREATED_AT,
    )


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


def test_happy_path_pins_external_omnigent_identity_and_schema():
    intent = _compile()
    assert intent.schema_id == EXECUTION_INTENT_SCHEMA
    assert intent.runtime.agent_kind == "external"
    assert intent.runtime.agent_id == "omnigent"
    assert intent.intent_digest is not None
    assert intent.intent_digest.startswith("sha256:")


def test_extra_field_is_forbidden():
    with pytest.raises(ValidationError):
        RemediationCheckpointPolicy(
            checkpointBranchBehavior="x",
            somethingUnmodelled="nope",
        )


def test_missing_required_authority_field_is_rejected():
    with pytest.raises(ValidationError):
        # baseBranch is required durable authority (default-branch readiness).
        _resolved(baseBranch=None)


def test_conflicting_publish_and_no_commit_is_rejected():
    request = _request(parameters={"publishMode": "pr", "noCommit": True})
    with pytest.raises(ExecutionIntentCompilationError) as exc:
        _compile(request, resolved=_resolved(allowedOperationClasses=("pull_request",)))
    assert exc.value.code == EXECUTION_INTENT_CONTRADICTORY_AUTHORITY


def test_authorized_reuse_requires_continuation_kinds():
    request = _request(parameters={"sessionMode": "authorized_reuse"})
    with pytest.raises(ExecutionIntentCompilationError):
        _compile(request, resolved=_resolved(allowedContinuationKinds=()))


def test_digest_is_stable_for_semantically_equal_intent():
    request = _request(parameters={"model": "claude-opus-4-8"})
    a = compile_execution_intent(
        request, resolved=_resolved(), created_at=_FIXED_CREATED_AT
    )
    b = compile_execution_intent(
        request, resolved=_resolved(), created_at=datetime(2000, 1, 1, tzinfo=UTC)
    )
    # createdAt differs but governing authority is identical.
    assert a.intent_digest == b.intent_digest


def test_editable_dimension_changes_digest():
    base = _compile(_request(parameters={"model": "claude-opus-4-8"}))
    changed = _compile(_request(parameters={"model": "claude-sonnet-5"}))
    assert base.intent_digest != changed.intent_digest


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        (EXECUTION_INTENT_SCHEMA, ExecutionIntentSchemaPolicy.PARSE),
        (
            "moonmind.omnigent.compiled-execution-intent/v0",
            ExecutionIntentSchemaPolicy.HISTORICAL_READ_ONLY,
        ),
        (
            "moonmind.omnigent.compiled-execution-intent/v2",
            ExecutionIntentSchemaPolicy.FAIL,
        ),
        (
            "moonmind.omnigent.compiled-execution-intent/vX",
            ExecutionIntentSchemaPolicy.FAIL,
        ),
        ("some.other.contract/v1", ExecutionIntentSchemaPolicy.FAIL),
        ("", ExecutionIntentSchemaPolicy.FAIL),
    ],
)
def test_unknown_schema_versions_follow_explicit_policy(schema, expected):
    assert classify_execution_intent_schema(schema) is expected


def test_unknown_top_level_schema_string_is_rejected():
    dumped = _compile().model_dump(by_alias=True, mode="json")
    dumped["schema"] = "moonmind.omnigent.compiled-execution-intent/v2"
    with pytest.raises(ValidationError):
        CompiledOmnigentExecutionIntent.model_validate(dumped)


def test_secret_shaped_value_is_rejected():
    with pytest.raises(ExecutionIntentCompilationError) as exc:
        _compile(resolved=_resolved(chatBindingPolicy="Bearer sk-live-abc"))
    assert exc.value.code == EXECUTION_INTENT_UNSAFE_INPUT


def test_host_socket_authority_value_is_rejected():
    with pytest.raises(ExecutionIntentCompilationError) as exc:
        _compile(resolved=_resolved(hostMode="unix:///var/run/docker.sock"))
    assert exc.value.code == EXECUTION_INTENT_UNSAFE_INPUT


def test_payload_bounds_reject_oversized_authority():
    # A single ref that pushes serialized authority past the bound is rejected;
    # large content belongs in an artifact referenced by a compact ref.
    oversized = "artifact://" + "x" * EXECUTION_INTENT_MAX_PAYLOAD_BYTES
    with pytest.raises(ExecutionIntentCompilationError) as exc:
        _compile(resolved=_resolved(effectiveLaunchSnapshotRef=oversized))
    assert exc.value.code == EXECUTION_INTENT_PAYLOAD_TOO_LARGE


# ---------------------------------------------------------------------------
# Compiler authority-boundary tests
# ---------------------------------------------------------------------------


def test_non_omnigent_runtime_is_rejected():
    request = _request(agentKind="managed", agentId="codex")
    with pytest.raises(ExecutionIntentCompilationError):
        _compile(request)


def test_model_not_eligible_for_profile_is_contradictory():
    request = _request(parameters={"model": "claude-sonnet-5"})
    with pytest.raises(ExecutionIntentCompilationError) as exc:
        _compile(request, resolved=_resolved(allowedModels=("claude-opus-4-8",)))
    assert exc.value.code == EXECUTION_INTENT_CONTRADICTORY_AUTHORITY


def test_operation_class_not_permitted_is_contradictory():
    request = _request(parameters={"publishMode": "pr"})
    with pytest.raises(ExecutionIntentCompilationError):
        _compile(request, resolved=_resolved(allowedOperationClasses=("read_only",)))


def test_authored_runtime_owned_key_is_rejected():
    # Image-policy / running-deployment drift: an author must not pin the image
    # digest; the compiler resolves it from immutable policy authority.
    request = _request(workspace_spec={"serverImageDigest": "sha256:attacker"})
    with pytest.raises(ExecutionIntentCompilationError) as exc:
        _compile(request)
    assert exc.value.code == EXECUTION_INTENT_UNSAFE_INPUT


def test_authored_resolved_repository_target_is_rejected():
    request = _request(workspace_spec={"resolvedRepositoryTarget": {"branch": "x"}})
    with pytest.raises(ExecutionIntentCompilationError) as exc:
        _compile(request)
    assert exc.value.code == EXECUTION_INTENT_UNSAFE_INPUT


def test_identity_mismatch_between_launch_and_resolved_is_contradictory():
    # Internally consistent launch envelope, but its identity disagrees with the
    # resolved authority (workflow wf-2 vs wf-1).
    launch = {
        "schemaVersion": "v1",
        "workflowId": "wf-2",
        "runId": "run-1",
        "logicalStepId": "ls-1",
        "executionOrdinal": 1,
        "stepExecutionId": "wf-2:run-1:ls-1:execution:1",
        "runtimeContextPolicy": "fresh_agent_run",
    }
    request = _request(stepExecution=launch)
    with pytest.raises(ExecutionIntentCompilationError) as exc:
        _compile(request)
    assert exc.value.code == EXECUTION_INTENT_CONTRADICTORY_AUTHORITY


# ---------------------------------------------------------------------------
# Cross-surface preservation
# ---------------------------------------------------------------------------


def test_preset_expansion_preserves_remediation_intent():
    # Typed and annotation-carried remediation intent compile to the same typed
    # authority, so a preset expansion path and a hand-authored path agree.
    typed = _request(
        parameters={
            "remediationLoop": {
                "enabled": True,
                "verifierOwner": "v",
                "remediatorOwner": "m",
                "maxAttempts": 2,
            }
        }
    )
    annotated = _request(
        parameters={
            "annotations": {
                "remediationLoop": {
                    "enabled": True,
                    "verifierOwner": "v",
                    "remediatorOwner": "m",
                    "maxAttempts": 2,
                }
            }
        }
    )
    assert _compile(typed).intent_digest == _compile(annotated).intent_digest
    assert _compile(typed).remediation.remediation_loop_enabled is True


def test_non_authority_parameters_do_not_change_intent_digest():
    # An oversized artifact-backed submission carries the same user-selectable
    # intent plus non-authority payload; the compiled intent (refs/digests only)
    # is identical.
    lean = _request(parameters={"model": "claude-opus-4-8"})
    padded = _request(
        parameters={
            "model": "claude-opus-4-8",
            "uiHint": "x" * 5000,
            "telemetry": {"clientVersion": "1.2.3"},
        }
    )
    assert _compile(lean).intent_digest == _compile(padded).intent_digest


def test_default_branch_authority_cannot_be_raced_by_submission():
    # The base branch is resolved durable authority; no authored field overrides
    # it, so planning and submission cannot disagree about repository authority.
    request = _request(
        workspace_spec={"baseBranch": "attacker-branch", "targetBranch": "feature/x"}
    )
    intent = _compile(request, resolved=_resolved(baseBranch="main"))
    assert intent.repository.base_branch == "main"
    assert intent.repository.target_branch == "feature/x"


def test_capability_and_chat_bootstrap_consume_same_authority():
    intent = _compile()
    view = intent.compact_runtime_view()
    evidence = intent.evidence()
    # Both derived views agree on the one admitted digest and never re-resolve.
    assert view["intentDigest"] == intent.intent_digest == evidence["intentDigest"]
    assert evidence["runtimeCapabilities"] == list(intent.launch.runtime_capabilities)
    assert view["providerProfileId"] == intent.runtime.provider_profile_id


def test_activity_retry_does_not_reresolve_authority():
    request = _request(parameters={"model": "claude-opus-4-8"})
    resolved = _resolved()
    first = compile_execution_intent(
        request, resolved=resolved, created_at=_FIXED_CREATED_AT
    )
    # A retry recompiles from the same admitted authority and gets the same
    # digest — no different profile/image/branch/policy sneaks in.
    retry = compile_execution_intent(
        request, resolved=resolved, created_at=datetime(2030, 5, 5, tzinfo=UTC)
    )
    assert first.intent_digest == retry.intent_digest


# ---------------------------------------------------------------------------
# Incident replays
# ---------------------------------------------------------------------------


def test_incident_3684_remediation_survives_annotation_stripping():
    # The controller intent is pinned in typed authority. Even if a later
    # transform drops the free-form annotations, the compiled intent still
    # carries the remediation loop.
    request = _request(
        parameters={
            "annotations": {
                "remediationLoop": {
                    "enabled": True,
                    "verifierOwner": "v",
                    "remediatorOwner": "m",
                }
            }
        }
    )
    intent = _compile(request)
    assert intent.remediation.remediation_loop_enabled is True

    dumped = intent.model_dump(by_alias=True, mode="json")
    # No free-form annotations map exists to strip; ownership is typed.
    assert "annotations" not in dumped
    rehydrated = CompiledOmnigentExecutionIntent.model_validate(dumped)
    assert rehydrated.remediation.remediation_loop_enabled is True
    assert rehydrated.intent_digest == intent.intent_digest


def test_incident_repository_target_omitted_blocks_admission():
    # A readiness race that leaves the repository target unresolved must fail
    # closed before an execution is created.
    with pytest.raises(ValidationError):
        _resolved(baseBranch=None)


def test_incident_image_policy_drift_cannot_be_authored():
    request = _request(parameters={"hostImageDigest": "sha256:stale-running"})
    with pytest.raises(ExecutionIntentCompilationError) as exc:
        _compile(request)
    assert exc.value.code == EXECUTION_INTENT_UNSAFE_INPUT


def test_incident_continuation_with_incomplete_capability_authority():
    request = _request(parameters={"sessionMode": "authorized_reuse"})
    with pytest.raises(ExecutionIntentCompilationError):
        _compile(request, resolved=_resolved(allowedContinuationKinds=()))


# ---------------------------------------------------------------------------
# Digest correlation / normal create journey
# ---------------------------------------------------------------------------


def test_create_journey_is_digest_correlated_end_to_end():
    resolved = _resolved()
    intent = compile_execution_intent(
        _request(parameters={"model": "claude-opus-4-8"}),
        resolved=resolved,
        created_at=_FIXED_CREATED_AT,
    )
    # Authored snapshot -> compiled intent -> runtime consumption all share one
    # digest, and the task-input snapshot digest is bound into identity.
    assert intent.compute_digest() == intent.intent_digest
    assert (
        intent.identity.task_input_snapshot_digest
        == resolved.task_input_snapshot_digest
    )
    persisted = intent.model_dump(by_alias=True, mode="json")
    consumer = CompiledOmnigentExecutionIntent.model_validate(persisted)
    assert consumer.intent_digest == intent.intent_digest
    assert consumer.compute_digest() == intent.intent_digest


def test_persisted_digest_mismatch_is_rejected():
    persisted = _compile().model_dump(by_alias=True, mode="json")
    persisted["intentDigest"] = "sha256:tampered"
    with pytest.raises(ValidationError):
        CompiledOmnigentExecutionIntent.model_validate(persisted)


# ---------------------------------------------------------------------------
# Migration adapter
# ---------------------------------------------------------------------------


def test_adapter_requires_resolved_durable_authority():
    with pytest.raises(ExecutionIntentCompilationError):
        derive_execution_intent_from_request(_request(), resolved=None)


def test_adapter_records_legacy_provenance_without_changing_digest():
    request = _request(parameters={"model": "claude-opus-4-8"})
    resolved = _resolved()
    full = compile_execution_intent(
        request, resolved=resolved, created_at=_FIXED_CREATED_AT
    )
    derived = derive_execution_intent_from_request(
        request,
        resolved=resolved,
        legacy_sections=frozenset({"session", "remediation"}),
        created_at=_FIXED_CREATED_AT,
    )
    assert derived.provenance.claims_full_authority is False
    assert derived.provenance.session is AuthorityProvenance.LEGACY_DERIVED
    assert derived.provenance.remediation is AuthorityProvenance.LEGACY_DERIVED
    assert derived.provenance.runtime is AuthorityProvenance.DURABLE
    # Provenance is derivation metadata, excluded from the digest.
    assert derived.intent_digest == full.intent_digest


def test_adapter_without_legacy_sections_claims_full_authority():
    derived = derive_execution_intent_from_request(
        _request(), resolved=_resolved(), created_at=_FIXED_CREATED_AT
    )
    assert derived.provenance.claims_full_authority is True


def test_read_only_default_operation_class():
    intent = _compile(_request())
    assert intent.repository.operation_class is RepositoryOperationClass.READ_ONLY
    assert intent.session.session_mode is SessionMode.FRESH
