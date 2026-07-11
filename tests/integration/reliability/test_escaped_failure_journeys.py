from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from moonmind.schemas.managed_session_models import SendCodexManagedSessionTurnRequest
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.schemas.workspace_locator_models import WorkspaceLocatorResolutionError
from moonmind.workflows.adapters.codex_session_adapter import _pr_resolver_terminal_contract
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
    TemporalSandboxActivities,
)
from moonmind.workflows.temporal.workflows import agent_run as agent_run_module
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from moonmind.workflows.terminal_evidence import evaluate_terminal_evidence
from tests.integration.reliability.helpers import NestedYieldProcess, load_replay
from tests.unit.workflows.adapters.test_codex_session_adapter import (
    _binding,
    _pr_resolver_request,
    _terminal_contract_test_adapter,
    _turn_response,
)
from tests.unit.workflows.temporal.workflows.test_run_integration import (
    _finalize_and_capture_summary,
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

    async def execute_activity(name: str, payload: dict, **kwargs: object) -> dict:
        assert name == "agent_runtime.evaluate_terminal_evidence"
        assert kwargs["task_queue"] == "mm.activity.agent_runtime"
        activities = TemporalAgentRuntimeActivities(client_adapter=object())
        evaluated = await activities.agent_runtime_evaluate_terminal_evidence(payload)
        return evaluated.model_dump(mode="json", by_alias=True)

    # Patch only the Temporal SDK handoff. Keep AgentRun's production catalog
    # lookup and route construction in the replay so catalog drift cannot be
    # hidden by a test double.
    monkeypatch.setattr(agent_run_module, "execute_typed_activity", execute_activity)
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

    parent = MoonMindRunWorkflow()
    parent._owner_type = "user"
    parent._owner_id = "mm-1201-replay"
    diagnostic = parent._record_result_failure_diagnostic(
        stage="execute",
        category=result.failure_class,
        source="child_workflow",
        step_id="batch-workflows",
        step_title="batch-workflows",
        message=result.summary,
        child_workflow_id="agent-run-mm-1201",
        terminal_evidence=result.metadata,
    )
    from moonmind.workflows.temporal.workflows import run as run_workflow_module

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime(2026, 7, 11, tzinfo=timezone.utc),
    )
    summary = await _finalize_and_capture_summary(
        monkeypatch,
        parent,
        parameters={"publishMode": "none"},
        status="failed",
        error=diagnostic["message"],
    )

    assert summary["finishOutcome"]["code"] == "FAILED"
    assert summary["failure"]["failureCode"] == expected["failureCode"]
    assert summary["failure"]["terminalContractMissingEvidence"] == expected[
        "missingEvidence"
    ]


async def test_completed_batch_no_op_replays_through_production_activity_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replay the endless WorkflowTaskFailed incident at its routing boundary."""
    manifest = load_replay("agent-run-terminal-evidence-routing", "manifest.json")
    workspace = tmp_path / "repo"
    artifacts = workspace / "artifacts"
    artifacts.mkdir(parents=True)
    targets_bytes = json.dumps(manifest["resolvedTargets"]).encode("utf-8")
    (artifacts / "batch-workflows-targets.json").write_bytes(targets_bytes)
    terminal_evidence = dict(manifest["terminalEvidence"])
    terminal_evidence["targetsSha256"] = hashlib.sha256(targets_bytes).hexdigest()
    (artifacts / "batch-workflows-result.json").write_text(
        json.dumps(terminal_evidence), encoding="utf-8"
    )

    async def execute_activity(name: str, payload: dict, **kwargs: object) -> dict:
        assert name == manifest["activityName"]
        assert kwargs["task_queue"] == manifest["expectedTaskQueue"]
        activities = TemporalAgentRuntimeActivities(client_adapter=object())
        evaluated = await activities.agent_runtime_evaluate_terminal_evidence(payload)
        return evaluated.model_dump(mode="json", by_alias=True)

    monkeypatch.setattr(agent_run_module, "execute_typed_activity", execute_activity)
    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        correlationId=manifest["incidentWorkflowId"],
        idempotencyKey=f"{manifest['incidentWorkflowId']}:replay",
        workspaceSpec={"workspacePath": str(workspace)},
        terminalContract=manifest["terminalContract"],
    )

    result = await MoonMindAgentRun()._evaluate_terminal_contract(
        request=request,
        result=AgentRunResult(
            summary="No child workflows were queued.",
            metadata={"workspacePath": str(workspace)},
        ),
    )

    assert result.failure_class is None
    assert result.metadata["terminalContractId"] == "batch_workflows_fanout.v1"
    assert result.metadata["queuedChildCount"] == 0


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
    handle = await adapter.start(_pr_resolver_request(binding, workspace))

    # Provider adapters translate one runtime turn. AgentRun owns terminal
    # evidence evaluation and any capability-aware bounded continuation.
    assert handle.status == "completed"
    assert len(requests) == 1
    assert {
        (item.session_id, item.session_epoch, item.thread_id) for item in requests
    } == {(binding.session_id, binding.session_epoch, "thread-terminal-contract")}


async def test_sandbox_checkpoint_rejects_managed_workspace_without_resolving_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replay_id = "managed-workspace-checkpoint-routing"
    expected = load_replay(replay_id, "expected-outcome.json")
    activities = TemporalSandboxActivities(workspace_root=tmp_path / "sandbox-root")

    sandbox_calls = 0

    def forbidden_sandbox_resolver(*_args: object, **_kwargs: object) -> Path:
        nonlocal sandbox_calls
        sandbox_calls += 1
        raise AssertionError("managed workspace reached sandbox resolver")

    monkeypatch.setattr(activities, "_resolve_workspace", forbidden_sandbox_resolver)
    payload = {
        "identity": {
            "workflowId": "wf-reliability-3145",
            "runId": "run-3145",
            "logicalStepId": "implement",
            "executionOrdinal": 1,
        },
        "boundary": "after_execution",
        "kind": "worktree_archive",
        "workspaceLocator": {
            "kind": "managed_runtime",
            "runtimeId": "codex",
            "agentRunId": "run-3145",
        },
        "artifactNamespace": "checkpoint",
        "idempotencyKey": "reliability-3145-after-execution",
    }
    with pytest.raises(WorkspaceLocatorResolutionError) as exc_info:
        await activities.workspace_capture_checkpoint(payload)

    assert exc_info.value.code == "WORKSPACE_AUTHORITY_MISMATCH"
    assert sandbox_calls == expected["sandboxResolverCalls"] == 0


async def test_checkpoint_finalization_fault_is_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replay_id = "managed-workspace-checkpoint-routing"
    workspace_root = tmp_path / "workspaces"
    repo = workspace_root / "temporal_sandbox" / "run-3145" / "repo"
    repo.mkdir(parents=True)
    activities = TemporalSandboxActivities(workspace_root=workspace_root)

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
