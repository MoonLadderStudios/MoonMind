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
)

from .store import ManagedRunStore
from .tmate_session import TmateSessionManager

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

        self._logger.warning(
            "git %s failed rc=%s stdout=%s stderr=%s",
            " ".join(args),
            process.returncode,
            stdout.decode("utf-8", errors="replace").strip()[:400],
            stderr.decode("utf-8", errors="replace").strip()[:400],
        )
        if allow_failure:
            return False
        raise RuntimeError(f"git {' '.join(args)} failed with exit code {process.returncode}")

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
        detail = stderr_text or stdout_text or "no output"
        rendered_cmd = " ".join(shlex.quote(part) for part in cmd)
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
                    detail = stderr_text or stdout_text or "no output"
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
    ) -> tuple[ManagedRunRecord, asyncio.subprocess.Process, dict[str, str] | None]:
        """Spawn a subprocess for the managed agent run.

        Idempotency: if an active record already exists for run_id, returns it
        without launching a new process.
        """
        existing = self._store.load(run_id)
        if existing is not None and existing.status not in (
            "completed",
            "failed",
            "canceled",
            "timed_out",
        ):
            raise RuntimeError(
                f"Active run already exists for run_id={run_id}"
            )

        from moonmind.workflows.temporal.runtime.strategies import get_strategy
        strategy = get_strategy(profile.runtime_id)

        cmd = self.build_command(profile, request, strategy=strategy)
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

        env_overrides = dict(profile.env_overrides) if profile.env_overrides else dict(
            os.environ
        )

        # Invoke strategy-level workspace preparation hook (e.g. RAG context
        # injection for Codex, .cursor/ config files for Cursor CLI).
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

        if strategy is not None:
            env_overrides = strategy.shape_environment(env_overrides, profile)

        for key in profile.passthrough_env_keys:
            value = os.environ.get(key)
            if value is None or not str(value).strip():
                continue
            env_overrides[key] = value
        use_tmate = TmateSessionManager.is_available()
        endpoints: dict[str, str] | None = None
        tmate_manager: TmateSessionManager | None = None

        if use_tmate:
            session_name = f"mm-{run_id.replace('-', '')[:16]}"
            tmate_manager = TmateSessionManager(session_name=session_name)
            try:
                tmate_endpoints = await tmate_manager.start(
                    command=cmd,
                    env=env_overrides,
                    cwd=resolved_workspace_path,
                    exit_code_capture=True,
                )
                process = tmate_manager.process
                # Verify the tmate-wrapped process is actually alive.
                # If tmate crashed during startup the process will already
                # have a non-None returncode.
                if process is None or (
                    process.returncode is not None and process.returncode != 0
                ):
                    raise RuntimeError(
                        f"tmate process exited immediately "
                        f"(rc={getattr(process, 'returncode', '?')})"
                    )
            except Exception:
                logger.warning(
                    "Tmate session failed for run_id=%s; "
                    "falling back to plain subprocess.",
                    run_id,
                    exc_info=True,
                )
                # Clean up the failed tmate session.
                try:
                    await tmate_manager.teardown()
                except Exception:
                    logger.debug(
                        "tmate teardown after fallback failed", exc_info=True
                    )
                tmate_manager = None
                use_tmate = False
                # Fall through to the plain subprocess path below.

        if use_tmate and tmate_manager is not None:
            tmate_endpoints = tmate_manager.endpoints
            process = tmate_manager.process
            endpoints = {
                "tmate_session_name": tmate_endpoints.session_name,
                "tmate_socket_path": tmate_endpoints.socket_path,
                "tmate_config_path": str(tmate_manager.config_path),
            }
            if tmate_manager.exit_code_path is not None:
                endpoints["exit_code_path"] = str(tmate_manager.exit_code_path)
            for attr in ("attach_ro", "attach_rw", "web_ro", "web_rw"):
                value = getattr(tmate_endpoints, attr, None)
                if value:
                    endpoints[attr] = value

        if not use_tmate:
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
        )
        self._store.save(record)
        return record, process, endpoints, tmate_manager
