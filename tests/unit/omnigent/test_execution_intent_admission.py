"""Admission-boundary tests for MoonLadderStudios/MoonMind#3706."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.omnigent.execution_intent import ExecutionIntentCompilationError
from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    CompiledExecutionIntentBinding,
)


class _Session:
    def __init__(self, record: SimpleNamespace) -> None:
        self.record = record
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _model, _key):
        return self.record

    async def commit(self) -> None:
        self.commits += 1


class _SessionFactory:
    def __init__(self, record: SimpleNamespace) -> None:
        self.sessions: list[_Session] = []
        self.record = record

    def __call__(self) -> _Session:
        session = _Session(self.record)
        self.sessions.append(session)
        return session


def _binding(
    *, artifact_ref: str = "artifact://art-intent"
) -> CompiledExecutionIntentBinding:
    digest = "sha256:" + "a" * 64
    return CompiledExecutionIntentBinding(
        intentSchema="moonmind.omnigent.compiled-execution-intent/v1",
        artifactRef=artifact_ref,
        intentDigest=digest,
        runtimeView={
            "schema": "moonmind.omnigent.compiled-execution-intent/v1",
            "intentDigest": digest,
            "workflowId": "workflow-1",
            "stepExecutionId": "workflow-1:run-1:step-1:execution:1",
            "agentRunId": "idem-1",
            "canonicalSessionSeed": "idem-1",
            "taskInputSnapshotRef": "art-input",
            "taskInputSnapshotDigest": "sha256:task-input",
            "agentKind": "external",
            "agentId": "omnigent",
            "executionProfileRef": "omnigent-codex@1",
            "executionProfileVersion": "1",
            "agentProfileRef": "omnigent-codex@1",
            "agentProfileDigest": "sha256:agent-profile",
            "providerProfileId": "profile-1",
            "credentialGeneration": "1",
            "model": "gpt-5",
            "effort": "high",
            "launchPolicyRef": "codex-on-demand@1",
            "launchPolicyDigest": "sha256:launch-policy",
            "effectiveLaunchSnapshotRef": "omnigent-launch:sha256:snapshot",
            "effectiveLaunchSnapshotDigest": "sha256:snapshot",
            "hostImageDigest": "image@sha256:host",
            "serverImageDigest": "image@sha256:server",
            "operationClass": "controlled_mutation",
            "baseBranch": "main",
            "targetBranch": "feature/3706",
            "checkoutCommit": "abc123",
            "remediationLoopEnabled": False,
            "sessionMode": "fresh",
            "claimsFullAuthority": True,
        },
    )


def _required_request(*, instruction_ref: str | None) -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="provider-1",
        executionIntentRequirement="required",
        correlationId="corr-1",
        idempotencyKey="idem-1",
        instructionRef=instruction_ref,
        stepExecution={
            "workflowId": "workflow-1",
            "runId": "run-1",
            "logicalStepId": "step-1",
            "executionOrdinal": 1,
            "stepExecutionId": "workflow-1:run-1:step-1:execution:1",
            "runtimeContextPolicy": "fresh_agent_run",
        },
    )


def _resolved_boundary_inputs() -> tuple[SimpleNamespace, dict, dict, SimpleNamespace]:
    profile = SimpleNamespace(
        profile_id="provider-1",
        credential_generation=3,
        runtime_id="codex_cli",
        model_tiers=[{"model": "gpt-5", "effort": "high"}],
        default_model="gpt-5",
        default_effort="high",
    )
    policy_snapshot = {"policyDigest": "sha256:" + "1" * 64}
    effective_launch = {
        "executionProfileRef": "omnigent-codex@1",
        "executionProfileDigest": "sha256:" + "2" * 64,
        "harness": "codex-native",
        "launchPolicyRef": "codex-on-demand@1",
        "snapshotRef": "omnigent-launch:sha256:" + "3" * 64,
        "hostMode": "on_demand_docker",
        "serverImageRef": "omnigent@sha256:" + "4" * 64,
        "hostImageRef": "omnigent-host@sha256:" + "5" * 64,
        "networkRef": "network:restricted",
        "egressProfileRef": "egress:restricted",
        "limits": {"timeoutSeconds": 3600},
        "boundaries": {"session": {"continuation": True}},
        "cleanup": {"mode": "remove"},
    }
    workspace_intent = SimpleNamespace(
        repository="https://github.com/acme/widgets.git",
        starting_branch="main",
        checkout_commit="abc123",
        required_capabilities=("repository",),
        restore_input_refs=(),
        external_state_refs=(),
        repository_kind="github_https",
        connection_ref="github:connection-1",
        workspace_locator=SimpleNamespace(kind="sandbox"),
        workspace_locator_payload=lambda: {
            "kind": "sandbox",
            "workspaceId": "workspace-1",
            "relativePath": "repo",
        },
        repository_mutation=True,
    )
    return profile, policy_snapshot, effective_launch, workspace_intent


@pytest.mark.asyncio
async def test_resolved_authority_binds_durable_inputs_before_compilation() -> None:
    record = SimpleNamespace(
        memo={"task_input_snapshot_ref": "art-task-input"},
        artifact_refs=[],
    )
    coordinator = object.__new__(OmnigentProfileBoundExecutionCoordinator)
    coordinator._session_factory = _SessionFactory(record)
    coordinator._artifact_service = SimpleNamespace(
        get_metadata=AsyncMock(
            return_value=(
                SimpleNamespace(sha256="6" * 64, status="complete"),
                [],
                False,
                None,
            )
        )
    )
    profile, policy, launch, workspace = _resolved_boundary_inputs()

    authority = await coordinator._resolved_execution_authority(
        request=_required_request(instruction_ref="Implement the change."),
        profile=profile,
        policy_snapshot=policy,
        effective_launch=launch,
        workspace_intent=workspace,
    )

    assert authority.task_input_snapshot_ref == "art-task-input"
    assert authority.task_input_snapshot_digest == "sha256:" + "6" * 64
    assert authority.instruction_digest == (
        "sha256:" + hashlib.sha256(b"Implement the change.").hexdigest()
    )
    assert authority.provider_profile_id == "provider-1"
    assert authority.base_branch == "main"
    assert authority.checkout_commit == "abc123"
    assert authority.runtime_capabilities == (
        "http",
        "sse",
        "websocket",
        "repository",
    )


@pytest.mark.asyncio
async def test_resolved_authority_rejects_missing_instruction_before_admission() -> None:
    record = SimpleNamespace(
        memo={"task_input_snapshot_ref": "art-task-input"},
        artifact_refs=[],
    )
    coordinator = object.__new__(OmnigentProfileBoundExecutionCoordinator)
    coordinator._session_factory = _SessionFactory(record)
    coordinator._artifact_service = SimpleNamespace(
        get_metadata=AsyncMock(
            return_value=(
                SimpleNamespace(sha256="6" * 64, status="complete"),
                [],
                False,
                None,
            )
        )
    )
    profile, policy, launch, workspace = _resolved_boundary_inputs()

    with pytest.raises(
        ExecutionIntentCompilationError,
        match="immutable instruction ref",
    ):
        await coordinator._resolved_execution_authority(
            request=_required_request(instruction_ref=None),
            profile=profile,
            policy_snapshot=policy,
            effective_launch=launch,
            workspace_intent=workspace,
        )


@pytest.mark.asyncio
async def test_artifact_authority_must_be_durably_complete() -> None:
    coordinator = object.__new__(OmnigentProfileBoundExecutionCoordinator)
    coordinator._artifact_service = SimpleNamespace(
        get_metadata=AsyncMock(
            return_value=(
                SimpleNamespace(sha256="6" * 64, status="pending"),
                [],
                False,
                None,
            )
        )
    )

    with pytest.raises(
        ExecutionIntentCompilationError,
        match="not durably complete",
    ):
        await coordinator._artifact_digest("artifact://art-task-input")


@pytest.mark.asyncio
async def test_create_record_binds_exact_compiled_intent_ref_and_digest() -> None:
    record = SimpleNamespace(memo={"task_input_snapshot_ref": "art-input"}, artifact_refs=[])
    session_factory = _SessionFactory(record)
    coordinator = object.__new__(OmnigentProfileBoundExecutionCoordinator)
    coordinator._session_factory = session_factory
    binding = _binding()

    await coordinator._bind_execution_intent_to_create_record(
        workflow_id="workflow-1",
        binding=binding,
    )

    assert record.memo["compiled_execution_intent_schema"] == binding.intent_schema
    assert record.memo["compiled_execution_intent_ref"] == binding.artifact_ref
    assert record.memo["compiled_execution_intent_digest"] == binding.intent_digest
    assert record.artifact_refs == [binding.artifact_ref]
    assert session_factory.sessions[-1].commits == 1


@pytest.mark.asyncio
async def test_create_record_rejects_a_conflicting_authority_binding() -> None:
    record = SimpleNamespace(
        memo={
            "compiled_execution_intent_ref": "art-other",
            "compiled_execution_intent_digest": "sha256:" + "b" * 64,
        },
        artifact_refs=["art-other"],
    )
    coordinator = object.__new__(OmnigentProfileBoundExecutionCoordinator)
    coordinator._session_factory = _SessionFactory(record)

    with pytest.raises(
        ExecutionIntentCompilationError,
        match="already binds different authority",
    ):
        await coordinator._bind_execution_intent_to_create_record(
            workflow_id="workflow-1",
            binding=_binding(),
        )


@pytest.mark.asyncio
async def test_persistence_is_artifact_backed_before_create_record_binding() -> None:
    record = SimpleNamespace(memo={}, artifact_refs=[])
    session_factory = _SessionFactory(record)
    coordinator = object.__new__(OmnigentProfileBoundExecutionCoordinator)
    coordinator._session_factory = session_factory
    coordinator._artifact_service = SimpleNamespace(
        create=AsyncMock(
            return_value=(SimpleNamespace(artifact_id="art-intent"), SimpleNamespace())
        ),
        write_complete=AsyncMock(return_value=SimpleNamespace(artifact_id="art-intent")),
    )
    digest = "sha256:" + "a" * 64
    lifecycle_evidence = {
        "executionIntentRef": "artifact://art-intent",
        "executionIntentDigest": digest,
    }
    coordinator._run_store = SimpleNamespace(
        get_lifecycle_event_metadata=AsyncMock(
            side_effect=[None, lifecycle_evidence]
        ),
        record_lifecycle_event=AsyncMock(),
    )
    intent = SimpleNamespace(
        schema_id="moonmind.omnigent.compiled-execution-intent/v1",
        intent_digest=digest,
        identity=SimpleNamespace(workflow_id="workflow-1"),
        model_dump=lambda **_kwargs: {
            "schema": "moonmind.omnigent.compiled-execution-intent/v1",
            "intentDigest": digest,
        },
        evidence=lambda: {"schema": "moonmind.omnigent.compiled-execution-intent/v1"},
        compact_runtime_view=lambda: _binding().runtime_view.model_dump(
            by_alias=True,
            mode="json",
        ),
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionIntentRequirement="required",
        correlationId="corr-1",
        idempotencyKey="idem-1",
        stepExecution={
            "workflowId": "workflow-1",
            "runId": "run-1",
            "logicalStepId": "step-1",
            "executionOrdinal": 1,
            "stepExecutionId": "workflow-1:run-1:step-1:execution:1",
            "runtimeContextPolicy": "fresh_agent_run",
        },
    )

    binding = await coordinator._persist_execution_intent(
        request=request,
        intent=intent,
    )

    assert binding.artifact_ref == "artifact://art-intent"
    coordinator._artifact_service.write_complete.assert_awaited_once()
    coordinator._run_store.record_lifecycle_event.assert_awaited_once()
    create_kwargs = coordinator._artifact_service.create.await_args.kwargs
    assert create_kwargs["metadata_json"]["traceability"] == (
        "MoonLadderStudios/MoonMind#3706"
    )
    assert record.memo["compiled_execution_intent_ref"] == "artifact://art-intent"
    assert record.memo["compiled_execution_intent_digest"] == digest
