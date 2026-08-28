"""Regression coverage for MoonLadderStudios/MoonMind#3705."""

from __future__ import annotations

import inspect
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pydantic import ValidationError

from moonmind.config.settings import TemporalSettings
from moonmind.omnigent.reconciler import (
    CompiledSessionIntent,
    DecisionKind,
    DurableSessionState,
    ObservationSet,
    ProviderSessionObservation,
    ProviderStatusClass,
    SubmissionState,
    TerminalOutcome,
    classify_provider_status,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
    OmnigentExecutionPlanBinding,
)
from moonmind.omnigent.harness_platform.execution_plan import AdmissionAuthority
from moonmind.omnigent.session_supervisor_rollback import (
    SUPERVISOR_ROLLBACK_POLICY_VERSION,
)
from moonmind.schemas.omnigent_session_models import (
    OMNIGENT_SESSION_FEATURE_GENERATION,
    OmnigentResolveIntentRequest,
    OmnigentPersistFailureRequest,
    OmnigentFailureAuthorityRequest,
    OmnigentSessionAdmissionDecision,
    OmnigentSessionAdmissionRequest,
    OmnigentSessionActivityRequest,
    OmnigentSessionContinueAsNewState,
    OmnigentSessionSignal,
    OmnigentSessionTerminalResult,
    OmnigentSessionWorkflowInput,
)
from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog
from moonmind.workflows.temporal.activities import omnigent_session_activities
from moonmind.workflows.temporal.workflow_registry import workflow_fleet_workflow_types
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.omnigent_session import (
    BOUNDED_COMMAND_ACTIVITIES,
    MAX_PENDING_SIGNAL_INTENTS,
    MoonMindOmnigentSessionWorkflow,
    canonical_omnigent_session_id,
    omnigent_session_workflow_id,
)


def _workflow_input(**updates: object) -> OmnigentSessionWorkflowInput:
    payload: dict[str, object] = {
        "sessionId": "oms_123",
        "compiledExecutionIntentRef": "art_intent_123",
        "compiledExecutionIntentDigest": "sha256:" + "a" * 64,
        "workflowId": "workflow-1",
        "stepExecutionId": "step-1",
        "agentRunId": "agent-run-1",
        "initialTurnAttemptId": "turn-1",
        "admittedFeatureGeneration": "omnigent-session-v1",
        "compatibilityVersion": "v1",
    }
    payload.update(updates)
    return OmnigentSessionWorkflowInput.model_validate(payload)


def test_session_identity_uses_canonical_owner_not_attempt_key() -> None:
    first = canonical_omnigent_session_id(
        workflow_id="workflow-1",
        step_execution_id="step-1",
        agent_run_id="agent-run-1",
    )
    second = canonical_omnigent_session_id(
        workflow_id="workflow-1",
        step_execution_id="step-1",
        agent_run_id="agent-run-1",
    )

    assert first == second
    assert first.startswith("oms_")
    assert omnigent_session_workflow_id(first) == f"omnigent-session:{first}"


def test_workflow_input_is_compact_closed_and_reference_only() -> None:
    value = _workflow_input()
    assert value.session_id == "oms_123"
    assert value.resume_state is None

    with pytest.raises(ValidationError):
        _workflow_input(providerToken="raw-secret")
    with pytest.raises(ValidationError):
        _workflow_input(workspacePath="/work/agent_jobs/run/repo")
    with pytest.raises(ValidationError):
        _workflow_input(compiledExecutionIntentRef="/tmp/intent.json")
    with pytest.raises(ValidationError):
        _workflow_input(admittedFeatureGeneration="omnigent-session-v2")


def test_agent_run_resolve_handoff_is_compact_with_legacy_replay_decode() -> None:
    binding = OmnigentExecutionPlanBinding(
        planRef="omnigent-execution-plan:sha256:" + "a" * 64,
        planDigest="sha256:" + "a" * 64,
        planArtifactRef="art-plan",
        taskInputSnapshotRef="art-task",
        taskInputSnapshotDigest="sha256:" + "b" * 64,
    )
    compact = OmnigentResolveIntentRequest(
        workflowId="workflow-1",
        stepExecutionId="step-1",
        agentRunId="agent-run-1",
        omnigentExecutionPlan=binding,
    ).model_dump(mode="json", by_alias=True, exclude_none=True)

    assert set(compact) == {
        "workflowId",
        "stepExecutionId",
        "agentRunId",
        "omnigentExecutionPlan",
        "admittedFeatureGeneration",
        "compatibilityVersion",
    }
    assert len(json.dumps(compact)) < 1_500
    assert "request" not in compact

    legacy = OmnigentResolveIntentRequest.model_validate(
        {
            "workflowId": "workflow-1",
            "stepExecutionId": "step-1",
            "agentRunId": "agent-run-1",
            "request": {"persisted": "legacy-history-payload"},
        }
    )
    assert legacy.request == {"persisted": "legacy-history-payload"}

    with pytest.raises(ValidationError, match="exactly one persisted plan authority"):
        OmnigentResolveIntentRequest(
            workflowId="workflow-1",
            stepExecutionId="step-1",
            agentRunId="agent-run-1",
            omnigentExecutionPlan=binding,
            request={"persisted": "ambiguous-authority-payload"},
        )


