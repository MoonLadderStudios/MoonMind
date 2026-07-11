from __future__ import annotations

import json
from pathlib import Path

import pytest

from moonmind.workflows.adapters.codex_session_adapter import (
    _INCOMPLETE_TERMINAL_CONTRACT_FAILURE_CODE,
    _MAX_INCOMPLETE_TERMINAL_CONTRACT_CONTINUATIONS,
    _pr_resolver_terminal_contract,
)
from moonmind.workflows.temporal.activity_runtime import TemporalSandboxActivities
from tests.reliability.helpers import FinalizationFault, NestedYieldProcess, load_replay


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
    assert expected["failureCode"] == _INCOMPLETE_TERMINAL_CONTRACT_FAILURE_CODE
    assert _MAX_INCOMPLETE_TERMINAL_CONTRACT_CONTINUATIONS == 2


async def test_managed_workspace_uses_checkpoint_resolver_and_fault_is_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replay_id = "managed-workspace-checkpoint-routing"
    expected = load_replay(replay_id, "expected-outcome.json")
    managed_root = tmp_path / "agent_jobs"
    repo = managed_root / "run-3145" / "repo"
    repo.mkdir(parents=True)
    activities = TemporalSandboxActivities(
        workspace_root=tmp_path / "sandbox-root",
        managed_workspace_root=managed_root,
    )

    sandbox_calls = 0

    def forbidden_sandbox_resolver(*_args: object, **_kwargs: object) -> Path:
        nonlocal sandbox_calls
        sandbox_calls += 1
        raise AssertionError("managed workspace reached sandbox resolver")

    monkeypatch.setattr(activities, "_resolve_workspace", forbidden_sandbox_resolver)
    assert activities._resolve_checkpoint_workspace(repo, must_exist=True) == repo
    assert sandbox_calls == 0
    assert expected["sandboxResolverCalls"] == 0

    fault = FinalizationFault()
    durable_execution_result = load_replay(replay_id, "execution-result.json")
    with pytest.raises(RuntimeError, match="injected finalization failure"):
        await fault.fail_once({"workspacePath": str(repo)})
    assert durable_execution_result["status"] == "completed"
    assert await fault.fail_once({"workspacePath": str(repo)}) == {
        "status": "captured",
        "diagnosticRefs": [],
    }
    assert fault.calls == 2
