"""Gemini CLI managed runtime strategy."""

from __future__ import annotations

from typing import Any

from moonmind.workflows.temporal.runtime.strategies.base import (
    ManagedRuntimeStrategy,
)

# Environment keys that Gemini CLI expects in the subprocess environment.
_GEMINI_ENV_PASSTHROUGH_KEYS: frozenset[str] = frozenset({
    "HOME",
    "GEMINI_HOME",
    "GEMINI_CLI_HOME",
})


class GeminiCliStrategy(ManagedRuntimeStrategy):
    """Strategy for launching ``gemini`` CLI runs."""

    @property
    def runtime_id(self) -> str:
        return "gemini_cli"

    @property
    def default_command_template(self) -> list[str]:
        return ["gemini"]

    @property
    def default_auth_mode(self) -> str:
        return "api_key"

    def build_command(
        self,
        profile: Any,
        request: Any,
    ) -> list[str]:
        """Construct Gemini CLI command.

        Extracted from ``ManagedRuntimeLauncher.build_command()``
        (the ``gemini_cli`` elif branch).
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
            cmd.extend(["--yolo", "--prompt", request.instruction_ref])

        return cmd

    def shape_environment(
        self,
        base_env: dict[str, str],
        profile: Any,
    ) -> dict[str, str]:
        """Pass through only Gemini-relevant environment keys.

        Picks ``HOME``, ``GEMINI_HOME``, and ``GEMINI_CLI_HOME`` from the
        worker process environment when present.
        """
        return {
            k: v for k, v in base_env.items()
            if k in _GEMINI_ENV_PASSTHROUGH_KEYS
        }
