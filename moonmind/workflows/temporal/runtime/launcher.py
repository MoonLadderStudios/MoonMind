"""Managed runtime subprocess launcher."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import shutil
from datetime import UTC, datetime
from pathlib import Path

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    ManagedRunRecord,
    ManagedRuntimeProfile,
    TERMINAL_AGENT_RUN_STATES,
)
from moonmind.utils.logging import SecretRedactor

from .store import ManagedRunStore

_OWNER_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

logger = logging.getLogger(__name__)


class ManagedRuntimeLauncher:
    """Spawns managed agent subprocesses and records them in the run store."""

    def __init__(self, store: ManagedRunStore) -> None:
        self._store = store
        self._logger = logging.getLogger(__name__)

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
    def _persist_gh_config(
        env: dict[str, str],
        workspace_path: str | None,
        *,
        support_root: str | None = None,
    ) -> None:
        """Write ``gh`` file-based auth so ``gh`` works even when AI CLIs
        strip secret env vars from tool-call subprocesses.

        Sets ``GH_CONFIG_DIR`` in *env* to a per-workspace directory
        containing ``hosts.yml`` with the GitHub token.

        Also injects a git credential helper into the workspace's
        ``.git/config`` so that ``git push`` can authenticate even when
        env vars like ``GITHUB_TOKEN`` are stripped from tool-call
        subprocesses (e.g. Claude Code, Gemini CLI).  Writing into
        ``.git/config`` is robust because git always reads this file
        regardless of the subprocess environment.
        """
        token = env.get("GITHUB_TOKEN") or env.get("GH_TOKEN")
        if not token or not workspace_path:
            return
        gh_root = Path(support_root or workspace_path)
        gh_dir = gh_root / ".moonmind" / "gh"
        try:
            gh_dir.mkdir(parents=True, exist_ok=True)
            hosts_path = gh_dir / "hosts.yml"
            hosts_path.write_text(
                f"github.com:\n"
                f"  oauth_token: {token}\n"
                f"  git_protocol: https\n",
                encoding="utf-8",
            )
            hosts_path.chmod(0o600)
            env["GH_CONFIG_DIR"] = str(gh_dir)
        except OSError:
            logger.debug("Failed to persist gh config", exc_info=True)
            return

        # Inject git credential helper into .git/config so git push works
        # even when the AI CLI strips env vars from tool-call subprocesses.
        try:
            git_config_path = Path(workspace_path) / ".git" / "config"
            if not git_config_path.exists():
                return

            # Write a git-credential-store file with the token
            cred_store_path = gh_dir / "git-credentials"
            cred_store_path.write_text(
                f"https://x-access-token:{token}@github.com\n",
                encoding="utf-8",
            )
            cred_store_path.chmod(0o600)

            # Append credential helper config to .git/config if not already
            # present.  We check for our marker to avoid duplicating on
            # idempotent re-launches.
            marker = "# moonmind-credential-helper"
            existing_config = git_config_path.read_text(encoding="utf-8")
            if marker not in existing_config:
                credential_section = (
                    f"\n{marker}\n"
                    f"[credential]\n"
                    f'\thelper = store --file="{cred_store_path}"\n'
                )
                git_config_path.write_text(
                    existing_config + credential_section,
                    encoding="utf-8",
                )
        except OSError:
            logger.debug("Failed to persist git credential config", exc_info=True)

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
        ) -> tuple[ManagedRunRecord, asyncio.subprocess.Process | None, dict[str, str] | None, list[str]]:
        """Spawn a subprocess for the managed agent run.

        Idempotency: if an active record already exists for run_id, returns it
        without launching a new process.

        External live-session relay endpoints are not produced here; managed runs use a
        direct subprocess with piped stdout/stderr for log streaming.
        """
        existing = self._store.load(run_id)
        if existing is not None and existing.status not in TERMINAL_AGENT_RUN_STATES:
            return existing, None, None, []

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

        if resolved_workspace_path is not None:
            self._persist_gh_config(
                env_overrides,
                resolved_workspace_path,
                support_root=str(run_root),
            )

        from moonmind.config.settings import settings as _mm_settings
        _git_name = str(_mm_settings.workflow.git_user_name or "").strip() or None
        _git_email = str(_mm_settings.workflow.git_user_email or "").strip() or None
        if _git_name:
            env_overrides.setdefault("GIT_AUTHOR_NAME", _git_name)
            env_overrides.setdefault("GIT_COMMITTER_NAME", _git_name)
        if _git_email:
            env_overrides.setdefault("GIT_AUTHOR_EMAIL", _git_email)
            env_overrides.setdefault("GIT_COMMITTER_EMAIL", _git_email)

        # The claude CLI refuses --dangerously-skip-permissions when running as root
        # (security restriction). For claude_code runtime, drop to the app user.
        _run_as_root = os.geteuid() == 0
        _is_claude_code = profile.runtime_id == "claude_code"
        _needs_priv_drop = _run_as_root and _is_claude_code

        # Transfer the full run workspace root to the app user so it can write
        # both the repo checkout and support files materialized alongside it
        # (for example .moonmind/GH_CONFIG_DIR and active skill projections).
        # Chowning only the repo subtree leaves root-owned support paths behind
        # and the dropped user can hang or fail before producing any output.
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
        return record, process, None, materializer.generated_files
