"""Abstract base class for managed runtime strategies.

Each managed CLI runtime (Gemini CLI, Codex CLI, Claude Code)
implements this interface to encapsulate its runtime-specific behavior.
The launcher and adapter delegate to registered strategies instead of
using if/elif branching on ``runtime_id``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from pydantic import ValidationError

from moonmind.schemas.agent_runtime_models import (
    AgentRunState,
    FailureClass as RuntimeFailureClass,
    RuntimeCommandInvocation,
    RuntimeCommandRenderResult,
)
from moonmind.workflows.temporal.runtime.output_parser import (
    ParsedOutput,
    PlainTextOutputParser,
    RuntimeOutputParser,
)
from moonmind.workflows.temporal.runtime.self_heal import FailureClass, is_failure_retryable

@dataclass(frozen=True, slots=True)
class ManagedRuntimeExitResult:
    """Structured managed-runtime exit classification."""

    status: AgentRunState
    failure_class: RuntimeFailureClass | None
    provider_error_code: str | None = None

class ManagedRuntimeStrategy(ABC):
    """Per-runtime strategy for managed agent lifecycle.

    Subclasses encapsulate command construction, environment shaping,
    workspace preparation, exit classification, and output parsing.
    """

    @property
    @abstractmethod
    def runtime_id(self) -> str:
        """The canonical runtime identifier (e.g. ``'gemini_cli'``)."""

    @property
    @abstractmethod
    def default_command_template(self) -> list[str]:
        """Default CLI argv prefix (e.g. ``['gemini']``).

        Used by the adapter when no explicit ``command_template`` is set
        on the profile.
        """

    @property
    def default_auth_mode(self) -> str:
        """Default auth mode for this runtime.

        Must align with the ``OAuthProviderSpec`` entries defined in
        ``docs/ManagedAgents/UniversalTmateOAuth.md``.  Both registries
        share the ``runtime_id`` namespace (``codex_cli``, ``gemini_cli``,
        ``claude_code``).
        """
        return "api_key"

    @abstractmethod
    def build_command(
        self,
        profile: Any,
        request: Any,
    ) -> list[str]:
        """Construct the final CLI command from *profile* and *request*.

        Parameters use ``Any`` to avoid hard-coupling to Pydantic models
        in the interface definition.  Concrete implementations import
        the specific types they need.
        """

    def supports_slash_passthrough(self) -> bool:
        """Whether this runtime supports slash-leading command pass-through."""

        return False

    def supports_native_command_transport(self) -> bool:
        """Whether this runtime can receive a structured command payload."""

        return False

    def materialized_command_allowlist(self) -> Mapping[str, Mapping[str, str]]:
        """Runtime-owned allowlist for known command materialization targets."""

        return {}

    def render_runtime_command(self, request: Any) -> RuntimeCommandRenderResult:
        """Render final instruction text after MoonMind context preparation."""

        command = getattr(request, "runtime_command", None)
        instruction = getattr(request, "instruction_ref", None) or ""
        if command is None:
            return RuntimeCommandRenderResult(
                status="ok",
                renderMode="plain_prompt",
                renderedInstruction=instruction,
            )
        if isinstance(command, dict):
            try:
                command = RuntimeCommandInvocation.model_validate(command)
            except ValidationError:
                return RuntimeCommandRenderResult(
                    status="failed",
                    failureReason="runtime_command_render_failed",
                    diagnostics={"message": "Invalid runtime command metadata."},
                )
        if not isinstance(command, RuntimeCommandInvocation):
            return RuntimeCommandRenderResult(
                status="failed",
                failureReason="runtime_command_render_failed",
                diagnostics={"message": "Invalid runtime command metadata."},
            )
        if command.render_mode == "native_command":
            return self._render_native_runtime_command(instruction, command)
        if command.render_mode == "materialized_command":
            return self._render_materialized_runtime_command(instruction, command)
        if command.recognition_mode == "escaped_literal" or not command.requires_runtime_recognition:
            literal = command.instruction_body or instruction
            prepared = self._prepared_context_after_runtime_command(
                instruction,
                command,
            )
            rendered = self._render_literal_runtime_command(
                literal=literal,
                prepared_context=prepared,
            )
            return RuntimeCommandRenderResult(
                status="ok",
                renderMode="plain_prompt",
                renderedInstruction=rendered,
                invocation=command,
            )
        if not self.supports_slash_passthrough():
            return RuntimeCommandRenderResult(
                status="fallback",
                renderMode="plain_prompt",
                renderedInstruction=instruction,
                fallbackEvent={
                    "reason": "unsupported_runtime",
                    "fallbackMode": "literal_prompt",
                },
                invocation=command,
            )
        raw_command = command.raw_command or (
            f"/{command.command}{(' ' + command.args) if command.args else ''}"
        )
        prepared = self._prepared_context_after_runtime_command(instruction, command)
        rendered = self._join_instruction_parts(
            raw_command,
            command.instruction_body,
            prepared,
        )
        return RuntimeCommandRenderResult(
            status="ok",
            renderMode="prompt_prefix",
            renderedInstruction=rendered,
            invocation=command,
        )

    def _render_native_runtime_command(
        self,
        instruction: str,
        command: RuntimeCommandInvocation,
    ) -> RuntimeCommandRenderResult:
        if not self.supports_native_command_transport():
            return RuntimeCommandRenderResult(
                status="unsupported",
                renderMode="unsupported",
                renderedInstruction=instruction,
                failureReason="runtime_command_render_failed",
                diagnostics={
                    "message": "Runtime does not support native command transport."
                },
                invocation=command,
            )
        prepared = self._prepared_context_after_runtime_command(instruction, command)
        return RuntimeCommandRenderResult(
            status="ok",
            renderMode="native_command",
            nativeCommandPayload={
                "type": "runtime.command",
                "command": command.command,
                "args": command.args,
                "rawCommand": command.raw_command,
                "instructionBody": command.instruction_body,
                "preparedContext": prepared,
            },
            invocation=command,
        )

    def _render_materialized_runtime_command(
        self,
        instruction: str,
        command: RuntimeCommandInvocation,
    ) -> RuntimeCommandRenderResult:
        allowlist = self.materialized_command_allowlist()
        target = allowlist.get(command.command)
        materialized_command = command.materialized_command or {}
        if command.hint_status != "hinted" or target is None:
            return RuntimeCommandRenderResult(
                status="failed",
                failureReason="runtime_command_render_failed",
                diagnostics={
                    "message": "Command is not allowlisted for materialized rendering."
                },
                invocation=command,
            )
        target_path = str(target.get("path") or "")
        invocation = str(target.get("invocation") or "")
        requested_path = str(materialized_command.get("path") or target_path)
        requested_invocation = str(
            materialized_command.get("invocation") or invocation
        )
        if requested_path != target_path or requested_invocation != invocation:
            return RuntimeCommandRenderResult(
                status="failed",
                failureReason="runtime_command_render_failed",
                diagnostics={
                    "message": "Materialized command target is outside the allowlist."
                },
                invocation=command,
            )
        prepared = self._prepared_context_after_runtime_command(instruction, command)
        return RuntimeCommandRenderResult(
            status="ok",
            renderMode="materialized_command",
            renderedInstruction=self._join_instruction_parts(
                invocation,
                command.instruction_body,
                prepared,
            ),
            materializedTargets=[
                {
                    "path": target_path,
                    "command": command.command,
                    "invocation": invocation,
                }
            ],
            invocation=command,
        )

    @staticmethod
    def _join_instruction_parts(*parts: str | None) -> str:
        return "\n\n".join(
            str(part).strip() for part in parts if str(part or "").strip()
        )

    def _render_literal_runtime_command(
        self,
        *,
        literal: str,
        prepared_context: str,
    ) -> str:
        return self._join_instruction_parts(
            "Literal runtime command text:",
            literal,
            prepared_context,
        )

    def _prepared_context_after_runtime_command(
        self,
        instruction: str,
        command: RuntimeCommandInvocation,
    ) -> str:
        prepared = instruction or ""
        raw_parts = [command.raw_command]
        if command.instruction_body:
            raw_parts.append(command.instruction_body)
        raw_instruction = "\n".join(part for part in raw_parts if part)
        for candidate in (raw_instruction, command.instruction_body):
            if candidate and candidate in prepared:
                prepared = prepared.replace(candidate, "", 1)
                break
        return prepared.strip()

    def get_model(self, profile: Any, request: Any) -> str | None:
        """Extract model from request parameters or profile default with overrides.

        Resolution order:
        1. ``request.parameters["model"]`` (task-level override with model_overrides applied).
        2. ``profile.default_model`` (provider-profile default).
        3. Runtime default from the canonical registry.
        """
        requested_model = request.parameters.get("model") if request.parameters else None

        if requested_model:
            overrides = getattr(profile, "model_overrides", {}) or {}
            resolved = overrides.get(requested_model, requested_model)
            return resolved

        profile_default = str(getattr(profile, "default_model", None) or "").strip() or None
        if profile_default:
            return profile_default

        # Runtime-default fallback so launch adapters never silently drop the model.
        from moonmind.workflows.tasks.runtime_defaults import resolve_runtime_defaults
        runtime_model, _ = resolve_runtime_defaults(self.runtime_id)
        return runtime_model

    def get_effort(self, profile: Any, request: Any) -> str | None:
        """Extract effort from request parameters or profile default."""
        return (
            request.parameters.get("effort") if request.parameters else None
        ) or getattr(profile, "default_effort", None)

    def shape_environment(
        self,
        base_env: dict[str, str],
        profile: Any,
    ) -> dict[str, str]:
        """Shape the subprocess environment for this runtime.

        Default implementation returns a copy of *base_env* unchanged.
        Override to filter, add, or clear runtime-specific variables.

        Note: The underlying helpers (``OAUTH_CLEARED_VARS``,
        ``shape_environment_for_oauth``) are also used by the OAuth
        Session orchestrator (``UniversalTmateOAuth.md`` §9.2).  Shared
        env-shaping logic should be factored into common utilities
        rather than duplicated.
        """
        return dict(base_env)

    async def prepare_workspace(
        self,
        workspace_path: Path,
        request: Any,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        """Pre-launch workspace setup (no-op by default).

        Override for runtimes that need workspace files:
        - Claude: ``CLAUDE.md``
        """

    def classify_exit(
        self,
        exit_code: int | None,
        stdout: str,
        stderr: str,
    ) -> tuple[str, str | None]:
        """Classify process exit into ``(status, failure_class | None)``.

        Default treats 0 as completed, non-zero as failed with
        ``'execution_error'`` class.  Override for runtimes with
        non-standard exit semantics.
        """
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
        """Return structured exit metadata for one managed runtime process."""
        status, failure_class = self.classify_exit(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )
        return ManagedRuntimeExitResult(
            status=status,
            failure_class=failure_class,
        )

    def create_output_parser(self) -> RuntimeOutputParser:
        """Factory for the runtime's stream parser.

        Returns ``PlainTextOutputParser`` by default.  Override for structured output
        formats like NDJSON (``--output-format stream-json``).

        See :class:`~moonmind.workflows.temporal.runtime.output_parser.RuntimeOutputParser`.
        """
        return PlainTextOutputParser()

    def terminate_on_live_rate_limit(self) -> bool:
        """Whether supervisor should stop the process on streamed rate-limit events."""
        return False

    def progress_stall_timeout_seconds(self, *, timeout_seconds: int) -> int | None:
        """Return the max allowed idle-progress window before the runtime is stalled.

        ``None`` disables supervisor-owned stall termination for the runtime.
        """

        return None

    def probe_progress_at(
        self,
        *,
        workspace_path: str | None,
        run_id: str,
        started_at: datetime,
    ) -> datetime | None:
        """Return the latest runtime-specific progress timestamp, if observable."""

        return None

    def should_retry_exit(self, failure_class: str | None) -> bool:
        """Determine if a failure class should trigger a self-heal retry.

        Uses the shared self-heal capability to determine if an exit
        classification warrants a retry attempt by the orchestrator.
        """
        if not failure_class:
            return False

        try:
            fc = FailureClass(failure_class)
            return is_failure_retryable(fc)
        except ValueError:
            return False
