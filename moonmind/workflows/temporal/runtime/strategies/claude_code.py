"""Claude Code managed runtime strategy."""

from __future__ import annotations

from typing import Any

from moonmind.workflows.temporal.runtime.strategies.base import (
    ManagedRuntimeStrategy,
)


class ClaudeCodeStrategy(ManagedRuntimeStrategy):
    """Strategy for launching ``claude`` CLI runs."""

    @property
    def runtime_id(self) -> str:
        return "claude_code"

    @property
    def default_command_template(self) -> list[str]:
        return ["claude"]

    def build_command(
        self,
        profile: Any,
        request: Any,
    ) -> list[str]:
        """Construct Claude Code CLI command.

        Extracted from ``ManagedRuntimeLauncher.build_command()``
        (the generic/claude_code else branch).
        """
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
            cmd.extend(["--prompt", request.instruction_ref])

        return cmd
