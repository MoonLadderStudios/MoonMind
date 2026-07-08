"""Claude Code managed runtime strategy."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
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
from moonmind.workflows.provider_failures import classify_provider_failure

_CLAUDE_PROGRESS_MTIME_PADDING_SECONDS = 5.0
_CLAUDE_WORKSPACE_PROGRESS_MAX_FILES = 50_000
_CLAUDE_WORKSPACE_PROGRESS_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".moonmind",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "skills_active",
        "venv",
    }
)
_CLAUDE_WORKSPACE_PROGRESS_SKIP_FILES = frozenset(
    {
        "live_streams.spool",
    }
)
_CLAUDE_PR_RESOLVER_PROGRESS_FILES: tuple[Path, ...] = (
    Path("var/pr_resolver/result.json"),
    Path("var/pr_resolver/snapshot.json"),
    Path("artifacts/pr_resolver_result.json"),
    Path("artifacts/pr_resolver_snapshot.json"),
    Path("artifacts/pr_resolver_addressed_comments.json"),
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
        if exit_code != 0:
            provider_failure = classify_provider_failure(f"{stdout}\n{stderr}")
            if provider_failure is not None:
                return ManagedRuntimeExitResult(
                    status="failed",
                    failure_class=provider_failure.failure_class,
                    provider_error_code=provider_failure.provider_error_code,
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

    def probe_progress_at(
        self,
        *,
        workspace_path: str | None,
        run_id: str,
        started_at: datetime,
    ) -> datetime | None:
        """Use workspace file mtimes as a silent-run progress signal."""

        if not workspace_path:
            return None
        workspace_root = Path(workspace_path)
        if not workspace_root.is_dir():
            return None

        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)

        threshold_ts = started_at.timestamp() - _CLAUDE_PROGRESS_MTIME_PADDING_SECONDS
        latest_ts: float | None = None
        for candidate in self._iter_progress_candidates(workspace_root):
            try:
                stat = candidate.stat()
            except OSError:
                continue
            if stat.st_mtime < threshold_ts:
                continue
            if latest_ts is None or stat.st_mtime > latest_ts:
                latest_ts = stat.st_mtime

        if latest_ts is None:
            return None
        return datetime.fromtimestamp(latest_ts, tz=UTC)

    @staticmethod
    def _iter_progress_candidates(workspace_root: Path) -> Iterator[Path]:
        yielded: set[Path] = set()
        for candidate in ClaudeCodeStrategy._iter_pr_resolver_progress_candidates(
            workspace_root
        ):
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = candidate
            yielded.add(resolved)
            yield candidate

        for candidate in ClaudeCodeStrategy._iter_workspace_progress_candidates(
            workspace_root
        ):
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = candidate
            if resolved in yielded:
                continue
            yield candidate

    @staticmethod
    def _iter_pr_resolver_progress_candidates(workspace_root: Path) -> Iterator[Path]:
        for rel_path in _CLAUDE_PR_RESOLVER_PROGRESS_FILES:
            yield workspace_root / rel_path

        attempts_dir = workspace_root / "var" / "pr_resolver" / "attempts"
        if attempts_dir.is_dir():
            yield from attempts_dir.glob("*.json")

        artifacts_dir = workspace_root / "artifacts"
        if artifacts_dir.is_dir():
            yield from artifacts_dir.glob("pr_resolver*.json")

    @staticmethod
    def _iter_workspace_progress_candidates(workspace_root: Path) -> Iterator[Path]:
        pending: list[Path] = [workspace_root]
        scanned_files = 0

        while pending:
            current = pending.pop()
            try:
                entries = list(current.iterdir())
            except OSError:
                continue

            for entry in entries:
                name = entry.name
                if name in _CLAUDE_WORKSPACE_PROGRESS_SKIP_FILES:
                    continue
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir():
                        if name in _CLAUDE_WORKSPACE_PROGRESS_SKIP_DIRS:
                            continue
                        pending.append(entry)
                        continue
                    if not entry.is_file():
                        continue
                except OSError:
                    continue

                scanned_files += 1
                yield entry
                if scanned_files >= _CLAUDE_WORKSPACE_PROGRESS_MAX_FILES:
                    return

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
