"""Cursor CLI managed runtime strategy."""

from __future__ import annotations

from typing import Any

from moonmind.workflows.temporal.runtime.output_parser import (
    NdjsonOutputParser,
    RuntimeOutputParser,
)
from moonmind.workflows.temporal.runtime.strategies.base import (
    ManagedRuntimeStrategy,
)


class CursorCliStrategy(ManagedRuntimeStrategy):
    """Strategy for launching ``cursor`` CLI runs."""

    @property
    def runtime_id(self) -> str:
        return "cursor_cli"

    @property
    def default_command_template(self) -> list[str]:
        return ["cursor"]

    @property
    def default_auth_mode(self) -> str:
        return "oauth"

    def build_command(
        self,
        profile: Any,
        request: Any,
    ) -> list[str]:
        """Construct Cursor CLI command.

        Extracted from ``ManagedRuntimeLauncher.build_command()``
        (the ``cursor_cli`` elif branch).
        """
        cmd = list(profile.command_template)

        model = self.get_model(profile, request)
        if model:
            cmd.extend(["--model", model])

        if request.instruction_ref:
            cmd.extend(["-p", request.instruction_ref])

        cmd.extend(["--output-format", "stream-json"])
        cmd.extend(["--force"])

        sandbox_mode = (
            request.parameters.get("sandbox_mode")
            if request.parameters
            else None
        )
        if sandbox_mode:
            cmd.extend(["--sandbox", sandbox_mode])

        return cmd

    def classify_exit(
        self,
        exit_code: int | None,
        stdout: str,
        stderr: str,
    ) -> tuple[str, str | None]:
        """Classify Cursor CLI exit with rate-limit and NDJSON awareness.

        Detects HTTP 429 rate limiting from NDJSON events or stderr,
        reporting ``'rate_limited'`` failure class to enable cooldown.
        """
        parser = self.create_output_parser()
        parsed = parser.parse(stdout, stderr)

        if parsed.rate_limited:
            return "failed", "integration_error"

        if exit_code == 0:
            return "completed", None
        return "failed", "execution_error"

    async def prepare_workspace(
        self,
        workspace_path: Any,
        request: Any,
    ) -> None:
        """Write .cursor/rules/moonmind-task.mdc and .cursor/cli.json."""
        from moonmind.agents.base.cursor_rules import write_task_rule_file
        from moonmind.agents.base.cursor_config import write_cursor_cli_json
        
        # We need Path type
        from pathlib import Path
        workspace_path_obj = Path(workspace_path)

        if getattr(request, "instruction_ref", None):
            write_task_rule_file(workspace_path_obj, request.instruction_ref)

        approval_policy = getattr(request, "approval_policy", None) or {"level": "full_autonomy"}
        write_cursor_cli_json(workspace_path_obj, approval_policy)

    def create_output_parser(self) -> RuntimeOutputParser:
        """Cursor CLI produces NDJSON via ``--output-format stream-json``."""
        return NdjsonOutputParser()

