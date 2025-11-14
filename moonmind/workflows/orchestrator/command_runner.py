"""Command runner for orchestrator plan steps."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID

import httpx

from .service_profiles import ServiceProfile
from .storage import ArtifactStorage, ArtifactWriteResult


class CommandRunnerError(RuntimeError):
    """Base class for orchestrator command runner failures."""


class AllowListViolation(CommandRunnerError):
    """Raised when a patch attempts to modify files outside the allow list."""


class CommandExecutionError(CommandRunnerError):
    """Raised when a subprocess command returns a non-zero exit code."""


@dataclass(slots=True)
class StepResult:
    """Outcome returned from executing an orchestrator step."""

    message: str
    artifacts: list[ArtifactWriteResult]
    metadata: dict[str, Any] | None = None


class CommandRunner:
    """Execute orchestrator plan steps with allow-list enforcement."""

    def __init__(
        self,
        *,
        run_id: UUID,
        profile: ServiceProfile,
        artifact_storage: ArtifactStorage,
    ) -> None:
        self._run_id = run_id
        self._profile = profile
        self._storage = artifact_storage
        self._workspace_root = profile.workspace_path.resolve()

    # ------------------------------------------------------------------
    # Step handlers
    # ------------------------------------------------------------------
    def analyze(self, parameters: Mapping[str, Any]) -> StepResult:
        log_name = str(parameters.get("logArtifact", "analyze.log"))
        instruction = str(parameters.get("instruction", ""))
        sanitized_instruction = instruction.replace("\r", " ").replace("\n", " ")
        lines = [
            f"Instruction: {sanitized_instruction.strip()}",
            f"Target service: {self._profile.compose_service}",
            parameters.get("notes", ""),
        ]
        artifact = self._storage.write_text(
            self._run_id, log_name, "\n".join(line for line in lines if line)
        )
        return StepResult(
            message="Analysis complete",
            artifacts=[artifact],
            metadata={"log": artifact.path},
        )

    def patch(self, parameters: Mapping[str, Any]) -> StepResult:
        workspace = self._resolve_workspace(parameters.get("workspace"))
        commands = parameters.get("commands") or []
        command_logs: list[str] = []
        for command in commands:
            completed = self._execute_command(_ensure_sequence(command), cwd=workspace)
            command_logs.append(self._format_command(command))
            if completed.stdout:
                command_logs.append(completed.stdout.strip())
            if completed.stderr:
                command_logs.append(completed.stderr.strip())

        diff_command = ["git", "diff", "HEAD"]
        try:
            diff_output = self._execute_command(diff_command, cwd=workspace).stdout
        except CommandExecutionError as exc:
            if "unknown revision" in str(exc) or "ambiguous argument 'HEAD'" in str(exc):
                diff_output = self._execute_command(["git", "diff"], cwd=workspace).stdout
            else:
                raise
        diff_artifact_name = str(parameters.get("diffArtifact", "patch.diff"))
        diff_artifact = self._storage.write_text(
            self._run_id, diff_artifact_name, diff_output or "# No changes generated"
        )

        unstaged_output = self._execute_command(
            ["git", "diff", "--name-only"], cwd=workspace
        ).stdout
        unstaged_files = [
            line.strip() for line in unstaged_output.splitlines() if line.strip()
        ]

        staged_output = self._execute_command(
            ["git", "diff", "--cached", "--name-only"], cwd=workspace
        ).stdout
        staged_files = [
            line.strip() for line in staged_output.splitlines() if line.strip()
        ]

        untracked_output = self._execute_command(
            ["git", "ls-files", "--others", "--exclude-standard"], cwd=workspace
        ).stdout
        untracked_files = [
            line.strip() for line in untracked_output.splitlines() if line.strip()
        ]

        validated_files: list[str] = []
        seen: set[str] = set()
        for path in (*unstaged_files, *staged_files, *untracked_files):
            if path and path not in seen:
                validated_files.append(path)
                seen.add(path)

        allowlist_override = parameters.get("allowlist")
        normalized_allowlist = None
        if allowlist_override:
            normalized_allowlist = [str(pattern) for pattern in allowlist_override]

        self._enforce_allowlist(validated_files, allowlist_override=normalized_allowlist)

        patch_log_artifact = self._storage.write_text(
            self._run_id, "patch.log", "\n".join(command_logs)
        )

        return StepResult(
            message=(
                "Patched files: "
                + (", ".join(validated_files) if validated_files else "none")
            ),
            artifacts=[diff_artifact, patch_log_artifact],
            metadata={
                "unstagedFiles": unstaged_files,
                "stagedFiles": staged_files,
                "untrackedFiles": untracked_files,
                "validatedFiles": validated_files,
            },
        )

    def build(self, parameters: Mapping[str, Any]) -> StepResult:
        workspace = self._resolve_workspace(parameters.get("workspace"))
        command = parameters.get("command") or [
            "docker",
            "compose",
            "--project-name",
            self._profile.compose_project,
            "build",
            self._profile.compose_service,
        ]
        result = self._execute_command(_ensure_sequence(command), cwd=workspace)
        artifact = self._storage.write_text(
            self._run_id,
            str(parameters.get("logArtifact", "build.log")),
            self._combine_streams(result),
        )
        return StepResult(
            message="Build completed",
            artifacts=[artifact],
        )

    def restart(self, parameters: Mapping[str, Any]) -> StepResult:
        workspace = self._resolve_workspace(parameters.get("workspace"))
        command = parameters.get("command") or [
            "docker",
            "compose",
            "--project-name",
            self._profile.compose_project,
            "up",
            "-d",
            "--no-deps",
            self._profile.compose_service,
        ]
        result = self._execute_command(_ensure_sequence(command), cwd=workspace)
        artifact = self._storage.write_text(
            self._run_id,
            str(parameters.get("logArtifact", "restart.log")),
            self._combine_streams(result),
        )
        timeout = int(parameters.get("restartTimeoutSeconds", 0))
        message = "Restart command issued"
        if timeout:
            message = f"Restart command issued (timeout {timeout}s)"
        return StepResult(message=message, artifacts=[artifact])

    def verify(self, parameters: Mapping[str, Any]) -> StepResult:
        health = parameters.get("healthcheck") or {}
        log_lines: list[str] = []
        if health:
            url = str(health.get("url"))
            method = str(health.get("method", "GET")).upper()
            expected_status = int(health.get("expectedStatus", 200))
            timeout_seconds = int(health.get("timeoutSeconds", 120))
            interval = float(health.get("intervalSeconds", 5.0))

            deadline = time.monotonic() + timeout_seconds
            attempt = 0
            while True:
                attempt += 1
                try:
                    response = httpx.request(method, url, timeout=min(interval, 10.0))
                except httpx.HTTPError as exc:  # pragma: no cover - network errors
                    log_lines.append(f"Attempt {attempt}: {exc}")
                else:
                    log_lines.append(
                        f"Attempt {attempt}: status={response.status_code}"
                    )
                    if response.status_code == expected_status:
                        break
                if time.monotonic() >= deadline:
                    raise CommandExecutionError(
                        f"Health check for {url} timed out after {timeout_seconds}s"
                    )
                time.sleep(interval)
        else:
            log_lines.append("No healthcheck configured; skipping HTTP verification.")

        artifact = self._storage.write_text(
            self._run_id,
            str(parameters.get("logArtifact", "verify.log")),
            "\n".join(log_lines) or "Verification completed",
        )
        return StepResult(message="Verification succeeded", artifacts=[artifact])

    def rollback(self, parameters: Mapping[str, Any]) -> StepResult:
        strategies = parameters.get("strategies") or []
        workspace = self._resolve_workspace(parameters.get("workspace"))
        log_lines: list[str] = []
        for strategy in strategies:
            strategy_type = strategy.get("type", "unknown")
            log_lines.append(f"Executing rollback strategy: {strategy_type}")
            for command in strategy.get("commands", []):
                result = self._execute_command(_ensure_sequence(command), cwd=workspace)
                log_lines.append(self._format_command(command))
                combined = self._combine_streams(result)
                if combined:
                    log_lines.append(combined)
        if not log_lines:
            log_lines.append("No rollback actions executed.")
        artifact = self._storage.write_text(
            self._run_id,
            str(parameters.get("logArtifact", "rollback.log")),
            "\n".join(log_lines),
        )
        return StepResult(message="Rollback executed", artifacts=[artifact])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _resolve_workspace(self, workspace: str | None) -> Path:
        if not workspace:
            return self._workspace_root
        path = Path(workspace)
        if not path.is_absolute():
            path = (self._workspace_root / path).resolve()
        return path

    def _enforce_allowlist(
        self,
        changed_files: Iterable[str],
        *,
        allowlist_override: Iterable[str] | None = None,
    ) -> None:
        violations = [
            path
            for path in changed_files
            if not self._profile.validate_path(path, allowlist=allowlist_override)
        ]
        if violations:
            raise AllowListViolation(
                "; ".join(f"{path} is not allow-listed" for path in violations)
            )

    def _execute_command(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        cmd_sequence = list(command)
        if not cmd_sequence:
            raise CommandExecutionError("Command sequence must not be empty")
        try:
            result = subprocess.run(
                cmd_sequence,
                cwd=str(cwd or self._workspace_root),
                text=True,
                capture_output=True,
                check=True,
            )
        except FileNotFoundError as exc:  # pragma: no cover - environment dependent
            raise CommandExecutionError(
                f"Command not found: {cmd_sequence[0]}"
            ) from exc
        except subprocess.CalledProcessError as exc:
            combined = self._combine_streams(exc)
            raise CommandExecutionError(
                f"Command {' '.join(cmd_sequence)} failed with code {exc.returncode}: {combined}"
            ) from exc
        return result

    def _combine_streams(self, completed: subprocess.CompletedProcess[str]) -> str:
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        if stdout and stderr:
            return f"{stdout}\n{stderr}"
        return stdout or stderr

    def _format_command(self, command: Sequence[str] | str) -> str:
        if isinstance(command, str):
            return command
        return " ".join(command)


def _ensure_sequence(command: Any) -> Sequence[str]:
    if isinstance(command, str):
        raise CommandExecutionError(
            "Command strings are not supported; provide a sequence of arguments"
        )
    if not isinstance(command, Sequence):
        raise CommandExecutionError("Command must be a sequence of strings")
    sequence = list(command)
    if not all(isinstance(item, str) for item in sequence):
        raise CommandExecutionError("Command sequences must contain only strings")
    return sequence


__all__ = [
    "AllowListViolation",
    "CommandExecutionError",
    "CommandRunner",
    "CommandRunnerError",
    "StepResult",
]
