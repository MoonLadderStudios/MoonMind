"""A commit refused for missing identity gets one repaired attempt of its step.

The chain runs on a real repository: the portable ``pr_resolve_commit.py``
helper writes the ``local_commit_failed`` receipt, the managed adapter
validates it, ``MoonMind.UserWorkflow`` decides the step retry, the
managed-session controller's preparation of the reused checkout reapplies
the admitted identity, and the resumed helper commits the preserved staged
work once.
Only the GitHub push/finalize that would follow is synthetic. An Omnigent
attempt runs in its own sandbox, restored without the ignored receipt, so
there the verdict stays final.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import pytest

pytest.importorskip("temporalio")

from moonmind.config.settings import settings
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
    ManagedRunRecord,
)
from moonmind.schemas.managed_session_models import (
    CodexManagedSessionRecord,
    LaunchCodexManagedSessionRequest,
)
from moonmind.workflows.adapters.managed_agent_adapter import ManagedAgentAdapter
from moonmind.workflows.temporal.runtime.managed_session_controller import (
    DockerCodexManagedSessionController,
)
from moonmind.workflows.temporal.runtime.managed_session_store import (
    ManagedSessionStore,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.run import (
    RUN_ADAPTER_RESOLVER_VERDICT_RETRY_DECISION_PATCH,
    RUN_AGENT_RUNTIME_RETRY_CLASSIFICATION_PATCH,
    RUN_CONDITIONAL_REGISTRY_READ_PATCH,
    RUN_EXPLICIT_STEP_RETRY_RECOMMENDATION_PATCH,
    RUN_FAIL_FAST_STEP_FAILURE_SUMMARY_PATCH,
    RUN_OMNIGENT_INTERRUPTED_STEP_WORKSPACE_PATCH,
    RUN_RESOLVER_HOST_REPAIR_RETRY_PATCH,
    MoonMindRunWorkflow,
)
from tests.helpers.publication_identity import recorded_publication_identity

REPO_ROOT = Path(__file__).resolve().parents[5]
PR_RESOLVE_COMMIT = (
    REPO_ROOT / ".agents" / "skills" / "pr-resolver" / "bin" / "pr_resolve_commit.py"
)
ADMITTED = {"name": "Admitted Worker", "email": "admitted@example.invalid"}
PR_URL = "https://github.com/o/r/pull/4486"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _result(repo: Path) -> dict[str, Any]:
    return json.loads(
        (repo / "var" / "pr_resolver" / "result.json").read_text(encoding="utf-8")
    )


class _ResolverWorkspace:
    """One reused checkout and the production owners that act on it."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "agent_jobs"
        self.repo = self.root / "task-1" / "repo"
        self.repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        _git(
            self.repo,
            "-c",
            "user.name=Base",
            "-c",
            "user.email=base@example.test",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "base",
        )
        # Never guess an identity from the host name or passwd entry.
        _git(self.repo, "config", "user.useConfigOnly", "true")
        (self.repo / ".git" / "info" / "exclude").write_text("var/\n", "utf-8")
        result = self.repo / "var" / "pr_resolver" / "result.json"
        result.parent.mkdir(parents=True)
        result.write_text(
            json.dumps(
                {
                    "pr_number": 4486,
                    "pr_url": PR_URL,
                    "status": "blocked",
                    "reason": "actionable_comments",
                    "next_step": "run_full_remediation",
                    "attempt_history": [{"stage": "finalize", "attempt": 1}],
                }
            ),
            encoding="utf-8",
        )
        self.base = _git(self.repo, "rev-parse", "HEAD")
        self.remediations = 0
        self.preparations = 0
        self.run_store = ManagedRunStore(tmp_path / "run_store")
        self.session_store = ManagedSessionStore(tmp_path / "session-store")
        self.session_request = LaunchCodexManagedSessionRequest(
            agentRunId="task-1",
            sessionId="sess-1",
            threadId="logical-thread-1",
            workspacePath=str(self.repo),
            sessionWorkspacePath=str(self.root / "task-1" / "session"),
            artifactSpoolPath=str(self.root / "task-1" / "artifacts"),
            codexHomePath="/tmp/codex-home",
            imageRef="img",
        )
        self.session_store.save(
            CodexManagedSessionRecord(
                sessionId="sess-1",
                sessionEpoch=1,
                agentRunId="task-1",
                containerId="ctr-1",
                threadId="logical-thread-1",
                runtimeId="codex_cli",
                imageRef="img",
                controlUrl="docker-exec://ctr-1",
                status="ready",
                workspacePath=self.session_request.workspace_path,
                sessionWorkspacePath=self.session_request.session_workspace_path,
                artifactSpoolPath=self.session_request.artifact_spool_path,
                startedAt="2026-10-16T12:00:00Z",
            )
        )

    async def prepare(self) -> None:
        """Relaunch the reused session exactly as a new step attempt does."""

        async def _live_container(
            command: tuple[str, ...], **_kwargs: Any
        ) -> tuple[int, str, str]:
            if command[:3] == ("docker", "inspect", "-f"):
                return 0, "ctr-1\n" if "{{.Id}}" in command else "sha256:img\n", ""
            if command == ("docker", "image", "inspect", "-f", "{{.Id}}", "img"):
                return 0, "sha256:img\n", ""
            raise AssertionError(f"unexpected command: {command}")

        controller = DockerCodexManagedSessionController(
            workspace_volume_name="agent_workspaces",
            codex_volume_name="codex_auth_volume",
            workspace_root=str(self.root),
            session_store=self.session_store,
            command_runner=_live_container,
        )
        await controller.launch_session(self.session_request)
        self.preparations += 1

    def remediate(self) -> None:
        """Stand-in for fix-comments: edit and stage exactly the fix."""

        self.remediations += 1
        (self.repo / "fix.py").write_text("print('addressed')\n", encoding="utf-8")
        _git(self.repo, "add", "fix.py")
        (self.repo / "unstaged.txt").write_text("keep me\n", encoding="utf-8")

    def commit(self) -> int:
        return subprocess.run(
            [
                sys.executable,
                str(PR_RESOLVE_COMMIT),
                "--pr",
                "4486",
                "-m",
                "Address PR feedback for #4486",
            ],
            cwd=self.repo,
            capture_output=True,
            text=True,
            check=False,
        ).returncode

    def finalize(self) -> None:
        """Synthetic push + finalize for the verified local head."""

        previous = _result(self.repo)
        (self.repo / "var" / "pr_resolver" / "result.json").write_text(
            json.dumps(
                {
                    "pr_number": previous["pr_number"],
                    "pr_url": previous["pr_url"],
                    "headSha": _git(self.repo, "rev-parse", "HEAD"),
                    "status": "merged",
                    "merge_outcome": "merged",
                    "mergeAutomationDisposition": "merged",
                    "attempt_history": previous["attempt_history"],
                }
            ),
            encoding="utf-8",
        )

    def resumes_commit(self) -> bool:
        """pr-resolver step 1: a receipt with its staged work still present."""

        result = _result(self.repo)
        receipt = result.get("localCommit") or {}
        return (
            result.get("reason") == "local_commit_failed"
            and result.get("pr_number") == 4486
            and receipt.get("stagedTree") == _git(self.repo, "write-tree")
        )

    async def attempt_result(
        self, ordinal: int, started_at: datetime
    ) -> AgentRunResult:
        run_id = f"resolver-attempt-{ordinal}"
        self.run_store.save(
            ManagedRunRecord(
                run_id=run_id,
                agent_id="codex_cli",
                runtime_id="codex_cli",
                status="completed",
                started_at=started_at,
                workspace_path=str(self.repo),
            )
        )
        adapter = ManagedAgentAdapter(
            profile_fetcher=_unused,
            slot_requester=_unused,
            slot_releaser=_unused,
            cooldown_reporter=_unused,
            workflow_id="wf-local-commit-retry",
            run_store=self.run_store,
        )
        return await adapter.fetch_result(run_id, pr_resolver_expected=True)


