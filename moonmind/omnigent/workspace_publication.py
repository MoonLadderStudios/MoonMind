"""Harness-neutral publication for an authoritative Omnigent workspace."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from moonmind.config.settings import settings
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.workspace_intent import (
    authored_repository_source,
    authored_starting_branch,
)
from moonmind.publish.service import PublishService
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.workspace_locator_models import (
    WORKSPACE_LOCATOR_ADAPTER,
    WORKSPACE_LOCATOR_UNSUPPORTED,
    SandboxWorkspaceLocator,
)
from moonmind.utils.logging import redact_sensitive_text
from moonmind.workflows.adapters.github_service import GitHubService
from moonmind.workflows.temporal.runtime.command_runner import run_runtime_command
from moonmind.workflows.temporal.runtime.git_auth import (
    build_github_token_git_environment,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecordStore,
    resolve_sandbox_workspace_locator,
)

_DEFAULT_PUBLISH_GIT_USER_NAME = "MoonMind Worker"
_DEFAULT_PUBLISH_GIT_USER_EMAIL = "moonmind-worker@users.noreply.github.com"


class OmnigentWorkspacePublicationService:
    """Publish and remotely verify one typed Omnigent sandbox workspace."""

    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self._workspace_root = Path(
            workspace_root
            or os.getenv("WORKFLOW_WORKSPACE_ROOT", "/work/agent_jobs")
        ).resolve()

    @staticmethod
    async def _run(
        *args: str,
        env: Mapping[str, str] | None = None,
        check: bool = True,
    ) -> tuple[int, str, str]:
        code, stdout, stderr = await run_runtime_command(
            args,
            env=env,
            timeout_seconds=600,
            output_limit_bytes=4096,
        )
        output = stdout.decode("utf-8", errors="replace")
        error = stderr.decode("utf-8", errors="replace")
        if check and code != 0:
            detail = redact_sensitive_text(error or output or "git failed")
            raise HarnessPlatformError(
                f"repository publication command failed: {detail[:512]}",
                code="OMNIGENT_REPOSITORY_PUBLICATION_FAILED",
            )
        return code, output, error

    @staticmethod
    async def _verified_no_commit_publication(
        *,
        run_command: Any,
        base_branch: str,
    ) -> dict[str, Any]:
        """Prove the unchanged local checkout is the exact remote base head."""

        normalized_base = str(base_branch or "").strip()
        if not normalized_base:
            raise HarnessPlatformError(
                "no-commit publication requires an authoritative base branch",
                code="OMNIGENT_REPOSITORY_PUBLICATION_UNVERIFIED",
            )
        head_result = await run_command(["git", "rev-parse", "HEAD"])
        head_sha = str(head_result.stdout or "").strip().lower()
        remote_ref = f"refs/heads/{normalized_base}"
        remote_result = await run_command(
            ["git", "ls-remote", "--heads", "origin", remote_ref],
            check=False,
        )
        remote_heads = {
            fields[0].lower()
            for line in str(remote_result.stdout or "").splitlines()
            if len(fields := line.split()) >= 2 and fields[1] == remote_ref
        }
        if (
            getattr(remote_result, "returncode", 1) != 0
            or re.fullmatch(r"[0-9a-f]{40,64}", head_sha) is None
            or remote_heads != {head_sha}
        ):
            raise HarnessPlatformError(
                "unchanged repository head did not match the exact remote base",
                code="OMNIGENT_REPOSITORY_PUBLICATION_UNVERIFIED",
            )
        return {
            "push_status": "no_commits",
            "push_branch": normalized_base,
            "push_base_branch": normalized_base,
            "push_head_sha": head_sha,
            "push_commit_count": 0,
            "remote_verified": True,
        }

    async def publish_workspace(
        self,
        *,
        workspace_locator: Mapping[str, Any],
        current_workflow_id: str,
        current_step_execution_id: str,
        publication_identity: str,
        publish_mode: str,
        base_branch: str | None,
        repository: str,
        github_token: str | None,
    ) -> dict[str, Any]:
        normalized_mode = str(publish_mode or "none").strip().lower()
        if normalized_mode not in {"branch", "pr"}:
            return {"push_status": "skipped"}
        locator = WORKSPACE_LOCATOR_ADAPTER.validate_python(workspace_locator)
        if not isinstance(locator, SandboxWorkspaceLocator):
            raise HarnessPlatformError(
                "Omnigent repository publication requires sandbox workspace authority",
                code=WORKSPACE_LOCATOR_UNSUPPORTED,
            )
        expected_id = hashlib.sha256(
            f"{current_workflow_id}:{current_step_execution_id}".encode("utf-8")
        ).hexdigest()[:24]
        owner_record = SandboxWorkspaceRecordStore(self._workspace_root).load(
            locator.workspace_id
        )
        workspace = resolve_sandbox_workspace_locator(
            locator,
            workspace_root=self._workspace_root,
            expected_workspace_id=expected_id,
            owner_record=owner_record,
            expected_workflow_id=current_workflow_id,
            expected_step_execution_id=current_step_execution_id,
            must_exist=True,
        )
        safe_workspace = workspace.resolve(strict=True)
        token = str(github_token or "").strip()
        command_env = build_github_token_git_environment(token, base_env=os.environ)
        git_user_name = (
            str(settings.workflow.git_user_name or "").strip()
            or _DEFAULT_PUBLISH_GIT_USER_NAME
        )
        git_user_email = (
            str(settings.workflow.git_user_email or "").strip()
            or _DEFAULT_PUBLISH_GIT_USER_EMAIL
        )
        command_env.update(
            {
                "GIT_AUTHOR_NAME": git_user_name,
                "GIT_COMMITTER_NAME": git_user_name,
                "GIT_AUTHOR_EMAIL": git_user_email,
                "GIT_COMMITTER_EMAIL": git_user_email,
            }
        )

        async def run_command(
            command: list[str],
            *,
            cwd: Path | None = None,
            check: bool = True,
            env: Mapping[str, str] | None = None,
            **_kwargs: Any,
        ) -> SimpleNamespace:
            del cwd
            authored = [str(part) for part in command]
            if authored and authored[0] == "git":
                authored[1:1] = [
                    "-c",
                    f"safe.directory={safe_workspace}",
                    "-C",
                    str(safe_workspace),
                ]
            selected_env = build_github_token_git_environment(
                token,
                base_env=(dict(env) if env is not None else command_env),
            )
            code, stdout, stderr = await self._run(
                *authored,
                env=selected_env,
                check=False,
            )
            if check and code != 0:
                detail = redact_sensitive_text(stderr or stdout or "git failed")
                raise HarnessPlatformError(
                    f"repository publication command failed: {detail[:512]}",
                    code="OMNIGENT_REPOSITORY_PUBLICATION_FAILED",
                )
            return SimpleNamespace(
                stdout=stdout,
                stderr=stderr,
                returncode=code,
            )

        published = await PublishService().publish(
            job_id=uuid5(NAMESPACE_URL, publication_identity),
            instruction="Publish completed Omnigent repository work",
            # PR creation remains owned by the durable parent workflow.
            publish_mode="branch",
            publish_base_branch=str(base_branch or "main").strip() or "main",
            runtime_mode="omnigent",
            repo_dir=safe_workspace,
            run_command=run_command,
            repo=str(repository or "").strip() or None,
            github_token=token or None,
            publish_existing_commits=True,
            verify_remote=True,
        )
        if published is None:
            return {"push_status": "skipped"}
        if published.status == "skipped":
            return await self._verified_no_commit_publication(
                run_command=run_command,
                base_branch=(
                    str(published.base_branch or base_branch or "main").strip()
                    or "main"
                ),
            )
        if (
            published.status != "published"
            or not published.branch_pushed
            or not published.remote_verified
            or not published.head_sha
            or not published.branch_name
            or not published.base_branch
            or not published.commits_ahead_of_base
        ):
            raise HarnessPlatformError(
                "repository publication did not produce authoritative remote evidence",
                code="OMNIGENT_REPOSITORY_PUBLICATION_UNVERIFIED",
            )
        result: dict[str, Any] = {
            "push_status": "pushed",
            "push_branch": published.branch_name,
            "push_base_branch": published.base_branch,
            "push_head_sha": published.head_sha,
            "push_commit_count": published.commits_ahead_of_base,
            "remote_verified": True,
            "pushRef": (
                f"git://{str(repository or 'repository').strip()}"
                f"/refs/heads/{published.branch_name}@{published.head_sha}"
            ),
        }
        if normalized_mode == "pr" and repository and token:
            pull_request = await GitHubService().resolve_pull_request_selector(
                repo=str(repository).strip(),
                selector=published.branch_name,
                github_token=token,
            )
            if pull_request.resolved and pull_request.pr_url:
                result["pull_request_url"] = pull_request.pr_url
        return result

    async def publish_request_workspace(
        self,
        *,
        request: AgentExecutionRequest,
        current_workflow_id: str,
        current_step_execution_id: str,
    ) -> dict[str, Any]:
        """Resolve publication inputs at the workspace-owning boundary."""

        parameters = request.parameters if isinstance(request.parameters, dict) else {}
        workspace_spec = (
            request.workspace_spec if isinstance(request.workspace_spec, dict) else {}
        )
        workspace_locator = workspace_spec.get("workspaceLocator")
        if not isinstance(workspace_locator, Mapping):
            raise HarnessPlatformError(
                "generic Omnigent repository publication requires workspace authority",
                code="OMNIGENT_REPOSITORY_PUBLICATION_FAILED",
            )
        return await self.publish_workspace(
            workspace_locator=workspace_locator,
            current_workflow_id=current_workflow_id,
            current_step_execution_id=current_step_execution_id,
            publication_identity=request.idempotency_key,
            publish_mode=str(parameters.get("publishMode") or "none"),
            base_branch=authored_starting_branch(request),
            repository=authored_repository_source(request),
            github_token=None,
        )


__all__ = ["OmnigentWorkspacePublicationService"]