@pytest.mark.asyncio
async def test_compact_plan_handoff_reconstructs_selected_authored_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = {
        "schemaVersion": "task-input-snapshot/v1",
        "attachmentRefs": [{"artifactRef": "art-top-level"}],
        "draft": {
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "omnigent",
            "requiredCapabilities": ["readResources"],
            "workflow": {
                "instructions": "Root instructions must not replace a selected step.",
                "git": {
                    "startingBranch": "main",
                    "targetBranch": "authority-fix",
                },
                "inputAttachments": [{"artifactId": "art-workflow"}],
                "steps": [
                    {"id": "prepare", "instructions": "Prepare the workspace."},
                    {
                        "id": "implement",
                        "instructions": "Implement the selected change.",
                        "inputAttachments": [{"ref": "art-step"}],
                    },
                ],
            },
        },
    }
    snapshot_digest = omnigent_session_activities._digest_bytes(
        omnigent_session_activities._json_bytes(snapshot)
    )
    binding = OmnigentExecutionPlanBinding(
        planRef="omnigent-execution-plan:sha256:" + "1" * 64,
        planDigest="sha256:" + "1" * 64,
        planArtifactRef="art-plan",
        taskInputSnapshotRef="art-task-snapshot",
        taskInputSnapshotDigest=snapshot_digest,
    )
    plan = SimpleNamespace(
        payload=SimpleNamespace(
            authority=SimpleNamespace(
                taskInputSnapshotRef=binding.task_input_snapshot_ref,
                taskInputSnapshotDigest=binding.task_input_snapshot_digest,
            ),
            credentialBindings={
                "primary-model": SimpleNamespace(
                    providerProfileRef="provider-profile-1"
                )
            },
            resolvedSkills={"resolvedSkillSetRef": "artifact:art-skills"},
            harnessId="opencode-native",
            launchPolicyRef="opencode-on-demand@1",
            agentSource={
                "kind": "upstream",
                "upstreamId": "opencode-ai/opencode",
            },
            modelConfig=SimpleNamespace(
                qualifiedId="opencode/gpt-5",
                effort="high",
            ),
        )
    )
    monkeypatch.setattr(
        omnigent_session_activities,
        "_read_json_artifact",
        AsyncMock(return_value=snapshot),
    )

    request = await omnigent_session_activities._reconstruct_plan_bound_request(
        binding=binding,
        plan=plan,
        workflow_id="workflow-1",
        step_execution_id="step-execution-1",
        agent_run_id="agent-run-1",
        logical_step_id="implement",
    )

    assert request.instruction_ref == "Implement the selected change."
    assert request.instruction_ref != binding.task_input_snapshot_ref
    assert request.idempotency_key == "step-execution-1"
    assert request.input_refs == ["art-top-level", "art-workflow", "art-step"]
    assert request.resolved_skillset_ref == "art-skills"
    assert request.workspace_spec["startingBranch"] == "main"
    assert request.workspace_spec["targetBranch"] == "authority-fix"
    assert request.parameters["model"] == "opencode/gpt-5"
    assert request.parameters["effort"] == "high"
    assert request.parameters["omnigent"]["agent"]["agentId"] == (
        "opencode-ai/opencode"
    )
    assert request.parameters["omnigent"]["session"]["modelOverride"] == (
        "opencode/gpt-5"
    )
    assert request.parameters["omnigent"]["session"]["reasoningEffort"] == (
        "high"
    )

    continuation_instruction = "Continue using only the selected evidence."
    continuation_refs = ["art-continuation", "art-selected-evidence"]
    continued = await omnigent_session_activities._reconstruct_plan_bound_request(
        binding=binding,
        plan=plan,
        workflow_id="workflow-2",
        step_execution_id="step-execution-2",
        agent_run_id="agent-run-2",
        logical_step_id="implement",
        execution_instruction_ref=continuation_instruction,
        execution_instruction_digest=(
            omnigent_session_activities._digest_bytes(
                continuation_instruction.encode("utf-8")
            )
        ),
        execution_input_refs=continuation_refs,
        execution_input_refs_digest=(
            omnigent_session_activities._digest_bytes(
                json.dumps(
                    continuation_refs, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            )
        ),
    )
    assert continued.instruction_ref == continuation_instruction
    assert continued.input_refs[-2:] == continuation_refs

    bundle_plan = SimpleNamespace(
        payload=SimpleNamespace(
            **{
                **vars(plan.payload),
                "agentSource": {
                    "kind": "bundle",
                    "importedAgentId": "imported-agent-42",
                },
            }
        )
    )
    bundled = await omnigent_session_activities._reconstruct_plan_bound_request(
        binding=binding,
        plan=bundle_plan,
        workflow_id="workflow-3",
        step_execution_id="step-execution-3",
        agent_run_id="agent-run-3",
        logical_step_id="implement",
    )
    assert bundled.parameters["omnigent"]["agent"]["agentId"] == (
        "imported-agent-42"
    )


def test_activity_request_carries_one_atomic_runtime_binding_fence() -> None:
    runtime_ref = "omnigent-runtime-binding:sha256:" + "c" * 64
    request = OmnigentSessionActivityRequest(
        sessionId="oms_123",
        compiledExecutionIntentRef="art_intent_123",
        compiledExecutionIntentDigest="sha256:" + "a" * 64,
        expectedRevision=7,
        fencingGeneration=1,
        runtimeBindingRef=runtime_ref,
        runtimeBindingRevision=4,
        runtimeBindingFencingGeneration=2,
    )

    assert request.runtime_binding_ref == runtime_ref
    with pytest.raises(ValidationError, match="must be recorded atomically"):
        OmnigentSessionActivityRequest(
            sessionId="oms_123",
            compiledExecutionIntentRef="art_intent_123",
            compiledExecutionIntentDigest="sha256:" + "a" * 64,
            expectedRevision=7,
            fencingGeneration=1,
            runtimeBindingRef=runtime_ref,
        )


def test_workflow_projects_loaded_runtime_binding_fence_to_side_effects() -> None:
    runtime_ref = "omnigent-runtime-binding:sha256:" + "d" * 64
    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._input = _workflow_input()
    durable = DurableSessionState(
        sessionId="oms_123",
        revision=7,
        ownerToken="omnigent-session:oms_123",
        fencingGeneration=1,
        runtimeBindingRef=runtime_ref,
        runtimeBindingRevision=4,
        runtimeBindingFencingGeneration=2,
    )

    request = supervisor._base_activity_request(durable)

    assert request.runtime_binding_ref == runtime_ref
    assert request.runtime_binding_revision == 4
    assert request.runtime_binding_fencing_generation == 2


def test_admission_contract_is_frozen_compact_and_fail_closed() -> None:
    request = OmnigentSessionAdmissionRequest(
        workflowId="workflow-1",
        stepExecutionId="step-1",
        agentRunId="agent-run-1",
        executionProfileRef="omnigent-codex",
    )
    admitted = OmnigentSessionAdmissionDecision(
        admitted=True,
        reasonCode="enabled",
        admissionMode="enabled",
        admittedFeatureGeneration=OMNIGENT_SESSION_FEATURE_GENERATION,
    )

    assert request.model_dump(mode="json", by_alias=True) == {
        "workflowId": "workflow-1",
        "stepExecutionId": "step-1",
        "agentRunId": "agent-run-1",
        "executionProfileRef": "omnigent-codex",
    }
    assert admitted.admitted_feature_generation == "omnigent-session-v1"
    with pytest.raises(ValidationError):
        OmnigentSessionAdmissionDecision(
            admitted=True,
            reasonCode="enabled",
            admissionMode="enabled",
            admittedFeatureGeneration="omnigent-session-v2",
        )


@pytest.mark.asyncio
async def test_plan_bound_admission_uses_persisted_host_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = OmnigentExecutionPlanBinding(
        planRef="omnigent-execution-plan:sha256:" + "a" * 64,
        planDigest="sha256:" + "a" * 64,
        planArtifactRef="art-plan",
        taskInputSnapshotRef="art-task",
        taskInputSnapshotDigest="sha256:" + "b" * 64,
    )
    plan = SimpleNamespace(
        payload=SimpleNamespace(
            credentialBindings={
                "primary-model": SimpleNamespace(
                    providerProfileRef="provider-profile-1",
                    materializerRef="codex-oauth-home@1",
                )
            },
            hostClassRef="omnigent-codex-current@1",
            hostImageRef="ghcr.io/example/other@sha256:" + "1" * 64,
            omnigentHostBuildDigest="sha256:" + "b" * 64,
            hostArchitecture="linux/amd64",
            harnessId="codex-native",
            harnessImplementationRef=(
                "omnigent-harness-implementation:sha256:"
                "96f9ac4c77a5ae0137b5f65d48be4eb021d741081da51af0f0a0717e5db395d5"
            ),
            launchPolicyRef="codex-on-demand@1",
            executionRealizerRef="codex-profile-bound@1",
        )
    )
    monkeypatch.setattr(
        omnigent_session_activities,
        "_load_verified_execution_plan",
        AsyncMock(return_value=plan),
    )
    validate_support = Mock()
    monkeypatch.setattr(
        omnigent_session_activities,
        "_validate_plan_support_authority",
        validate_support,
    )
    # The generic omnigent host registry now requires host env config; legacy
    # hermetic tests should not fail closed for missing deployment env.
    monkeypatch.setattr(
        "moonmind.omnigent.realizers.registry.get_default_registry",
        lambda: SimpleNamespace(require=lambda _ref: None),
    )

    decision = await (
        omnigent_session_activities.omnigent_evaluate_session_admission_activity(
            OmnigentSessionAdmissionRequest(
                workflowId="workflow-1",
                stepExecutionId="step-1",
                agentRunId="agent-run-1",
                executionProfileRef="provider-profile-1",
                omnigentExecutionPlan=binding,
            ).model_dump(mode="json", by_alias=True)
        )
    )

    assert decision["admitted"] is True
    validate_support.assert_called_once_with(plan)


@pytest.mark.asyncio
async def test_plan_admission_rejects_missing_support_evidence() -> None:
    persisted = SimpleNamespace(
        payload=SimpleNamespace(
            authority=object(),
            admissionAuthority=None,
            executionRealizerRef="generic-omnigent-host@1",
        )
    )

    with pytest.raises(ValueError, match="lacks persisted admission evidence"):
        await omnigent_session_activities._validate_plan_admission_authority(
            persisted
        )


@pytest.mark.asyncio
async def test_pre_evidence_codex_plan_keeps_recorded_legacy_realizer() -> None:
    persisted = SimpleNamespace(
        payload=SimpleNamespace(
            authority=object(),
            admissionAuthority=None,
            executionRealizerRef="codex-profile-bound@1",
        )
    )

    await omnigent_session_activities._validate_plan_admission_authority(
        persisted
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "bad_value", "message"),
    [
        ("featureGeneration", "omnigent-session-v2", "feature generation"),
        ("replayCompatibilityVersion", "v2", "replay compatibility"),
        (
            "rollbackPolicyVersion",
            "moonmind.omnigent-session-supervisor-rollback/v2",
            "rollback policy",
        ),
    ],
)
async def test_plan_admission_rejects_mismatched_replay_authority(
    field: str,
    bad_value: str,
    message: str,
) -> None:
    values = {
        "supportEvidenceRef": "artifact:art-support",
        "supportEvidenceDigest": "sha256:" + "a" * 64,
        "featureGeneration": OMNIGENT_SESSION_FEATURE_GENERATION,
        "replayCompatibilityVersion": "v1",
        "rollbackPolicyVersion": SUPERVISOR_ROLLBACK_POLICY_VERSION,
    }
    values[field] = bad_value
    persisted = SimpleNamespace(
        payload=SimpleNamespace(
            authority=object(),
            admissionAuthority=AdmissionAuthority.model_validate(values),
        )
    )

    with pytest.raises(ValueError, match=message):
        await omnigent_session_activities._validate_plan_admission_authority(
            persisted
        )