async def _unused(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("not part of result collection")


async def _immediate_wait_condition(
    predicate: Callable[[], bool], **_kwargs: Any
) -> None:
    assert predicate() is True


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _ResolverWorkspace:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("MOONMIND_STEP_EXECUTION_ID", "step:resolve")
    for name in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "EMAIL",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings.workflow, "git_user_name", None)
    monkeypatch.setattr(settings.workflow, "git_user_email", None)
    recorded_publication_identity(monkeypatch, ADMITTED)
    monkeypatch.setattr(
        "moonmind.omnigent.git_identity.os.chown", lambda *_a, **_k: None
    )
    return _ResolverWorkspace(tmp_path)


def _install_run_harness(
    monkeypatch: pytest.MonkeyPatch,
    workflow: MoonMindRunWorkflow,
    *,
    enabled_patches: set[str],
    child: Callable[[int], Any],
    runtime: str = "codex_cli",
) -> list[AgentExecutionRequest]:
    requests: list[AgentExecutionRequest] = []

    async def fake_execute_typed_activity(
        activity_type: str, payload: Any, **_k: Any
    ) -> Any:
        assert activity_type == "artifact.read"
        return json.dumps(
            {
                "plan_version": "1.0",
                "metadata": {
                    "title": "Resolve PR",
                    "created_at": "2026-10-16T00:00:00Z",
                    "registry_snapshot": {
                        "digest": "reg:sha256:123",
                        "artifact_ref": "art:sha256:456",
                    },
                },
                "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                "nodes": [
                    {
                        "id": "resolve",
                        "tool": {"type": "agent_runtime", "name": runtime},
                        "inputs": {
                            "instructions": "Resolve PR #4486.",
                            "runtime": {"mode": runtime},
                        },
                    }
                ],
                "edges": [],
            }
        ).encode("utf-8")

    async def fake_execute_child_workflow(
        workflow_name: str, request: AgentExecutionRequest, **_k: Any
    ) -> AgentRunResult:
        assert workflow_name == "MoonMind.AgentRun"
        requests.append(request)
        return await child(len(requests))

    async def passthrough(request: AgentExecutionRequest) -> AgentExecutionRequest:
        return request

    async def no_manifest(*_a: Any, **_k: Any) -> None:
        return None

    info = type(
        "WorkflowInfo",
        (),
        {
            "task_queue": "mm.workflow.user.v2",
            "namespace": "default",
            "workflow_id": "wf-local-commit-retry",
            "run_id": "run-local-commit-retry",
            "search_attributes": {},
        },
    )
    module_workflow = run_workflow_module.workflow
    monkeypatch.setattr(module_workflow, "info", info)
    monkeypatch.setattr(module_workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(module_workflow, "upsert_search_attributes", lambda _a: None)
    monkeypatch.setattr(module_workflow, "now", lambda: datetime.now(UTC))
    monkeypatch.setattr(
        module_workflow,
        "logger",
        type(
            "Logger",
            (),
            {"info": lambda *a, **k: None, "warning": lambda *a, **k: None},
        ),
    )
    monkeypatch.setattr(
        run_workflow_module, "execute_typed_activity", fake_execute_typed_activity
    )
    monkeypatch.setattr(
        module_workflow, "execute_child_workflow", fake_execute_child_workflow
    )
    monkeypatch.setattr(
        module_workflow, "patched", lambda patch_id: patch_id in enabled_patches
    )
    monkeypatch.setattr(module_workflow, "wait_condition", _immediate_wait_condition)
    monkeypatch.setattr(workflow, "_maybe_bind_workflow_scoped_session", passthrough)
    monkeypatch.setattr(workflow, "_record_step_execution_manifest", no_manifest)
    workflow._owner_id = "owner-1"
    workflow._repo = "o/r"
    workflow._integration = None
    return requests


_PATCHES = {
    RUN_CONDITIONAL_REGISTRY_READ_PATCH,
    RUN_AGENT_RUNTIME_RETRY_CLASSIFICATION_PATCH,
    RUN_EXPLICIT_STEP_RETRY_RECOMMENDATION_PATCH,
    RUN_ADAPTER_RESOLVER_VERDICT_RETRY_DECISION_PATCH,
    RUN_RESOLVER_HOST_REPAIR_RETRY_PATCH,
    RUN_FAIL_FAST_STEP_FAILURE_SUMMARY_PATCH,
}


@pytest.mark.asyncio
async def test_identity_refusal_resumes_only_the_commit_in_one_repaired_attempt(
    monkeypatch: pytest.MonkeyPatch, workspace: _ResolverWorkspace
) -> None:
    receipts: list[dict[str, Any]] = []

    async def resolver_attempt(ordinal: int) -> AgentRunResult:
        started_at = datetime.now(UTC)
        if ordinal > 1:
            await workspace.prepare()
        if not workspace.resumes_commit():
            workspace.remediate()
        if workspace.commit() == 0:
            workspace.finalize()
        else:
            receipts.append(_result(workspace.repo))
        return await workspace.attempt_result(ordinal, started_at)

    workflow = MoonMindRunWorkflow()
    requests = _install_run_harness(
        monkeypatch, workflow, enabled_patches=_PATCHES, child=resolver_attempt
    )

    await workflow._run_execution_stage(
        parameters={"publishMode": "none"}, plan_ref="plan-ref"
    )

    # One refusal, one repaired attempt of the same logical step.
    assert len(requests) == 2
    assert requests[1].step_execution is not None
    assert requests[1].step_execution.reason == "runtime_recovered"
    assert [r["localCommit"]["kind"] for r in receipts] == ["identity_missing"]
    assert receipts[0]["mergeAutomationDisposition"] == "failed"
    assert receipts[0]["next_step"] == "provide_commit_identity"
    # The remediation ran once; the retry only committed and finalized.
    assert workspace.remediations == 1
    assert workspace.preparations == 1
    repo = workspace.repo
    assert _git(repo, "rev-list", "--count", f"{workspace.base}..HEAD") == "1"
    assert (
        _git(repo, "rev-parse", "HEAD^{tree}")
        == receipts[0]["localCommit"]["stagedTree"]
    )
    assert _git(repo, "log", "-1", "--format=%an <%ae>|%cn") == (
        f"{ADMITTED['name']} <{ADMITTED['email']}>|{ADMITTED['name']}"
    )
    assert (repo / "fix.py").read_text(encoding="utf-8") == "print('addressed')\n"
    assert (repo / "unstaged.txt").read_text(encoding="utf-8") == "keep me\n"
    final = _result(repo)
    assert (final["pr_number"], final["pr_url"]) == (4486, PR_URL)
    assert final["attempt_history"] == [{"stage": "finalize", "attempt": 1}]
    assert final["headSha"] == _git(repo, "rev-parse", "HEAD")


@pytest.mark.asyncio
async def test_identity_still_missing_after_the_repaired_attempt_stops(
    monkeypatch: pytest.MonkeyPatch, workspace: _ResolverWorkspace
) -> None:
    """Preparation that cannot supply identity gets no second retry."""

    async def resolver_attempt(ordinal: int) -> AgentRunResult:
        started_at = datetime.now(UTC)
        if not workspace.resumes_commit():
            workspace.remediate()
        assert workspace.commit() == 4
        return await workspace.attempt_result(ordinal, started_at)

    workflow = MoonMindRunWorkflow()
    requests = _install_run_harness(
        monkeypatch, workflow, enabled_patches=_PATCHES, child=resolver_attempt
    )

    with pytest.raises(ValueError, match="local_commit_failed"):
        await workflow._run_execution_stage(
            parameters={"publishMode": "none"}, plan_ref="plan-ref"
        )

    assert len(requests) == 2
    assert workspace.remediations == 1
    receipt = _result(workspace.repo)["localCommit"]
    assert (receipt["kind"], receipt["attempts"]) == ("identity_missing", 2)
    assert _git(workspace.repo, "rev-parse", "HEAD") == workspace.base
    assert _git(workspace.repo, "diff", "--cached", "--name-only") == "fix.py"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["signing_failed", "hook_rejected", "other"])
