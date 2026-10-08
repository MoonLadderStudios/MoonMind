"""A step's publication keeps one logical effect across agent attempts (#4627).

An interrupted Omnigent step is retried as a new Step Execution with its own
idempotency key. The predecessor may already have pushed its candidate (and
its PR may have been created, lost its acknowledgement, or failed). The
successor must reconcile that confirmed effect through the existing publisher
instead of pushing a second candidate branch and opening a second PR.

Real Git (a bare destination with a receive hook) and the real publication
and PR-reconciliation code run here; only GitHub's HTTP API is a fake.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import httpx
import pytest

from moonmind.config.settings import settings
from moonmind.omnigent.workspace_publication import OmnigentWorkspacePublicationService
from moonmind.publish.saved_candidate import publish_pull_request
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.adapters.github_service import GitHubService
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)

REPOSITORY = "example/repository"
WORKFLOW_ID = "mm:workflow-4627"
RUN_ID = "run-1"
LOGICAL_STEP_ID = "tpl:implement:03"


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


class FakeGitHub:
    """GitHub's pulls API over the real bare destination repository."""

    def __init__(self, origin: Path, *, failed_creates: int = 0) -> None:
        self.origin = origin
        self.failed_creates = failed_creates
        self.pulls: list[dict] = []
        self.create_posts = 0

    def _payload(self, pull: dict) -> dict:
        head_sha = git("rev-parse", f"refs/heads/{pull['head']}", cwd=self.origin)
        return {
            "number": pull["number"],
            "html_url": f"https://github.com/{REPOSITORY}/pull/{pull['number']}",
            "state": "open",
            "draft": False,
            "merged_at": None,
            "head": {
                "ref": pull["head"],
                "sha": head_sha,
                "repo": {"full_name": REPOSITORY},
            },
            "base": {"ref": pull["base"], "repo": {"full_name": REPOSITORY}},
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        ref_prefix = f"/repos/{REPOSITORY}/git/ref/heads/"
        if request.url.path.startswith(ref_prefix):
            branch = request.url.path.removeprefix(ref_prefix)
            tip = subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
                cwd=self.origin,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if not tip:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"object": {"sha": tip}})
        assert request.url.path == f"/repos/{REPOSITORY}/pulls"
        if request.method == "GET":
            params = parse_qs(request.url.query.decode())
            head = params.get("head", [""])[0].split(":", 1)[-1]
            return httpx.Response(
                200,
                json=[
                    self._payload(pull)
                    for pull in self.pulls
                    if not head or pull["head"] == head
                ],
            )
        assert request.method == "POST"
        self.create_posts += 1
        if self.failed_creates:
            self.failed_creates -= 1
            return httpx.Response(502, json={"message": "Bad Gateway"})
        data = json.loads(request.content)
        pull = {"number": len(self.pulls) + 1, "head": data["head"], "base": data["base"]}
        self.pulls.append(pull)
        return httpx.Response(201, json=self._payload(pull))


def _request(ordinal: int, workspace_id: str) -> AgentExecutionRequest:
    step_execution_id = f"{WORKFLOW_ID}:{RUN_ID}:{LOGICAL_STEP_ID}:execution:{ordinal}"
    return AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": WORKFLOW_ID,
            # The Run workflow keys each Step Execution's agent request by its
            # own ordinal, so the per-attempt key changes on every successor.
            "idempotencyKey": f"{step_execution_id}:agent_execute",
            "workspaceSpec": {
                "repository": REPOSITORY,
                "startingBranch": "main",
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": workspace_id,
                    "relativePath": "repo",
                },
            },
            "parameters": {"publishMode": "pr"},
            "stepExecution": {
                "workflowId": WORKFLOW_ID,
                "runId": RUN_ID,
                "logicalStepId": LOGICAL_STEP_ID,
                "executionOrdinal": ordinal,
                "stepExecutionId": step_execution_id,
                "runtimeContextPolicy": "fresh_agent_run",
                "reason": "initial_execution" if ordinal == 1 else "runtime_recovered",
            },
        }
    )


def _attempt_workspace(tmp_path: Path, origin: Path, ordinal: int, content: str):
    """A Step Execution's own fresh checkout carrying (restored) work."""

    step_execution_id = f"{WORKFLOW_ID}:{RUN_ID}:{LOGICAL_STEP_ID}:execution:{ordinal}"
    workspace_id = hashlib.sha256(
        f"{WORKFLOW_ID}:{step_execution_id}".encode()
    ).hexdigest()[:24]
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    workspace.parent.mkdir(parents=True)
    git("clone", "--branch", "main", str(origin), str(workspace), cwd=tmp_path)
    SandboxWorkspaceRecordStore(tmp_path).ensure(
        SandboxWorkspaceRecord(workspace_id, WORKFLOW_ID, step_execution_id, "repo")
    )
    (workspace / "work.txt").write_text(content)
    return _request(ordinal, workspace_id), step_execution_id, workspace


async def _publish(publisher, request, step_execution_id):
    return await publisher.publish_request_workspace(
        request=request,
        current_workflow_id=WORKFLOW_ID,
        current_step_execution_id=step_execution_id,
    )


