"""Managed runtime subprocess launcher."""

from __future__ import annotations

import asyncio
import logging
import os
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

logger = logging.getLogger(__name__)

TMATE_SOCKET_DIR = Path("/tmp/moonmind/tmate")
TMATE_FOREGROUND_RESTART_OFF = "set tmate-foreground-restart 0\n"


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
        env_overrides = dict(profile.env_overrides) if profile.env_overrides else dict(
            os.environ
        )

        # Ensure HOME and GEMINI_HOME are set for gemini cli
        if "GEMINI_HOME" not in env_overrides and "GEMINI_HOME" in os.environ:
            env_overrides["GEMINI_HOME"] = os.environ["GEMINI_HOME"]
        if "GEMINI_CLI_HOME" not in env_overrides and "GEMINI_CLI_HOME" in os.environ:
            env_overrides["GEMINI_CLI_HOME"] = os.environ["GEMINI_CLI_HOME"]
        if "HOME" not in env_overrides and "HOME" in os.environ:
            env_overrides["HOME"] = os.environ["HOME"]

        use_tmate = shutil.which("tmate") is not None
        endpoints: dict[str, str] | None = None

        if use_tmate:
            TMATE_SOCKET_DIR.mkdir(parents=True, exist_ok=True)
            socket_path = TMATE_SOCKET_DIR / f"{run_id}.sock"
            config_path = TMATE_SOCKET_DIR / f"{run_id}.conf"
            exit_code_path = TMATE_SOCKET_DIR / f"{run_id}.exit"
            for path in (socket_path, config_path, exit_code_path):
                path.unlink(missing_ok=True)

            config_path.write_text(
                TMATE_FOREGROUND_RESTART_OFF,
                encoding="utf-8",
            )
            env_overrides["MM_EXIT_FILE"] = str(exit_code_path)

            session_name = f"mm-{run_id.replace('-', '')[:16]}"
            wrapped_command = shlex.join(
                [
                    "bash",
                    "-c",
                    "\"$@\"\n"
                    "rc=$?\n"
                    "printf '%s\\n' \"$rc\" > \"$MM_EXIT_FILE\"\n"
                    "exit 0\n",
                    "--",
                    *cmd,
                ]
            )

            tmate_cmd = [
                "tmate",
                "-S",
                str(socket_path),
                "-f",
                str(config_path),
                "-F",
                "new-session",
                "-A",
                "-s",
                session_name,
                wrapped_command,
            ]

            process = await asyncio.create_subprocess_exec(
                *tmate_cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env_overrides,
                cwd=workspace_path,
            )

            endpoints = {
                "tmate_session_name": session_name,
                "tmate_socket_path": str(socket_path),
                "tmate_config_path": str(config_path),
                "exit_code_path": str(exit_code_path),
            }

            try:
                async def _wait_for_ready() -> None:
                    ready_proc = await asyncio.create_subprocess_exec(
                        "tmate",
                        "-S",
                        str(socket_path),
                        "wait",
                        "tmate-ready",
                    )
                    await ready_proc.wait()

                await asyncio.wait_for(_wait_for_ready(), timeout=10.0)

                async def get_endpoint(key: str) -> str | None:
                    endpoint_proc = await asyncio.create_subprocess_exec(
                        "tmate",
                        "-S",
                        str(socket_path),
                        "display",
                        "-p",
                        f"#{{{key}}}",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, _ = await endpoint_proc.communicate()
                    value = stdout.decode().strip()
                    return value or None

                for key, endpoint_key in (
                    ("attach_ro", "tmate_ssh_ro"),
                    ("attach_rw", "tmate_ssh"),
                    ("web_ro", "tmate_web_ro"),
                    ("web_rw", "tmate_web"),
                ):
                    endpoint_value = await get_endpoint(endpoint_key)
                    if endpoint_value:
                        endpoints[key] = endpoint_value
            except Exception:
                logger.warning(
                    "Failed to fetch tmate endpoints for run_id=%s",
                    run_id,
                    exc_info=True,
                )
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
