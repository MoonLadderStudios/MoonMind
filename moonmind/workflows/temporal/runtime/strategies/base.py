"""Abstract base class for managed runtime strategies.

Each managed CLI runtime (Codex CLI, Claude Code)
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

from moonmind.schemas.agent_runtime_models import (
    AgentRunState,
    FailureClass as RuntimeFailureClass,
    RuntimeCommandRenderResult,
)
from moonmind.workflows.runtime_commands import RuntimeCommandRenderer
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
        """The canonical runtime identifier (for example, ``'codex_cli'``)."""

    @property
    @abstractmethod
    def default_command_template(self) -> list[str]:
        """Default CLI argv prefix (for example, ``['codex']``).

        Used by the adapter when no explicit ``command_template`` is set
        on the profile.
        """

    @property
    def default_auth_mode(self) -> str:
        """Default auth mode for this runtime.

        Must align with the ``OAuthProviderSpec`` entries defined in
        ``docs/ManagedAgents/UniversalTmateOAuth.md``.  Both registries
        share the ``runtime_id`` namespace (``codex_cli``, ``claude_code``).
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
        return RuntimeCommandRenderer(
            slash_passthrough=self.supports_slash_passthrough(),
            native_command_transport=self.supports_native_command_transport(),
            command_allowlist=self.materialized_command_allowlist(),
        ).render_runtime_command(request)

    def get_model(self, profile: Any, request: Any) -> str | None:
        """Extract model from request parameters or profile default with overrides.

        Resolution order:
        1. ``request.parameters["model"]`` (task-level override with model_overrides applied).
        2. ``profile.default_model`` (provider-profile default).
        3. Runtime default from the canonical registry.
        """
        parameters = request.parameters or {}
        requested_model = parameters.get("model")

        if requested_model:
            overrides = getattr(profile, "model_overrides", {}) or {}
            resolved = overrides.get(requested_model, requested_model)
            return resolved

        if parameters.get("modelTier") is not None:
            from moonmind.workflows.executions.model_resolver import resolve_model_effort

            return resolve_model_effort(
                runtime_id=self.runtime_id,
                profile=profile,
                requested_model_tier=parameters.get("modelTier"),
                tier_fallback=parameters.get("tierFallback"),
                advisory_preview=parameters.get("tierPreview"),
            ).model

        from moonmind.runtime_intent import is_custom_selection

        if is_custom_selection(parameters):
            from moonmind.workflows.executions.model_resolver import (
                resolve_model_effort,
            )

            return resolve_model_effort(
                runtime_id=self.runtime_id, profile=profile, authored_runtime=parameters
            ).model
        profile_default = (
            str(getattr(profile, "default_model", None) or "").strip() or None
        )
        if profile_default:
            return profile_default

        # Runtime-default fallback so launch adapters never silently drop the model.
        from moonmind.workflows.executions.runtime_defaults import (
            resolve_runtime_defaults,
        )

        runtime_model, _ = resolve_runtime_defaults(self.runtime_id)
        return runtime_model

    def get_effort(self, profile: Any, request: Any) -> str | None:
        """Extract effort from request parameters or profile default."""
        parameters = request.parameters or {}
        if parameters.get("effort"):
            return parameters.get("effort")
        if parameters.get("modelTier") is not None:
            from moonmind.workflows.executions.model_resolver import resolve_model_effort

            return resolve_model_effort(
                runtime_id=self.runtime_id,
                profile=profile,
                requested_model_tier=parameters.get("modelTier"),
                tier_fallback=parameters.get("tierFallback"),
                advisory_preview=parameters.get("tierPreview"),
            ).effort
        from moonmind.runtime_intent import is_custom_selection

        if is_custom_selection(parameters):
            from moonmind.workflows.executions.model_resolver import (
                resolve_model_effort,
            )

            return resolve_model_effort(
                runtime_id=self.runtime_id, profile=profile, authored_runtime=parameters
            ).effort
        return getattr(profile, "default_effort", None)

    def effort_application_status(self, effort: str | None) -> str:
        """Describe how this strategy applies a backend-resolved effort value."""
        return "unknown" if effort else "metadata_only"

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
