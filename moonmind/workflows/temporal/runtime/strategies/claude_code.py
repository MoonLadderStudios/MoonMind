"""Claude Code managed runtime strategy."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from moonmind.workflows.temporal.runtime.output_parser import (
    ClaudeCodeOutputParser,
    ParsedOutput,
    RuntimeOutputParser,
)
from moonmind.workflows.temporal.runtime.strategies.base import (
    ManagedRuntimeExitResult,
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

    def supports_slash_passthrough(self) -> bool:
        return True

    def materialized_command_allowlist(self) -> dict[str, dict[str, str]]:
        return {
            "review": {
                "path": ".claude/commands/review.md",
                "invocation": "/project:review",
            }
        }

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

        # Skip --model when the profile already sets ANTHROPIC_MODEL via env overrides
        # (e.g. MiniMax profile).  Passing --model with a non-Anthropic model name causes
        # the Claude CLI to reject the run immediately.
        env_overrides: dict = getattr(profile, "env_overrides", {}) or {}
        anthropic_model_override = env_overrides.get("ANTHROPIC_MODEL")
        has_env_model = (
            isinstance(anthropic_model_override, str)
            and anthropic_model_override.strip() != ""
        )
        if not has_env_model:
            model = self.get_model(profile, request)
            if model:
                cmd.extend(["--model", model])

        effort = self.get_effort(profile, request)
        if effort:
            cmd.extend(["--effort", effort])

        # -p (--print) enables non-interactive mode, required for managed runs.
        # --dangerously-skip-permissions ensures tool execution (e.g. edits) can
        # proceed without interactive prompts in the managed workspace.
        cmd.extend(["-p", "--dangerously-skip-permissions"])
        if request.instruction_ref:
            cmd.append(request.instruction_ref)

        return cmd

    def classify_exit(
        self,
        exit_code: int | None,
        stdout: str,
        stderr: str,
    ) -> tuple[str, str | None]:
        result = self.classify_result(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )
        return result.status, result.failure_class

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
        # Defer the default exit-code mapping to the base classify_exit.
        # Calling super().classify_result here would recurse via self.classify_exit.
        status, failure_class = super().classify_exit(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )
        return ManagedRuntimeExitResult(status=status, failure_class=failure_class)

    def create_output_parser(self) -> RuntimeOutputParser:
        return ClaudeCodeOutputParser()

    def terminate_on_live_rate_limit(self) -> bool:
        return True

    async def prepare_workspace(
        self,
        workspace_path: Path,
        request: Any,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        """Inject shared retrieval context for Claude Code.

        The turn instruction (and any prepended skill activation summary built
        by :meth:`ManagedRuntimeLauncher._project_run_skill_snapshot`) is
        passed to the Claude CLI via ``-p`` in :meth:`build_command`. Writing
        the same content into ``CLAUDE.md`` would conflate it with project
        context — Claude Code reads ``CLAUDE.md`` as untrusted retrieved
        instructions when it ships under a SAFETY NOTICE wrapper, so the task
        is delivered as a first-class user prompt instead.
        """
        if not getattr(request, "instruction_ref", None):
            return

        from moonmind.rag.context_injection import ContextInjectionService

        service = ContextInjectionService(
            env=dict(environment) if environment is not None else None
        )
        await service.inject_context(
            request=request,
            workspace_path=workspace_path,
        )
