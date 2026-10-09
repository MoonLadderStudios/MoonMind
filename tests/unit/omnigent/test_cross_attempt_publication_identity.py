"""One repository effect per logical Step across Step Executions (#4627).

An update can replace the agent runtime after its Step Execution pushed the
candidate branch but before the push (or the review request) was acknowledged.
The successor Step Execution restores the saved workspace and must reconcile
that same branch and review request through the existing publisher, never
publish a second candidate.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.config.settings import settings
from moonmind.omnigent.workspace_publication import (
    OmnigentWorkspacePublicationService,
    logical_step_publication_identity,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)
from moonmind.workflows.temporal.step_executions import (
    step_execution_id,
    step_execution_operation_idempotency_key,
)

WORKFLOW_ID = "mm:workflow-1"
RUN_ID = "run-1"
STEP = "implement"


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _request(ordinal: int) -> AgentExecutionRequest:
    """The request the Run workflow builds for one Step Execution."""

    return AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": WORKFLOW_ID,
            "idempotencyKey": step_execution_operation_idempotency_key(
                workflow_id=WORKFLOW_ID,
                run_id=RUN_ID,
                logical_step_id=STEP,
                execution_ordinal=ordinal,
                operation="agent_execute",
            ),
            "stepExecution": {
                "workflowId": WORKFLOW_ID,
                "runId": RUN_ID,
                "logicalStepId": STEP,
                "executionOrdinal": ordinal,
                "stepExecutionId": step_execution_id(
                    workflow_id=WORKFLOW_ID,
                    run_id=RUN_ID,
                    logical_step_id=STEP,
                    execution_ordinal=ordinal,
                ),
                "runtimeContextPolicy": "fresh_agent_run",
                "reason": "initial_execution" if ordinal == 1 else "runtime_recovered",
            },
        }
    )


def test_successor_execution_keeps_the_logical_step_publication_identity():
    first, successor, later = _request(1), _request(2), _request(5)

    # The first execution keeps its historical identity for in-flight retries.
    assert logical_step_publication_identity(first) == first.idempotency_key
    assert logical_step_publication_identity(successor) == first.idempotency_key
    assert logical_step_publication_identity(later) == first.idempotency_key
    # Distinct logical Steps and unrelated keys keep distinct identities.
    other = _request(2).model_copy(
        update={
            "step_execution": _request(2).step_execution.model_copy(
                update={
                    "logical_step_id": "verify",
                    "step_execution_id": step_execution_id(
                        workflow_id=WORKFLOW_ID,
                        run_id=RUN_ID,
                        logical_step_id="verify",
                        execution_ordinal=2,
                    ),
                }
            )
        }
    )
    assert logical_step_publication_identity(other) == other.idempotency_key
    legacy = successor.model_copy(update={"idempotency_key": "legacy-key"})
    assert logical_step_publication_identity(legacy) == "legacy-key"


def _workspace(root: Path, request: AgentExecutionRequest) -> tuple[dict, Path]:
    step_id = request.step_execution.step_execution_id
    workspace_id = hashlib.sha256(f"{WORKFLOW_ID}:{step_id}".encode()).hexdigest()[:24]
    workspace = root / "temporal_sandbox" / workspace_id / "repo"
    SandboxWorkspaceRecordStore(root).ensure(
        SandboxWorkspaceRecord(workspace_id, WORKFLOW_ID, step_id, "repo")
    )
    locator = {"kind": "sandbox", "workspaceId": workspace_id, "relativePath": "repo"}
    return locator, workspace


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_acknowledgement", ["push", "review_request"])
@pytest.mark.parametrize(
    "logical_identity", [True, False], ids=["logical-step", "previous-ordinal-key"]
)
async def test_successor_reconciles_the_lost_attempts_branch_and_review_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lost_acknowledgement: str,
    logical_identity: bool,
) -> None:
    monkeypatch.setattr(settings.security, "high_security_mode", False)
    for prefix in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{prefix}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{prefix}_EMAIL", "test@example.invalid")
    origin = tmp_path / "origin.git"
    git("init", "--bare", "--initial-branch=main", str(origin), cwd=tmp_path)
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "--initial-branch=main", cwd=seed)
    (seed / "base.txt").write_text("base\n")
    git("add", ".", cwd=seed)
    git("commit", "-m", "base", cwd=seed)
    git("remote", "add", "origin", str(origin), cwd=seed)
    git("push", "origin", "main", cwd=seed)

    first, successor = _request(1), _request(2)
    first_locator, first_workspace = _workspace(tmp_path, first)
    first_workspace.parent.mkdir(parents=True)
    git("clone", "--branch", "main", str(origin), str(first_workspace), cwd=tmp_path)
    (first_workspace / "work.txt").write_text("interrupted attempt's work\n")
    git("add", ".", cwd=first_workspace)
    git("commit", "-m", "agent work", cwd=first_workspace)

    review_lookups: list[str] = []

    async def resolve_review_request(**kwargs):
        review_lookups.append(kwargs["selector"])
        if lost_acknowledgement == "review_request" and len(review_lookups) == 1:
            raise RuntimeError("GitHub API unavailable after the push succeeded")
        return SimpleNamespace(
            resolved=True, pr_url="https://github.com/example/repository/pull/7"
        )

    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.GitHubService."
        "resolve_pull_request_selector",
        AsyncMock(side_effect=resolve_review_request),
    )
    publisher = OmnigentWorkspacePublicationService(tmp_path)

    def identity(request: AgentExecutionRequest) -> str:
        if logical_identity:
            return logical_step_publication_identity(request)
        return request.idempotency_key

    def publish(request, locator):
        return publisher.publish_workspace(
            workspace_locator=locator,
            current_workflow_id=WORKFLOW_ID,
            current_step_execution_id=request.step_execution.step_execution_id,
            publication_identity=identity(request),
            publish_mode="pr",
            base_branch="main",
            repository="example/repository",
            github_token="fixture-credential",
        )

    # The interrupted attempt's push reaches the remote; its acknowledgement
    # (or the review request that follows it) never reaches the Run workflow.
    try:
        await publish(first, first_locator)
    except Exception:
        assert lost_acknowledgement == "review_request"
    pushed = git("for-each-ref", "--format=%(refname:short)", "refs/heads/", cwd=origin)
    assert len([ref for ref in pushed.splitlines() if ref != "main"]) == 1

    # The successor restores the saved bytes into its own workspace and the
    # agent continues the work after the last durable boundary.
    successor_locator, successor_workspace = _workspace(tmp_path, successor)
    successor_workspace.parent.mkdir(parents=True)
    shutil.copytree(first_workspace, successor_workspace)
    shutil.rmtree(first_workspace)
    (successor_workspace / "work.txt").write_text("completed after the update\n")
    git("commit", "-am", "continue after restore", cwd=successor_workspace)
    successor_head = git("rev-parse", "HEAD", cwd=successor_workspace)

    evidence = await publish(successor, successor_locator)

    candidates = [
        ref
        for ref in git(
            "for-each-ref", "--format=%(refname:short)", "refs/heads/", cwd=origin
        ).splitlines()
        if ref != "main"
    ]
    if not logical_identity:
        # The ordinal-bearing key leaves a second, orphaned candidate behind.
        assert len(candidates) == 2
        assert len(set(review_lookups)) == 2
        return
    assert candidates == [evidence["push_branch"]]
    assert git("rev-parse", f"refs/heads/{candidates[0]}", cwd=origin) == successor_head
    assert evidence["pull_request_url"] == (
        "https://github.com/example/repository/pull/7"
    )
    # Every review-request lookup targets the one branch, so the existing
    # selector adopts the same review request instead of creating another.
    assert set(review_lookups) == {evidence["push_branch"]}
    assert len(review_lookups) == 2
