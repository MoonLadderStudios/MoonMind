"""Gemini CLI managed runtime strategy."""

from __future__ import annotations

from typing import Any

from moonmind.workflows.temporal.runtime.output_parser import (
    GeminiCliOutputParser,
    ParsedOutput,
    RuntimeOutputParser,
)
from moonmind.workflows.temporal.runtime.strategies.base import (
    ManagedRuntimeExitResult,
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

        model = self.get_model(profile, request)
        if model:
            cmd.extend(["--model", model])

        if request.instruction_ref:
            cmd.extend(["--yolo", "--prompt", request.instruction_ref])

        return cmd

    def shape_environment(
        self,
        base_env: dict[str, str],
        profile: Any,
    ) -> dict[str, str]:
        """Pass through Gemini-relevant environment keys.

        Picks ``HOME``, ``GEMINI_HOME``, and ``GEMINI_CLI_HOME`` from the
        worker process environment when present, adding them to base_env.
        """
        import os

        env = dict(base_env)
        for k in _GEMINI_ENV_PASSTHROUGH_KEYS:
            if k not in env and k in os.environ:
                env[k] = os.environ[k]
        return env

    def classify_exit(
        self,
        exit_code: int | None,
        stdout: str,
        stderr: str,
    ) -> tuple[str, str | None]:
        parser = self.create_output_parser()
        parsed = parser.parse(stdout, stderr)
        if parsed.rate_limited:
            return "failed", "integration_error"
        if exit_code == 0:
            return "completed", None
        return "failed", "execution_error"

    def classify_result(
        self,
        *,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        parsed_output: ParsedOutput | None = None,
    ) -> ManagedRuntimeExitResult:
        parsed = parsed_output or self.create_output_parser().parse(stdout, stderr)
        if parsed.rate_limited:
            return ManagedRuntimeExitResult(
                status="failed",
                failure_class="integration_error",
                provider_error_code="429",
            )
        if exit_code == 0:
            return ManagedRuntimeExitResult(status="completed", failure_class=None)
        return ManagedRuntimeExitResult(
            status="failed",
            failure_class="execution_error",
        )

    def create_output_parser(self) -> RuntimeOutputParser:
        return GeminiCliOutputParser()

    def terminate_on_live_rate_limit(self) -> bool:
        return True