@pytest.mark.asyncio
async def test_plan_admission_rejects_mismatched_support_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = {"supportCombinationKey": "support:recorded"}
    digest = "sha256:" + hashlib.sha256(
        json.dumps(recorded, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    authority = AdmissionAuthority(
        supportEvidenceRef="artifact:art-support",
        supportEvidenceDigest=digest,
        featureGeneration=OMNIGENT_SESSION_FEATURE_GENERATION,
        replayCompatibilityVersion="v1",
        rollbackPolicyVersion=SUPERVISOR_ROLLBACK_POLICY_VERSION,
    )
    persisted = SimpleNamespace(
        payload=SimpleNamespace(authority=object(), admissionAuthority=authority)
    )

    async def read_support(ref: str) -> dict[str, str]:
        assert ref == "art-support"
        return recorded

    monkeypatch.setattr(
        omnigent_session_activities, "_read_json_artifact", read_support
    )
    from moonmind.omnigent import execution_support_evidence

    monkeypatch.setattr(
        execution_support_evidence,
        "validate_protected_execution_support_evidence",
        lambda *_args, **_kwargs: object(),
    )

    def reject_mismatch(*_args, **_kwargs) -> None:
        raise ValueError("protected support evidence conflicts with the execution plan")

    monkeypatch.setattr(
        execution_support_evidence,
        "assert_protected_evidence_matches_plan",
        reject_mismatch,
    )

    with pytest.raises(ValueError, match="support evidence conflicts"):
        await omnigent_session_activities._validate_plan_admission_authority(
            persisted
        )


@pytest.mark.asyncio
async def test_plan_admission_rejects_active_rollback_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = SimpleNamespace(
        payload=SimpleNamespace(
            credentialBindings={
                "primary-model": SimpleNamespace(
                    providerProfileRef="provider-profile-1",
                    materializerRef="codex-oauth-home@1",
                )
            },
            hostClassRef="omnigent-codex-current@1",
            hostImageRef=None,
            omnigentHostBuildDigest=None,
            hostArchitecture=None,
            harnessId="codex-native",
            harnessImplementationRef=(
                "omnigent-harness-implementation:sha256:"
                "96f9ac4c77a5ae0137b5f65d48be4eb021d741081da51af0f0a0717e5db395d5"
            ),
            launchPolicyRef="codex-on-demand@1",
            executionRealizerRef="codex-profile-bound@1",
        )
    )
    binding = OmnigentExecutionPlanBinding(
        planRef="omnigent-execution-plan:sha256:" + "a" * 64,
        planDigest="sha256:" + "a" * 64,
        planArtifactRef="art-plan",
        taskInputSnapshotRef="art-task",
        taskInputSnapshotDigest="sha256:" + "b" * 64,
    )
    monkeypatch.setattr(
        omnigent_session_activities,
        "_load_verified_execution_plan",
        AsyncMock(return_value=plan),
    )
    monkeypatch.setattr(
        omnigent_session_activities,
        "_validate_plan_support_authority",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "moonmind.omnigent.realizers.registry.get_default_registry",
        lambda: SimpleNamespace(require=lambda _ref: None),
    )
    from moonmind.omnigent import session_supervisor_rollback

    monkeypatch.setattr(
        session_supervisor_rollback,
        "rollback_mode_from_settings",
        lambda _flags: "disable_new_admission",
    )

    with pytest.raises(ValueError, match="rollback generation blocks"):
        await omnigent_session_activities.omnigent_evaluate_session_admission_activity(
            OmnigentSessionAdmissionRequest(
                workflowId="workflow-1",
                stepExecutionId="step-1",
                agentRunId="agent-run-1",
                executionProfileRef="provider-profile-1",
                omnigentExecutionPlan=binding,
            ).model_dump(mode="json", by_alias=True)
        )


@pytest.mark.asyncio
async def test_side_effect_rejects_obsolete_runtime_binding_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A delayed cleanup/host owner cannot claim current live authority."""

    import moonmind.omnigent.control_plane as control_plane_module

    plan_ref = "omnigent-execution-plan:sha256:" + "a" * 64
    old_ref = "omnigent-runtime-binding:sha256:" + "b" * 64
    current_ref = "omnigent-runtime-binding:sha256:" + "c" * 64
    command = SimpleNamespace(
        session_id="oms_123",
        expected_session_revision=7,
        fencing_generation=1,
        status="pending",
    )
    session = SimpleNamespace(
        fencing_generation=1,
        revision=7,
        moonmind_workflow_id="workflow-1",
        metadata={
            "executionPlanRef": plan_ref,
            "runtimeBindingRef": current_ref,
            "runtimeBindingRevision": 5,
            "runtimeBindingFencingGeneration": 3,
        },
    )

    class Commands:
        async def get(self, command_id: str):
            assert command_id == "cleanup-command"
            return command

        async def claim_command(self, *_args: object, **_kwargs: object):
            return SimpleNamespace(
                record=command,
                outcome=control_plane_module.ControlPlaneOutcome.APPLIED,
            )

    class Sessions:
        async def load_for_update(self, session_id: str):
            assert session_id == "oms_123"
            return session

    class Store:
        @asynccontextmanager
        async def transaction(self):
            yield SimpleNamespace(commands=Commands(), sessions=Sessions())

    monkeypatch.setattr(
        control_plane_module,
        "OmnigentControlPlaneStore",
        lambda _session_maker: Store(),
    )
    monkeypatch.setattr(
        omnigent_session_activities,
        "_load_verified_execution_plan",
        AsyncMock(return_value=SimpleNamespace(planRef=plan_ref)),
    )
    monkeypatch.setattr(
        omnigent_session_activities,
        "_load_current_runtime_binding",
        AsyncMock(
            return_value=(
                object(),
                SimpleNamespace(
                    binding=SimpleNamespace(runtimeBindingRef=current_ref),
                    revision=5,
                    fencing_generation=3,
                ),
            )
        ),
    )
    request = OmnigentSessionActivityRequest(
        sessionId="oms_123",
        compiledExecutionIntentRef="art_intent_123",
        compiledExecutionIntentDigest="sha256:" + "d" * 64,
        omnigentExecutionPlan=OmnigentExecutionPlanBinding(
            planRef=plan_ref,
            planDigest="sha256:" + "a" * 64,
            planArtifactRef="art-plan",
            taskInputSnapshotRef="art-task",
            taskInputSnapshotDigest="sha256:" + "e" * 64,
        ),
        expectedRevision=7,
        fencingGeneration=1,
        commandId="cleanup-command",
        runtimeBindingRef=old_ref,
        runtimeBindingRevision=4,
        runtimeBindingFencingGeneration=2,
    )

    with pytest.raises(ValueError, match="authority is obsolete"):
        await omnigent_session_activities._claim_command(request)


def _exact_host_evidence_fixture() -> tuple[SimpleNamespace, dict[str, object]]:
    from moonmind.omnigent.harness_platform.catalog import (
        HarnessImplementationIdentity,
    )

    host_class_ref = "omnigent-codex-current@1"
    host_image_ref = "ghcr.io/example/omnigent-host@sha256:" + "a" * 64
    host_build_digest = "sha256:" + "b" * 64
    implementation = HarnessImplementationIdentity.model_validate(
        {
            "sourceKind": "core",
            "package": "omnigent",
            "version": "1.0.0",
            "digest": "sha256:" + "e" * 64,
        }
    )
    plan = SimpleNamespace(
        payload=SimpleNamespace(
            hostClassRef=host_class_ref,
            hostImageRef=host_image_ref,
            omnigentHostBuildDigest=host_build_digest,
            hostArchitecture="linux/amd64",
            harnessId="codex-native",
            harnessImplementationRef=implementation.implementation_ref(),
            supportIdentity=SimpleNamespace(vendorRuntimeRefs=()),
            classAdmissionDecision={
                "requiredSatisfied": [],
                "preferredSatisfied": [],
                "degraded": [],
                "unknown": [],
            },
            modelConfig=SimpleNamespace(
                qualifiedId=None,
                modelConfigDigest="sha256:" + "1" * 64,
            ),
            resolvedSkills={
                "resolvedSkillSetRef": "artifact:skillset-1",
                "resolvedSkillSetDigest": "sha256:" + "2" * 64,
                "skillDeliveryRef": "skill-delivery:sha256:" + "3" * 64,
            },
            workspaceIntentRef="workspace-intent:sha256:" + "4" * 64,
        )
    )
    preflight: dict[str, object] = {
        "hostId": "host-1",
        "resolvedSkillsetRef": "skillset-1",
        "workspaceMountAttested": True,
        "skillDeliveryAttested": True,
        "restrictedEgressAttested": True,
        "egressEvidenceRef": "art-egress-1",
        "workspaceResolution": {"workspaceId": "workspace-1"},
        "hostRegistrationEvidence": {
            "hostId": "host-1",
            "imageRef": host_image_ref,
            "omnigentVersion": "1.0.0",
            "omnigentBuildDigest": host_build_digest,
            "harnessImplementation": implementation.model_dump(
                mode="json", by_alias=True
            ),
            "runtimeDependencies": [],
            "architecture": "linux/amd64",
            "capabilities": {},
        },
    }
    return plan, preflight


@pytest.mark.asyncio
async def test_runtime_binding_requires_positive_skill_delivery_attestation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, preflight = _exact_host_evidence_fixture()
    preflight.pop("skillDeliveryAttested")

    async def persist(**kwargs: object) -> str:
        return f"art-{kwargs['artifact_type']}"

    monkeypatch.setattr(omnigent_session_activities, "_write_json_artifact", persist)
    with pytest.raises(RuntimeError, match="Skill delivery attestation"):
        await omnigent_session_activities._persist_host_runtime_evidence(
            request=SimpleNamespace(),
            plan=plan,
            preflight=preflight,
            model_options={},
            host_lease_generation=1,
        )


@pytest.mark.asyncio
async def test_runtime_binding_exact_capabilities_use_preflight_attestations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, preflight = _exact_host_evidence_fixture()
    captured: dict[str, object] = {}

    async def persist(**kwargs: object) -> str:
        return f"art-{kwargs['artifact_type']}"

    from moonmind.omnigent.harness_platform import capabilities

    real_validate = capabilities.validate_exact_host_capabilities

    def capture_validate(**kwargs: object):
        captured.update(kwargs)
        return real_validate(**kwargs)

    monkeypatch.setattr(omnigent_session_activities, "_write_json_artifact", persist)
    monkeypatch.setattr(
        capabilities,
        "validate_exact_host_capabilities",
        capture_validate,
    )
    refs = await omnigent_session_activities._persist_host_runtime_evidence(
        request=SimpleNamespace(),
        plan=plan,
        preflight=preflight,
        model_options={},
        host_lease_generation=1,
    )

    assert captured["mount_attested"] is True
    assert captured["network_attested"] is True
    assert captured["required_capabilities"] == []
    assert refs["cleanup_authority_refs"] == ["art-egress-1"]


def test_failure_contract_carries_only_typed_bounded_evidence() -> None:
    authority = OmnigentFailureAuthorityRequest(
        sessionId="oms_123",
        compiledExecutionIntentRef="art_intent_123",
        compiledExecutionIntentDigest="sha256:" + "a" * 64,
        workflowId="workflow-1",
        stepExecutionId="step-1",
        agentRunId="agent-run-1",
    )
    request = OmnigentPersistFailureRequest(
        sessionId="oms_123",
        compiledExecutionIntentRef="art_intent_123",
        compiledExecutionIntentDigest="sha256:" + "a" * 64,
        expectedRevision=5,
        fencingGeneration=2,
        decisionId="decision-5",
        commandId="command-5",
        status="cleanup_incomplete",
        failedActivity="omnigent.stop_host",
        reasonCode="bounded_activity_exhausted",
    )

    assert authority.workflow_id == "workflow-1"
    assert request.status == "cleanup_incomplete"
    assert request.failed_activity == "omnigent.stop_host"
    with pytest.raises(ValidationError):
        OmnigentPersistFailureRequest(
            **request.model_dump(mode="python", by_alias=True),
            error="provider token and unbounded exception prose",
        )


def test_signal_contract_carries_only_safe_ids_and_refs() -> None:
    signal = OmnigentSessionSignal(
        requestId="request-1",
        observationRef="art_observation_1",
        turnAttemptId="turn-2",
        reasonCode="operator_reconcile",
        observedAt=datetime(2026, 8, 18, tzinfo=UTC),
    )
    assert signal.request_id == "request-1"

    with pytest.raises(ValidationError):
        OmnigentSessionSignal(
            requestId="request-2",
            metadata={"token": "not-allowed"},
        )


def test_terminal_result_rejects_paths_and_inline_provider_payloads() -> None:
    with pytest.raises(ValidationError, match="opaque artifact reference"):
        OmnigentSessionTerminalResult(
            status="completed",
            result=AgentRunResult(outputRefs=["/tmp/provider-output.json"]),
        )
    with pytest.raises(ValidationError, match="reference-only"):
        OmnigentSessionTerminalResult(
            status="completed",
            result=AgentRunResult(
                metadata={"publication": {"providerPayload": "inline"}}
            ),
        )


def test_reconciliation_input_ignores_bounded_executor_diagnostics() -> None:
    mapping, frontier = omnigent_session_activities._observation_payload(
        [
            SimpleNamespace(
                observed_at=datetime(2026, 8, 18, tzinfo=UTC),
                bounded_index={
                    "providerSession": {
                        "observedAt": "2026-08-18T00:00:00Z",
                        "rawStatus": "idle",
                    },
                    "snapshotCandidate": {
                        "attemptId": "turn-1",
                        "signature": [2, "item-2"],
                    },
                },
            )
        ]
    )

    assert set(mapping) == {"providerSession"}
    assert frontier["snapshotFrontier"] is None
    ObservationSet.model_validate(mapping)


@pytest.mark.asyncio
async def test_snapshot_requires_stable_marked_turn_before_idle_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale idle projection cannot synthesize terminal on its first read."""

    import moonmind.omnigent.bridge_store as bridge_store_module
    import moonmind.omnigent.control_plane as control_plane_module

    marker = "MoonMind-Omnigent-Run:\n  idempotencyKey: turn-1"
    snapshot = {
        "status": "idle",
        "items": [
            {
                "id": "user-1",
                "type": "message",
                "data": {
                    "role": "user",
                    "content": [{"text": marker}],
                },
            },
            {
                "id": "assistant-1",
                "type": "message",
                "data": {
                    "role": "assistant",
                    "content": [{"text": "Finished."}],
                },
            },
        ],
    }
    session = SimpleNamespace(
        session_id="oms_123",
        active_turn_attempt_id="turn-1",
        provider_session_ref="provider-session-1",
        provider_event_cursor=None,
        snapshot_frontier=None,
        cleanup_state="pending",
        terminal_state=None,
        terminal_evidence_ref=None,
        revision=7,
        host_lease_ref="host-lease-1",
        provider_profile_id="profile-1",
        metadata={"providerLeaseRef": "profile-lease-1"},
    )
    turn = SimpleNamespace(
        turn_attempt_id="turn-1",
        idempotency_key="turn-idempotency-1",
    )
    prior_observations: list[object] = []
    writes: list[dict[str, object]] = []
    client_state = {"available": True}

    class FakeSessions:
        async def get(self, _session_id: str) -> object:
            return session

        async def advance_observation_frontier(
            self, _session_id: str, **_kwargs: object
        ) -> object:
            return session

    class FakeTurns:
        async def get(self, _turn_id: str) -> object:
            return turn

    class FakeObservations:
        async def list_for_session(
            self, _session_id: str, *, limit: int, latest: bool
        ) -> list[object]:
            assert limit == 500
            assert latest is True
            return list(prior_observations)

        async def append(self, **kwargs: object) -> object:
            # The real repository is idempotent on (session, dedup key); a fake
            # that always appends cannot catch an identity collision.
            identity = (kwargs["session_id"], kwargs["deduplication_key"])
            for existing in writes:
                if (
                    existing["session_id"],
                    existing["deduplication_key"],
                ) == identity:
                    return SimpleNamespace(**existing)
            writes.append(dict(kwargs))
            return SimpleNamespace(**kwargs)

    repos = SimpleNamespace(
        sessions=FakeSessions(),
        turn_attempts=FakeTurns(),
        observations=FakeObservations(),
    )

    class FakeStore:
        @asynccontextmanager
        async def transaction(self):
            yield repos

    class FakeBridgeStore:
        async def get_existing(self, _idempotency_key: str) -> object:
            return SimpleNamespace(
                first_message_marker=marker,
                metadata_={"first_message_pre_dispatch_item_ids": []},
            )

    class FakeClient:
        async def get_session(self, _session_id: str) -> dict[str, object]:
            if not client_state["available"]:
                raise RuntimeError("provider unavailable")
            return snapshot

    class FakeHttpClient:
        async def aclose(self) -> None:
            return None

    async def fake_client_context() -> tuple[FakeHttpClient, FakeClient]:
        return FakeHttpClient(), FakeClient()

    monkeypatch.setattr(
        control_plane_module,
        "OmnigentControlPlaneStore",
        lambda _session_maker: FakeStore(),
    )
    monkeypatch.setattr(
        bridge_store_module,
        "OmnigentBridgeSessionStore",
        lambda _session_maker: FakeBridgeStore(),
    )
    monkeypatch.setattr(
        omnigent_session_activities,
        "_omnigent_client_context",
        fake_client_context,
    )
    request = {
        "sessionId": "oms_123",
        "compiledExecutionIntentRef": "art_intent_123",
        "compiledExecutionIntentDigest": "sha256:" + "a" * 64,
        "expectedRevision": 7,
        "fencingGeneration": 1,
    }

    await omnigent_session_activities.omnigent_observe_snapshot_activity(request)
    first_index = dict(writes[-1]["bounded_index"])
    assert first_index["providerSession"]["rawStatus"] == "idle"
    assert "providerTurn" not in first_index
    prior_observations.append(
        SimpleNamespace(
            observation_type="provider_snapshot",
            observed_at=datetime.now(UTC) - timedelta(seconds=61),
            bounded_index=first_index,
        )
    )

    await omnigent_session_activities.omnigent_observe_snapshot_activity(request)
    second_index = dict(writes[-1]["bounded_index"])
    assert second_index["providerTurn"]["turnComplete"] is True
    # The confirming read repeats the identical provider snapshot, so it must
    # still persist as its own row instead of deduplicating against the pending
    # observation and losing the completion evidence.
    assert len(writes) == 2
    assert writes[0]["source_digest"] == writes[1]["source_digest"]
    assert writes[0]["deduplication_key"] != writes[1]["deduplication_key"]
    assert writes[0]["observation_id"] != writes[1]["observation_id"]

    # A retry of that same confirming read is still deduplicated.
    await omnigent_session_activities.omnigent_observe_snapshot_activity(request)
    assert len(writes) == 2

    # The provider snapshot may remain byte-identical after terminal state is
    # recorded. Resource authority changed, so persist a new bounded index for
    # harvest/cleanup instead of deduplicating against pre-terminal evidence.
    session.terminal_state = "completed"
    session.terminal_evidence_ref = "artifact://terminal-evidence"
    await omnigent_session_activities.omnigent_observe_snapshot_activity(request)
    assert len(writes) == 3
    terminal_resource_index = dict(writes[-1]["bounded_index"])
    assert terminal_resource_index["evidence"] == {
        "observedAt": terminal_resource_index["evidence"]["observedAt"],
        "terminalEvidenceAvailable": True,
        "artifactsAvailable": True,
    }

    session.cleanup_state = "host_stopped"
    client_state["available"] = False
    unavailable = (
        await omnigent_session_activities.omnigent_observe_snapshot_activity(
            request
        )
    )
    resource_index = dict(writes[-1]["bounded_index"])
    assert unavailable["readStatus"] == "unavailable"
    assert resource_index["host"]["runnerReady"] is False
    assert resource_index["profileLease"]["consumerActive"] is False


def test_every_reconciler_command_routes_to_bounded_activity_phases() -> None:
    assert BOUNDED_COMMAND_ACTIVITIES[DecisionKind.ENSURE_PROFILE_LEASE] == (
        "omnigent.ensure_provider_profile_lease",
    )
    assert BOUNDED_COMMAND_ACTIVITIES[DecisionKind.HARVEST_EVIDENCE] == (
        "omnigent.harvest_evidence",
        "omnigent.publish_workspace",
    )
    assert BOUNDED_COMMAND_ACTIVITIES[DecisionKind.BEGIN_CLEANUP] == (
        "omnigent.stop_provider_session",
        "omnigent.stop_host",
    )
    assert BOUNDED_COMMAND_ACTIVITIES[DecisionKind.RELEASE_LEASES] == (
        "omnigent.release_leases",
    )


def test_workflow_exposes_typed_wakes_controls_and_compact_query() -> None:
    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._initialize(_workflow_input())

    signal = OmnigentSessionSignal(requestId="wake-1", reasonCode="callback")
    supervisor.provider_observation_available(signal)
    supervisor.provider_callback_or_host_exit_recorded(signal)
    supervisor.approval_or_intervention_changed(signal)
    supervisor.operator_reconcile_requested(signal)
    supervisor.submit_authorized_turn(
        OmnigentSessionSignal(
            requestId="turn-2-request",
            turnAttemptId="turn-2",
            instructionRef="art_instruction_2",
        )
    )
    supervisor.cancel_or_interrupt_requested(
        OmnigentSessionSignal(requestId="cancel-1", reasonCode="operator_cancel")
    )
    supervisor.cleanup_requested(
        OmnigentSessionSignal(requestId="cleanup-1", reasonCode="operator_cleanup")
    )

    state = supervisor.get_state()
    assert state["sessionId"] == "oms_123"
    assert state["wakeSequence"] == 7
    assert state["cancelRequested"] is True
    assert state["cleanupRequested"] is True
    assert state["pendingIntentCount"] == 3
    assert "compiledExecutionIntentRef" not in state


def test_production_registry_and_catalog_include_supervisor_boundary() -> None:
    types = workflow_fleet_workflow_types(TemporalSettings())
    assert "MoonMind.OmnigentSession" in types

    catalog = build_default_activity_catalog()
    required = {
        "omnigent.evaluate_session_admission",
        "omnigent.resolve_intent",
        "omnigent.load_reconciliation_inputs",
        "omnigent.load_failure_authority",
        "omnigent.ensure_provider_profile_lease",
        "omnigent.ensure_host",
        "omnigent.ensure_provider_session",
        "omnigent.submit_turn",
        "omnigent.read_event_batch",
        "omnigent.observe_snapshot",
        "omnigent.harvest_evidence",
        "omnigent.publish_workspace",
        "omnigent.stop_provider_session",
        "omnigent.stop_host",
        "omnigent.release_leases",
        "omnigent.persist_decision",
        "omnigent.persist_signal_intents",
        "omnigent.record_terminal",
        "omnigent.persist_failure",
    }
    for activity_name in required:
        route = catalog.resolve_activity(activity_name)
        assert route.timeouts.start_to_close_seconds <= 300
        assert route.timeouts.schedule_to_close_seconds <= 600
        assert route.timeouts.heartbeat_timeout_seconds is None

    assert catalog.resolve_activity(
        "omnigent.read_event_batch"
    ).timeouts.start_to_close_seconds <= 30


def test_continue_as_new_carries_only_bounded_summary_state() -> None:
    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._initialize(_workflow_input())
    supervisor._decision_count = 101
    supervisor._observation_count = 203
    supervisor._turn_attempt_count = 2
    supervisor._last_revision = 17
    supervisor._last_event_cursor = "cursor-9"
    supervisor._last_snapshot_frontier = "snapshot-8"
    supervisor._terminal_result_ref = "art_result_1"

    carried = supervisor._build_continue_as_new_input()
    dumped = carried.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert dumped["sessionId"] == "oms_123"
    assert dumped["resumeState"] == {
        "continueAsNewCount": 1,
        "decisionCount": 101,
        "observationCount": 203,
        "turnAttemptCount": 2,
        "lastSessionRevision": 17,
        "lastEventCursor": "cursor-9",
        "lastSnapshotFrontier": "snapshot-8",
        "terminalResultRef": "art_result_1",
    }
    assert "providerToken" not in str(dumped)
    assert "workspacePath" not in str(dumped)


def test_continue_as_new_thresholds_reset_per_history_segment() -> None:
    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._initialize(
        _workflow_input(
            resumeState=OmnigentSessionContinueAsNewState(
                continueAsNewCount=2,
                decisionCount=200,
                observationCount=900,
                turnAttemptCount=25,
            )
        )
    )
    supervisor._segment_started_at = datetime(2026, 8, 18, tzinfo=UTC)
    with (
        patch(
            "moonmind.workflows.temporal.workflows.omnigent_session.workflow.info",
            return_value=SimpleNamespace(
                is_continue_as_new_suggested=False,
                get_current_history_length=lambda: 1,
            ),
        ),
        patch(
            "moonmind.workflows.temporal.workflows.omnigent_session.workflow.now",
            return_value=datetime(2026, 8, 18, tzinfo=UTC),
        ),
    ):
        assert supervisor._should_continue_as_new() is False


@pytest.mark.asyncio
async def test_signal_intent_is_not_lost_when_persistence_retries() -> None:
    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._initialize(_workflow_input())
    supervisor.cancel_or_interrupt_requested(
        OmnigentSessionSignal(requestId="cancel-retry")
    )
    durable = DurableSessionState(
        sessionId="oms_123",
        revision=1,
        ownerToken="owner",
        fencingGeneration=1,
    )
    supervisor._execute_activity = AsyncMock(side_effect=RuntimeError("retry"))

    with pytest.raises(RuntimeError, match="retry"):
        await supervisor._persist_pending_signal_intents(durable)
    assert len(supervisor._pending_signal_intents) == 1

    supervisor._execute_activity = AsyncMock(return_value={"appliedIntentCount": 1})
    assert await supervisor._persist_pending_signal_intents(durable) is True
    assert supervisor._pending_signal_intents == []


@pytest.mark.asyncio
async def test_timeout_reconciles_authoritative_snapshot_before_terminal_intent() -> None:
    """A missed terminal edge wins over an already elapsed workflow deadline."""

    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    intent = CompiledSessionIntent(
        sessionId="oms_123",
        provider="omnigent",
        requiresProfileLease=False,
        requiresHost=False,
        maxTurnAttempts=1,
        reconcileIntervalSeconds=30,
        turnPromptDigest="sha256:prompt",
    )
    durable = DurableSessionState(
        sessionId="oms_123",
        revision=5,
        ownerToken="owner",
        fencingGeneration=1,
        providerSessionAttached=True,
        providerSessionId="provider-session-1",
        attemptId="turn-1",
        submission=SubmissionState.ACCEPTED,
    )
    load_count = 0
    calls: list[str] = []

    class StopAfterTerminalDecision(RuntimeError):
        pass

    async def execute(activity_name: str, _payload: object) -> object:
        nonlocal load_count
        calls.append(activity_name)
        if activity_name == "omnigent.load_reconciliation_inputs":
            load_count += 1
            observations = (
                ObservationSet()
                if load_count == 1
                else ObservationSet(
                    providerSession=ProviderSessionObservation(
                        observedAt=now,
                        providerSessionId="provider-session-1",
                        rawStatus="completed",
                        snapshotDigest="snapshot-terminal",
                    )
                )
            )
            return {
                "intent": intent.model_dump(mode="json", by_alias=True),
                "durable": durable.model_dump(mode="json", by_alias=True),
                "observations": observations.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                ),
                "phase": "turn_in_flight",
                # SQLite persistence returns this UTC contract timestamp
                # without tzinfo; the workflow clock remains UTC-aware.
                "timeoutAt": (
                    now.replace(tzinfo=None) - timedelta(seconds=1)
                ).isoformat(),
            }
        if activity_name == "omnigent.heartbeat_host_lease":
            return {"hostLeaseHeartbeat": "renewed"}
        if activity_name == "omnigent.read_event_batch":
            return {"observationCount": 0}
        if activity_name == "omnigent.observe_snapshot":
            return {
                "observationCount": 1,
                "snapshotFrontier": "snapshot-terminal",
            }
        if activity_name == "omnigent.persist_decision":
            return {"decisionId": "decision-terminal"}
        if activity_name == "omnigent.record_terminal":
            raise StopAfterTerminalDecision
        raise AssertionError(activity_name)

    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._execute_activity = execute  # type: ignore[method-assign]
    supervisor._update_visibility = lambda: None  # type: ignore[method-assign]
    with patch(
        "moonmind.workflows.temporal.workflows.omnigent_session.workflow.now",
        return_value=now,
    ):
        with pytest.raises(StopAfterTerminalDecision):
            await supervisor.run(_workflow_input())

    assert calls[:5] == [
        "omnigent.load_reconciliation_inputs",
        "omnigent.heartbeat_host_lease",
        "omnigent.read_event_batch",
        "omnigent.observe_snapshot",
        "omnigent.load_reconciliation_inputs",
    ]
    assert "omnigent.persist_signal_intents" not in calls
    assert calls[-1] == "omnigent.record_terminal"


@pytest.mark.asyncio
async def test_unavailable_snapshot_does_not_satisfy_timeout_reconciliation() -> None:
    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._initialize(_workflow_input())
    durable = DurableSessionState(
        sessionId="oms_123",
        revision=1,
        ownerToken="owner",
        fencingGeneration=1,
    )
    supervisor._execute_activity = AsyncMock(
        side_effect=(
            {"hostLeaseHeartbeat": "renewed"},
            {"observationCount": 0, "readStatus": "unavailable"},
            {
                "observationCount": 0,
                "readStatus": "unavailable",
                "snapshotFrontier": None,
            },
        )
    )

    assert await supervisor._observe_after_wait(durable) is False
    assert supervisor._timeout_snapshot_observed is False


def test_agent_run_patch_preserves_legacy_replay_and_selects_new_supervisor() -> None:
    source = inspect.getsource(MoonMindAgentRun.run)

    assert "OMNIGENT_SESSION_SUPERVISOR_PATCH_ID" in source
    assert "OMNIGENT_SESSION_ADMISSION_PATCH_ID" in source
    assert "OMNIGENT_COMPACT_RESOLVE_INTENT_PATCH_ID" in source
    assert '"omnigent.evaluate_session_admission"' in source
    assert '"MoonMind.OmnigentSession"' in source
    assert "omnigent_session_workflow_id" in source
    assert "ChildWorkflowCancellationType.ABANDON" in source
    assert '"cancel_or_interrupt_requested"' in source
    assert "OMNIGENT_PROFILE_BOUND_EXECUTION_PATCH_ID" in source
    assert '"integration.omnigent.profile_bound_execute"' in source
    resolve_call = source.split('"omnigent.resolve_intent"', 1)[1].split(
        "cancellation_type", 1
    )[0]
    assert "_omnigent_resolve_intent_payload" in resolve_call
    assert "compact_plan_authority" in resolve_call


def test_agent_run_compact_intent_patch_preserves_legacy_activity_shape() -> None:
    binding = OmnigentExecutionPlanBinding(
        planRef="omnigent-execution-plan:sha256:" + "1" * 64,
        planDigest="sha256:" + "1" * 64,
        planArtifactRef="art_plan",
        taskInputSnapshotRef="art_task",
        taskInputSnapshotDigest="sha256:" + "2" * 64,
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="profile-1",
        omnigentExecutionPlan=binding,
        correlationId="workflow-1",
        idempotencyKey="step-1",
        instructionRef="art_task",
        parameters={"providerPayload": "must-not-enter-new-history"},
    )
    common = {
        "workflow_id": "workflow-1",
        "step_execution_id": "step-1",
        "agent_run_id": "agent-run-1",
        "admitted_feature_generation": OMNIGENT_SESSION_FEATURE_GENERATION,
    }

    compact = MoonMindAgentRun._omnigent_resolve_intent_payload(
        request, compact_plan_authority=True, **common
    )
    legacy = MoonMindAgentRun._omnigent_resolve_intent_payload(
        request, compact_plan_authority=False, **common
    )

    assert compact["omnigentExecutionPlan"] == binding.model_dump(
        mode="json", by_alias=True
    )
    assert compact["executionInstructionRef"] == "art_task"
    assert compact["executionInstructionDigest"].startswith("sha256:")
    assert "request" not in compact
    assert "providerPayload" not in json.dumps(compact)
    assert legacy["request"]["parameters"]["providerPayload"] == (
        "must-not-enter-new-history"
    )


# ---------------------------------------------------------------------------
# Codex review follow-ups on MoonLadderStudios/MoonMind#3742
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("terminal_state", "expected"),
    [
        ("completed", TerminalOutcome.SUCCESS),
        ("success", TerminalOutcome.SUCCESS),
        ("failed", TerminalOutcome.FAILURE),
        # A timeout is a system failure, not a user cancellation. Classifying it
        # as cancelled makes a later `failed` provider snapshot look like a
        # contradictory terminal and quarantines an already timed-out session.
        ("timed_out", TerminalOutcome.FAILURE),
        ("timeout", TerminalOutcome.FAILURE),
        ("delivery_unknown", TerminalOutcome.FAILURE),
        ("canceled", TerminalOutcome.CANCELLED),
        ("cancelled", TerminalOutcome.CANCELLED),
    ],
)
def test_durable_terminal_outcome_matches_reducer_classification(
    terminal_state: str, expected: TerminalOutcome
) -> None:
    assert (
        omnigent_session_activities._durable_terminal_outcome(
            terminal_state, TerminalOutcome, classify_provider_status
        )
        is expected
    )
    if terminal_state in {"timed_out", "timeout", "failed"}:
        assert (
            classify_provider_status(terminal_state)
            is ProviderStatusClass.TERMINAL_FAILURE
        )


