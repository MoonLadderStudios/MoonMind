"""Server-owned launch coverage for MoonLadderStudios/MoonMind#3621."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from api_service.services.checkpoint_branch_service import CheckpointBranchService
from api_service.services.checkpoint_branch_turn_execution import (
    CheckpointBranchTurnExecutionOwner,
    CheckpointBranchTurnLaunchError,
    build_branch_turn_execution_identity,
)
from moonmind.omnigent.checkpoints import CandidateWorkspaceAuthority
from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.schemas.checkpoint_branch_models import (
    CheckpointBranchContinueModel,
    CheckpointBranchForkModel,
    CheckpointBranchTurnLaunchRequest,
)
from moonmind.schemas.temporal_models import StepExecutionCheckpointModel


class _ScalarResult:
    def scalar_one(self) -> int:
        return 1


class _Session:
    def __init__(self) -> None:
        self.commit = AsyncMock()
        self.refresh = AsyncMock()
        self.profile = SimpleNamespace(
            profile_id="profile-1",
            enabled=True,
            auth_state="connected",
            credential_generation=1,
            runtime_id="codex_cli",
            default_model="gpt-5.4",
            default_effort="high",
        )

    async def execute(self, _statement):
        return _ScalarResult()

    async def get(self, _model, _identity):
        return self.profile


def _authority_graph():
    turn = SimpleNamespace(
        branch_turn_id="turn-1",
        branch_id="branch-1",
        parent_turn_id=None,
        source_checkpoint_ref="artifact://source-checkpoint",
        source_checkpoint_digest="sha256:" + "a" * 64,
        source_state_kind=None,
        source_state_ref=None,
        source_state_digest=None,
        workspace_policy="apply_previous_execution_diff_to_clean_baseline",
        runtime_context_policy="fresh_agent_run",
        instruction_ref="artifact://instruction",
        instruction_digest="sha256:" + "b" * 64,
        created_step_execution_id=None,
        runtime_agent_run_id=None,
        context_bundle_ref=None,
        step_execution_manifest_ref=None,
        diagnostics={
            "followUpRetrieval": {
                "collections": ["repo"],
                "budgets": {"tokens": 500},
            }
        },
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
    )
    branch = SimpleNamespace(
        branch_id="branch-1",
        workflow_id="source-workflow",
        root_workflow_id="source-workflow",
        source_run_id="source-run",
        logical_step_id="implement",
        source_execution_ordinal=1,
        source_checkpoint_boundary="after_execution",
        source_checkpoint_ref="artifact://source-checkpoint",
        source_checkpoint_digest="sha256:" + "a" * 64,
        source_state_kind=None,
        source_state_ref=None,
        source_state_digest=None,
        parent_branch_id=None,
        parent_turn_id=None,
        current_head_version=1,
        current_head_checkpoint_ref="artifact://source-checkpoint",
        workspace_policy="apply_previous_execution_diff_to_clean_baseline",
        runtime_context_policy="fresh_agent_run",
        git_repository="MoonLadderStudios/MoonMind",
        git_base_branch="main",
        git_base_commit="abc123",
        git_work_branch="mm/source/branch-1",
        diagnostics={
            "runtimeSelection": {
                "providerProfileRef": "profile-1",
                "executionProfileRef": "profile-1",
                "model": "gpt-5.4",
                "effort": "high",
                "publishMode": "none",
            }
        },
    )
    binding = SimpleNamespace(
        repository="MoonLadderStudios/MoonMind",
        base_branch="main",
        base_commit="abc123",
        work_branch="mm/source/branch-1",
    )
    source = SimpleNamespace(
        namespace="default", workflow_id="source-workflow", run_id="source-run"
    )
    omnigent = SimpleNamespace(
        idempotency_key="source-message",
        launch_policy_ref="policy-1@1",
        source_branch="main",
        publication_state="none",
        provider_profile_id="profile-1",
        execution_profile_ref="profile-1",
        baseline_commit="abc123",
        model_dump=lambda **_kwargs: {
            "schemaVersion": "v2",
            "providerProfileId": "profile-1",
            "executionProfileRef": "profile-1",
            "launchPolicyRef": "policy-1@1",
        },
    )
    checkpoint = SimpleNamespace(omnigent=omnigent)
    profile = SimpleNamespace(
        profile_id="profile-1",
        runtime_id="codex_cli",
        default_model="gpt-5.4",
        default_effort="high",
    )
    policy = {"boundaries": {"execution": {"profileRef": "profile-1"}}}
    return branch, turn, binding, source, checkpoint, profile, policy


def _valid_source_checkpoint() -> tuple[bytes, bytes]:
    instruction = b"Implement the isolated branch repair."
    checkpoint = StepExecutionCheckpointModel(
        checkpointId=(
            "source-workflow:source-run:implement:execution:1:"
            "checkpoint:after_execution"
        ),
        boundary="after_execution",
        source={
            "workflowId": "source-workflow",
            "runId": "source-run",
            "logicalStepId": "implement",
            "executionOrdinal": 1,
        },
        taskInputSnapshotRef="artifact://instruction",
        planDigest="sha256:" + "d" * 64,
        workspace={"kind": "git_commit", "headCommit": "def456"},
        omnigentCheckpoint={
            "workflowId": "source-workflow",
            "runId": "source-run",
            "logicalStepId": "implement",
            "stepExecutionId": "source-step-execution-1",
            "attemptOrdinal": 1,
            "boundary": "after_execution",
            "providerProfileId": "profile-1",
            "credentialRef": "credential://profile-1",
            "credentialGeneration": 1,
            "hostBindingRef": "omnigent-oauth:profile-1",
            "endpointRef": "default",
            "bridgeSessionId": "source-bridge-1",
            "omnigentSessionId": "source-provider-session-1",
            "externalStateRef": "artifact://external-state",
            "externalStateDigest": "sha256:" + "e" * 64,
            "idempotencyKey": "source-message-1",
            "executionProfileRef": "profile-1",
            "launchPolicyRef": "policy-1@1",
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": "source-workspace-1",
                "relativePath": "repo",
            },
            "baselineCommit": "abc123",
            "headCommit": "def456",
            "headRef": "artifact://head",
            "headDigest": "sha256:" + "f" * 64,
            "workspaceCheckpointRef": "artifact://workspace",
            "workspaceCheckpointDigest": "sha256:" + "0" * 64,
            "sourceBranch": "main",
            "publicationState": "none",
            "capturedAt": "2026-08-12T00:00:00Z",
            "producerVersion": "moonmind-test",
            "validation": {
                "valid": True,
                "liveReattachAvailable": False,
                "workspaceColdRestoreAvailable": True,
                "branchCreationAvailable": True,
            },
        },
        createdAt="2026-08-12T00:00:00Z",
    )
    return (
        checkpoint.model_dump_json(by_alias=True, exclude_none=True).encode(),
        instruction,
    )


@pytest.mark.asyncio
async def test_canonical_coordinator_compiles_fresh_branch_restore_authority() -> None:
    checkpoint_model = StepExecutionCheckpointModel.model_validate_json(
        _valid_source_checkpoint()[0]
    )
    assert checkpoint_model.omnigent is not None
    checkpoint = checkpoint_model.omnigent
    coordinator = OmnigentProfileBoundExecutionCoordinator(
        session_factory=SimpleNamespace(),
        lease_client=SimpleNamespace(),
        host_repository=SimpleNamespace(),
        host_runtime=SimpleNamespace(),
        run_store=SimpleNamespace(),
        execution_runner=AsyncMock(),
        artifact_gateway=SimpleNamespace(),
    )
    coordinator.execute = AsyncMock(  # type: ignore[method-assign]
        return_value=AgentRunResult(summary="branch completed")
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="profile-1",
        correlationId="branch-turn-1",
        idempotencyKey="branch-message-1",
        inputRefs=["artifact://branch-instruction"],
        workspaceSpec={
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": "branch-workspace-1",
                "relativePath": "repo",
            },
            "repository": "MoonLadderStudios/MoonMind",
            "startingBranch": "main",
            "targetBranch": "mm/source/branch-1",
        },
    )
    candidate = CandidateWorkspaceAuthority(
        loopId="source-workflow:implement",
        attemptOrdinal=1,
        headRef=checkpoint.head_ref,
        headDigest=checkpoint.head_digest,
        checkpointRef=checkpoint.workspace_checkpoint_ref,
        checkpointDigest=checkpoint.workspace_checkpoint_digest,
    )

    result = await coordinator.branch_from_checkpoint(
        request=request,
        checkpoint=checkpoint,
        current_credential_generation=1,
        candidate_workspace=candidate,
    )

    assert result.summary == "branch completed"
    dispatched = coordinator.execute.await_args.args[0]
    assert dispatched.idempotency_key == "branch-message-1"
    assert dispatched.idempotency_key != checkpoint.idempotency_key
    assert dispatched.parameters["checkpointRestore"]["mode"] == "branch"
    assert dispatched.parameters["checkpointRestore"]["sourceBridgeSessionId"] == (
        "source-bridge-1"
    )
    assert "source-provider-session-1" not in json.dumps(
        dispatched.model_dump(by_alias=True, mode="json", exclude_none=True)
    )
    assert checkpoint.external_state_ref in dispatched.input_refs
    assert candidate.head_ref in dispatched.input_refs
    assert candidate.checkpoint_ref in dispatched.input_refs


@pytest.mark.asyncio
async def test_profile_bound_coordinator_rejects_unenforceable_usd_budget_before_mutation(
) -> None:
    run_store = SimpleNamespace(get_or_create=AsyncMock())
    coordinator = OmnigentProfileBoundExecutionCoordinator(
        session_factory=SimpleNamespace(),
        lease_client=SimpleNamespace(),
        host_repository=SimpleNamespace(),
        host_runtime=SimpleNamespace(),
        run_store=run_store,
        execution_runner=AsyncMock(),
        artifact_gateway=SimpleNamespace(),
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="profile-1",
        correlationId="budgeted-turn",
        idempotencyKey="budgeted-turn-1",
        parameters={"maxBudgetUsd": 2.5},
    )

    result = await coordinator.execute(request)

    assert result.failure_class == "user_error"
    assert result.provider_error_code == "OMNIGENT_MAX_BUDGET_ENFORCEMENT_UNAVAILABLE"
    run_store.get_or_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_allocates_and_claims_canonical_omnigent_request_before_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()
    client = SimpleNamespace()
    owner = CheckpointBranchTurnExecutionOwner(
        session,  # type: ignore[arg-type]
        principal="service:test",
        client=client,  # type: ignore[arg-type]
        artifact_service=SimpleNamespace(),  # type: ignore[arg-type]
    )
    branch, turn, binding, source, checkpoint, profile, policy = _authority_graph()
    branch.diagnostics["runtimeSelection"]["agentProfileSnapshot"] = {
        "profileId": "agent-profile-1",
        "version": 3,
        "digest": "sha256:agent-profile-v3",
        "providerProfileRef": "profile-1",
        "executionProfileRef": "profile-1",
        "launchPolicyRef": "policy-1@1",
        "agentId": "stock-codex",
        "document": {
            "model": {"model": "profile-model", "effort": "medium"},
            "rag": {"collections": ["repo"]},
        },
    }
    turn.diagnostics["remediationContextRef"] = "artifact://remediation/context"
    turn.diagnostics["maxBudgetUsd"] = 2.5
    owner._load_graph_authority = AsyncMock(  # type: ignore[method-assign]
        return_value=(branch, turn, binding, source)
    )
    owner._validate_source_authority = AsyncMock(  # type: ignore[method-assign]
        return_value=(checkpoint, profile, policy)
    )
    owner._validated_remediation_context_ref = AsyncMock(  # type: ignore[method-assign]
        return_value="artifact://remediation/context"
    )
    written: dict[str, bytes] = {}

    async def _write_artifact(*, content_type, payload, kind, branch_turn_id):
        assert content_type
        assert branch_turn_id == "turn-1"
        written[kind] = payload
        return f"artifact://test/{kind}"

    events: list[str] = []

    async def _claim(_service, **kwargs):
        events.append("claim")
        turn.created_step_execution_id = kwargs["created_step_execution_id"]
        turn.runtime_agent_run_id = kwargs["runtime_agent_run_id"]
        turn.context_bundle_ref = kwargs["context_bundle_ref"]
        turn.step_execution_manifest_ref = kwargs["step_execution_manifest_ref"]
        turn.diagnostics = {
            **turn.diagnostics,
            "executionWorkflowId": kwargs["execution_workflow_id"],
            "operatorLaunchIdempotencyKey": "operator-launch-1",
        }
        return turn

    async def _start(**_kwargs):
        events.append("start")

    owner._write_artifact = _write_artifact  # type: ignore[method-assign]
    owner._start_claimed_turn = _start  # type: ignore[method-assign]
    monkeypatch.setattr(CheckpointBranchService, "claim_turn_execution", _claim)

    launched = await owner.launch(
        workflow_id="source-workflow",
        branch_id="branch-1",
        branch_turn_id="turn-1",
        intent={"idempotencyKey": "operator-launch-1"},
    )

    assert events == ["claim", "start"]
    assert session.commit.await_count == 1
    assert launched.created_step_execution_id == (
        "checkpoint-branch-turn:turn-1:branch-turn-turn-1:implement:execution:1"
    )
    manifest = json.loads(
        written["output.branch_turn.step_execution_manifest.json"]
    )
    request = manifest["agentExecutionRequest"]
    assert (request["agentKind"], request["agentId"]) == ("external", "omnigent")
    assert request["executionProfileRef"] == "profile-1"
    assert request["checkpointRecovery"]["recoveryAction"] == "branch_required"
    assert request["stepExecution"]["runtimeContextPolicy"] == "fresh_agent_run"
    assert request["stepExecution"]["runtimeSessionReset"] == {
        "mode": "new_agent_run",
        "sourceProviderSessionReused": False,
        "sourceOAuthLeaseReused": False,
    }
    assert request["parameters"]["followUpRetrieval"]["collections"] == ["repo"]
    assert request["parameters"]["followUpRetrieval"]["maxContextTokens"] == 500
    assert request["parameters"]["model"] == "profile-model"
    assert request["parameters"]["effort"] == "medium"
    assert request["parameters"]["maxBudgetUsd"] == 2.5
    assert request["parameters"]["agentProfile"]["profileId"] == "agent-profile-1"
    assert request["parameters"]["omnigent"]["executionTargetRef"] == "profile-1"
    assert "artifact://remediation/context" in request["inputRefs"]
    assert "artifact://remediation/context" in request["stepExecution"][
        "preparedInputRefs"
    ]
    context = json.loads(written["runtime.branch_turn.context_bundle.json"])
    assert context["remediationContextRef"] == "artifact://remediation/context"
    assert request["workspaceSpec"]["targetBranch"] == "mm/source/branch-1"

    await owner.launch(
        workflow_id="source-workflow",
        branch_id="branch-1",
        branch_turn_id="turn-1",
        intent={"idempotencyKey": "authorized-recovery-operation"},
    )
    assert events == ["claim", "start", "start"]
    assert owner._validate_source_authority.await_count == 1


@pytest.mark.asyncio
async def test_owner_validates_turn_owned_remediation_context_binding() -> None:
    record = SimpleNamespace(artifact_ref="artifact://remediation/context")
    result = SimpleNamespace(scalar_one_or_none=lambda: record)
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    owner = CheckpointBranchTurnExecutionOwner(
        session,  # type: ignore[arg-type]
        principal="service:test",
        client=SimpleNamespace(),  # type: ignore[arg-type]
        artifact_service=SimpleNamespace(),  # type: ignore[arg-type]
    )
    owner._read_ref = AsyncMock(return_value=b"{}")  # type: ignore[method-assign]
    turn = SimpleNamespace(
        branch_id="branch-1",
        branch_turn_id="turn-1",
        diagnostics={"remediationContextRef": "artifact://remediation/context"},
    )

    ref = await owner._validated_remediation_context_ref(turn)

    assert ref == "artifact://remediation/context"
    owner._read_ref.assert_awaited_once_with(
        "artifact://remediation/context",
        field_name="remediationContextRef",
    )


@pytest.mark.asyncio
async def test_owner_rejects_stale_authority_before_claim_or_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()
    owner = CheckpointBranchTurnExecutionOwner(
        session,  # type: ignore[arg-type]
        principal="service:test",
        client=SimpleNamespace(),  # type: ignore[arg-type]
        artifact_service=SimpleNamespace(),  # type: ignore[arg-type]
    )
    branch, turn, binding, source, *_rest = _authority_graph()
    owner._load_graph_authority = AsyncMock(  # type: ignore[method-assign]
        return_value=(branch, turn, binding, source)
    )
    owner._validate_source_authority = AsyncMock(  # type: ignore[method-assign]
        side_effect=CheckpointBranchTurnLaunchError(
            "credential_generation_changed", "credential generation changed"
        )
    )
    claim = AsyncMock()
    start = AsyncMock()
    monkeypatch.setattr(CheckpointBranchService, "claim_turn_execution", claim)
    owner._start_claimed_turn = start  # type: ignore[method-assign]

    with pytest.raises(CheckpointBranchTurnLaunchError) as exc_info:
        await owner.launch(
            workflow_id="source-workflow",
            branch_id="branch-1",
            branch_turn_id="turn-1",
            intent={"idempotencyKey": "operator-launch-1"},
        )

    assert exc_info.value.code == "credential_generation_changed"
    claim.assert_not_awaited()
    start.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_current_write_contracts_forbid_runtime_authority_fields() -> None:
    forbidden = {
        "createdStepExecutionId": "caller-step",
        "runtimeAgentRunId": "caller-run",
        "providerSessionId": "caller-session",
        "diagnosticsRef": "artifact://caller/diagnostics",
    }
    for field, value in forbidden.items():
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            CheckpointBranchTurnLaunchRequest.model_validate(
                {"idempotencyKey": "launch-1", field: value}
            )
        for model, payload in (
            (
                CheckpointBranchContinueModel,
                {
                    "instructionRef": "artifact://instruction",
                    "instructionDigest": "sha256:" + "a" * 64,
                    "idempotencyKey": "continue-1",
                    field: value,
                },
            ),
            (
                CheckpointBranchForkModel,
                {
                    "branchId": "child-1",
                    "instructionRef": "artifact://instruction",
                    "instructionDigest": "sha256:" + "a" * 64,
                    "idempotencyKey": "fork-1",
                    field: value,
                },
            ),
        ):
            with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
                model.model_validate(payload)


def test_branch_turn_execution_identity_is_replay_stable_and_turn_scoped() -> None:
    first = build_branch_turn_execution_identity(
        branch_id="branch-1",
        branch_turn_id="turn-1",
        logical_step_id="implement",
        ordinal=2,
    )
    replay = build_branch_turn_execution_identity(
        branch_id="branch-1",
        branch_turn_id="turn-1",
        logical_step_id="implement",
        ordinal=2,
    )
    next_turn = build_branch_turn_execution_identity(
        branch_id="branch-1",
        branch_turn_id="turn-2",
        logical_step_id="implement",
        ordinal=3,
    )

    assert replay == first
    assert next_turn.step_execution_id != first.step_execution_id
    assert next_turn.agent_run_workflow_id != first.agent_run_workflow_id


def test_branch_turn_execution_identity_is_bounded_for_maximum_logical_step() -> None:
    logical_step_id = "logical-step-" + "x" * 242

    identity = build_branch_turn_execution_identity(
        branch_id="branch-1",
        branch_turn_id="cbt-01k2yxt6xrxe5e0mkmx1zwcm6n",
        logical_step_id=logical_step_id,
        ordinal=123,
    )
    replay = build_branch_turn_execution_identity(
        branch_id="branch-1",
        branch_turn_id="cbt-01k2yxt6xrxe5e0mkmx1zwcm6n",
        logical_step_id=logical_step_id,
        ordinal=123,
    )

    assert identity == replay
    assert len(identity.step_execution_id) <= 255
    assert identity.step_execution_id.endswith(":execution:123")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (
            lambda branch, turn, binding, source: setattr(
                source, "run_id", "new-run"
            ),
            "source_run_stale",
        ),
        (
            lambda branch, turn, binding, source: setattr(
                branch, "root_workflow_id", "other-workflow"
            ),
            "root_workflow_mismatch",
        ),
        (
            lambda branch, turn, binding, source: setattr(
                branch, "current_head_version", 2
            ),
            "branch_head_stale",
        ),
        (
            lambda branch, turn, binding, source: setattr(
                branch, "current_head_checkpoint_ref", "artifact://other"
            ),
            "branch_head_checkpoint_changed",
        ),
        (
            lambda branch, turn, binding, source: setattr(
                branch, "workspace_policy", "clean_checkout"
            ),
            "workspace_policy_changed",
        ),
        (
            lambda branch, turn, binding, source: setattr(
                branch, "runtime_context_policy", "reuse_session_same_epoch"
            ),
            "runtime_context_policy_changed",
        ),
        (
            lambda branch, turn, binding, source: (
                setattr(branch, "runtime_context_policy", "reuse_session_same_epoch"),
                setattr(turn, "runtime_context_policy", "reuse_session_same_epoch"),
            ),
            "runtime_context_policy_unsupported",
        ),
        (
            lambda branch, turn, binding, source: setattr(
                branch, "source_state_kind", "external_state_ref"
            ),
            "source_state_authority_changed",
        ),
        (
            lambda branch, turn, binding, source: (
                setattr(branch, "source_state_kind", "external_state_ref"),
                setattr(turn, "source_state_kind", "external_state_ref"),
            ),
            "source_state_authority_invalid",
        ),
        (
            lambda branch, turn, binding, source: setattr(
                binding, "work_branch", "main"
            ),
            "git_binding_not_isolated",
        ),
        (
            lambda branch, turn, binding, source: setattr(
                branch, "git_repository", "other/repo"
            ),
            "git_binding_changed",
        ),
    ],
)
async def test_owner_rejects_stored_authority_mismatches_before_artifact_or_runtime(
    monkeypatch: pytest.MonkeyPatch,
    mutation,
    code: str,
) -> None:
    session = _Session()
    owner = CheckpointBranchTurnExecutionOwner(
        session,  # type: ignore[arg-type]
        principal="service:test",
        client=SimpleNamespace(),  # type: ignore[arg-type]
        artifact_service=SimpleNamespace(),  # type: ignore[arg-type]
    )
    branch, turn, binding, source, *_rest = _authority_graph()
    mutation(branch, turn, binding, source)
    owner._load_graph_authority = AsyncMock(  # type: ignore[method-assign]
        return_value=(branch, turn, binding, source)
    )
    owner._read_ref = AsyncMock()  # type: ignore[method-assign]
    start = AsyncMock()
    claim = AsyncMock()
    owner._start_claimed_turn = start  # type: ignore[method-assign]
    monkeypatch.setattr(CheckpointBranchService, "claim_turn_execution", claim)

    with pytest.raises(CheckpointBranchTurnLaunchError) as exc_info:
        await owner.launch(
            workflow_id="source-workflow",
            branch_id="branch-1",
            branch_turn_id="turn-1",
            intent={
                "idempotencyKey": "operator-launch-1",
                "expectedBranchHeadVersion": 1,
            },
        )

    assert exc_info.value.code == code
    owner._read_ref.assert_not_awaited()
    claim.assert_not_awaited()
    start.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("checkpoint_schema", "checkpoint_schema_invalid"),
        ("checkpoint_digest", "checkpoint_digest_mismatch"),
        ("checkpoint_lineage", "checkpoint_lineage_mismatch"),
        ("checkpoint_boundary", "checkpoint_boundary_mismatch"),
        ("instruction_digest", "instruction_digest_mismatch"),
        ("model", "model_selection_invalid"),
        ("effort", "effort_selection_invalid"),
        ("publish", "publish_intent_unsupported"),
        ("launch_policy", "launch_policy_mismatch"),
        ("provider_profile", "provider_profile_mismatch"),
        ("execution_profile", "execution_profile_mismatch"),
        ("credential_generation", "credential_generation_changed"),
        ("profile_readiness", "provider_profile_not_ready"),
        ("repository_baseline", "repository_baseline_mismatch"),
        ("retrieval", "retrieval_authority_invalid"),
    ],
)
async def test_owner_rejects_complete_authority_mismatch_matrix_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    code: str,
) -> None:
    """Drive stored authority mismatches through the real launch validator."""

    session = _Session()
    owner = CheckpointBranchTurnExecutionOwner(
        session,  # type: ignore[arg-type]
        principal="service:test",
        client=SimpleNamespace(),  # type: ignore[arg-type]
        artifact_service=SimpleNamespace(),  # type: ignore[arg-type]
    )
    branch, turn, binding, source, *_rest = _authority_graph()
    checkpoint_bytes, instruction_bytes = _valid_source_checkpoint()
    if case == "checkpoint_schema":
        checkpoint_bytes = b"{}"
    elif case == "checkpoint_lineage":
        checkpoint_payload = json.loads(checkpoint_bytes)
        checkpoint_payload["source"]["workflowId"] = "other-workflow"
        checkpoint_payload["omnigentCheckpoint"]["workflowId"] = "other-workflow"
        checkpoint_payload["checkpointId"] = (
            "other-workflow:source-run:implement:execution:1:"
            "checkpoint:after_execution"
        )
        checkpoint_bytes = json.dumps(checkpoint_payload).encode()
    elif case == "checkpoint_boundary":
        checkpoint_payload = json.loads(checkpoint_bytes)
        checkpoint_payload["boundary"] = "before_execution"
        checkpoint_payload["omnigentCheckpoint"]["boundary"] = "before_execution"
        checkpoint_payload["checkpointId"] = (
            "source-workflow:source-run:implement:execution:1:"
            "checkpoint:before_execution"
        )
        checkpoint_bytes = json.dumps(checkpoint_payload).encode()

    checkpoint_digest = "sha256:" + hashlib.sha256(checkpoint_bytes).hexdigest()
    instruction_digest = "sha256:" + hashlib.sha256(instruction_bytes).hexdigest()
    if case != "checkpoint_digest":
        turn.source_checkpoint_digest = checkpoint_digest
        branch.source_checkpoint_digest = checkpoint_digest
    if case != "instruction_digest":
        turn.instruction_digest = instruction_digest

    selection = dict(branch.diagnostics["runtimeSelection"])
    if case == "model":
        selection["model"] = ""
    elif case == "effort":
        selection["effort"] = 7
    elif case == "publish":
        selection["publishMode"] = "force_merge"
    elif case == "launch_policy":
        selection["launchPolicyRef"] = "policy-other@1"
    elif case == "provider_profile":
        selection["providerProfileRef"] = "profile-other"
    elif case == "execution_profile":
        selection["executionProfileRef"] = "profile-other"
    elif case == "credential_generation":
        session.profile.credential_generation = 2
    elif case == "profile_readiness":
        session.profile.enabled = False
    elif case == "repository_baseline":
        binding.base_commit = "changed-baseline"
        branch.git_base_commit = "changed-baseline"
    elif case == "retrieval":
        turn.diagnostics["followUpRetrieval"] = ["not", "a", "mapping"]
    branch.diagnostics["runtimeSelection"] = selection

    async def _read_ref(ref: str, *, field_name: str) -> bytes:
        assert field_name in {"sourceCheckpointRef", "instructionRef"}
        if ref == turn.source_checkpoint_ref:
            return checkpoint_bytes
        if ref == turn.instruction_ref:
            return instruction_bytes
        raise AssertionError(f"unexpected authority ref {ref}")

    owner._load_graph_authority = AsyncMock(  # type: ignore[method-assign]
        return_value=(branch, turn, binding, source)
    )
    owner._read_ref = _read_ref  # type: ignore[method-assign]
    owner._write_artifact = AsyncMock()  # type: ignore[method-assign]
    start = AsyncMock()
    claim = AsyncMock()
    owner._start_claimed_turn = start  # type: ignore[method-assign]
    monkeypatch.setattr(CheckpointBranchService, "claim_turn_execution", claim)

    with pytest.raises(CheckpointBranchTurnLaunchError) as exc_info:
        await owner.launch(
            workflow_id="source-workflow",
            branch_id="branch-1",
            branch_turn_id="turn-1",
            intent={
                "idempotencyKey": "operator-launch-1",
                "expectedBranchHeadVersion": 1,
            },
        )

    assert exc_info.value.code == code
    owner._write_artifact.assert_not_awaited()
    claim.assert_not_awaited()
    start.assert_not_awaited()
