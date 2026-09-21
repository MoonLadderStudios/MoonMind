"""Shared service for publishing changes to repositories."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Protocol
from uuid import UUID

from moonmind.security.outbound_scan import (
    OutboundBundleItem,
    is_binary_git_diff_marker,
    push_scan_coverage_error,
    resolve_high_security_mode,
    scan_outbound_bundle,
)
from moonmind.workflows.adapters.github_service import GitHubService
from moonmind.utils.cli import verify_cli_is_executable
from moonmind.utils.logging import redact_sensitive_text
from moonmind.publish.sanitization import (
    sanitize_metadata_footer_value,
    sanitize_publish_subject,
)

class CommandResult(Protocol):
    """Protocol for a command result with stdout."""

    @property
    def stdout(self) -> str:
        pass

CommandRunner = Callable[
    ...,
    Awaitable[CommandResult],
]

@dataclass(frozen=True)
class PublishResult:
    mode: str
    status: Literal["published", "skipped", "failed"]
    reason_code: str | None = None
    reason: str | None = None
    commit_created: bool = False
    branch_pushed: bool = False
    pr_url: str | None = None
    branch_name: str | None = None
    base_branch: str | None = None
    head_sha: str | None = None
    commits_ahead_of_base: int | None = None
    remote_verified: bool = False

    def summary_text(self) -> str | None:
        if self.status == "skipped":
            return self.reason
        if self.status != "published":
            return self.reason
        if self.pr_url:
            return f"published PR {self.pr_url}"
        if self.mode == "pr" and self.branch_name:
            return f"published PR from {self.branch_name}"
        if self.branch_name:
            return f"published branch {self.branch_name}"
        return self.reason

_PUBLISH_PUSH_SCAN_MAX_COMMIT_METADATA_CHARS = 100_000
_PUBLISH_PUSH_SCAN_MAX_FILE_DIFF_CHARS = 200_000
_PUBLISH_PUSH_SCAN_MAX_CHANGED_FILES = 200


def push_env_from_bound_credential(
    acquired: Any,
    *,
    base_env: dict[str, str] | None = None,
    repository: str = "",
    endpoint: str = "https://github.com",
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Build the git-push env from a bound credential (PAT or App).

    Existing immediate publication consumes App-issued credentials through
    this same path instead of a parallel App-only implementation: the token
    enters ``GITHUB_TOKEN``/``GH_TOKEN`` inside the trusted ``use_now``
    boundary with ``GIT_TERMINAL_PROMPT=0``, and the redaction tuple carries
    the opaque value for ``run_command``. Server-held credentials stay
    server-held; diagnostics must use the metadata-only binding.

    When ``repository`` parses as an ``owner/name`` identity, the shared
    bound-Git credential-helper contract is layered on top so the push to
    ``origin`` authenticates through the admitted repository's trusted
    endpoint instead of ambient configuration.
    """

    captured: list[str] = []

    def _capture(raw: bytes) -> None:
        captured.append(bytes(raw).decode("utf-8", errors="strict").strip())

    acquired.credential.use_now(_capture)
    token = captured[0] if captured else ""
    if not token or "\n" in token or "\r" in token:
        raise ValueError("credential material is invalid")
    env = dict(base_env or {})
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env["GITHUB_TOKEN"] = token
    env["GH_TOKEN"] = token
    owner, sep, name = str(repository or "").strip().strip("/").partition("/")
    if sep and owner.strip() and name.strip() and "/" not in name.strip():
        try:
            from moonmind.auth.github_app import build_bound_git_env

            bound = build_bound_git_env(
                token.encode("utf-8"),
                repository=f"{owner.strip()}/{name.strip()}",
                endpoint=endpoint,
            )
            bound_env = dict(bound.get("env") or {})
            for key in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0",
                        "GIT_CONFIG_KEY_1", "GIT_CONFIG_VALUE_1"):
                if key in bound_env:
                    env[key] = bound_env[key]
        except ValueError:
            pass
    return env, (token,)