def test_durable_terminal_outcome_is_none_without_terminal_state() -> None:
    for empty in (None, "", "   "):
        assert (
            omnigent_session_activities._durable_terminal_outcome(
                empty, TerminalOutcome, classify_provider_status
            )
            is None
        )


def _signal(request_id: str, **updates: object) -> OmnigentSessionSignal:
    payload: dict[str, object] = {"requestId": request_id}
    payload.update(updates)
    return OmnigentSessionSignal.model_validate(payload)


def test_full_signal_backlog_never_throws_from_a_signal_handler() -> None:
    """Raising here would fail the workflow task and replay the same signal."""

    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._initialize(_workflow_input())
    for index in range(MAX_PENDING_SIGNAL_INTENTS):
        supervisor._queue_signal_intent(
            "approval_or_intervention_changed", _signal(f"req-{index}")
        )
    assert len(supervisor._pending_signal_intents) == MAX_PENDING_SIGNAL_INTENTS

    # An overflowing non-recovery intent is counted, not raised.
    supervisor._queue_signal_intent(
        "approval_or_intervention_changed", _signal("req-overflow")
    )
    assert supervisor._dropped_signal_intents == 1
    assert len(supervisor._pending_signal_intents) == MAX_PENDING_SIGNAL_INTENTS

    # Cancellation and cleanup are the intents needed to recover a wedged
    # session, so they are still admitted past the bound.
    supervisor.cancel_or_interrupt_requested(_signal("cancel-1"))
    supervisor.cleanup_requested(_signal("cleanup-1"))
    queued_kinds = [
        item["kind"] for item in supervisor._pending_signal_intents
    ]
    assert "cancel_or_interrupt_requested" in queued_kinds
    assert "cleanup_requested" in queued_kinds
    assert supervisor._cancel_requested is True
    assert supervisor._cleanup_requested is True

    # A second cancel does not grow the backlog without bound either.
    supervisor.cancel_or_interrupt_requested(_signal("cancel-2"))
    assert queued_kinds.count("cancel_or_interrupt_requested") == 1
    assert supervisor.get_state()["droppedIntentCount"] == 2


