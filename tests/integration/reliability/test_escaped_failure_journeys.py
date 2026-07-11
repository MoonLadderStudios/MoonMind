from __future__ import annotations

import json
from pathlib import Path

import pytest

from moonmind.schemas.managed_session_models import SendCodexManagedSessionTurnRequest
from moonmind.schemas.workspace_locator_models import WorkspaceLocatorResolutionError
from moonmind.workflows.adapters.codex_session_adapter import (
    CodexSessionRunFailedError,
    _pr_resolver_terminal_contract,
)
from moonmind.workflows.temporal.activity_runtime import TemporalSandboxActivities
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
