"""Job handlers for the standalone Codex worker daemon."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit
from uuid import UUID

from moonmind.agents.codex_worker.utils import verify_cli_is_executable


class CodexWorkerHandlerError(RuntimeError):
    """Raised when handler payloads or command execution are invalid."""


@dataclass(frozen=True, slots=True)
class ArtifactUpload:
    """Represents one artifact file to upload for a completed job."""

    path: Path
    name: str
    content_type: str | None = None
    digest: str | None = None


@dataclass(frozen=True, slots=True)
class WorkerExecutionResult:
    """Normalized execution result consumed by worker terminal updates."""

    succeeded: bool
    summary: str | None
    error_message: str | None
    artifacts: tuple[ArtifactUpload, ...] = ()


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Captured output from a single subprocess command."""

    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class CodexExecPayload:
    """Validated `codex_exec` payload structure."""

    repository: str
    instruction: str
    ref: str | None
    workdir_mode: str
    codex_model: str | None
    codex_effort: str | None
    publish_mode: str
    publish_base_branch: str | None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "CodexExecPayload":
        """Parse and validate a queue payload for codex execution."""

        repository = str(payload.get("repository", "")).strip()
        instruction = str(payload.get("instruction", "")).strip()
        if not repository:
            raise CodexWorkerHandlerError("codex_exec payload requires 'repository'")
        if not instruction:
            raise CodexWorkerHandlerError("codex_exec payload requires 'instruction'")

        ref_raw = payload.get("ref")
        ref = str(ref_raw).strip() if ref_raw is not None else None
        if ref == "":
            ref = None

        workdir_raw = payload.get("workdirMode")
        workdir_mode = str(workdir_raw or "fresh_clone").strip() or "fresh_clone"
        if workdir_mode not in {"fresh_clone", "reuse"}:
            raise CodexWorkerHandlerError(
                "workdirMode must be one of: fresh_clone, reuse"
            )

        codex_model, codex_effort = _parse_codex_overrides(payload)

        publish_raw = payload.get("publish")
        publish_payload = publish_raw if isinstance(publish_raw, Mapping) else {}
        publish_mode = str(publish_payload.get("mode", "none")).strip() or "none"
        if publish_mode not in {"none", "branch", "pr"}:
            raise CodexWorkerHandlerError(
                "publish.mode must be one of: none, branch, pr"
            )

        publish_base_raw = publish_payload.get("baseBranch")
        publish_base_branch = (
            str(publish_base_raw).strip() if publish_base_raw is not None else None
        )
        if publish_base_branch == "":
            publish_base_branch = None

        return cls(
            repository=repository,
            instruction=instruction,
            ref=ref,
            workdir_mode=workdir_mode,
            codex_model=codex_model,
            codex_effort=codex_effort,
            publish_mode=publish_mode,
            publish_base_branch=publish_base_branch,
        )


@dataclass(frozen=True, slots=True)
class CodexSkillPayload:
    """Validated `codex_skill` payload structure."""

    skill_id: str
    inputs: dict[str, Any]
    repository: str | None
    instruction: str | None
    ref: str | None
    workdir_mode: str
    codex_model: str | None
    codex_effort: str | None
    publish_mode: str
    publish_base_branch: str | None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "CodexSkillPayload":
        """Parse and validate a queue payload for skills-first execution."""

        skill_id = str(payload.get("skillId", "")).strip() or "speckit"
        raw_inputs = payload.get("inputs")
        inputs = dict(raw_inputs) if isinstance(raw_inputs, Mapping) else {}

        repository = (
            str(inputs.get("repo", "")).strip()
            or str(inputs.get("repository", "")).strip()
            or str(payload.get("repository", "")).strip()
            or None
        )
        instruction = (
            str(inputs.get("instruction", "")).strip()
            or str(payload.get("instruction", "")).strip()
            or None
        )
        ref = (
            str(inputs.get("ref", "")).strip()
            or str(payload.get("ref", "")).strip()
            or None
        )
        workdir_mode = (
            str(inputs.get("workdirMode", "")).strip()
            or str(payload.get("workdirMode", "")).strip()
            or "fresh_clone"
        )
        payload_codex_model, payload_codex_effort = _parse_codex_overrides(payload)
        input_codex_model, input_codex_effort = _parse_codex_overrides(inputs)
        codex_model = payload_codex_model or input_codex_model
        codex_effort = payload_codex_effort or input_codex_effort
        publish_mode = (
            str(inputs.get("publishMode", "")).strip()
            or str(payload.get("publishMode", "")).strip()
            or "none"
        )
        publish_base_branch = (
            str(inputs.get("publishBaseBranch", "")).strip()
            or str(payload.get("publishBaseBranch", "")).strip()
            or None
        )

        if workdir_mode not in {"fresh_clone", "reuse"}:
            raise CodexWorkerHandlerError(
                "codex_skill workdirMode must be one of: fresh_clone, reuse"
            )
        if publish_mode not in {"none", "branch", "pr"}:
            raise CodexWorkerHandlerError(
                "codex_skill publishMode must be one of: none, branch, pr"
            )

        return cls(
            skill_id=skill_id,
            inputs=inputs,
            repository=repository,
            instruction=instruction,
            ref=ref,
            workdir_mode=workdir_mode,
            codex_model=codex_model,
            codex_effort=codex_effort,
            publish_mode=publish_mode,
            publish_base_branch=publish_base_branch,
        )


