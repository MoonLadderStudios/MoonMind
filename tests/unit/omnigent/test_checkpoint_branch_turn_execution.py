"""Server-owned launch coverage for MoonLadderStudios/MoonMind#3621."""

from __future__ import annotations

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
from moonmind.schemas.checkpoint_branch_models import (
    CheckpointBranchContinueModel,
    CheckpointBranchForkModel,
    CheckpointBranchTurnLaunchRequest,
)


class _ScalarResult:
    def scalar_one(self) -> int:
        return 1


class _Session:
    def __init__(self) -> None:
        self.commit = AsyncMock()
        self.refresh = AsyncMock()

    async def execute(self, _statement):
        return _ScalarResult()


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
    owner._load_graph_authority = AsyncMock(  # type: ignore[method-assign]
        return_value=(branch, turn, binding, source)
    )
    owner._validate_source_authority = AsyncMock(  # type: ignore[method-assign]
        return_value=(checkpoint, profile, policy)
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
