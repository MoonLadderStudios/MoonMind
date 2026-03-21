"""Abstract base class for managed runtime strategies.

Each managed CLI runtime (Gemini CLI, Codex CLI, Cursor CLI, Claude Code)
implements this interface to encapsulate its runtime-specific behavior.
The launcher and adapter delegate to registered strategies instead of
using if/elif branching on ``runtime_id``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


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
        ``claude_code``, ``cursor_cli``).
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

    def shape_environment(
        self,
        base_env: dict[str, str],
        profile: Any,
    ) -> dict[str, str]:
        """Shape the subprocess environment for this runtime.

        Default implementation returns a copy of *base_env* unchanged.
        Override to filter, add, or clear runtime-specific variables.

        Note: The underlying helpers (``_OAUTH_CLEARED_VARS``,
        ``_shape_environment_for_oauth``) are also used by the OAuth
        Session orchestrator (``UniversalTmateOAuth.md`` §9.2).  Shared
        env-shaping logic should be factored into common utilities
        rather than duplicated.
        """
        return dict(base_env)

    async def prepare_workspace(
        self,
        workspace_path: Path,
        request: Any,
    ) -> None:
        """Pre-launch workspace setup (no-op by default).

        Override for runtimes that need workspace files:
        - Cursor: ``.cursor/rules/``, ``.cursor/cli.json``
        - Gemini: ``.gemini/`` instruction files
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

    def create_output_parser(self) -> Any:
        """Factory for the runtime's stream parser.

        Returns ``None`` by default.  Override for structured output
        formats like Cursor NDJSON (``--output-format stream-json``).
        """
        return None