async def _open_pull_request(evidence) -> object:
    # The durable parent owns PR creation through the existing reconciler.
    return await publish_pull_request(
        github=GitHubService(),
        repository=REPOSITORY,
        head_branch=evidence["push_branch"],
        base_branch="main",
        candidate_sha=evidence["push_head_sha"],
        draft=False,
        title="Implement #4627",
        body="Interrupted-step publication",
        token="fixture-credential",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "predecessor_outcome",
    ["pull_request_ack_lost", "pull_request_failed", "push_ack_lost"],
)
@pytest.mark.parametrize("successor_changed", [False, True])
async def test_successor_attempt_reconciles_predecessor_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    predecessor_outcome: str,
    successor_changed: bool,
) -> None:
    monkeypatch.setattr(settings.security, "high_security_mode", False)
    for prefix in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{prefix}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{prefix}_EMAIL", "test@example.invalid")
    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.resolve_github_credential",
        AsyncMock(return_value=SimpleNamespace(token="fixture-credential")),
    )
    origin = tmp_path / "origin.git"
    git("init", "--bare", "--initial-branch=main", str(origin), cwd=tmp_path)
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "--initial-branch=main", cwd=seed)
    (seed / "work.txt").write_text("base\n")
    git("add", ".", cwd=seed)
    git("commit", "-m", "base", cwd=seed)
    git("remote", "add", "origin", str(origin), cwd=seed)
    git("push", "origin", "main", cwd=seed)
    # Count the destination's actual accepted ref updates.
    pushes = tmp_path / "pushes.log"
    hook = origin / "hooks" / "post-receive"
    hook.write_text(f"#!/bin/sh\ncat >> '{pushes}'\n")
    hook.chmod(0o755)

    fake = FakeGitHub(
        origin, failed_creates=1 if predecessor_outcome == "pull_request_failed" else 0
    )
    client = httpx.AsyncClient
    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(fake.handle), **kwargs),
    )
    publisher = OmnigentWorkspacePublicationService(tmp_path)

    # Execution 1 completes its compute and pushes its candidate.
    first_request, first_id, _ = _attempt_workspace(
        tmp_path, origin, 1, "completed work\n"
    )
    first = await _publish(publisher, first_request, first_id)
    assert first["push_status"] == "pushed"
    if predecessor_outcome != "push_ack_lost":
        outcome = await _open_pull_request(first)
        assert outcome.status == (
            "unavailable" if predecessor_outcome == "pull_request_failed" else "created"
        )
    # Its acknowledgement is lost with the host; the Run workflow starts
    # execution 2, which restores the saved work into its own fresh checkout.
    successor_content = "completed work\nplus more\n" if successor_changed else (
        "completed work\n"
    )
    second_request, second_id, second_workspace = _attempt_workspace(
        tmp_path, origin, 2, successor_content
    )
    assert second_request.idempotency_key != first_request.idempotency_key
    second = await _publish(publisher, second_request, second_id)
    outcome = await _open_pull_request(second)

    branches = git("branch", "--format=%(refname:short)", cwd=origin).splitlines()
    assert sorted(branches) == sorted(["main", first["push_branch"]])
    assert second["push_branch"] == first["push_branch"]
    assert second["push_status"] == "pushed"
    assert second["remote_verified"] is True
    assert git("status", "--porcelain", cwd=second_workspace) == ""
    assert (
        git("rev-parse", f"refs/heads/{second['push_branch']}", cwd=origin)
        == second["push_head_sha"]
    )
    assert (
        git("show", f"{second['push_head_sha']}:work.txt", cwd=origin) + "\n"
        == successor_content
    )
    candidate_updates = [
        line
        for line in pushes.read_text().splitlines()
        if line.endswith(f"refs/heads/{first['push_branch']}")
    ]
    if successor_changed:
        # New work replaces the candidate on the same logical branch.
        assert len(candidate_updates) == 2
        assert second["push_head_sha"] != first["push_head_sha"]
    else:
        # The predecessor's confirmed push already carries these bytes.
        assert len(candidate_updates) == 1
        assert second["push_head_sha"] == first["push_head_sha"]
    # Exactly one PR exists for the logical step and it tracks the final head.
    assert len(fake.pulls) == 1
    assert outcome.status in {"created", "adopted"}
    assert outcome.head_sha == second["push_head_sha"]
    assert fake.create_posts == (
        2 if predecessor_outcome == "pull_request_failed" else 1
    )


def test_first_attempt_keeps_its_published_branch_identity() -> None:
    """In-flight first attempts keep the branch name they already published."""

    from moonmind.omnigent.workspace_publication import logical_publication_identity

    first = _request(1, "w1")
    assert logical_publication_identity(first) == first.idempotency_key
    assert logical_publication_identity(_request(3, "w3")) == first.idempotency_key
    # A request outside the Step Execution key shape keeps its own identity.
    other = first.model_copy(update={"idempotency_key": "custom-key"})
    assert logical_publication_identity(other) == "custom-key"
