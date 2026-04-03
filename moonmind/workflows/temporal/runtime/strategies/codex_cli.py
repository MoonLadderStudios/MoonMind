"""Codex CLI managed runtime strategy."""

from __future__ import annotations

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
        for part in command_template:
            if skip_next:
                skip_next = False
                continue
            if part in cls._MANAGED_POLICY_FLAGS:
                continue
            if part in cls._MANAGED_POLICY_FLAGS_WITH_VALUE:
                skip_next = True
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