async def test_commit_refusal_the_host_cannot_repair_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, workspace: _ResolverWorkspace, kind: str
) -> None:
    _git(workspace.repo, "config", "user.name", "Explicit Owner")
    _git(workspace.repo, "config", "user.email", "owner@example.test")
    if kind == "signing_failed":
        _git(workspace.repo, "config", "commit.gpgsign", "true")
        _git(workspace.repo, "config", "gpg.program", "false")
    elif kind == "hook_rejected":
        hook = workspace.repo / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text("#!/bin/sh\necho 'lint failed' >&2\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
    else:
        (workspace.repo / ".git" / "index.lock").write_text("", encoding="utf-8")

    async def resolver_attempt(ordinal: int) -> AgentRunResult:
        started_at = datetime.now(UTC)
        if kind == "other":
            # A concurrent writer holds the index; stage before it appears.
            (workspace.repo / ".git" / "index.lock").unlink()
            workspace.remediate()
            (workspace.repo / ".git" / "index.lock").write_text("", encoding="utf-8")
        else:
            workspace.remediate()
        assert workspace.commit() == 4
        return await workspace.attempt_result(ordinal, started_at)

    workflow = MoonMindRunWorkflow()
    requests = _install_run_harness(
        monkeypatch, workflow, enabled_patches=_PATCHES, child=resolver_attempt
    )

    with pytest.raises(ValueError, match="local_commit_failed"):
        await workflow._run_execution_stage(
            parameters={"publishMode": "none"}, plan_ref="plan-ref"
        )

    assert len(requests) == 1
    assert _result(workspace.repo)["localCommit"]["kind"] == kind
    assert _git(workspace.repo, "rev-parse", "HEAD") == workspace.base


@pytest.mark.asyncio
async def test_identity_refusal_in_an_attempt_owned_omnigent_sandbox_stays_final(
    monkeypatch: pytest.MonkeyPatch, workspace: _ResolverWorkspace
) -> None:
    """A fresh sandbox would lose the receipt and repeat the remediation."""

    async def resolver_attempt(ordinal: int) -> AgentRunResult:
        started_at = datetime.now(UTC)
        if not workspace.resumes_commit():
            workspace.remediate()
        assert workspace.commit() == 4
        return await workspace.attempt_result(ordinal, started_at)

    workflow = MoonMindRunWorkflow()
    requests = _install_run_harness(
        monkeypatch,
        workflow,
        enabled_patches=_PATCHES | {RUN_OMNIGENT_INTERRUPTED_STEP_WORKSPACE_PATCH},
        child=resolver_attempt,
        runtime="omnigent",
    )

    with pytest.raises(ValueError, match="provide_commit_identity"):
        await workflow._run_execution_stage(
            parameters={"publishMode": "none"}, plan_ref="plan-ref"
        )

    assert len(requests) == 1
    assert workspace.remediations == 1
    result = _result(workspace.repo)
    assert (result["reason"], result["next_step"]) == (
        "local_commit_failed",
        "provide_commit_identity",
    )
    assert result["mergeAutomationDisposition"] == "failed"
    receipt = result["localCommit"]
    assert (receipt["kind"], receipt["attempts"]) == ("identity_missing", 1)
    assert _git(workspace.repo, "rev-parse", "HEAD") == workspace.base
    assert _git(workspace.repo, "diff", "--cached", "--name-only") == "fix.py"
