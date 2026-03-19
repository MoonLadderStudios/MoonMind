"""Managed runtime subprocess launcher."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    ManagedRunRecord,
    ManagedRuntimeProfile,
)

from .store import ManagedRunStore


class ManagedRuntimeLauncher:
    """Spawns managed agent subprocesses and records them in the run store."""

    def __init__(self, store: ManagedRunStore) -> None:
        self._store = store

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
        env_overrides = dict(profile.env_overrides) if profile.env_overrides else dict(os.environ)
        
        # Ensure HOME and GEMINI_HOME are set for gemini cli
        if "GEMINI_HOME" not in env_overrides and "GEMINI_HOME" in os.environ:
            env_overrides["GEMINI_HOME"] = os.environ["GEMINI_HOME"]
        if "GEMINI_CLI_HOME" not in env_overrides and "GEMINI_CLI_HOME" in os.environ:
            env_overrides["GEMINI_CLI_HOME"] = os.environ["GEMINI_CLI_HOME"]
        if "HOME" not in env_overrides and "HOME" in os.environ:
            env_overrides["HOME"] = os.environ["HOME"]

        import shutil
        import shlex
        import logging
        logger = logging.getLogger(__name__)
        
        use_tmate = shutil.which("tmate") is not None
        endpoints = None

        if use_tmate:
            socket_dir = "/tmp/moonmind/tmate"
            os.makedirs(socket_dir, exist_ok=True)
            socket_path = os.path.join(socket_dir, f"{run_id}.sock")
            if os.path.exists(socket_path):
                os.remove(socket_path)
                
            session_name = f"mm-{run_id.replace('-', '')[:16]}"
            cmd_str = " ".join(shlex.quote(c) for c in cmd)
            
            # Start tmate server in foreground
            tmate_cmd = [
                "tmate", "-S", socket_path, "-F",
                "new-session", "-A", "-s", session_name, cmd_str
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
                logger.warning(f"Failed to fetch tmate endpoints: {e}")
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