def test_repeated_signal_request_id_is_deduplicated_not_requeued() -> None:
    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._initialize(_workflow_input())
    supervisor.cancel_or_interrupt_requested(_signal("cancel-1"))
    supervisor.cancel_or_interrupt_requested(_signal("cancel-1"))
    assert len(supervisor._pending_signal_intents) == 1
    assert supervisor._dropped_signal_intents == 0
    # A distinct request id is still real new intent.
    supervisor.cleanup_requested(_signal("cleanup-1"))
    assert len(supervisor._pending_signal_intents) == 2


@pytest.mark.asyncio
async def test_poll_cycle_renews_host_lease_before_observing() -> None:
    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._initialize(_workflow_input())
    durable = DurableSessionState(
        sessionId="oms_123",
        revision=1,
        ownerToken="owner",
        fencingGeneration=1,
    )
    calls: list[str] = []

    async def execute(activity_name: str, _payload: object) -> object:
        calls.append(activity_name)
        if activity_name == "omnigent.heartbeat_host_lease":
            return {"hostLeaseHeartbeat": "renewed"}
        return {"observationCount": 0}

    supervisor._execute_activity = execute  # type: ignore[method-assign]
    assert await supervisor._observe_after_wait(durable) is True
    assert calls == [
        "omnigent.heartbeat_host_lease",
        "omnigent.read_event_batch",
        "omnigent.observe_snapshot",
    ]
    assert supervisor.get_state()["hostLeaseHeartbeat"] == "renewed"


