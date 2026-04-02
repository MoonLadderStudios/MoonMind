"""Managed runtime subprocess launcher."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shlex
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    ManagedRunRecord,
    ManagedRuntimeProfile,
    TERMINAL_AGENT_RUN_STATES,
)
from moonmind.utils.logging import SecretRedactor

from .github_auth_broker import GitHubAuthBrokerManager
from .store import ManagedRunStore

_OWNER_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

logger = logging.getLogger(__name__)


class ManagedRuntimeLauncher:
    """Spawns managed agent subprocesses and records them in the run store."""

    def __init__(self, store: ManagedRunStore) -> None:
        self._store = store
        self._logger = logging.getLogger(__name__)
        self._github_auth_brokers = GitHubAuthBrokerManager()

    @staticmethod
    def _extract_workspace_branch(workspace_spec: dict[str, object] | None) -> str | None:
        if not isinstance(workspace_spec, dict):
            return None
        for key in ("targetBranch", "startingBranch", "branch"):
            value = workspace_spec.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _normalize_clone_source(repo_ref: str) -> str | None:
        normalized = str(repo_ref or "").strip()
        if not normalized:
            return None
        if normalized.startswith(("http://", "https://", "git@", "file://")):
            return normalized
        if _OWNER_REPO_PATTERN.fullmatch(normalized):
            return f"https://github.com/{normalized}.git"

        repo_path = Path(normalized).expanduser()
        if repo_path.exists():
            return str(repo_path.resolve())
        return None

    @staticmethod
    def _workspace_root() -> Path:
        root = os.environ.get("MOONMIND_AGENT_RUNTIME_STORE", "/work/agent_jobs")
        return Path(root).resolve() / "workspaces"

    @staticmethod
    def _build_github_socket_path(*, run_id: str, support_root: str | None) -> str:
        """Keep broker sockets on a short path to avoid AF_UNIX length limits."""
        socket_root = Path("/tmp")
        if not socket_root.is_dir():
            socket_root = Path(tempfile.gettempdir())
        socket_root = socket_root / "mm-gh"

        material = run_id
        if support_root:
            material = f"{Path(support_root).resolve()}::{run_id}"
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
        return str(socket_root / f"{digest}.sock")

    def _resolve_workspace_ownership_root(
        self,
        *,
        resolved_workspace_path: str | None,
        run_id: str,
    ) -> str | None:
        if not resolved_workspace_path:
            return None

        resolved_path = Path(resolved_workspace_path).resolve()
        run_root = resolved_path.parent

        if (
            resolved_path.name == "repo"
            and run_root.name == run_id
            and run_root.parent.name == "workspaces"
        ):
            return str(run_root)
        if resolved_path.name == run_id and resolved_path.parent.name == "workspaces":
            return str(resolved_path)
        return str(resolved_path)

    def _find_existing_workspace_repo(self, *, exclude_run_id: str) -> str | None:
        workspace_root = self._workspace_root()
        if not workspace_root.exists():
            return None

        candidates: list[Path] = []
        for child in workspace_root.iterdir():
            if not child.is_dir() or child.name == exclude_run_id:
                continue
            repo_dir = child / "repo"
            if (repo_dir / ".git").exists():
                candidates.append(repo_dir)
        if not candidates:
            return None

        candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return str(candidates[0])

    async def _run_git_command(
        self,
        args: list[str],
        *,
        allow_failure: bool = False,
    ) -> bool:
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode == 0:
            return True

        redactor = SecretRedactor.from_environ()
        self._logger.warning(
            "git %s failed rc=%s stdout=%s stderr=%s",
            redactor.scrub(" ".join(args)),
            process.returncode,
            redactor.scrub(stdout.decode("utf-8", errors="replace").strip()[:400]),
            redactor.scrub(stderr.decode("utf-8", errors="replace").strip()[:400]),
        )
        if allow_failure:
            return False
        raise RuntimeError(f"git {redactor.scrub(' '.join(args))} failed with exit code {process.returncode}")

    async def _prepare_workspace(
        self,
        *,
        run_id: str,
        request: AgentExecutionRequest,
        workspace_path: str | None,
    ) -> str | None:
        if workspace_path:
            return workspace_path

        workspace_root = self._workspace_root()
        run_workspace = workspace_root / run_id / "repo"
        if run_workspace.exists():
            return str(run_workspace)

        workspace_spec = (
            request.workspace_spec if isinstance(request.workspace_spec, dict) else {}
        )
        repo_ref = workspace_spec.get("repository") or workspace_spec.get("repo")
        clone_source = (
            self._normalize_clone_source(str(repo_ref))
            if isinstance(repo_ref, str)
            else None
        )
        if clone_source is None:
            clone_source = self._find_existing_workspace_repo(exclude_run_id=run_id)
        if clone_source is None:
            return None

        run_workspace.parent.mkdir(parents=True, exist_ok=True)
        branch = self._extract_workspace_branch(workspace_spec)
        clone_args: list[str] = ["clone"]
        if branch and not Path(clone_source).exists():
            clone_args.extend(["--branch", branch, "--single-branch"])
        clone_args.extend(["--", clone_source, str(run_workspace)])
        cloned = await self._run_git_command(clone_args, allow_failure=True)
        if not cloned:
            return None

        if branch:
            checkout_ok = await self._run_git_command(
                ["-C", str(run_workspace), "checkout", branch],
                allow_failure=True,
            )
            if not checkout_ok:
                await self._run_git_command(
                    ["-C", str(run_workspace), "fetch", "origin", branch],
                    allow_failure=True,
                )
                await self._run_git_command(
                    ["-C", str(run_workspace), "checkout", "-B", branch, f"origin/{branch}"],
                    allow_failure=True,
                )
        return str(run_workspace)

    @staticmethod
    def _resolve_repository_source(repository: str) -> str:
        normalized = str(repository or "").strip()
        if not normalized:
            raise RuntimeError("workspaceSpec.repository must be non-empty")
        if "://" in normalized or normalized.startswith("git@"):
            return normalized
        if normalized.startswith("/") or normalized.startswith("./") or normalized.startswith("../"):
            return normalized
        if normalized.count("/") == 1:
            suffix = "" if normalized.endswith(".git") else ".git"
            return f"https://github.com/{normalized}{suffix}"
        raise RuntimeError(
            "Unsupported workspaceSpec.repository format; expected owner/repo, URL, or local path"
        )

    async def _run_command(
        self,
        *cmd: str,
        cwd: str | None = None,
    ) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await process.communicate()
        return (
            int(process.returncode),
            stdout.decode("utf-8", errors="replace").strip(),
            stderr.decode("utf-8", errors="replace").strip(),
        )

    async def _run_checked_command(
        self,
        *cmd: str,
        cwd: str | None = None,
    ) -> None:
        returncode, stdout_text, stderr_text = await self._run_command(
            *cmd,
            cwd=cwd,
        )
        if returncode == 0:
            return
        
        redactor = SecretRedactor.from_environ()
        detail = redactor.scrub(stderr_text or stdout_text or "no output")
        rendered_cmd = redactor.scrub(" ".join(shlex.quote(part) for part in cmd))
        raise RuntimeError(
            f"Command failed with exit code {returncode}: {rendered_cmd}; {detail}"
        )

    async def _prepare_workspace_path(
        self,
        *,
        run_id: str,
        request: AgentExecutionRequest,
        workspace_path: str | Path | None,
    ) -> str | None:
        if workspace_path is not None:
            resolved = Path(workspace_path).expanduser().resolve()
            if not resolved.exists():
                raise RuntimeError(f"workspace_path does not exist: {resolved}")
            return str(resolved)

        workspace_spec = (
            request.workspace_spec
            if isinstance(request.workspace_spec, dict)
            else {}
        )
        repository = str(
            workspace_spec.get("repository")
            or workspace_spec.get("repo")
            or ""
        ).strip()
        if not repository:
            return None

        workspace_root = (self._store.store_root.parent / "workspaces" / run_id).resolve()
        repo_path = (workspace_root / "repo").resolve()
        workspace_root.mkdir(parents=True, exist_ok=True)
        if repo_path.exists():
            return str(repo_path)

        source = self._resolve_repository_source(repository)
        branch = str(
            workspace_spec.get("startingBranch")
            or workspace_spec.get("branch")
            or ""
        ).strip()

        clone_cmd = ["git", "clone"]
        if branch:
            clone_cmd.extend(["--branch", branch, "--single-branch"])
        clone_cmd.extend([source, str(repo_path)])
        await self._run_checked_command(*clone_cmd, cwd=str(workspace_root))

        new_branch = str(workspace_spec.get("targetBranch") or "").strip()
        if new_branch:
            returncode, stdout_text, stderr_text = await self._run_command(
                "git",
                "-C",
                str(repo_path),
                "checkout",
                new_branch,
            )
            if returncode != 0:
                failure_detail = (stderr_text or stdout_text).lower()
                branch_missing = (
                    "did not match any file(s) known to git" in failure_detail
                    or "pathspec" in failure_detail
                )
                if not branch_missing:
                    redactor = SecretRedactor.from_environ()
                    detail = redactor.scrub(stderr_text or stdout_text or "no output")
                    rendered_cmd = " ".join(
                        shlex.quote(part)
                        for part in [
                            "git",
                            "-C",
                            str(repo_path),
                            "checkout",
                            new_branch,
                        ]
                    )
                    rendered_cmd = redactor.scrub(rendered_cmd)
                    raise RuntimeError(
                        f"Command failed with exit code {returncode}: {rendered_cmd}; {detail}"
                    )
                await self._run_checked_command(
                    "git",
                    "-C",
                    str(repo_path),
                    "checkout",
                    "-b",
                    new_branch,
                )

        return str(repo_path)

    @staticmethod
    async def _resolve_github_token_for_launch(env: dict[str, str]) -> str | None:
        token = str(env.get("GITHUB_TOKEN") or env.get("GH_TOKEN") or "").strip()
        if token:
            return token

        from moonmind.config.settings import settings as _mm_settings
        from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
            resolve_managed_api_key_reference,
        )

        secret_ref = str(
            getattr(_mm_settings.github, "github_token_secret_ref", "") or ""
        ).strip()
        if not secret_ref:
            return None

        try:
            return await resolve_managed_api_key_reference(secret_ref)
        except Exception:
            logger.warning(
                "Failed to resolve GitHub token secret ref for managed runtime launch",
                exc_info=True,
            )
            return None

    @staticmethod
    def _write_executable_script(path: Path, content: str) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(0o700)
        return str(path)

    @staticmethod
    def _render_gh_wrapper_script(*, socket_path: str, real_gh_path: str) -> str:
        return (
            "#!/usr/bin/env python3\n"
            "from moonmind.workflows.temporal.runtime.github_auth_broker import run_gh_wrapper\n"
            "\n"
            "if __name__ == \"__main__\":\n"
            f"    raise SystemExit(run_gh_wrapper(socket_path={socket_path!r}, real_gh_path={real_gh_path!r}))\n"
        )

    @staticmethod
    def _render_git_credential_helper_script(*, socket_path: str) -> str:
        return (
            "#!/usr/bin/env python3\n"
            "from moonmind.workflows.temporal.runtime.github_auth_broker import run_git_credential_helper\n"
            "\n"
            "if __name__ == \"__main__\":\n"
            f"    raise SystemExit(run_git_credential_helper(socket_path={socket_path!r}))\n"
        )

    @staticmethod
    def _format_git_config_value(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    @staticmethod
    def _persist_gh_config(
        env: dict[str, str],
        workspace_path: str | None,
        *,
        support_root: str | None = None,
        github_socket_path: str | None = None,
        real_gh_path: str | None = None,
    ) -> list[str]:
        """Persist workspace-scoped git/gh support config for managed runs.

        The generated support files give agent subprocesses a stable git/gh view
        of the workspace without writing plaintext GitHub credentials to disk.
        """
        if not workspace_path:
            return []

        resolved_workspace = str(Path(workspace_path).resolve())
        git_name = str(
            env.get("GIT_AUTHOR_NAME") or env.get("GIT_COMMITTER_NAME") or ""
        ).strip()
        git_email = str(
            env.get("GIT_AUTHOR_EMAIL") or env.get("GIT_COMMITTER_EMAIL") or ""
        ).strip()

        support_dir = Path(support_root or workspace_path) / ".moonmind"
        bin_dir = support_dir / "bin"
        git_config_path = support_dir / "gitconfig"
        cleanup_paths: list[str] = []
        git_helper_path: Path | None = None

        support_dir.mkdir(parents=True, exist_ok=True)
        cleanup_paths.append(str(git_config_path))

        if github_socket_path:
            git_helper_path = bin_dir / "git-credential-moonmind"
            cleanup_paths.append(
                ManagedRuntimeLauncher._write_executable_script(
                    git_helper_path,
                    ManagedRuntimeLauncher._render_git_credential_helper_script(
                        socket_path=github_socket_path,
                    ),
                )
            )
            if real_gh_path:
                cleanup_paths.append(
                    ManagedRuntimeLauncher._write_executable_script(
                        bin_dir / "gh",
                        ManagedRuntimeLauncher._render_gh_wrapper_script(
                            socket_path=github_socket_path,
                            real_gh_path=real_gh_path,
                        ),
                    )
                )
            existing_path = str(env.get("PATH") or "").strip()
            env["PATH"] = (
                f"{bin_dir}{os.pathsep}{existing_path}" if existing_path else str(bin_dir)
            )
            env.setdefault("GIT_TERMINAL_PROMPT", "0")

        git_config_lines = [
            "# moonmind-managed-git-config\n",
            "[safe]\n",
            (
                "\tdirectory = "
                f"{ManagedRuntimeLauncher._format_git_config_value(resolved_workspace)}\n"
            ),
        ]
        if git_helper_path is not None:
            git_helper_command = shlex.quote(str(git_helper_path))
            git_config_lines.extend(
                [
                    "[credential]\n",
                    f"\thelper = !{git_helper_command}\n",
                ]
            )
        if git_name or git_email:
            git_config_lines.append("[user]\n")
            if git_name:
                git_config_lines.append(f"\tname = {git_name}\n")
            if git_email:
                git_config_lines.append(f"\temail = {git_email}\n")
        git_config_path.write_text("".join(git_config_lines), encoding="utf-8")
        git_config_path.chmod(0o600)
        env["GIT_CONFIG_GLOBAL"] = str(git_config_path)

        if git_helper_path is None:
            return cleanup_paths

        repo_git_config_path = Path(workspace_path) / ".git" / "config"
        if not repo_git_config_path.exists():
            return cleanup_paths

        marker = "# moonmind-credential-helper"
        existing_config = repo_git_config_path.read_text(encoding="utf-8")
        if marker not in existing_config:
            git_helper_command = shlex.quote(str(git_helper_path))
            credential_section = (
                f"\n{marker}\n"
                f"[credential]\n"
                f"\thelper = !{git_helper_command}\n"
            )
            repo_git_config_path.write_text(
                existing_config + credential_section,
                encoding="utf-8",
            )
        return cleanup_paths

    async def cleanup_run_support(self, run_id: str) -> None:
        """Stop any in-memory runtime support services for the run."""
        await self._github_auth_brokers.stop(run_id)

    def build_command(
        self,
        profile: ManagedRuntimeProfile,
        request: AgentExecutionRequest,
        strategy: Any = None,
    ) -> list[str]:
        """Construct the CLI command from a runtime profile and request params."""
        cmd = list(profile.command_template)

        # --- Strategy delegation (Phase 1) ---
        # Check the strategy registry before falling through to the
        # legacy if/elif block.  Registered runtimes are handled by
        # their strategy; unregistered runtimes use the existing code.
        if strategy is None:
            from moonmind.workflows.temporal.runtime.strategies import get_strategy
            strategy = get_strategy(profile.runtime_id)
            
        if strategy is not None:
            return strategy.build_command(profile, request)

        # Generic fallback for any future unregistered runtime.
        model = (
            request.parameters.get("model") if request.parameters else None
        ) or profile.default_model
        if model:
            cmd.extend(["--model", model])
        effort = (
            request.parameters.get("effort") if request.parameters else None
        ) or profile.default_effort
        if effort:
            cmd.extend(["--effort", effort])
        if request.instruction_ref:
            cmd.extend(["--prompt", request.instruction_ref])

        return cmd

    async def launch(
        self,
        *,
        run_id: str,
        request: AgentExecutionRequest,
        profile: ManagedRuntimeProfile,
        workspace_path: str | Path | None = None,
        ) -> tuple[ManagedRunRecord, asyncio.subprocess.Process | None, list[str]]:
        """Spawn a subprocess for the managed agent run.

        Idempotency: if an active record already exists for run_id, returns it
        without launching a new process.

        Managed runs use a direct subprocess with piped stdout/stderr for log
        streaming. Terminal-relay endpoints are not part of this contract.
        """
        existing = self._store.load(run_id)
        if existing is not None and existing.status not in TERMINAL_AGENT_RUN_STATES:
            return existing, None, []

        from moonmind.workflows.temporal.runtime.strategies import get_strategy
        strategy = get_strategy(profile.runtime_id)
        resolved_workspace_path = await self._prepare_workspace_path(
            run_id=run_id,
            request=request,
            workspace_path=workspace_path,
        )
        if resolved_workspace_path is None:
            resolved_workspace_path = await self._prepare_workspace(
                run_id=run_id,
                request=request,
                workspace_path=workspace_path,
            )

        # Phase 4 Materialization
        from moonmind.workflows.adapters.materializer import ProviderProfileMaterializer
        from moonmind.workflows.adapters.secret_boundary import SecretResolverBoundary
        
        # Resolve secrets async up-front so the async materializer can access them.
        from moonmind.workflows.temporal.runtime.managed_api_key_resolve import resolve_managed_api_key_reference
        resolved_secrets = {}
        for ref_key, secret_name in (getattr(profile, "secret_refs", None) or {}).items():
            resolved_secrets[ref_key] = await resolve_managed_api_key_reference(secret_name)

        class AsyncDictSecretResolver(SecretResolverBoundary):
            async def resolve_secrets(self, secret_refs: dict[str, str]) -> dict[str, str]:
                return {k: resolved_secrets[k] for k in secret_refs if k in resolved_secrets}

        materializer = ProviderProfileMaterializer(
            base_env=dict(os.environ),
            secret_resolver=AsyncDictSecretResolver()
        )

        env_overrides, mat_cmd = await materializer.materialize(profile)
        # Update profile with the materialized command template so build_command uses it
        profile.command_template = mat_cmd

        cmd = self.build_command(profile, request, strategy=strategy)
        
        # Invoke strategy-level workspace preparation hook (e.g. RAG context
        # injection for Codex).
        if resolved_workspace_path is not None and strategy is not None:
            try:
                await strategy.prepare_workspace(
                    Path(resolved_workspace_path), request
                )
            except Exception:
                logger.warning(
                    "strategy.prepare_workspace failed for run_id=%s runtime=%s",
                    run_id,
                    profile.runtime_id,
                    exc_info=True,
                )

        # Expose active skills projection if present
        run_root: Path | None = None
        if resolved_workspace_path is not None:
            run_root = Path(resolved_workspace_path).resolve().parent
            active_skills_dir = run_root / ".agents" / "skills_active"
            if active_skills_dir.exists():
                workspace_agents_dir = Path(resolved_workspace_path) / ".agents"
                target_skills_dir = workspace_agents_dir / "skills"
                active_link = target_skills_dir / "active"
                try:
                    if target_skills_dir.exists():
                        if target_skills_dir.is_symlink():
                            logger.warning(
                                "Expected .agents/skills to be a directory, but found a symlink at %s; "
                                "skipping active skills linkage.",
                                target_skills_dir,
                            )
                            raise OSError("Cannot link active skills into symlinked .agents/skills")
                    else:
                        target_skills_dir.mkdir(parents=True)

                    if active_link.lexists():
                        if not active_link.is_symlink():
                            logger.warning(
                                "Expected .agents/skills/active to be a symlink, but found %s; "
                                "leaving it unchanged.",
                                active_link,
                            )
                        else:
                            active_link.unlink()
                    
                    if not active_link.lexists():
                        active_link.symlink_to(active_skills_dir, target_is_directory=True)
                except OSError as ex:
                    logger.warning("Failed to link active skills directory: %s", ex)

        if strategy is not None:
            env_overrides = strategy.shape_environment(env_overrides, profile)

        for key in profile.passthrough_env_keys:
            value = os.environ.get(key)
            if value is None or not str(value).strip():
                continue
            env_overrides[key] = value

        from moonmind.config.settings import settings as _mm_settings
        _git_name = str(_mm_settings.workflow.git_user_name or "").strip() or None
        _git_email = str(_mm_settings.workflow.git_user_email or "").strip() or None
        if _git_name:
            env_overrides.setdefault("GIT_AUTHOR_NAME", _git_name)
            env_overrides.setdefault("GIT_COMMITTER_NAME", _git_name)
        if _git_email:
            env_overrides.setdefault("GIT_AUTHOR_EMAIL", _git_email)
            env_overrides.setdefault("GIT_COMMITTER_EMAIL", _git_email)

        github_token = await self._resolve_github_token_for_launch(env_overrides)
        cleanup_paths: list[str] = list(materializer.generated_files)
        github_socket_path: str | None = None
        real_gh_path = shutil.which("gh")
        process: asyncio.subprocess.Process
        try:
            if github_token and run_root is not None:
                github_socket_path = self._build_github_socket_path(
                    run_id=run_id,
                    support_root=str(run_root),
                )
                await self._github_auth_brokers.start(
                    run_id=run_id,
                    token=github_token,
                    socket_path=github_socket_path,
                )
                cleanup_paths.append(github_socket_path)
                env_overrides.pop("GITHUB_TOKEN", None)
                env_overrides.pop("GH_TOKEN", None)

            if resolved_workspace_path is not None:
                cleanup_paths.extend(
                    self._persist_gh_config(
                        env_overrides,
                        resolved_workspace_path,
                        support_root=str(run_root),
                        github_socket_path=github_socket_path,
                        real_gh_path=real_gh_path,
                    )
                )

            # The claude CLI refuses --dangerously-skip-permissions when running as root
            # (security restriction). For claude_code runtime, drop to the app user.
            _run_as_root = os.geteuid() == 0
            _is_claude_code = profile.runtime_id == "claude_code"
            _needs_priv_drop = _run_as_root and _is_claude_code

            # Transfer the full run workspace root to the app user so it can write
            # both repo files and launcher support artifacts beside the repo.
            # Chowning only the repo subtree leaves root-owned support paths behind.
            if _needs_priv_drop and resolved_workspace_path is not None:
                ownership_root = self._resolve_workspace_ownership_root(
                    resolved_workspace_path=resolved_workspace_path,
                    run_id=run_id,
                )
                await self._run_checked_command(
                    "chown", "-R", "app:app", ownership_root,
                )

            if _needs_priv_drop:
                # runuser -u does not rewrite HOME/USER/LOGNAME for us when we pass
                # an explicit env block, so seed the target-user login context
                # explicitly before launching Claude Code.
                env_overrides["HOME"] = "/home/app"
                env_overrides["USER"] = "app"
                env_overrides["LOGNAME"] = "app"
                # Use runuser with env= so secrets do not appear in process argv.
                process = await asyncio.create_subprocess_exec(
                    "runuser", "-u", "app", "--",
                    *cmd,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env_overrides,
                    cwd=resolved_workspace_path,
                )
            else:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env_overrides,
                    cwd=resolved_workspace_path,
                )
        except Exception:
            await self.cleanup_run_support(run_id)
            for path in cleanup_paths:
                try:
                    os.remove(path)
                except OSError:
                    self._logger.debug(
                        "Best-effort cleanup failed for support path %s",
                        path,
                        exc_info=True,
                    )
            raise

        record = ManagedRunRecord(
            run_id=run_id,
            agent_id=request.agent_id,
            runtime_id=profile.runtime_id,
            status="launching",
            pid=process.pid,
            started_at=datetime.now(tz=UTC),
            workspace_path=resolved_workspace_path,
            live_stream_capable=bool(resolved_workspace_path),
        )
        self._store.save(record)
        return record, process, cleanup_paths
