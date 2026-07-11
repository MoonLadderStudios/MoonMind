from __future__ import annotations

import json
from pathlib import Path

import pytest

from moonmind.schemas.managed_session_models import SendCodexManagedSessionTurnRequest
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.adapters.codex_session_adapter import (
    CodexSessionRunFailedError,
    _pr_resolver_terminal_contract,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
    TemporalSandboxActivities,
)
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.terminal_evidence import evaluate_terminal_evidence
from tests.integration.reliability.helpers import NestedYieldProcess, load_replay
from tests.unit.workflows.adapters.test_codex_session_adapter import (
    _binding,
    _pr_resolver_request,
    _terminal_contract_test_adapter,
    _turn_response,
)


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.integration_ci,
    pytest.mark.reliability_journey,
]


async def test_completed_batch_turn_without_fanout_evidence_fails() -> None:
    replay_id = "batch-workflows-missing-fanout-evidence"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    evaluation = evaluate_terminal_evidence(
        manifest["terminalContract"], workspace_path=manifest["workspacePath"]
    )
    assert manifest["agentTurn"]["status"] == "completed"
    assert manifest["postRecords"] == []
    assert evaluation.satisfied is False
    assert evaluation.failure_code == expected["failureCode"]
    assert expected["parentState"] == "failed"


async def test_completed_batch_turn_is_rejected_at_agent_run_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay the escaped MM-1201 journey through AgentRun's authority handoff."""
    replay_id = "batch-workflows-missing-fanout-evidence"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    agent_run = MoonMindAgentRun()

    async def execute_activity(name: str, payload: dict, **_kwargs: object) -> dict:
        assert name == "agent_runtime.evaluate_terminal_evidence"
        activities = TemporalAgentRuntimeActivities(client_adapter=object())
        evaluated = await activities.agent_runtime_evaluate_terminal_evidence(payload)
        return evaluated.model_dump(mode="json", by_alias=True)

    monkeypatch.setattr(agent_run, "_execute_routed_activity", execute_activity)
    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        correlationId="mm-1201",
        idempotencyKey="mm-1201:replay",
        workspaceSpec={"workspacePath": manifest["workspacePath"]},
        terminalContract=manifest["terminalContract"],
    )
    provider_result = AgentRunResult(
        summary=manifest["agentTurn"]["assistantText"],
        metadata={"workspacePath": manifest["workspacePath"]},
    )

    result = await agent_run._evaluate_terminal_contract(
        request=request, result=provider_result
    )

    assert result.failure_class == expected["failureClass"]
    assert result.metadata["failureCode"] == expected["failureCode"]
    assert result.metadata["terminalContractMissingEvidence"] == expected["missingEvidence"]
    assert result.metadata["terminalContractAuthority"] == "MoonMind.AgentRun"


def _materialize_workspace_fixture(replay_id: str, workspace: Path) -> None:
    manifest = load_replay(replay_id, "workspace-manifest.json")
    for item in manifest["artifacts"]:
        target = workspace / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(item["content"]), encoding="utf-8")


async def test_nested_yield_attempts_remain_non_terminal(tmp_path: Path) -> None:
    replay_id = "incomplete-terminal-contract"
    expected = load_replay(replay_id, "expected-outcome.json")
    process = NestedYieldProcess("inner-shell-3145")
    workspace = tmp_path / "repo"
    _materialize_workspace_fixture(replay_id, workspace)

    first_yield = process.first_tool_yield()
    wrapper_result = process.wrapper_completes()
    satisfied, missing, metadata = _pr_resolver_terminal_contract(str(workspace))

    assert first_yield == {"session_id": "inner-shell-3145", "status": "running"}
    assert wrapper_result["status"] == "completed"
    assert process.inner_active is True, "wrapper completion terminated inner process"
    assert satisfied is False, "attempt artifact incorrectly became terminal evidence"
    assert missing == expected["missingEvidence"]
    assert metadata["prResolverLatestAttempt"]["attemptCount"] == 2
    requests: list[SendCodexManagedSessionTurnRequest] = []

    async def send_turn(
        request: SendCodexManagedSessionTurnRequest,
    ) -> object:
        requests.append(request)
        return _turn_response(
            session_id=request.session_id,
            session_epoch=request.session_epoch,
            container_id=request.container_id,
            thread_id=request.thread_id,
            turn_id=f"turn-{len(requests)}",
        )

    binding = _binding()
    adapter = _terminal_contract_test_adapter(tmp_path, send_turn=send_turn)
    with pytest.raises(CodexSessionRunFailedError) as exc_info:
        await adapter.start(_pr_resolver_request(binding, workspace))

    result = exc_info.value.agent_run_result
    assert result.failure_class == "execution_error"
    assert result.metadata["failureCode"] == expected["failureCode"]
    assert result.metadata["terminalContractContinuationCount"] == 2
    assert len(requests) == 3
    assert {
        (item.session_id, item.session_epoch, item.thread_id) for item in requests
    } == {(binding.session_id, binding.session_epoch, "thread-terminal-contract")}


async def test_managed_workspace_uses_checkpoint_resolver_and_fault_is_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replay_id = "managed-workspace-checkpoint-routing"
    expected = load_replay(replay_id, "expected-outcome.json")
    workspace_root = tmp_path / "sandbox-root"
    repo = workspace_root / "temporal_sandbox" / "run-3145" / "repo"
    repo.mkdir(parents=True)
    activities = TemporalSandboxActivities(
        workspace_root=workspace_root,
    )
    assert activities._resolve_workspace(repo, must_exist=True) == repo

    durable_execution_result = load_replay(replay_id, "execution-result.json")
    original_capture = activities._capture_workspace_evidence
    calls = 0

    async def fail_once(model: object, workspace: Path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("injected finalization failure")
        return await original_capture(model, workspace)

    monkeypatch.setattr(activities, "_capture_workspace_evidence", fail_once)
    payload = {
        "identity": {
            "workflowId": "wf-reliability-3145",
            "runId": "run-3145",
            "logicalStepId": "implement",
            "executionOrdinal": 1,
        },
        "boundary": "after_execution",
        "kind": "worktree_archive",
        "workspacePath": str(repo),
        "artifactNamespace": "checkpoint",
        "idempotencyKey": "reliability-3145-after-execution",
    }
    first = await activities.workspace_capture_checkpoint(payload)
    assert first["status"] == "invalid"
    assert durable_execution_result["status"] == "completed"
    second = await activities.workspace_capture_checkpoint(payload)
    assert second["status"] == "captured"
    assert durable_execution_result["status"] == "completed"
    assert calls == 2
