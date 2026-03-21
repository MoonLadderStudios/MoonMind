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

_OWNER_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class ManagedRuntimeLauncher:
    """Spawns managed agent subprocesses and records them in the run store."""

    def __init__(self, store: ManagedRunStore) -> None:
        self._store = store
        self._logger = logging.getLogger(__name__)

    @staticmethod
    def _extract_workspace_branch(workspace_spec: dict[str, object] | None) -> str | None:
        if not isinstance(workspace_spec, dict):
            return None
        for key in ("newBranch", "startingBranch", "branch"):
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
    def _build_tmate_wrapper_script(
        cmd: list[str],
        *,
        socket_path: str,
        session_name: str,
    ) -> str:
        """Build a shell script that runs agent cmd and force-closes tmate session."""
        cmd_str = shlex.join(cmd)
        return (
            "#!/usr/bin/env bash\n"
            "set +e\n"
            f"{cmd_str}\n"
            "mm_rc=$?\n"
            f"tmate -S {shlex.quote(socket_path)} "
            f"kill-session -t {shlex.quote(session_name)} >/dev/null 2>&1 || true\n"
            "exit \"$mm_rc\"\n"
        )

    def build_command(
        self,
        profile: ManagedRuntimeProfile,
        request: AgentExecutionRequest,
    ) -> list[str]:
        """Construct the CLI command from a runtime profile and request params."""
        cmd = list(profile.command_template)

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
            if profile.runtime_id == "gemini_cli":
                cmd.extend(["--yolo", "--prompt", request.instruction_ref])
            else:
                cmd.extend(["--instruction-ref", request.instruction_ref])

        return cmd

    async def launch(
        self,
        *,
        run_id: str,
        request: AgentExecutionRequest,
        profile: ManagedRuntimeProfile,
        workspace_path: str | None = None,
    ) -> tuple[ManagedRunRecord, asyncio.subprocess.Process, dict[str, str] | None]:
        """Spawn a subprocess for the managed agent run.

        Idempotency: if an active record already exists for run_id, returns it
        without launching a new process.
        """
        existing = self._store.load(run_id)
        if existing is not None and existing.status not in (
            "completed",
            "failed",
            "cancelled",
            "timed_out",
        ):
            raise RuntimeError(
                f"Active run already exists for run_id={run_id}"
            )

        cmd = self.build_command(profile, request)
        workspace_path = await self._prepare_workspace(
            run_id=run_id,
            request=request,
            workspace_path=workspace_path,
        )
        env_overrides = dict(profile.env_overrides) if profile.env_overrides else dict(os.environ)
        
        # Ensure HOME and GEMINI_HOME are set for gemini cli
        if "GEMINI_HOME" not in env_overrides and "GEMINI_HOME" in os.environ:
            env_overrides["GEMINI_HOME"] = os.environ["GEMINI_HOME"]
        if "GEMINI_CLI_HOME" not in env_overrides and "GEMINI_CLI_HOME" in os.environ:
            env_overrides["GEMINI_CLI_HOME"] = os.environ["GEMINI_CLI_HOME"]
        if "HOME" not in env_overrides and "HOME" in os.environ:
            env_overrides["HOME"] = os.environ["HOME"]

        use_tmate = shutil.which("tmate") is not None
        endpoints = None

        if use_tmate:
            socket_dir = "/tmp/moonmind/tmate"
            os.makedirs(socket_dir, exist_ok=True)
            socket_path = os.path.join(socket_dir, f"{run_id}.sock")
            wrapper_path = Path(socket_dir) / f"{run_id}.wrapper.sh"
            if os.path.exists(socket_path):
                os.remove(socket_path)
            if wrapper_path.exists():
                wrapper_path.unlink()
                
            session_name = f"mm-{run_id.replace('-', '')[:16]}"
            wrapper_script = self._build_tmate_wrapper_script(
                cmd,
                socket_path=socket_path,
                session_name=session_name,
            )
            wrapper_path.write_text(wrapper_script, encoding="utf-8")
            wrapper_path.chmod(0o700)
            session_cmd = shlex.join(["bash", str(wrapper_path)])

            # Start tmate server in foreground
            tmate_cmd = [
                "tmate", "-S", socket_path, "-F",
                "new-session", "-A", "-s", session_name, session_cmd
            ]
            
            process = await asyncio.create_subprocess_exec(
                *tmate_cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env_overrides,
                cwd=workspace_path,
            )
            
            try:
                # Wait for tmate-ready
                async def _wait_for_ready():
                    ready_proc = await asyncio.create_subprocess_exec("tmate", "-S", socket_path, "wait", "tmate-ready")
                    await ready_proc.wait()
                
                await asyncio.wait_for(_wait_for_ready(), timeout=10.0)
                
                async def get_endpoint(key):
                    p = await asyncio.create_subprocess_exec(
                        "tmate", "-S", socket_path, "display", "-p", f"#{{{key}}}",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    stdout, _ = await p.communicate()
                    return stdout.decode().strip() or None

                endpoints = {
                    "attach_ro": await get_endpoint("tmate_ssh_ro"),
                    "attach_rw": await get_endpoint("tmate_ssh"),
                    "web_ro": await get_endpoint("tmate_web_ro"),
                    "web_rw": await get_endpoint("tmate_web"),
                    "tmate_session_name": session_name,
                    "tmate_socket_path": socket_path,
                }
            except Exception as e:
                self._logger.warning("Failed to fetch tmate endpoints: %s", e)
                pass # Proceed even if endpoints fail
                
        else:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env_overrides,
                cwd=workspace_path,
            )

        record = ManagedRunRecord(
            run_id=run_id,
            agent_id=request.agent_id,
            runtime_id=profile.runtime_id,
            status="launching",
            pid=process.pid,
            started_at=datetime.now(tz=UTC),
            workspace_path=workspace_path,
        )
        self._store.save(record)
        return record, process, endpoints