def gh_env_from_bound_credential(
    acquired: Any, *, base_env: dict[str, str] | None = None
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Build the deferred ``gh`` env projection from a bound credential.

    Shares ``moonmind.auth.github_app.build_gh_env`` so the existing
    CLI-PR fallback path consumes App credentials with the same
    server-held/redaction contract as PAT.
    """

    from moonmind.auth.github_app import build_gh_env

    holder: list[dict[str, Any]] = []

    def _build(raw: bytes) -> None:
        holder.append(build_gh_env(raw))

    acquired.credential.use_now(_build)
    projected = holder[0]
    env = dict(base_env or {})
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.update(dict(projected.get("env") or {}))
    return env, tuple(projected.get("redact") or ())


class PublishService:
    """Service to publish changes to Git branches or Pull Requests."""

    def __init__(
        self,
        *,
        git_binary: str = "git",
        gh_binary: str = "gh",
        github_create_pull_request: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self._git_binary = git_binary
        self._gh_binary = gh_binary
        self._github_create_pull_request = github_create_pull_request

    @staticmethod
    def _extract_first_instruction_sentence(instruction: str) -> str | None:
        for line in str(instruction or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            first_sentence = stripped
            punctuation_match = re.search(r"[.!?]", stripped)
            if punctuation_match:
                first_sentence = stripped[: punctuation_match.end()]
            return " ".join(first_sentence.split())
        return None

    @classmethod
    def _sanitize_publish_subject(cls, value: str, *, max_chars: int) -> str:
        return sanitize_publish_subject(
            value,
            max_chars=max_chars,
            redact_uuids=True,
        )

    @staticmethod
    def _sanitize_metadata_footer_value(
        value: str | None, *, fallback: str = "unknown"
    ) -> str:
        return sanitize_metadata_footer_value(value, fallback=fallback)

    @classmethod
    def _derive_default_publish_subject(
        cls,
        *,
        instruction: str,
        max_chars: int,
    ) -> str:
        candidate = cls._extract_first_instruction_sentence(instruction)
        if candidate:
            return cls._sanitize_publish_subject(candidate, max_chars=max_chars)
        return cls._sanitize_publish_subject("Automated update", max_chars=max_chars)

    @classmethod
    def _derive_default_pr_body(
        cls,
        *,
        job_id: UUID,
        runtime_mode: str,
        base_branch: str,
        head_branch: str,
    ) -> str:
        runtime_value = cls._sanitize_metadata_footer_value(
            runtime_mode, fallback="codex"
        )
        base_value = cls._sanitize_metadata_footer_value(base_branch)
        head_value = cls._sanitize_metadata_footer_value(head_branch)
        lines = [
            "Automated PR generated by queue publish stage.",
            "",
            "---",
            "<!-- moonmind:begin -->",
            f"MoonMind Job: {job_id}",
            f"Runtime: {runtime_value}",
            f"Base: {base_value}",
            f"Head: {head_value}",
            "<!-- moonmind:end -->",
        ]
        return "\n".join(lines)

    async def publish(
        self,
        *,
        job_id: UUID,
        instruction: str,
        publish_mode: str,
        publish_base_branch: str | None,
        runtime_mode: str,
        repo_dir: Path,
        run_command: CommandRunner,
        repo: str | None = None,
        github_token: str | None = None,
        bound_credential: Any | None = None,
        publish_existing_commits: bool = False,
        publication_branch_name: str | None = None,
        verify_remote: bool = False,
    ) -> PublishResult | None:
        """Publish the changes to a branch or a pull request.

        Args:
            job_id: The job identifier.
            instruction: The original instruction for the task.
            publish_mode: The publish mode ("none", "branch", "pr").
            publish_base_branch: Base branch to target for PRs (default: main).
            runtime_mode: The runtime that generated the changes (e.g. "codex", "claude").
            repo_dir: Path to the git repository.
            run_command: Async callable that runs a shell command and returns an object with a `stdout` attribute.
            bound_credential: Optional already-acquired bound credential (PAT or
                GitHub App) for the admitted operation. When present and no
                explicit token is given, push/gh env projections consume it
                through the bound helpers with redaction instead of ambient
                resolution. Acquire it via
                ``moonmind.auth.github_app_wiring.acquire_bound_credential_for_connection``.
        """
        if publish_mode == "none":
            return None

        status = await run_command(
            [self._git_binary, "status", "--porcelain"],
            cwd=repo_dir,
            check=False,
        )
        worktree_changed = bool(status.stdout.strip())
        if not worktree_changed and not publish_existing_commits:
            return PublishResult(
                mode=publish_mode,
                status="skipped",
                reason_code="no_commit",
                reason="No repository changes were available to commit or publish.",
            )

        branch_name = str(publication_branch_name or "").strip() or (
            f"moonmind-job-{str(job_id)[:8]}"
        )
        await run_command(
            [self._git_binary, "checkout", "-B", branch_name],
            cwd=repo_dir,
        )
        commit_created = False
        if worktree_changed:
            await run_command(
                [self._git_binary, "add", "-A"],
                cwd=repo_dir,
            )
            commit_message = self._derive_default_publish_subject(
                instruction=instruction,
                max_chars=72,
            )
            await run_command(
                [
                    self._git_binary,
                    "commit",
                    "-m",
                    commit_message,
                ],
                cwd=repo_dir,
                redaction_values=(commit_message,),
            )
            commit_created = True
        base_branch = publish_base_branch or "main"
        base_ref = f"origin/{base_branch}"
        commit_count: int | None = None
        if publish_existing_commits or verify_remote:
            count_result = await run_command(
                [
                    self._git_binary,
                    "rev-list",
                    "--count",
                    f"{base_ref}..{branch_name}",
                ],
                cwd=repo_dir,
                check=False,
            )
            try:
                commit_count = int(str(count_result.stdout or "").strip())
            except (TypeError, ValueError):
                commit_count = None
            if publish_existing_commits and not worktree_changed and commit_count == 0:
                return PublishResult(
                    mode=publish_mode,
                    status="skipped",
                    reason_code="no_commit",
                    reason=(
                        "No repository commits were available over the authored "
                        "publish base."
                    ),
                    branch_name=branch_name,
                    base_branch=base_branch,
                    commits_ahead_of_base=0,
                )
            if publish_existing_commits and commit_count is None:
                raise RuntimeError(
                    "could not measure repository commits over the authored "
                    "publish base"
                )
        await self._scan_git_push_before_publish(
            repo_dir=repo_dir,
            branch_name=branch_name,
            base_ref=base_ref,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        token = str(github_token or "").strip()
        push_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        resolved_github_credential = None
        # A bound acquisition for the admitted operation (PAT or App
        # installation, already scoped at issuance) sits between an explicit
        # token and ambient resolution; it needs no repo gate.
        token_from_bound = False
        if not token and bound_credential is not None:
            push_env, bound_redact = push_env_from_bound_credential(
                bound_credential,
                base_env=push_env,
                repository=str(repo or ""),
            )
            token = bound_redact[0] if bound_redact else ""
            token_from_bound = bool(token)
        if repo and not token:
            from moonmind.auth.github_credentials import resolve_github_credential

            resolved_github_credential = await resolve_github_credential(repo=repo)
            token = resolved_github_credential.token or ""
        if token:
            push_env["GITHUB_TOKEN"] = token
            push_env["GH_TOKEN"] = token
        remote_sha = ""
        if verify_remote:
            remote_result = await run_command(
                [
                    self._git_binary,
                    "ls-remote",
                    "--heads",
                    "origin",
                    f"refs/heads/{branch_name}",
                ],
                cwd=repo_dir,
                check=False,
                env=push_env,
                redaction_values=(token,) if token else (),
            )
            remote_line = str(remote_result.stdout or "").strip().splitlines()
            if remote_line:
                remote_sha = remote_line[0].split(maxsplit=1)[0].strip()
            if branch_name == base_branch:
                if getattr(remote_result, "returncode", 1) != 0 or not re.fullmatch(
                    r"(?:[0-9a-f]{40}|[0-9a-f]{64})", remote_sha
                ):
                    raise RuntimeError("selected publication branch has no remote head")
                ancestry = await run_command(
                    [
                        self._git_binary,
                        "merge-base",
                        "--is-ancestor",
                        remote_sha,
                        branch_name,
                    ],
                    cwd=repo_dir,
                    check=False,
                )
                if getattr(ancestry, "returncode", 1) != 0:
                    raise RuntimeError(
                        "selected publication branch is not a fast-forward of the remote head"
                    )
        push_command = [self._git_binary, "push", "-u"]
        if verify_remote:
            # The ancestry check protects shared history; the exact-tip lease
            # also rejects deletion or replacement between inspection and push.
            push_command.append(
                f"--force-with-lease=refs/heads/{branch_name}:{remote_sha}"
            )
        push_command.extend(("origin", branch_name))
        await run_command(
            push_command,
            cwd=repo_dir,
            env=push_env,
            redaction_values=(token,) if token else (),
        )
        branch_pushed = True
        head_sha: str | None = None
        remote_verified = False
        if verify_remote:
            head_result = await run_command(
                [self._git_binary, "rev-parse", "HEAD"],
                cwd=repo_dir,
            )
            head_sha = str(head_result.stdout or "").strip() or None
            verify_result = await run_command(
                [
                    self._git_binary,
                    "ls-remote",
                    "--heads",
                    "origin",
                    f"refs/heads/{branch_name}",
                ],
                cwd=repo_dir,
                check=False,
                env=push_env,
                redaction_values=(token,) if token else (),
            )
            remote_lines = str(verify_result.stdout or "").strip().splitlines()
            verified_sha = (
                remote_lines[0].split(maxsplit=1)[0].strip()
                if remote_lines
                else ""
            )
            remote_verified = bool(head_sha and verified_sha == head_sha)
            if not remote_verified:
                raise RuntimeError(
                    "published branch failed exact remote-head verification"
                )

        if publish_mode == "branch":
            return PublishResult(
                mode=publish_mode,
                status="published",
                reason=f"published branch {branch_name}",
                commit_created=commit_created,
                branch_pushed=branch_pushed,
                branch_name=branch_name,
                base_branch=base_branch,
                head_sha=head_sha,
                commits_ahead_of_base=commit_count,
                remote_verified=remote_verified,
            )

        pr_title = self._derive_default_publish_subject(
            instruction=instruction,
            max_chars=90,
        )
        pr_body = self._derive_default_pr_body(
            job_id=job_id,
            runtime_mode=runtime_mode,
            base_branch=base_branch,
            head_branch=branch_name,
        )
        if repo and token:
            create_pull_request = (
                self._github_create_pull_request
                or GitHubService().create_pull_request
            )
            result = await create_pull_request(
                repo=repo,
                head=branch_name,
                base=base_branch,
                title=pr_title,
                body=pr_body,
                github_token=token,
            )
            if not getattr(result, "created", False):
                summary = getattr(result, "summary", "GitHub create PR failed.")
                raise RuntimeError(str(summary))
            url = getattr(result, "url", None)
            return PublishResult(
                mode=publish_mode,
                status="published",
                reason=(
                    f"published PR {url}"
                    if url
                    else f"published PR from {branch_name}"
                ),
                commit_created=commit_created,
                branch_pushed=branch_pushed,
                pr_url=str(url) if url else None,
                branch_name=branch_name,
                base_branch=base_branch,
                head_sha=head_sha,
                commits_ahead_of_base=commit_count,
                remote_verified=remote_verified,
            )

        if resolved_github_credential is None and not token_from_bound:
            from moonmind.auth.github_credentials import resolve_github_credential

            resolved_github_credential = await resolve_github_credential()
            token = resolved_github_credential.token or ""
        if not token:
            raise RuntimeError(
                "GitHub CLI PR fallback requires an explicit resolved token; "
                "ambient gh auth state is not a managed publishing contract. "
                f"{resolved_github_credential.safe_summary}"
            )

        verify_cli_is_executable(self._gh_binary)
        if token_from_bound and bound_credential is not None:
            gh_env, _gh_bound_redact = gh_env_from_bound_credential(
                bound_credential,
                base_env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        else:
            gh_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            gh_env["GITHUB_TOKEN"] = token
            gh_env["GH_TOKEN"] = token
        if repo:
            gh_env["GH_REPO"] = repo
        await run_command(
            [
                self._gh_binary,
                "pr",
                "create",
                "--base",
                base_branch,
                "--head",
                branch_name,
                "--title",
                pr_title,
                "--body",
                pr_body,
            ],
            cwd=repo_dir,
            env=gh_env,
            redaction_values=tuple(
                value for value in (pr_title, pr_body, token) if value
            ),
        )
        return PublishResult(
            mode=publish_mode,
            status="published",
            reason=f"published PR from {branch_name}",
            commit_created=commit_created,
            branch_pushed=branch_pushed,
            branch_name=branch_name,
            base_branch=base_branch,
            head_sha=head_sha,
            commits_ahead_of_base=commit_count,
            remote_verified=remote_verified,
        )

    async def _scan_git_push_before_publish(
        self,
        *,
        repo_dir: Path,
        branch_name: str,
        base_ref: str,
        env: dict[str, str],
    ) -> None:
        if not resolve_high_security_mode():
            return

        if base_ref.startswith("origin/"):
            with contextlib.suppress(Exception):
                await self._read_git_text_for_scan(
                    repo_dir=repo_dir,
                    env=env,
                    timeout=30,
                    args=["fetch", "origin", base_ref.removeprefix("origin/")],
                )
        commit_range = f"{base_ref}..{branch_name}"
        try:
            commit_metadata = await self._read_git_text_for_scan(
                repo_dir=repo_dir,
                env=env,
                timeout=15,
                args=[
                    "log",
                    (
                        "--format=commit %H%nparents %P%nauthor %an <%ae>%n"
                        + "subject %s%nbody%n%B%n---END-COMMIT---"
                    ),
                    commit_range,
                ],
            )
            changed_files_text = await self._read_git_text_for_scan(
                repo_dir=repo_dir,
                env=env,
                timeout=15,
                args=["diff", "--name-only", "-z", commit_range],
            )
            # NUL-separated output preserves exact pathnames (whitespace,
            # newlines, or otherwise quoted names); never strip entries.
            all_changed_files = [
                entry for entry in changed_files_text.split("\x00") if entry
            ]
            coverage_error = push_scan_coverage_error(
                commit_range=commit_range,
                commit_metadata_len=len(commit_metadata),
                max_commit_metadata_chars=_PUBLISH_PUSH_SCAN_MAX_COMMIT_METADATA_CHARS,
                changed_file_count=len(all_changed_files),
                max_changed_files=_PUBLISH_PUSH_SCAN_MAX_CHANGED_FILES,
            )
            if coverage_error is not None:
                raise RuntimeError(coverage_error)
            bundle = [
                OutboundBundleItem(
                    location=f"git.push.commits:{commit_range}",
                    content=commit_metadata[
                        :_PUBLISH_PUSH_SCAN_MAX_COMMIT_METADATA_CHARS
                    ],
                )
            ]
            changed_files = all_changed_files[:_PUBLISH_PUSH_SCAN_MAX_CHANGED_FILES]
            diff_semaphore = asyncio.Semaphore(10)
            oversized_diff_path: str | None = None
            binary_diff_path: str | None = None

            async def _diff_item(changed_file: str) -> OutboundBundleItem:
                nonlocal oversized_diff_path, binary_diff_path
                async with diff_semaphore:
                    file_diff = await self._read_git_text_for_scan(
                        repo_dir=repo_dir,
                        env=env,
                        timeout=20,
                        args=[
                            "diff",
                            "--no-ext-diff",
                            "--text",
                            commit_range,
                            "--",
                            changed_file,
                        ],
                    )
                # A binary marker means the blob was not inspected as text;
                # fail closed instead of accepting the short marker as clean.
                if is_binary_git_diff_marker(file_diff):
                    binary_diff_path = binary_diff_path or changed_file
                    return OutboundBundleItem(
                        location=f"git.push.diff:{changed_file}",
                        content=file_diff[:_PUBLISH_PUSH_SCAN_MAX_FILE_DIFF_CHARS],
                    )
                if len(file_diff) > _PUBLISH_PUSH_SCAN_MAX_FILE_DIFF_CHARS:
                    oversized_diff_path = oversized_diff_path or changed_file
                    return OutboundBundleItem(
                        location=f"git.push.diff:{changed_file}",
                        content=file_diff[:_PUBLISH_PUSH_SCAN_MAX_FILE_DIFF_CHARS],
                    )
                return OutboundBundleItem(
                    location=f"git.push.diff:{changed_file}",
                    content=file_diff,
                )

            bundle.extend(
                await asyncio.gather(*(_diff_item(path) for path in changed_files))
            )
            if oversized_diff_path is not None or binary_diff_path is not None:
                coverage_error = push_scan_coverage_error(
                    commit_range=commit_range,
                    commit_metadata_len=len(commit_metadata),
                    max_commit_metadata_chars=_PUBLISH_PUSH_SCAN_MAX_COMMIT_METADATA_CHARS,
                    changed_file_count=len(all_changed_files),
                    max_changed_files=_PUBLISH_PUSH_SCAN_MAX_CHANGED_FILES,
                    oversized_diff_path=oversized_diff_path,
                    binary_diff_path=binary_diff_path,
                )
                assert coverage_error is not None
                raise RuntimeError(coverage_error)
        except RuntimeError as exc:
            # Preserve fail-closed coverage/block reasons verbatim; only
            # redact unexpected git failures.
            message = str(exc)
            if message.startswith("outbound git push blocked:"):
                raise
            safe_detail = redact_sensitive_text(message)
            raise RuntimeError(
                "outbound git push blocked: could not build high security "
                f"scan payload for {commit_range}: {safe_detail}"
            ) from exc
        except Exception as exc:
            safe_detail = redact_sensitive_text(str(exc))
            raise RuntimeError(
                "outbound git push blocked: could not build high security "
                f"scan payload for {commit_range}: {safe_detail}"
            ) from exc

        try:
            scan_result = scan_outbound_bundle(bundle, high_security_mode=True)
        except Exception as exc:  # noqa: BLE001 - scanner failure must fail closed
            raise RuntimeError(
                "outbound git push blocked: high security scan enforcement "
                f"unavailable for {commit_range}: {exc.__class__.__name__}"
            ) from exc
        if scan_result.allowed:
            return
        diagnostics = "; ".join(scan_result.sanitized_diagnostics)
        raise RuntimeError(
            "outbound git push blocked by high security scan: " + diagnostics
        )

    async def _read_git_text_for_scan(
        self,
        *,
        repo_dir: Path,
        env: dict[str, str],
        timeout: int,
        args: list[str],
    ) -> str:
        proc = await asyncio.create_subprocess_exec(
            self._git_binary,
            "-c",
            f"safe.directory={repo_dir.resolve()}",
            "-C",
            str(repo_dir),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        if proc.returncode != 0:
            detail = (
                stderr_bytes.decode("utf-8", errors="replace").strip()
                or stdout_bytes.decode("utf-8", errors="replace").strip()
                or f"git exited with {proc.returncode}"
            )
            raise RuntimeError(detail)
        return stdout_bytes.decode("utf-8", errors="replace")