@pytest.mark.asyncio
async def test_heartbeat_activity_renews_only_a_renewable_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lease owned by cleanup is reported, not renewed out from under it."""

    import moonmind.omnigent.control_plane as control_plane_module
    import moonmind.omnigent.oauth_hosts as oauth_hosts_module

    session = SimpleNamespace(
        session_id="oms_123",
        host_lease_ref="host-lease-1",
        cleanup_state="pending",
    )
    lease_state = {"status": "assigned", "cleanupClaimed": False}
    heartbeats: list[str] = []

    class FakeSessions:
        async def get(self, _session_id: str) -> object:
            return session

    class FakeStore:
        @asynccontextmanager
        async def transaction(self):
            yield SimpleNamespace(sessions=FakeSessions())

    class FakeHosts:
        def __init__(self, _session_factory: object) -> None:
            pass

        async def get_host_lease(self, lease_id: str) -> object:
            if lease_state["status"] == "missing":
                return None
            return SimpleNamespace(
                lease_id=lease_id, status=lease_state["status"]
            )

        async def heartbeat_host_lease(self, lease_id: str) -> object:
            if lease_state["cleanupClaimed"]:
                raise oauth_hosts_module.OmnigentOAuthHostError(
                    "host lease cleanup is owned by the janitor",
                    code=oauth_hosts_module.HOST_CLEANUP_CLAIMED_ERROR_CODE,
                )
            heartbeats.append(lease_id)
            return SimpleNamespace(lease_id=lease_id, status="assigned")

    monkeypatch.setattr(
        control_plane_module,
        "OmnigentControlPlaneStore",
        lambda _session_maker: FakeStore(),
    )
    monkeypatch.setattr(
        oauth_hosts_module, "OmnigentOAuthHostRepository", FakeHosts
    )
    request = {
        "sessionId": "oms_123",
        "compiledExecutionIntentRef": "art_intent_123",
        "compiledExecutionIntentDigest": "sha256:" + "a" * 64,
        "expectedRevision": 7,
        "fencingGeneration": 1,
    }
    heartbeat = omnigent_session_activities.omnigent_heartbeat_host_lease_activity

    assert (await heartbeat(request))["hostLeaseHeartbeat"] == "renewed"
    assert heartbeats == ["host-lease-1"]

    # `draining` is owned by whoever won the cleanup fence.
    lease_state["status"] = "draining"
    assert (await heartbeat(request))["hostLeaseHeartbeat"] == "not_renewable"

    # Read as renewable, then drained before the heartbeat CAS landed.
    lease_state["status"] = "assigned"
    lease_state["cleanupClaimed"] = True
    assert (await heartbeat(request))["hostLeaseHeartbeat"] == "cleanup_claimed"

    lease_state["cleanupClaimed"] = False
    lease_state["status"] = "missing"
    assert (await heartbeat(request))["hostLeaseHeartbeat"] == "missing"

    lease_state["status"] = "assigned"
    session.host_lease_ref = None
    assert (await heartbeat(request))["hostLeaseHeartbeat"] == "not_attached"
    assert heartbeats == ["host-lease-1"]


@pytest.mark.asyncio
async def test_stop_host_does_not_run_cleanup_it_did_not_win(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two cleanup owners must not delete the same host concurrently."""

    import moonmind.omnigent.control_plane as control_plane_module
    import moonmind.omnigent.oauth_hosts as oauth_hosts_module

    session = SimpleNamespace(
        session_id="oms_123",
        host_lease_ref="host-lease-1",
        cleanup_state="host_stopped",
        metadata={},
        revision=7,
        fencing_generation=1,
    )
    claims: list[dict[str, object]] = []

    class FakeSessions:
        async def get(self, _session_id: str) -> object:
            return session

    class FakeStore:
        @asynccontextmanager
        async def transaction(self):
            yield SimpleNamespace(sessions=FakeSessions())

    class FakeHosts:
        def __init__(self, _session_factory: object) -> None:
            pass

        async def get_host_lease(self, lease_id: str) -> object:
            return SimpleNamespace(
                lease_id=lease_id,
                status="draining",
                last_heartbeat_at=datetime.now(UTC),
                binding_ref="binding-1",
            )

        async def claim_host_lease_cleanup(self, lease_id: str, **kwargs: object):
            claims.append({"leaseId": lease_id, **kwargs})
            return None

        async def validate_binding(self, _binding_ref: str) -> object:
            raise AssertionError("cleanup ran without winning the fence")

    monkeypatch.setattr(
        control_plane_module,
        "OmnigentControlPlaneStore",
        lambda _session_maker: FakeStore(),
    )
    monkeypatch.setattr(
        oauth_hosts_module, "OmnigentOAuthHostRepository", FakeHosts
    )

    async def fake_claim(_request: object) -> tuple[object, bool]:
        return SimpleNamespace(status="claimed"), True

    async def fake_settle(_request: object, **_kwargs: object) -> dict[str, object]:
        return {"commandId": "cmd-1", "outcome": "settled"}

    monkeypatch.setattr(
        omnigent_session_activities, "_claim_command", fake_claim
    )
    monkeypatch.setattr(
        omnigent_session_activities, "_settle_command", fake_settle
    )

    result = await omnigent_session_activities.omnigent_stop_host_activity(
        {
            "sessionId": "oms_123",
            "compiledExecutionIntentRef": "art_intent_123",
            "compiledExecutionIntentDigest": "sha256:" + "a" * 64,
            "expectedRevision": 7,
            "fencingGeneration": 1,
            "commandId": "cmd-1",
        }
    )

    assert result["outcome"] == "settled"
    # The fence was attempted with the observed status *and* heartbeat, so a
    # lease heartbeated since the read cannot hand authority to a second owner.
    assert len(claims) == 3
    assert claims[0]["expected_status"] == "draining"
    assert "expected_last_heartbeat_at" in claims[0]