class CodexExecHandler:
    """Executes `codex_exec` jobs and produces uploadable artifacts."""

    def __init__(
        self,
        *,
        workdir_root: Path,
        codex_binary: str = "codex",
        git_binary: str = "git",
        gh_binary: str = "gh",
        default_codex_model: str | None = None,
        default_codex_effort: str | None = None,
        redaction_values: tuple[str, ...] = (),
    ) -> None:
        # Normalize to an absolute path so subprocess cwd/path arguments remain
        # stable regardless of the caller's current working directory.
        self._workdir_root = Path(workdir_root).expanduser().resolve()
        self._codex_binary = codex_binary
        self._git_binary = git_binary
        self._gh_binary = gh_binary
        self._codex_sandbox_mode = self._resolve_codex_sandbox_mode()
        self._default_codex_model = _clean_optional_string(
            default_codex_model,
            fallback=os.environ.get("MOONMIND_CODEX_MODEL")
            or os.environ.get("CODEX_MODEL"),
        )
        self._default_codex_effort = _clean_optional_string(
            default_codex_effort,
            fallback=os.environ.get("MOONMIND_CODEX_EFFORT")
            or os.environ.get("CODEX_MODEL_REASONING_EFFORT")
            or os.environ.get("MODEL_REASONING_EFFORT"),
        )
        env_token = str(os.environ.get("GITHUB_TOKEN", "")).strip()
        values = [value for value in redaction_values if value]
        if env_token:
            values.append(env_token)
        self._redaction_values = tuple(dict.fromkeys(values))

    async def handle(
        self,
        *,
        job_id: UUID,
        payload: Mapping[str, Any],
    ) -> WorkerExecutionResult:
        """Process a `codex_exec` payload and return a normalized result."""

        artifacts: list[ArtifactUpload] = []
        job_root = self._workdir_root / str(job_id)
        artifacts_dir = job_root / "artifacts"
        log_path = artifacts_dir / "codex_exec.log"
        patch_path = artifacts_dir / "changes.patch"

        try:
            parsed = CodexExecPayload.from_payload(payload)
            artifacts_dir.mkdir(parents=True, exist_ok=True)

            repo_dir = await self._prepare_repository(
                job_id=job_id,
                payload=parsed,
                job_root=job_root,
                log_path=log_path,
            )

            await self._run_command(
                self._build_codex_exec_command(parsed),
                cwd=repo_dir,
                log_path=log_path,
            )

            diff_result = await self._run_command(
                [self._git_binary, "diff"],
                cwd=repo_dir,
                log_path=log_path,
                check=False,
            )
            patch_path.write_text(diff_result.stdout or "", encoding="utf-8")

            artifacts.extend(
                [
                    ArtifactUpload(
                        path=log_path,
                        name="logs/codex_exec.log",
                        content_type="text/plain",
                    ),
                    ArtifactUpload(
                        path=patch_path,
                        name="patches/changes.patch",
                        content_type="text/x-diff",
                    ),
                ]
            )

            publish_note = await self._maybe_publish(
                job_id=job_id,
                payload=parsed,
                repo_dir=repo_dir,
                log_path=log_path,
            )
            summary = "codex_exec completed"
            if publish_note:
                summary = f"{summary}; {publish_note}"

            return WorkerExecutionResult(
                succeeded=True,
                summary=summary,
                error_message=None,
                artifacts=tuple(artifacts),
            )
        except Exception as exc:
            if log_path.exists():
                artifacts.append(
                    ArtifactUpload(
                        path=log_path,
                        name="logs/codex_exec.log",
                        content_type="text/plain",
                    )
                )
            if patch_path.exists():
                artifacts.append(
                    ArtifactUpload(
                        path=patch_path,
                        name="patches/changes.patch",
                        content_type="text/x-diff",
                    )
                )
            return WorkerExecutionResult(
                succeeded=False,
                summary=None,
                error_message=str(exc),
                artifacts=tuple(artifacts),
            )

    async def handle_skill(
        self,
        *,
        job_id: UUID,
        payload: Mapping[str, Any],
        selected_skill: str,
        fallback: bool = False,
    ) -> WorkerExecutionResult:
        """Process a `codex_skill` payload via skill-first compatibility mapping."""

        parsed = CodexSkillPayload.from_payload(payload)
        if parsed.repository is None:
            raise CodexWorkerHandlerError(
                "codex_skill payload requires 'inputs.repo' (or repository)"
            )

        instruction = parsed.instruction
        if not instruction:
            instruction = (
                f"Execute skill '{selected_skill}' with inputs:\n"
                + json.dumps(parsed.inputs, indent=2, sort_keys=True)
            )

        mapped_payload: dict[str, Any] = {
            "repository": parsed.repository,
            "instruction": instruction,
            "workdirMode": parsed.workdir_mode,
            "publish": {
                "mode": parsed.publish_mode,
                "baseBranch": parsed.publish_base_branch,
            },
        }
        if parsed.ref:
            mapped_payload["ref"] = parsed.ref
        codex_overrides: dict[str, str] = {}
        if parsed.codex_model:
            codex_overrides["model"] = parsed.codex_model
        if parsed.codex_effort:
            codex_overrides["effort"] = parsed.codex_effort
        if codex_overrides:
            mapped_payload["codex"] = codex_overrides

        result = await self.handle(job_id=job_id, payload=mapped_payload)
        if not result.succeeded:
            return result

        mode = "direct_fallback" if fallback else "skill"
        summary = result.summary or "codex_skill completed"
        skill_summary = f"{summary}; skill={selected_skill}; executionPath={mode}"
        return WorkerExecutionResult(
            succeeded=True,
            summary=skill_summary,
            error_message=None,
            artifacts=result.artifacts,
        )

    async def _prepare_repository(
        self,
        *,
        job_id: UUID,
        payload: CodexExecPayload,
        job_root: Path,
        log_path: Path,
    ) -> Path:
        repo_dir = job_root / "repo"
        if payload.workdir_mode == "fresh_clone" and repo_dir.exists():
            shutil.rmtree(repo_dir)

        if not repo_dir.exists():
            job_root.mkdir(parents=True, exist_ok=True)
            await self._run_command(
                [
                    self._git_binary,
                    "clone",
                    self._to_clone_url(payload.repository),
                    str(repo_dir),
                ],
                cwd=job_root,
                log_path=log_path,
            )

        if payload.ref:
            await self._run_command(
                [self._git_binary, "fetch", "--all", "--prune"],
                cwd=repo_dir,
                log_path=log_path,
                check=False,
            )
            await self._run_command(
                [self._git_binary, "checkout", payload.ref],
                cwd=repo_dir,
                log_path=log_path,
            )

        return repo_dir

    async def _maybe_publish(
        self,
        *,
        job_id: UUID,
        payload: CodexExecPayload,
        repo_dir: Path,
        log_path: Path,
    ) -> str | None:
        if payload.publish_mode == "none":
            return None

        status = await self._run_command(
            [self._git_binary, "status", "--porcelain"],
            cwd=repo_dir,
            log_path=log_path,
            check=False,
        )
        if not status.stdout.strip():
            return "publish skipped: no local changes"

        branch_name = f"moonmind-job-{str(job_id)[:8]}"
        await self._run_command(
            [self._git_binary, "checkout", "-B", branch_name],
            cwd=repo_dir,
            log_path=log_path,
        )
        await self._run_command(
            [self._git_binary, "add", "-A"],
            cwd=repo_dir,
            log_path=log_path,
        )
        await self._run_command(
            [
                self._git_binary,
                "commit",
                "-m",
                f"MoonMind worker output for job {job_id}",
            ],
            cwd=repo_dir,
            log_path=log_path,
        )
        await self._run_command(
            [self._git_binary, "push", "-u", "origin", branch_name],
            cwd=repo_dir,
            log_path=log_path,
        )

        if payload.publish_mode == "branch":
            return f"published branch {branch_name}"

        verify_cli_is_executable(self._gh_binary)
        base_branch = payload.publish_base_branch or "main"
        await self._run_command(
            [
                self._gh_binary,
                "pr",
                "create",
                "--base",
                base_branch,
                "--head",
                branch_name,
                "--title",
                f"MoonMind worker result for job {job_id}",
                "--body",
                "Automated PR generated by moonmind-codex-worker.",
            ],
            cwd=repo_dir,
            log_path=log_path,
        )
        return f"published PR from {branch_name}"

    async def _run_command(
        self,
        command: list[str],
        *,
        cwd: Path,
        log_path: Path,
        check: bool = True,
    ) -> CommandResult:
        self._append_log(log_path, self._redact_text(f"$ {' '.join(command)}"))
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise CodexWorkerHandlerError(
                f"failed to execute command '{command[0]}': {exc}"
            ) from exc

        stdout_bytes, stderr_bytes = await process.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if stdout:
            self._append_log(log_path, self._redact_text(stdout.rstrip("\n")))
        if stderr:
            self._append_log(log_path, self._redact_text(stderr.rstrip("\n")))

        result = CommandResult(
            command=tuple(command),
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
        )
        if check and result.returncode != 0:
            raise CodexWorkerHandlerError(
                f"command failed ({result.returncode}): {' '.join(command)}"
            )
        return result

    def _build_codex_exec_command(self, payload: CodexExecPayload) -> list[str]:
        """Build codex execution command with task override -> worker defaults."""

        resolved_model = payload.codex_model or self._default_codex_model
        resolved_effort = payload.codex_effort or self._default_codex_effort

        command = [
            self._codex_binary,
            "exec",
            "--sandbox",
            self._codex_sandbox_mode,
        ]
        if resolved_model:
            command.extend(["--model", resolved_model])
        if resolved_effort:
            escaped_effort = resolved_effort.replace("\\", "\\\\").replace('"', '\\"')
            command.extend(
                [
                    "--config",
                    f'model_reasoning_effort="{escaped_effort}"',
                ]
            )
        command.append(payload.instruction)
        return command

    @staticmethod
    def _to_clone_url(repository: str) -> str:
        if repository.startswith("http://") or repository.startswith("https://"):
            parsed = urlsplit(repository)
            if parsed.username is not None or parsed.password is not None:
                raise CodexWorkerHandlerError(
                    "repository URL must not include embedded credentials"
                )
            return repository
        if repository.startswith("git@"):
            return repository
        return f"https://github.com/{repository}.git"

    @staticmethod
    def _resolve_codex_sandbox_mode() -> str:
        configured = str(
            os.environ.get("MOONMIND_CODEX_SANDBOX_MODE", "workspace-write")
        ).strip()
        if configured in {"read-only", "workspace-write", "danger-full-access"}:
            return configured
        return "workspace-write"

    def _redact_text(self, text: str) -> str:
        redacted = text
        for value in self._redaction_values:
            redacted = redacted.replace(value, "[REDACTED]")
        return redacted

    @staticmethod
    def _append_log(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{text}\n")


def _clean_optional_string(
    value: object, *, fallback: object | None = None
) -> str | None:
    """Normalize optional string values from payload/env sources."""

    candidate = value if value is not None else fallback
    if candidate is None:
        return None
    cleaned = str(candidate).strip()
    return cleaned or None


def _parse_codex_overrides(payload: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Extract optional codex model/effort overrides from payload mapping."""

    raw = payload.get("codex")
    if raw is None:
        return (None, None)
    if not isinstance(raw, Mapping):
        raise CodexWorkerHandlerError(
            "codex field must be an object containing optional model/effort"
        )
    model = _clean_optional_string(raw.get("model"))
    effort = _clean_optional_string(raw.get("effort"))
    return (model, effort)


__all__ = [
    "ArtifactUpload",
    "CodexExecHandler",
    "CodexExecPayload",
    "CodexSkillPayload",
    "CodexWorkerHandlerError",
    "WorkerExecutionResult",
]
