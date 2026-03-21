"""Codex CLI managed runtime strategy."""

from __future__ import annotations

from typing import Any

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

    @property
    def runtime_id(self) -> str:
        return "codex_cli"

    @property
    def default_command_template(self) -> list[str]:
        return ["codex", "exec", "--full-auto"]

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
        cmd = list(profile.command_template)

        model = (
            request.parameters.get("model") if request.parameters else None
        ) or profile.default_model
        if model:
            cmd.extend(["-m", model])

        if request.instruction_ref:
            cmd.append(request.instruction_ref)

        return cmd

    def shape_environment(
        self,
        base_env: dict[str, str],
        profile: Any,
    ) -> dict[str, str]:
        """Pass through only Codex-relevant environment keys."""
        return {
            k: v for k, v in base_env.items()
            if k in _CODEX_ENV_PASSTHROUGH_KEYS
        }