@pytest.mark.asyncio
async def test_profile_lease_request_carries_owning_workflow_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An activity-owned grant is rejected without `metadata.workflowId`."""

    from moonmind.workflows.temporal.workflows.provider_profile_manager import (
        MoonMindProviderProfileManagerWorkflow,
    )

    import moonmind.omnigent.control_plane as control_plane_module
    import moonmind.provider_profiles.lease_client as lease_client_module

    session = SimpleNamespace(
        session_id="oms_123",
        revision=7,
        provider_profile_generation=3,
        step_execution_id="step-1",
    )
    captured: dict[str, object] = {}

    class FakeSessions:
        async def get(self, _session_id: str) -> object:
            return session

        async def bind_runtime_authority(self, _session_id: str, **_kwargs: object):
            if captured.get("failBind"):
                raise RuntimeError("simulated control-plane bind failure")
            captured["boundRuntimeAuthority"] = dict(_kwargs)
            return session

    class FakeStore:
        @asynccontextmanager
        async def transaction(self):
            yield SimpleNamespace(sessions=FakeSessions())

    class FakeLeaseClient:
        def __init__(self, _adapter: object) -> None:
            pass

        async def acquire_execution_lease(self, **kwargs: object) -> object:
            captured.update(kwargs)
            return SimpleNamespace(
                lease_id="profile-lease-1", owner_id=kwargs["owner_id"]
            )

        async def release_lease(self, lease: object) -> None:
            captured["releasedLeaseId"] = lease.lease_id

    class FakeDbSession:
        reads = 0

        async def get(self, _model: object, _profile_id: str) -> object:
            type(self).reads += 1
            return SimpleNamespace(
                enabled=True,
                auth_state="connected",
                runtime_id="codex_cli",
                credential_generation=(
                    3 if type(self).reads == 1 else 4
                ),
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

    async def fake_intent(_request: object) -> object:
        return SimpleNamespace(
            execution_profile_ref="omnigent-codex", idempotency_key="idem-1"
        )

    monkeypatch.setattr(
        control_plane_module,
        "OmnigentControlPlaneStore",
        lambda _session_maker: FakeStore(),
    )
    monkeypatch.setattr(
        lease_client_module, "ProviderProfileLeaseClient", FakeLeaseClient
    )
    monkeypatch.setattr(
        omnigent_session_activities, "_load_intent_request", fake_intent
    )

    async def fake_claim(_request: object) -> tuple[object, bool]:
        return SimpleNamespace(status="claimed"), True

    async def fake_settle(_request: object, **_kwargs: object) -> dict[str, object]:
        return {"commandId": "cmd-1"}

    monkeypatch.setattr(
        omnigent_session_activities, "_claim_command", fake_claim
    )
    monkeypatch.setattr(
        omnigent_session_activities, "_settle_command", fake_settle
    )
    monkeypatch.setattr(
        "api_service.db.base.async_session_maker",
        lambda: FakeDbSession(),
        raising=False,
    )

    await omnigent_session_activities.omnigent_ensure_provider_profile_lease_activity(
        {
            "sessionId": "oms_123",
            "compiledExecutionIntentRef": "art_intent_123",
            "compiledExecutionIntentDigest": "sha256:" + "a" * 64,
            "expectedRevision": 7,
            "fencingGeneration": 1,
            "commandId": "cmd-1",
        }
    )

    metadata = dict(captured["metadata"])  # type: ignore[arg-type]
    assert metadata["workflowId"] == omnigent_session_workflow_id("oms_123")
    assert metadata["stepExecutionId"] == "step-1"
    assert metadata["ownerIsWorkflow"] is False
    # The manager's allowlist is what makes a session-only key unusable here.
    safe = MoonMindProviderProfileManagerWorkflow._safe_lease_metadata(
        {"metadata": metadata}
    )
    assert safe["workflowId"] == omnigent_session_workflow_id("oms_123")
    assert "canonicalSessionId" not in safe
    assert captured["boundRuntimeAuthority"]["credential_generation"] == 4  # type: ignore[index]

    captured["failBind"] = True
    FakeDbSession.reads = 0
    ensure_provider_lease = (
        omnigent_session_activities
        .omnigent_ensure_provider_profile_lease_activity
    )
    with pytest.raises(RuntimeError, match="control-plane bind failure"):
        await ensure_provider_lease(
            {
                "sessionId": "oms_123",
                "compiledExecutionIntentRef": "art_intent_123",
                "compiledExecutionIntentDigest": "sha256:" + "a" * 64,
                "expectedRevision": 7,
                "fencingGeneration": 1,
                "commandId": "cmd-2",
            }
        )
    assert captured["releasedLeaseId"] == "profile-lease-1"
