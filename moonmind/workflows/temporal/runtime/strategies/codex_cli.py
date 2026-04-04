"""Codex CLI managed runtime strategy."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.runtime.strategies.base import (
    ManagedRuntimeStrategy,
)

# Environment keys that Codex CLI expects in the subprocess environment.
_CODEX_ENV_PASSTHROUGH_KEYS: frozenset[str] = frozenset({
    "HOME",
    "CODEX_HOME",
    "CODEX_CONFIG_HOME",
    "CODEX_CONFIG_PATH",
})
_CODEX_PROGRESS_TIMEOUT_SECONDS = 300
_CODEX_PROGRESS_MTIME_PADDING_SECONDS = 5.0


class CodexCliStrategy(ManagedRuntimeStrategy):
    """Strategy for launching ``codex`` CLI runs."""

    _MANAGED_POLICY_FLAGS = frozenset({
        "--full-auto",
        "--dangerously-bypass-approvals-and-sandbox",
    })
    _MANAGED_POLICY_FLAGS_WITH_VALUE = frozenset({
        "-a",
        "--ask-for-approval",
        "-s",
        "--sandbox",
    })

    @property
    def runtime_id(self) -> str:
        return "codex_cli"

    @property
    def default_command_template(self) -> list[str]:
        return ["codex", "exec"]

    def progress_stall_timeout_seconds(self, *, timeout_seconds: int) -> int | None:
        """Cap how long a Codex run may stop advancing its session state."""

        normalized_timeout = int(timeout_seconds) if timeout_seconds > 0 else 0
        if normalized_timeout <= 0:
            return _CODEX_PROGRESS_TIMEOUT_SECONDS
        return min(normalized_timeout, _CODEX_PROGRESS_TIMEOUT_SECONDS)

    def build_command(
        self,
        profile: Any,
        request: Any,
    ) -> list[str]:
        """Construct Codex CLI command.

        Extracted from ``ManagedRuntimeLauncher.build_command()``
        (the ``codex_cli`` if branch).

        Codex CLI uses ``-m`` for model and does NOT support
        ``--effort``.  Prompt is a positional argument.
        """
        cmd = self._sanitize_command_template(profile.command_template)

        requested_model = (
            str(request.parameters.get("model") or "").strip()
            if request.parameters
            else ""
        )
        command_behavior = getattr(profile, "command_behavior", {}) or {}
        suppress_default_model_flag = bool(
            command_behavior.get("suppress_default_model_flag")
        )

        model = None
        if requested_model:
            model = self.get_model(profile, request)
        elif not suppress_default_model_flag:
            model = self.get_model(profile, request)
        if model:
            cmd.extend(["-m", model])

        if request.instruction_ref:
            cmd.append(request.instruction_ref)

        return cmd

    @classmethod
    def _sanitize_command_template(cls, command_template: list[str]) -> list[str]:
        """Drop sandbox/approval flags so managed policy comes only from config."""

        sanitized: list[str] = []
        skip_next = False
        managed_policy_flags = (
            cls._MANAGED_POLICY_FLAGS | cls._MANAGED_POLICY_FLAGS_WITH_VALUE
        )
        for part in command_template:
            if skip_next:
                skip_next = False
                continue
            if part in cls._MANAGED_POLICY_FLAGS:
                continue
            if part in cls._MANAGED_POLICY_FLAGS_WITH_VALUE:
                skip_next = True
                continue
            if any(part.startswith(flag + "=") for flag in managed_policy_flags):
                continue
            sanitized.append(part)
        return sanitized

    def shape_environment(
        self,
        base_env: dict[str, str],
        profile: Any,
    ) -> dict[str, str]:
        """Pass through Codex-relevant environment keys.
        
        Adds Codex config variables from the worker process environment
        to base_env when present.
        """
        import os
        
        env = dict(base_env)
        for k in _CODEX_ENV_PASSTHROUGH_KEYS:
            if k not in env and k in os.environ:
                env[k] = os.environ[k]
        return env

    async def prepare_workspace(
        self,
        workspace_path: Path,
        request: AgentExecutionRequest,
    ) -> None:
        """Inject RAG context into the instruction before building the command."""
        from moonmind.rag.context_injection import ContextInjectionService
        service = ContextInjectionService()
        await service.inject_context(
            request=request,
            workspace_path=workspace_path,
        )

    def probe_progress_at(
        self,
        *,
        workspace_path: str | None,
        run_id: str,
        started_at: datetime,
    ) -> datetime | None:
        """Use Codex session artifacts as the managed-run progress signal."""

        codex_home = self._resolve_codex_home(workspace_path)
        if codex_home is None:
            return None

        threshold_ts = started_at.timestamp() - _CODEX_PROGRESS_MTIME_PADDING_SECONDS
        latest_ts: float | None = None
        for pattern in ("sessions/**/*.jsonl", "state_*.sqlite", "logs_*.sqlite"):
            for candidate in codex_home.glob(pattern):
                try:
                    stat = candidate.stat()
                except OSError:
                    continue
                if stat.st_mtime < threshold_ts:
                    continue
                if latest_ts is None or stat.st_mtime > latest_ts:
                    latest_ts = stat.st_mtime

        if latest_ts is None:
            return None
        return datetime.fromtimestamp(latest_ts, tz=UTC)

    @staticmethod
    def _resolve_codex_home(workspace_path: str | None) -> Path | None:
        normalized = str(workspace_path or "").strip()
        if not normalized:
            return None

        resolved_workspace = Path(normalized).resolve()
        run_root = (
            resolved_workspace.parent
            if resolved_workspace.name == "repo"
            else resolved_workspace
        )
        codex_home = run_root / ".moonmind" / "codex-home"
        if not codex_home.exists():
            return None
        return codex_home
