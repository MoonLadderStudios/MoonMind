"""Daemon loop and queue API client for the standalone Codex worker."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import socket
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from os import environ
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import UUID

import httpx

import moonmind.utils.logging as moonmind_logging
from moonmind.agents.codex_worker.handlers import (
    ArtifactUpload,
    CodexExecHandler,
    CommandResult,
    WorkerExecutionResult,
)
from moonmind.agents.codex_worker.secret_refs import (
    SecretReferenceError,
    VaultSecretResolver,
    load_vault_token,
)
from moonmind.config.settings import settings
from moonmind.workflows.agent_queue.task_contract import (
    CANONICAL_TASK_JOB_TYPE,
    LEGACY_TASK_JOB_TYPES,
    SUPPORTED_EXECUTION_RUNTIMES,
    TaskContractError,
    build_canonical_task_view,
    build_task_stage_plan,
)
from moonmind.workflows.skills.materializer import (
    SkillMaterializationError,
    materialize_run_skill_workspace,
)
from moonmind.workflows.skills.resolver import (
    SkillResolutionError,
    resolve_run_skill_selection,
)
from moonmind.workflows.skills.workspace_links import (
    SkillWorkspaceError,
    ensure_shared_skill_links,
)
from moonmind.workflows.speckit_celery.workspace import generate_branch_name

logger = logging.getLogger(__name__)


class QueueClientError(RuntimeError):
    """Raised when queue API requests fail."""


@dataclass(frozen=True, slots=True)
class CodexWorkerConfig:
    """Runtime configuration for the standalone Codex worker."""

    moonmind_url: str
    worker_id: str
    worker_token: str | None
    poll_interval_ms: int
    lease_seconds: int
    workdir: Path
    allowed_types: tuple[str, ...] = ("task", "codex_exec", "codex_skill")
    legacy_job_types_enabled: bool = True
    worker_runtime: str = "codex"
    default_skill: str = "speckit"
    allowed_skills: tuple[str, ...] = ("speckit",)
    default_codex_model: str | None = None
    default_codex_effort: str | None = None
    default_gemini_model: str | None = None
    default_gemini_effort: str | None = None
    default_claude_model: str | None = None
    default_claude_effort: str | None = None
    gemini_binary: str = "gemini"
    claude_binary: str = "claude"
    worker_capabilities: tuple[str, ...] = ("codex", "git", "gh")
    docker_binary: str = "docker"
    container_workspace_volume: str | None = None
    container_default_timeout_seconds: int = 3600
    vault_address: str | None = None
    vault_token: str | None = None
    vault_token_file: Path | None = None
    vault_namespace: str | None = None
    vault_allowed_mounts: tuple[str, ...] = ("kv",)
    vault_timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "CodexWorkerConfig":
        """Load worker settings from environment variables."""

        source = env or environ
        moonmind_url = str(source.get("MOONMIND_URL", "")).strip()
        if not moonmind_url:
            raise ValueError("MOONMIND_URL must be configured")

        worker_id = (
            str(source.get("MOONMIND_WORKER_ID", "")).strip() or socket.gethostname()
        )
        poll_interval_ms = int(
            str(source.get("MOONMIND_POLL_INTERVAL_MS", "1500")).strip()
        )
        lease_seconds = int(str(source.get("MOONMIND_LEASE_SECONDS", "120")).strip())
        if poll_interval_ms < 1:
            raise ValueError("MOONMIND_POLL_INTERVAL_MS must be >= 1")
        if lease_seconds < 1:
            raise ValueError("MOONMIND_LEASE_SECONDS must be >= 1")

        workdir_raw = (
            str(source.get("MOONMIND_WORKDIR", "var/worker")).strip() or "var/worker"
        )
        worker_token = str(source.get("MOONMIND_WORKER_TOKEN", "")).strip() or None
        legacy_enabled_raw = (
            str(source.get("MOONMIND_ENABLE_LEGACY_JOB_TYPES", "true")).strip().lower()
        )
        legacy_job_types_enabled = legacy_enabled_raw not in {
            "0",
            "false",
            "no",
            "off",
            "",
        }
        allowed_types = (
            ("task", "codex_exec", "codex_skill")
            if legacy_job_types_enabled
            else ("task",)
        )

        default_skill = (
            str(
                source.get(
                    "MOONMIND_DEFAULT_SKILL",
                    source.get("SPEC_WORKFLOW_DEFAULT_SKILL", "speckit"),
                )
            ).strip()
            or "speckit"
        )
        allowed_skills_csv = str(
            source.get(
                "MOONMIND_ALLOWED_SKILLS",
                source.get("SPEC_WORKFLOW_ALLOWED_SKILLS", default_skill),
            )
        ).strip()
        allowed_skills_items = [
            item.strip() for item in allowed_skills_csv.split(",") if item.strip()
        ]
        if default_skill not in allowed_skills_items:
            allowed_skills_items.append(default_skill)
        allowed_skills = tuple(dict.fromkeys(allowed_skills_items))

        default_codex_model = (
            str(
                source.get(
                    "MOONMIND_CODEX_MODEL",
                    source.get("CODEX_MODEL", ""),
                )
            ).strip()
            or None
        )
        default_codex_effort = (
            str(
                source.get(
                    "MOONMIND_CODEX_EFFORT",
                    source.get(
                        "CODEX_MODEL_REASONING_EFFORT",
                        source.get("MODEL_REASONING_EFFORT", ""),
                    ),
                )
            ).strip()
            or None
        )
        worker_runtime = (
            str(source.get("MOONMIND_WORKER_RUNTIME", "codex")).strip().lower()
            or "codex"
        )
        allowed_worker_runtimes = {"codex", "gemini", "claude", "universal"}
        if worker_runtime not in allowed_worker_runtimes:
            supported = ", ".join(sorted(allowed_worker_runtimes))
            raise ValueError(f"MOONMIND_WORKER_RUNTIME must be one of: {supported}")

        default_gemini_model = (
            str(
                source.get(
                    "MOONMIND_GEMINI_MODEL",
                    source.get("GEMINI_MODEL", ""),
                )
            ).strip()
            or None
        )
        default_gemini_effort = (
            str(
                source.get(
                    "MOONMIND_GEMINI_EFFORT",
                    source.get("GEMINI_REASONING_EFFORT", ""),
                )
            ).strip()
            or None
        )
        default_claude_model = (
            str(
                source.get(
                    "MOONMIND_CLAUDE_MODEL",
                    source.get("CLAUDE_MODEL", ""),
                )
            ).strip()
            or None
        )
        default_claude_effort = (
            str(
                source.get(
                    "MOONMIND_CLAUDE_EFFORT",
                    source.get("CLAUDE_REASONING_EFFORT", ""),
                )
            ).strip()
            or None
        )
        gemini_binary = (
            str(source.get("MOONMIND_GEMINI_BINARY", "gemini")).strip() or "gemini"
        )
        claude_binary = (
            str(source.get("MOONMIND_CLAUDE_BINARY", "claude")).strip() or "claude"
        )

        capability_csv = str(source.get("MOONMIND_WORKER_CAPABILITIES", "")).strip()
        if capability_csv:
            worker_capabilities = tuple(
                dict.fromkeys(
                    [item.strip() for item in capability_csv.split(",") if item.strip()]
                )
            )
        else:
            if worker_runtime == "universal":
                worker_capabilities = ("codex", "gemini", "claude", "git", "gh")
            else:
                worker_capabilities = (worker_runtime, "git", "gh")

        docker_binary = (
            str(source.get("MOONMIND_DOCKER_BINARY", "docker")).strip() or "docker"
        )
        container_workspace_volume = (
            str(source.get("MOONMIND_CONTAINER_WORKSPACE_VOLUME", "")).strip() or None
        )
        container_default_timeout_seconds = int(
            str(source.get("MOONMIND_CONTAINER_TIMEOUT_SECONDS", "3600")).strip()
        )
        if container_default_timeout_seconds < 1:
            raise ValueError("MOONMIND_CONTAINER_TIMEOUT_SECONDS must be >= 1")

        vault_address = str(source.get("MOONMIND_VAULT_ADDR", "")).strip() or None
        vault_token_file_raw = str(source.get("MOONMIND_VAULT_TOKEN_FILE", "")).strip()
        vault_token_file = Path(vault_token_file_raw) if vault_token_file_raw else None
        vault_token: str | None = None
        if vault_address:
            vault_token = load_vault_token(
                token=str(source.get("MOONMIND_VAULT_TOKEN", "")).strip() or None,
                token_file=vault_token_file,
            )
        vault_namespace = (
            str(source.get("MOONMIND_VAULT_NAMESPACE", "")).strip() or None
        )
        vault_mounts_csv = (
            str(source.get("MOONMIND_VAULT_ALLOWED_MOUNTS", "kv")).strip() or "kv"
        )
        vault_allowed_mounts = tuple(
            dict.fromkeys(
                [item.strip() for item in vault_mounts_csv.split(",") if item.strip()]
            )
        ) or ("kv",)
        vault_timeout_seconds = float(
            str(source.get("MOONMIND_VAULT_TIMEOUT_SECONDS", "10")).strip() or "10"
        )
        if vault_timeout_seconds <= 0:
            raise ValueError("MOONMIND_VAULT_TIMEOUT_SECONDS must be > 0")
        return cls(
            moonmind_url=moonmind_url.rstrip("/"),
            worker_id=worker_id,
            worker_token=worker_token,
            poll_interval_ms=poll_interval_ms,
            lease_seconds=lease_seconds,
            workdir=Path(workdir_raw),
            allowed_types=allowed_types,
            legacy_job_types_enabled=legacy_job_types_enabled,
            worker_runtime=worker_runtime,
            default_skill=default_skill,
            allowed_skills=allowed_skills,
            default_codex_model=default_codex_model,
            default_codex_effort=default_codex_effort,
            default_gemini_model=default_gemini_model,
            default_gemini_effort=default_gemini_effort,
            default_claude_model=default_claude_model,
            default_claude_effort=default_claude_effort,
            gemini_binary=gemini_binary,
            claude_binary=claude_binary,
            worker_capabilities=worker_capabilities,
            docker_binary=docker_binary,
            container_workspace_volume=container_workspace_volume,
            container_default_timeout_seconds=container_default_timeout_seconds,
            vault_address=vault_address,
            vault_token=vault_token,
            vault_token_file=vault_token_file,
            vault_namespace=vault_namespace,
            vault_allowed_mounts=vault_allowed_mounts,
            vault_timeout_seconds=vault_timeout_seconds,
        )


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    """Normalized job returned by queue claim API."""

    id: UUID
    type: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PreparedTaskWorkspace:
    """Resolved workspace context generated by the prepare stage."""

    job_root: Path
    repo_dir: Path
    artifacts_dir: Path
    prepare_log_path: Path
    execute_log_path: Path
    publish_log_path: Path
    task_context_path: Path
    publish_result_path: Path
    default_branch: str
    starting_branch: str
    new_branch: str | None
    working_branch: str
    workdir_mode: str
    repo_command_env: dict[str, str] | None
    publish_command_env: dict[str, str] | None


@dataclass(frozen=True, slots=True)
class TaskAuthContext:
    """Resolved auth references and runtime token-source metadata."""

    repo_auth_ref: str | None
    publish_auth_ref: str | None
    repo_auth_source: str
    publish_auth_source: str | None
    repo_command_env: dict[str, str] | None
    publish_command_env: dict[str, str] | None


@dataclass(frozen=True, slots=True)
class ContainerCacheVolume:
    """Normalized cache volume mount for container task execution."""

    name: str
    target: str


@dataclass(frozen=True, slots=True)
class ContainerTaskSpec:
    """Normalized task.container payload used by execute stage."""

    image: str
    command: tuple[str, ...]
    workdir: str | None
    env: dict[str, str]
    artifacts_subdir: str
    timeout_seconds: int
    pull_mode: str
    cpus: str | None
    memory: str | None
    cache_volumes: tuple[ContainerCacheVolume, ...]


class QueueApiClient:
    """HTTP client wrapper for queue and artifact endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        worker_token: str | None,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        headers: dict[str, str] = {"Accept": "application/json"}
        if worker_token:
            headers["X-MoonMind-Worker-Token"] = worker_token
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
            headers=headers,
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        """Close the underlying HTTP client when owned by this wrapper."""

        if self._owns_client:
            await self._client.aclose()

    async def claim_job(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        allowed_types: Sequence[str] | None = None,
        worker_capabilities: Sequence[str] | None = None,
    ) -> ClaimedJob | None:
        payload: dict[str, Any] = {
            "workerId": worker_id,
            "leaseSeconds": lease_seconds,
        }
        if allowed_types:
            payload["allowedTypes"] = list(allowed_types)
        if worker_capabilities:
            payload["workerCapabilities"] = list(worker_capabilities)
        data = await self._post_json("/api/queue/jobs/claim", json=payload)
        job_data = data.get("job")
        if not job_data:
            return None
        return ClaimedJob(
            id=UUID(str(job_data["id"])),
            type=str(job_data["type"]),
            payload=dict(job_data.get("payload") or {}),
        )

    async def heartbeat(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        lease_seconds: int,
    ) -> None:
        await self._post_json(
            f"/api/queue/jobs/{job_id}/heartbeat",
            json={"workerId": worker_id, "leaseSeconds": lease_seconds},
        )

    async def complete_job(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        result_summary: str | None,
    ) -> None:
        payload = {"workerId": worker_id}
        if result_summary:
            payload["resultSummary"] = result_summary
        await self._post_json(f"/api/queue/jobs/{job_id}/complete", json=payload)

    async def fail_job(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        error_message: str,
        retryable: bool = False,
    ) -> None:
        await self._post_json(
            f"/api/queue/jobs/{job_id}/fail",
            json={
                "workerId": worker_id,
                "errorMessage": error_message,
                "retryable": retryable,
            },
        )

    async def append_event(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        level: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        body: dict[str, Any] = {
            "workerId": worker_id,
            "level": level,
            "message": message,
        }
        if payload is not None:
            body["payload"] = payload
        await self._post_json(f"/api/queue/jobs/{job_id}/events", json=body)

    async def upload_artifact(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        artifact: ArtifactUpload,
    ) -> None:
        if not artifact.path.exists():
            raise QueueClientError(f"artifact file does not exist: {artifact.path}")

        content_type = artifact.content_type or "application/octet-stream"
        data: dict[str, str] = {
            "name": artifact.name,
            "workerId": worker_id,
        }
        if artifact.content_type:
            data["contentType"] = artifact.content_type

        digest = artifact.digest or self._sha256_file(artifact.path)
        if digest:
            data["digest"] = digest

        with artifact.path.open("rb") as handle:
            files = {
                "file": (artifact.path.name, handle, content_type),
            }
            try:
                response = await self._client.post(
                    f"/api/queue/jobs/{job_id}/artifacts/upload",
                    data=data,
                    files=files,
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise QueueClientError(
                    f"artifact upload failed for job {job_id}: {exc}"
                ) from exc

    async def _post_json(self, path: str, *, json: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=json)
            response.raise_for_status()
            return dict(response.json()) if response.content else {}
        except httpx.HTTPError as exc:
            raise QueueClientError(f"queue API request failed: {path}: {exc}") from exc

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(8192), b""):
                digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"


class CodexWorker:
    """Single-worker daemon that claims and executes queue jobs."""

    def __init__(
        self,
        *,
        config: CodexWorkerConfig,
        queue_client: QueueApiClient,
        codex_exec_handler: CodexExecHandler,
    ) -> None:
        self._config = config
        self._queue_client = queue_client
        self._codex_exec_handler = codex_exec_handler
        self._secret_redactor = moonmind_logging.SecretRedactor.from_environ(
            placeholder="[REDACTED]"
        )
        self._dynamic_redaction_values: set[str] = set()
        self._vault_secret_resolver: VaultSecretResolver | None = None
        if self._config.vault_address and self._config.vault_token:
            self._vault_secret_resolver = VaultSecretResolver(
                address=self._config.vault_address,
                token=self._config.vault_token,
                namespace=self._config.vault_namespace,
                allowed_mounts=self._config.vault_allowed_mounts,
                timeout_seconds=self._config.vault_timeout_seconds,
            )

    async def run_forever(self, *, stop_event: asyncio.Event | None = None) -> None:
        """Continuously process queue jobs until asked to stop."""

        run_stop = stop_event or asyncio.Event()
        try:
            while not run_stop.is_set():
                try:
                    claimed_work = await self.run_once()
                except Exception:
                    logger.exception("Unhandled exception in CodexWorker.run_forever")
                    await asyncio.sleep(self._config.poll_interval_ms / 1000.0)
                    continue
                if claimed_work:
                    continue
                await asyncio.sleep(self._config.poll_interval_ms / 1000.0)
        finally:
            if self._vault_secret_resolver is not None:
                with suppress(Exception):
                    await self._vault_secret_resolver.aclose()

    async def run_once(self) -> bool:
        """Claim and process one job if available."""

        job = await self._claim_next_job()
        if job is None:
            return False

        supported_types = {CANONICAL_TASK_JOB_TYPE, *LEGACY_TASK_JOB_TYPES}
        if job.type not in supported_types:
            await self._emit_event(
                job_id=job.id,
                level="error",
                message="Unsupported job type",
                payload={"jobType": job.type},
            )
            await self._queue_client.fail_job(
                job_id=job.id,
                worker_id=self._config.worker_id,
                error_message=f"unsupported job type: {job.type}",
                retryable=False,
            )
            return True
        if (
            not self._config.legacy_job_types_enabled
            and job.type in LEGACY_TASK_JOB_TYPES
        ):
            await self._emit_event(
                job_id=job.id,
                level="error",
                message="Legacy queue job types are disabled for this worker",
                payload={"jobType": job.type},
            )
            await self._queue_client.fail_job(
                job_id=job.id,
                worker_id=self._config.worker_id,
                error_message=f"legacy job type disabled: {job.type}",
                retryable=False,
            )
            return True

        try:
            canonical_payload = build_canonical_task_view(
                job_type=job.type,
                payload=job.payload,
            )
        except TaskContractError as exc:
            await self._emit_event(
                job_id=job.id,
                level="error",
                message="Job payload failed task-contract normalization",
                payload={"jobType": job.type, "error": str(exc)},
            )
            await self._queue_client.fail_job(
                job_id=job.id,
                worker_id=self._config.worker_id,
                error_message=f"invalid job payload: {exc}",
                retryable=False,
            )
            return True

        runtime_mode = (
            str(canonical_payload.get("targetRuntime") or "codex").strip().lower()
        )
        if runtime_mode not in SUPPORTED_EXECUTION_RUNTIMES:
            await self._emit_event(
                job_id=job.id,
                level="error",
                message="Task runtime is not recognized by worker",
                payload={
                    "jobType": job.type,
                    "targetRuntime": runtime_mode,
                },
            )
            await self._queue_client.fail_job(
                job_id=job.id,
                worker_id=self._config.worker_id,
                error_message=f"unsupported task runtime: {runtime_mode}",
                retryable=False,
            )
            return True
        if not self._runtime_can_execute(runtime_mode):
            await self._emit_event(
                job_id=job.id,
                level="error",
                message="Task runtime is not executable by this worker runtime mode",
                payload={
                    "jobType": job.type,
                    "targetRuntime": runtime_mode,
                    "workerRuntime": self._config.worker_runtime,
                },
            )
            await self._queue_client.fail_job(
                job_id=job.id,
                worker_id=self._config.worker_id,
                error_message=(
                    "unsupported task runtime for worker "
                    f"({self._config.worker_runtime}): {runtime_mode}"
                ),
                retryable=False,
            )
            return True

        policy_error = self._validate_required_job_policy(canonical_payload)
        if policy_error is not None:
            await self._emit_event(
                job_id=job.id,
                level="error",
                message="Task rejected by worker policy requirements",
                payload={
                    "jobType": job.type,
                    "targetRuntime": runtime_mode,
                    "error": policy_error,
                },
            )
            await self._queue_client.fail_job(
                job_id=job.id,
                worker_id=self._config.worker_id,
                error_message=policy_error,
                retryable=False,
            )
            return True

        skill_meta = self._execution_metadata(canonical_payload)
        await self._emit_event(
            job_id=job.id,
            level="info",
            message="Worker claimed job",
            payload={"jobType": job.type, "targetRuntime": runtime_mode, **skill_meta},
        )

        selected_skill = str(
            skill_meta.get("selectedSkill") or self._config.default_skill
        )
        uses_skills = bool(skill_meta.get("usedSkills"))
        if uses_skills and selected_skill not in self._config.allowed_skills:
            await self._emit_event(
                job_id=job.id,
                level="error",
                message="Skill is not allowlisted for this worker",
                payload={"jobType": job.type, **skill_meta},
            )
            await self._queue_client.fail_job(
                job_id=job.id,
                worker_id=self._config.worker_id,
                error_message=f"skill not allowlisted: {selected_skill}",
                retryable=False,
            )
            return True

        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(job_id=job.id, stop_event=heartbeat_stop)
        )

        staged_artifacts: list[ArtifactUpload] = []
        try:
            stage_plan = build_task_stage_plan(canonical_payload)
            await self._emit_event(
                job_id=job.id,
                level="info",
                message="moonmind.task.plan",
                payload={"jobType": job.type, "stages": stage_plan, **skill_meta},
            )

            prepared = await self._run_prepare_stage(
                job_id=job.id,
                canonical_payload=canonical_payload,
                source_payload=job.payload,
                selected_skill=selected_skill,
                job_type=job.type,
                skill_meta=skill_meta,
            )
            staged_artifacts.extend(self._prepare_stage_artifacts(prepared))

            await self._emit_stage_event(
                job_id=job.id,
                stage="moonmind.task.execute",
                status="started",
                payload={"jobType": job.type, **skill_meta},
            )
            result = await self._run_execute_stage(
                job_id=job.id,
                canonical_payload=canonical_payload,
                source_payload=job.payload,
                runtime_mode=runtime_mode,
                selected_skill=selected_skill,
                prepared=prepared,
                uses_skills=uses_skills,
                use_fallback=bool(skill_meta.get("usedFallback")),
            )
            execute_artifacts = self._normalize_execute_artifacts(
                result_artifacts=result.artifacts,
                execute_log_path=prepared.execute_log_path,
            )
            staged_artifacts.extend(execute_artifacts)
            await self._emit_stage_event(
                job_id=job.id,
                stage="moonmind.task.execute",
                status="finished" if result.succeeded else "failed",
                payload={"jobType": job.type, "summary": result.summary, **skill_meta},
            )
            if result.succeeded:
                publish_note = await self._run_publish_stage(
                    job_id=job.id,
                    canonical_payload=canonical_payload,
                    prepared=prepared,
                    skill_meta=skill_meta,
                    job_type=job.type,
                    staged_artifacts=staged_artifacts,
                )
                if publish_note:
                    base_summary = result.summary or "task completed"
                    result = type(result)(
                        succeeded=True,
                        summary=f"{base_summary}; {publish_note}",
                        error_message=None,
                        artifacts=result.artifacts,
                    )

            await self._upload_artifacts(job_id=job.id, artifacts=staged_artifacts)
            if result.succeeded:
                await self._queue_client.complete_job(
                    job_id=job.id,
                    worker_id=self._config.worker_id,
                    result_summary=result.summary,
                )
                await self._emit_event(
                    job_id=job.id,
                    level="info",
                    message="Job completed",
                    payload={
                        "summary": result.summary,
                        "jobType": job.type,
                        **skill_meta,
                    },
                )
            else:
                terminal_error = self._redact_text(
                    result.error_message or "codex_exec failed"
                )
                await self._queue_client.fail_job(
                    job_id=job.id,
                    worker_id=self._config.worker_id,
                    error_message=terminal_error,
                    retryable=False,
                )
                await self._emit_event(
                    job_id=job.id,
                    level="error",
                    message="Job failed",
                    payload={
                        "error": terminal_error,
                        "jobType": job.type,
                        **skill_meta,
                    },
                )
        except Exception as exc:
            if staged_artifacts:
                try:
                    await self._upload_artifacts(
                        job_id=job.id,
                        artifacts=staged_artifacts,
                    )
                except Exception:
                    logger.warning(
                        "Best-effort artifact upload failed during exception path",
                        exc_info=True,
                    )
            terminal_error = self._redact_text(str(exc))
            await self._queue_client.fail_job(
                job_id=job.id,
                worker_id=self._config.worker_id,
                error_message=terminal_error,
                retryable=False,
            )
            await self._emit_event(
                job_id=job.id,
                level="error",
                message="Worker exception while executing job",
                payload={"error": terminal_error, "jobType": job.type, **skill_meta},
            )
        finally:
            heartbeat_stop.set()
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task

        return True

    async def _claim_next_job(self) -> ClaimedJob | None:
        """Claim next eligible job using policy-safe claim parameters."""

        # Repository allowlist enforcement stays server-side; worker only forwards
        # allowed job types and capabilities from its local runtime config.
        return await self._queue_client.claim_job(
            worker_id=self._config.worker_id,
            lease_seconds=self._config.lease_seconds,
            allowed_types=self._config.allowed_types,
            worker_capabilities=self._config.worker_capabilities,
        )

    def _execution_metadata(
        self, canonical_payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Return normalized skill execution metadata for job events."""

        task_node = canonical_payload.get("task")
        if isinstance(task_node, Mapping):
            skill_node = task_node.get("skill")
            runtime_node = task_node.get("runtime")
        else:
            skill_node = None
            runtime_node = None
        if isinstance(skill_node, Mapping):
            selected_skill = str(skill_node.get("id") or "auto").strip() or "auto"
        else:
            selected_skill = "auto"
        if isinstance(runtime_node, Mapping):
            selected_model = str(runtime_node.get("model") or "").strip() or None
            selected_effort = str(runtime_node.get("effort") or "").strip() or None
        else:
            selected_model = None
            selected_effort = None
        execution_path = "direct_only"
        used_skills = selected_skill != "auto"
        used_fallback = False

        if used_skills:
            execution_path = "skill"
            if selected_skill != "speckit":
                execution_path = "direct_fallback"
                used_fallback = True

        return {
            "selectedSkill": selected_skill,
            "executionPath": execution_path,
            "usedSkills": used_skills,
            "usedFallback": used_fallback,
            "shadowModeRequested": False,
            "runtimeModel": selected_model,
            "runtimeEffort": selected_effort,
        }

    @staticmethod
    def _safe_workdir_mode(source_payload: Mapping[str, Any]) -> str:
        candidate = str(source_payload.get("workdirMode", "fresh_clone")).strip()
        if candidate in {"fresh_clone", "reuse"}:
            return candidate
        return "fresh_clone"

    def _build_exec_payload(
        self,
        *,
        canonical_payload: Mapping[str, Any],
        source_payload: Mapping[str, Any],
        ref_override: str | None = None,
        publish_mode_override: str | None = None,
        publish_base_override: str | None = None,
        workdir_mode_override: str | None = None,
        include_ref: bool = True,
    ) -> dict[str, Any]:
        task_node = canonical_payload.get("task")
        task = task_node if isinstance(task_node, Mapping) else {}
        runtime_node = task.get("runtime")
        runtime = runtime_node if isinstance(runtime_node, Mapping) else {}
        git_node = task.get("git")
        git = git_node if isinstance(git_node, Mapping) else {}
        publish_node = task.get("publish")
        publish = publish_node if isinstance(publish_node, Mapping) else {}

        payload: dict[str, Any] = {
            "repository": canonical_payload.get("repository"),
            "instruction": task.get("instructions"),
            "workdirMode": workdir_mode_override
            or self._safe_workdir_mode(source_payload),
            "publish": {
                "mode": publish_mode_override or publish.get("mode") or "branch",
                "baseBranch": (
                    publish_base_override
                    if publish_base_override is not None
                    else publish.get("prBaseBranch")
                ),
            },
        }
        selected_ref = (
            ref_override if ref_override is not None else git.get("startingBranch")
        )
        if include_ref and selected_ref:
            payload["ref"] = selected_ref

        codex_overrides: dict[str, str] = {}
        runtime_model = str(runtime.get("model") or "").strip()
        runtime_effort = str(runtime.get("effort") or "").strip()
        if runtime_model:
            codex_overrides["model"] = runtime_model
        if runtime_effort:
            codex_overrides["effort"] = runtime_effort
        if codex_overrides:
            payload["codex"] = codex_overrides
        return payload

    async def _run_prepare_stage(
        self,
        *,
        job_id: UUID,
        canonical_payload: Mapping[str, Any],
        source_payload: Mapping[str, Any],
        selected_skill: str,
        job_type: str,
        skill_meta: Mapping[str, Any],
    ) -> PreparedTaskWorkspace:
        """Prepare workspace, repository checkout, and branch selection."""

        await self._emit_stage_event(
            job_id=job_id,
            stage="moonmind.task.prepare",
            status="started",
            payload={"jobType": job_type, **dict(skill_meta)},
        )
        try:
            job_root = self._config.workdir / str(job_id)
            artifacts_dir = job_root / "artifacts"
            logs_dir = artifacts_dir / "logs"
            prepare_log_path = logs_dir / "prepare.log"
            execute_log_path = logs_dir / "execute.log"
            publish_log_path = logs_dir / "publish.log"
            task_context_path = artifacts_dir / "task_context.json"
            publish_result_path = artifacts_dir / "publish_result.json"
            repo_dir = job_root / "repo"
            home_dir = job_root / "home"
            skills_active_path = job_root / "skills_active"

            artifacts_dir.mkdir(parents=True, exist_ok=True)
            logs_dir.mkdir(parents=True, exist_ok=True)
            home_dir.mkdir(parents=True, exist_ok=True)
            skills_active_path.mkdir(parents=True, exist_ok=True)

            materialized_skill_payload: dict[str, Any] | None = None
            if selected_skill != "auto":
                try:
                    selection = resolve_run_skill_selection(
                        run_id=str(job_id),
                        context={"skill_selection": [selected_skill]},
                    )
                    materialized_workspace = materialize_run_skill_workspace(
                        selection=selection,
                        run_root=job_root,
                        cache_root=self._resolve_skills_cache_root(),
                        verify_signatures=settings.spec_workflow.skills_verify_signatures,
                    )
                    materialized_skill_payload = materialized_workspace.to_payload()
                    self._append_stage_log(
                        prepare_log_path,
                        ("materialized selected skill workspace: " f"{selected_skill}"),
                    )
                except (SkillResolutionError, SkillMaterializationError) as exc:
                    raise RuntimeError(f"skill materialization failed: {exc}") from exc
            else:
                try:
                    ensure_shared_skill_links(
                        run_root=job_root, skills_active_path=skills_active_path
                    )
                except SkillWorkspaceError as exc:
                    self._append_stage_log(
                        prepare_log_path, f"skill-link setup warning: {exc}"
                    )

            workdir_mode = self._safe_workdir_mode(source_payload)
            task_node = canonical_payload.get("task")
            task = task_node if isinstance(task_node, Mapping) else {}
            git_node = task.get("git")
            git = git_node if isinstance(git_node, Mapping) else {}
            publish_node = task.get("publish")
            publish = publish_node if isinstance(publish_node, Mapping) else {}
            publish_mode = (
                str(publish.get("mode") or "branch").strip().lower() or "branch"
            )

            repository = str(canonical_payload.get("repository") or "").strip()
            if not repository:
                raise ValueError("repository is required for prepare stage")
            auth_context = await self._resolve_task_auth_context(
                canonical_payload=canonical_payload,
                publish_mode=publish_mode,
            )
            self._append_stage_log(
                prepare_log_path,
                (
                    "repo auth source: "
                    f"{auth_context.repo_auth_source}"
                    + (
                        f" ({auth_context.repo_auth_ref})"
                        if auth_context.repo_auth_ref
                        else ""
                    )
                ),
            )
            if auth_context.publish_auth_source is not None:
                self._append_stage_log(
                    prepare_log_path,
                    (
                        "publish auth source: "
                        f"{auth_context.publish_auth_source}"
                        + (
                            f" ({auth_context.publish_auth_ref})"
                            if auth_context.publish_auth_ref
                            else ""
                        )
                    ),
                )

            clone_runner_available = hasattr(self._codex_exec_handler, "_run_command")
            if clone_runner_available:
                if workdir_mode == "fresh_clone" and repo_dir.exists():
                    shutil.rmtree(repo_dir)
                if not repo_dir.exists():
                    job_root.mkdir(parents=True, exist_ok=True)
                    await self._run_stage_command(
                        [
                            "git",
                            "clone",
                            self._resolve_clone_url(repository),
                            str(repo_dir),
                        ],
                        cwd=job_root,
                        log_path=prepare_log_path,
                        env=auth_context.repo_command_env,
                    )
                await self._run_stage_command(
                    ["git", "fetch", "--all", "--prune"],
                    cwd=repo_dir,
                    log_path=prepare_log_path,
                    check=False,
                    env=auth_context.repo_command_env,
                )

            default_branch = await self._resolve_default_branch(
                repo_dir=repo_dir,
                log_path=prepare_log_path,
                fallback="main",
                env=auth_context.repo_command_env,
            )
            starting_branch_input = str(git.get("startingBranch") or "").strip() or None
            new_branch_input = str(git.get("newBranch") or "").strip() or None
            starting_branch = starting_branch_input or default_branch

            if new_branch_input:
                new_branch = new_branch_input
            elif starting_branch != default_branch:
                new_branch = None
            else:
                suffix = selected_skill if selected_skill != "auto" else None
                new_branch = generate_branch_name(job_id, prefix="task", suffix=suffix)

            working_branch = new_branch or starting_branch
            if clone_runner_available:
                await self._ensure_working_branch(
                    repo_dir=repo_dir,
                    starting_branch=starting_branch,
                    new_branch=new_branch,
                    log_path=prepare_log_path,
                    env=auth_context.repo_command_env,
                )

            context_payload = {
                "repository": repository,
                "runtime": canonical_payload.get("targetRuntime"),
                "skill": {
                    "id": selected_skill,
                    "args": (
                        (task.get("skill") or {}).get("args")
                        if isinstance(task.get("skill"), Mapping)
                        else {}
                    ),
                },
                "workdirMode": workdir_mode,
                "publishMode": publish_mode,
                "auth": {
                    "repoAuthRef": auth_context.repo_auth_ref,
                    "publishAuthRef": auth_context.publish_auth_ref,
                    "repoAuthSource": auth_context.repo_auth_source,
                    "publishAuthSource": auth_context.publish_auth_source,
                },
                "resolved": {
                    "defaultBranch": default_branch,
                    "startingBranch": {
                        "value": starting_branch,
                        "explicit": starting_branch_input is not None,
                    },
                    "newBranch": {
                        "value": new_branch,
                        "explicit": new_branch_input is not None,
                    },
                    "workingBranch": working_branch,
                },
                "workspace": {
                    "jobRoot": str(job_root),
                    "repo": str(repo_dir),
                    "home": str(home_dir),
                    "skillsActive": str(skills_active_path),
                    "artifacts": str(artifacts_dir),
                },
                "timestamp": datetime.now(UTC).isoformat(),
            }
            if materialized_skill_payload is not None:
                context_payload["skillsMaterialized"] = materialized_skill_payload
            redacted_context_payload = self._redact_payload(context_payload)
            task_context_path.write_text(
                json.dumps(redacted_context_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            await self._emit_event(
                job_id=job_id,
                level="info",
                message="task.git.defaultBranchResolved",
                payload={
                    "defaultBranch": default_branch,
                    "startingBranch": starting_branch,
                    "newBranch": new_branch,
                    "workingBranch": working_branch,
                },
            )
            await self._emit_stage_event(
                job_id=job_id,
                stage="moonmind.task.prepare",
                status="finished",
                payload={
                    "jobType": job_type,
                    "defaultBranch": default_branch,
                    "startingBranch": starting_branch,
                    "newBranch": new_branch,
                    "workingBranch": working_branch,
                    **dict(skill_meta),
                },
            )

            return PreparedTaskWorkspace(
                job_root=job_root,
                repo_dir=repo_dir,
                artifacts_dir=artifacts_dir,
                prepare_log_path=prepare_log_path,
                execute_log_path=execute_log_path,
                publish_log_path=publish_log_path,
                task_context_path=task_context_path,
                publish_result_path=publish_result_path,
                default_branch=default_branch,
                starting_branch=starting_branch,
                new_branch=new_branch,
                working_branch=working_branch,
                workdir_mode=workdir_mode,
                repo_command_env=auth_context.repo_command_env,
                publish_command_env=auth_context.publish_command_env,
            )
        except Exception as exc:
            await self._emit_stage_event(
                job_id=job_id,
                stage="moonmind.task.prepare",
                status="failed",
                payload={
                    "jobType": job_type,
                    "error": str(exc),
                    **dict(skill_meta),
                },
            )
            raise

    async def _run_publish_stage(
        self,
        *,
        job_id: UUID,
        canonical_payload: Mapping[str, Any],
        prepared: PreparedTaskWorkspace,
        skill_meta: Mapping[str, Any],
        job_type: str,
        staged_artifacts: list[ArtifactUpload],
    ) -> str | None:
        """Publish repository changes according to task publish policy."""

        task_node = canonical_payload.get("task")
        task = task_node if isinstance(task_node, Mapping) else {}
        publish_node = task.get("publish")
        publish = publish_node if isinstance(publish_node, Mapping) else {}
        publish_mode = str(publish.get("mode") or "branch").strip().lower() or "branch"
        if publish_mode == "none":
            await self._emit_event(
                job_id=job_id,
                level="info",
                message="moonmind.task.publish",
                payload={
                    "status": "skipped",
                    "reason": "publish mode is none",
                    **dict(skill_meta),
                },
            )
            return None

        await self._emit_stage_event(
            job_id=job_id,
            stage="moonmind.task.publish",
            status="started",
            payload={"jobType": job_type, "mode": publish_mode, **dict(skill_meta)},
        )
        try:
            self._append_stage_log(
                prepared.publish_log_path, f"publish mode: {publish_mode}"
            )
            self._append_stage_log(
                prepared.publish_log_path,
                f"working branch: {prepared.working_branch}",
            )

            status = await self._run_stage_command(
                ["git", "status", "--porcelain"],
                cwd=prepared.repo_dir,
                log_path=prepared.publish_log_path,
                check=False,
                env=prepared.publish_command_env,
            )
            if not status.stdout.strip():
                result_payload = {
                    "mode": publish_mode,
                    "branch": prepared.working_branch,
                    "prUrl": None,
                    "skipped": True,
                    "reason": "no local changes",
                }
                prepared.publish_result_path.write_text(
                    json.dumps(result_payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                staged_artifacts.extend(
                    [
                        ArtifactUpload(
                            path=prepared.publish_log_path,
                            name="logs/publish.log",
                            content_type="text/plain",
                        ),
                        ArtifactUpload(
                            path=prepared.publish_result_path,
                            name="publish_result.json",
                            content_type="application/json",
                        ),
                    ]
                )
                await self._emit_stage_event(
                    job_id=job_id,
                    stage="moonmind.task.publish",
                    status="finished",
                    payload={
                        "jobType": job_type,
                        "mode": publish_mode,
                        "skipped": True,
                        **dict(skill_meta),
                    },
                )
                await self._emit_event(
                    job_id=job_id,
                    level="info",
                    message="moonmind.task.publish",
                    payload={
                        "status": "skipped",
                        "reason": "no local changes",
                        **dict(skill_meta),
                    },
                )
                return "publish skipped: no local changes"

            await self._run_stage_command(
                ["git", "checkout", prepared.working_branch],
                cwd=prepared.repo_dir,
                log_path=prepared.publish_log_path,
                env=prepared.publish_command_env,
            )
            await self._run_stage_command(
                ["git", "add", "-A"],
                cwd=prepared.repo_dir,
                log_path=prepared.publish_log_path,
                env=prepared.publish_command_env,
            )

            commit_message = str(publish.get("commitMessage") or "").strip() or (
                f"MoonMind task result for job {job_id}"
            )
            await self._run_stage_command(
                ["git", "commit", "-m", commit_message],
                cwd=prepared.repo_dir,
                log_path=prepared.publish_log_path,
                env=prepared.publish_command_env,
            )
            await self._run_stage_command(
                ["git", "push", "-u", "origin", prepared.working_branch],
                cwd=prepared.repo_dir,
                log_path=prepared.publish_log_path,
                env=prepared.publish_command_env,
            )

            pr_url: str | None = None
            publish_note = f"published branch {prepared.working_branch}"
            if publish_mode == "pr":
                pr_base = (
                    str(publish.get("prBaseBranch") or "").strip()
                    or prepared.starting_branch
                )
                pr_title = str(publish.get("prTitle") or "").strip() or (
                    f"MoonMind task result for job {job_id}"
                )
                pr_body = str(publish.get("prBody") or "").strip() or (
                    "Automated PR generated by moonmind-codex-worker."
                )
                pr_result = await self._run_stage_command(
                    [
                        self._resolve_gh_binary(),
                        "pr",
                        "create",
                        "--base",
                        pr_base,
                        "--head",
                        prepared.working_branch,
                        "--title",
                        pr_title,
                        "--body",
                        pr_body,
                    ],
                    cwd=prepared.repo_dir,
                    log_path=prepared.publish_log_path,
                    env=prepared.publish_command_env,
                )
                pr_url = self._extract_pr_url(pr_result.stdout)
                publish_note = (
                    f"published PR from {prepared.working_branch}"
                    if pr_url is None
                    else f"published PR {pr_url}"
                )

            result_payload = {
                "mode": publish_mode,
                "branch": prepared.working_branch,
                "baseBranch": prepared.starting_branch,
                "prUrl": pr_url,
                "skipped": False,
            }
            prepared.publish_result_path.write_text(
                json.dumps(result_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            staged_artifacts.extend(
                [
                    ArtifactUpload(
                        path=prepared.publish_log_path,
                        name="logs/publish.log",
                        content_type="text/plain",
                    ),
                    ArtifactUpload(
                        path=prepared.publish_result_path,
                        name="publish_result.json",
                        content_type="application/json",
                    ),
                ]
            )
            await self._emit_stage_event(
                job_id=job_id,
                stage="moonmind.task.publish",
                status="finished",
                payload={
                    "jobType": job_type,
                    "mode": publish_mode,
                    "workingBranch": prepared.working_branch,
                    "prUrl": pr_url,
                    **dict(skill_meta),
                },
            )
            await self._emit_event(
                job_id=job_id,
                level="info",
                message="moonmind.task.publish",
                payload={
                    "status": "published",
                    "mode": publish_mode,
                    "workingBranch": prepared.working_branch,
                    "prUrl": pr_url,
                    **dict(skill_meta),
                },
            )
            return publish_note
        except Exception as exc:
            await self._emit_stage_event(
                job_id=job_id,
                stage="moonmind.task.publish",
                status="failed",
                payload={"jobType": job_type, "error": str(exc), **dict(skill_meta)},
            )
            raise

    def _prepare_stage_artifacts(
        self, prepared: PreparedTaskWorkspace
    ) -> list[ArtifactUpload]:
        """Return standard prepare-stage artifacts."""

        return [
            ArtifactUpload(
                path=prepared.prepare_log_path,
                name="logs/prepare.log",
                content_type="text/plain",
            ),
            ArtifactUpload(
                path=prepared.task_context_path,
                name="task_context.json",
                content_type="application/json",
            ),
        ]

    def _normalize_execute_artifacts(
        self,
        *,
        result_artifacts: Sequence[ArtifactUpload],
        execute_log_path: Path,
    ) -> list[ArtifactUpload]:
        """Map execute artifacts to standard stage naming conventions."""

        normalized: list[ArtifactUpload] = []
        has_execute_log = False
        for artifact in result_artifacts:
            if artifact.name == "logs/codex_exec.log":
                if artifact.path.exists():
                    execute_log_path.parent.mkdir(parents=True, exist_ok=True)
                    if artifact.path != execute_log_path:
                        shutil.copy2(artifact.path, execute_log_path)
                    has_execute_log = True
                    normalized.append(
                        ArtifactUpload(
                            path=execute_log_path,
                            name="logs/execute.log",
                            content_type="text/plain",
                            digest=artifact.digest,
                        )
                    )
                continue
            normalized.append(artifact)

        if not has_execute_log and execute_log_path.exists():
            normalized.append(
                ArtifactUpload(
                    path=execute_log_path,
                    name="logs/execute.log",
                    content_type="text/plain",
                )
            )
        return normalized

    async def _emit_stage_event(
        self,
        *,
        job_id: UUID,
        stage: str,
        status: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        event_payload = {"stage": stage, "status": status}
        if payload:
            event_payload.update(payload)
        await self._emit_event(
            job_id=job_id,
            level="info" if status != "failed" else "error",
            message=stage,
            payload=event_payload,
        )

    def _append_stage_log(self, log_path: Path, line: str) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).isoformat()
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {self._redact_text(line)}\n")

    async def _resolve_default_branch(
        self,
        *,
        repo_dir: Path,
        log_path: Path,
        fallback: str,
        env: Mapping[str, str] | None = None,
    ) -> str:
        runner_available = hasattr(self._codex_exec_handler, "_run_command")
        if not runner_available or not repo_dir.exists():
            return fallback

        symbolic = await self._run_stage_command(
            ["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
            cwd=repo_dir,
            log_path=log_path,
            check=False,
            env=env,
        )
        resolved = symbolic.stdout.strip()
        if symbolic.returncode == 0 and resolved:
            if resolved.startswith("origin/"):
                return resolved.removeprefix("origin/")
            return resolved

        remote_show = await self._run_stage_command(
            ["git", "remote", "show", "origin"],
            cwd=repo_dir,
            log_path=log_path,
            check=False,
            env=env,
        )
        for line in remote_show.stdout.splitlines():
            marker = "HEAD branch:"
            if marker in line:
                candidate = line.split(marker, 1)[1].strip()
                if candidate:
                    return candidate
        return fallback

    async def _ensure_working_branch(
        self,
        *,
        repo_dir: Path,
        starting_branch: str,
        new_branch: str | None,
        log_path: Path,
        env: Mapping[str, str] | None = None,
    ) -> None:
        checkout = await self._run_stage_command(
            ["git", "checkout", starting_branch],
            cwd=repo_dir,
            log_path=log_path,
            check=False,
            env=env,
        )
        if checkout.returncode != 0:
            await self._run_stage_command(
                ["git", "checkout", "-B", starting_branch, f"origin/{starting_branch}"],
                cwd=repo_dir,
                log_path=log_path,
                env=env,
            )
        if new_branch:
            await self._run_stage_command(
                ["git", "checkout", "-B", new_branch, starting_branch],
                cwd=repo_dir,
                log_path=log_path,
                env=env,
            )

    async def _run_stage_command(
        self,
        command: list[str],
        *,
        cwd: Path,
        log_path: Path,
        check: bool = True,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        runner = getattr(self._codex_exec_handler, "_run_command", None)
        if callable(runner):
            return await runner(
                command,
                cwd=cwd,
                log_path=log_path,
                check=check,
                env=dict(env) if env is not None else None,
                redaction_values=tuple(self._dynamic_redaction_values),
            )
        self._append_stage_log(log_path, f"$ {' '.join(command)}")
        return CommandResult(tuple(command), 0, "", "")

    def _resolve_clone_url(self, repository: str) -> str:
        to_clone_url = getattr(self._codex_exec_handler, "_to_clone_url", None)
        if callable(to_clone_url):
            return str(to_clone_url(repository))
        if repository.startswith(("http://", "https://", "git@")):
            return repository
        return f"https://github.com/{repository}.git"

    def _resolve_gh_binary(self) -> str:
        gh_binary = getattr(self._codex_exec_handler, "_gh_binary", None)
        if isinstance(gh_binary, str) and gh_binary.strip():
            return gh_binary
        return "gh"

    def _resolve_skills_cache_root(self) -> Path:
        cache_root = Path(settings.spec_workflow.skills_cache_root)
        if not cache_root.is_absolute():
            cache_root = (Path.cwd() / cache_root).resolve()
        cache_root.mkdir(parents=True, exist_ok=True)
        return cache_root

    @staticmethod
    def _extract_pr_url(stdout: str) -> str | None:
        for line in stdout.splitlines():
            candidate = line.strip()
            if candidate.startswith("http://") or candidate.startswith("https://"):
                return candidate
        return None

    def _build_skill_payload(
        self,
        *,
        canonical_payload: Mapping[str, Any],
        selected_skill: str,
        source_payload: Mapping[str, Any],
        ref_override: str | None = None,
        publish_mode_override: str | None = None,
        publish_base_override: str | None = None,
        workdir_mode_override: str | None = None,
        include_ref: bool = True,
    ) -> dict[str, Any]:
        task_node = canonical_payload.get("task")
        task = task_node if isinstance(task_node, Mapping) else {}
        runtime_node = task.get("runtime")
        runtime = runtime_node if isinstance(runtime_node, Mapping) else {}
        git_node = task.get("git")
        git = git_node if isinstance(git_node, Mapping) else {}
        publish_node = task.get("publish")
        publish = publish_node if isinstance(publish_node, Mapping) else {}
        skill_node = task.get("skill")
        skill = skill_node if isinstance(skill_node, Mapping) else {}
        raw_args = skill.get("args")
        args = dict(raw_args) if isinstance(raw_args, Mapping) else {}

        repository = str(canonical_payload.get("repository") or "").strip()
        instructions = str(task.get("instructions") or "").strip()
        starting_branch = str(git.get("startingBranch") or "").strip()
        selected_ref = (
            str(ref_override).strip() if ref_override is not None else starting_branch
        )
        workdir_mode = workdir_mode_override or self._safe_workdir_mode(source_payload)

        if repository:
            args.setdefault("repo", repository)
        if instructions:
            args.setdefault("instruction", instructions)
        if include_ref and selected_ref:
            args.setdefault("ref", selected_ref)
        args.setdefault("workdirMode", workdir_mode)
        args.setdefault(
            "publishMode", publish_mode_override or publish.get("mode") or "branch"
        )
        publish_base = (
            publish_base_override
            if publish_base_override is not None
            else publish.get("prBaseBranch")
        )
        if publish_base:
            args.setdefault("publishBaseBranch", publish_base)

        payload: dict[str, Any] = {
            "skillId": selected_skill,
            "inputs": args,
            "repository": repository,
            "instruction": instructions,
            "workdirMode": workdir_mode,
            "publishMode": publish_mode_override or publish.get("mode") or "branch",
            "publishBaseBranch": publish_base,
        }
        if include_ref and selected_ref:
            payload["ref"] = selected_ref

        codex_overrides: dict[str, str] = {}
        runtime_model = str(runtime.get("model") or "").strip()
        runtime_effort = str(runtime.get("effort") or "").strip()
        if runtime_model:
            codex_overrides["model"] = runtime_model
        if runtime_effort:
            codex_overrides["effort"] = runtime_effort
        if codex_overrides:
            payload["codex"] = codex_overrides
        return payload

    async def _run_execute_stage(
        self,
        *,
        job_id: UUID,
        canonical_payload: Mapping[str, Any],
        source_payload: Mapping[str, Any],
        runtime_mode: str,
        selected_skill: str,
        prepared: PreparedTaskWorkspace,
        uses_skills: bool,
        use_fallback: bool,
    ) -> WorkerExecutionResult:
        """Execute task instructions via the selected runtime adapter."""

        container_spec = self._extract_container_task_spec(canonical_payload)
        if container_spec is not None:
            return await self._run_container_execute_stage(
                job_id=job_id,
                canonical_payload=canonical_payload,
                prepared=prepared,
                container_spec=container_spec,
            )

        runtime_model, runtime_effort = self._resolve_runtime_overrides(
            canonical_payload=canonical_payload,
            runtime_mode=runtime_mode,
        )
        if runtime_mode == "codex":
            if uses_skills:
                return await self._codex_exec_handler.handle_skill(
                    job_id=job_id,
                    payload=self._build_skill_payload(
                        canonical_payload=canonical_payload,
                        selected_skill=selected_skill,
                        source_payload=source_payload,
                        ref_override=None,
                        publish_mode_override="none",
                        publish_base_override=None,
                        workdir_mode_override="reuse",
                        include_ref=False,
                    ),
                    selected_skill=selected_skill,
                    fallback=use_fallback,
                )

            exec_payload = self._build_exec_payload(
                canonical_payload=canonical_payload,
                source_payload=source_payload,
                ref_override=None,
                publish_mode_override="none",
                publish_base_override=None,
                workdir_mode_override="reuse",
                include_ref=False,
            )
            codex_overrides: dict[str, str] = {}
            if runtime_model:
                codex_overrides["model"] = runtime_model
            if runtime_effort:
                codex_overrides["effort"] = runtime_effort
            if codex_overrides:
                exec_payload["codex"] = codex_overrides
            return await self._codex_exec_handler.handle(
                job_id=job_id,
                payload=exec_payload,
            )

        instruction = self._compose_instruction_for_runtime(
            canonical_payload=canonical_payload,
            selected_skill=selected_skill,
            runtime_mode=runtime_mode,
        )
        command = self._build_non_codex_runtime_command(
            runtime_mode=runtime_mode,
            instruction=instruction,
            model=runtime_model,
            effort=runtime_effort,
        )
        try:
            await self._run_stage_command(
                command,
                cwd=prepared.repo_dir,
                log_path=prepared.execute_log_path,
            )
            patch_path = prepared.artifacts_dir / "changes.patch"
            patch_result = await self._run_stage_command(
                ["git", "diff"],
                cwd=prepared.repo_dir,
                log_path=prepared.execute_log_path,
                check=False,
            )
            patch_path.write_text(patch_result.stdout or "", encoding="utf-8")
            return WorkerExecutionResult(
                succeeded=True,
                summary=f"{runtime_mode} task execution completed",
                error_message=None,
                artifacts=(
                    ArtifactUpload(
                        path=prepared.execute_log_path,
                        name="logs/execute.log",
                        content_type="text/plain",
                    ),
                    ArtifactUpload(
                        path=patch_path,
                        name="patches/changes.patch",
                        content_type="text/x-diff",
                    ),
                ),
            )
        except Exception as exc:
            return WorkerExecutionResult(
                succeeded=False,
                summary=None,
                error_message=str(exc),
                artifacts=(
                    ArtifactUpload(
                        path=prepared.execute_log_path,
                        name="logs/execute.log",
                        content_type="text/plain",
                    ),
                ),
            )

    def _extract_container_task_spec(
        self, canonical_payload: Mapping[str, Any]
    ) -> ContainerTaskSpec | None:
        task_node = canonical_payload.get("task")
        task = task_node if isinstance(task_node, Mapping) else {}
        container_node = task.get("container")
        container = container_node if isinstance(container_node, Mapping) else {}
        if not bool(container.get("enabled")):
            return None

        image = str(container.get("image") or "").strip()
        if not image:
            raise ValueError(
                "task.container.image is required when task.container.enabled=true"
            )

        raw_command = container.get("command")
        if not isinstance(raw_command, list):
            raise ValueError("task.container.command must be a list when enabled")
        command = tuple(str(item).strip() for item in raw_command if str(item).strip())
        if not command:
            raise ValueError(
                "task.container.command is required when task.container.enabled=true"
            )

        workdir = str(container.get("workdir") or "").strip() or None
        artifacts_subdir = self._sanitize_artifacts_subdir(
            str(container.get("artifactsSubdir") or "").strip() or None
        )

        timeout_raw = container.get("timeoutSeconds")
        if timeout_raw is None or timeout_raw == "":
            timeout_seconds = self._config.container_default_timeout_seconds
        else:
            timeout_seconds = int(timeout_raw)
        if timeout_seconds < 1:
            raise ValueError("task.container.timeoutSeconds must be >= 1")

        pull_mode = str(container.get("pull") or "if-missing").strip().lower()
        if pull_mode not in {"if-missing", "always"}:
            raise ValueError("task.container.pull must be if-missing or always")

        resources_node = container.get("resources")
        resources = resources_node if isinstance(resources_node, Mapping) else {}
        cpus = str(resources.get("cpus") or "").strip() or None
        memory = str(resources.get("memory") or "").strip() or None

        env_node = container.get("env")
        env = env_node if isinstance(env_node, Mapping) else {}
        normalized_env: dict[str, str] = {}
        for raw_key, raw_value in env.items():
            key = str(raw_key).strip()
            if not key:
                continue
            normalized_env[key] = str(raw_value)

        cache_volumes: list[ContainerCacheVolume] = []
        cache_node = container.get("cacheVolumes")
        if isinstance(cache_node, list):
            for item in cache_node:
                if not isinstance(item, Mapping):
                    continue
                name = str(item.get("name") or "").strip()
                target = str(item.get("target") or "").strip()
                if not name or not target:
                    continue
                cache_volumes.append(ContainerCacheVolume(name=name, target=target))

        return ContainerTaskSpec(
            image=image,
            command=command,
            workdir=workdir,
            env=normalized_env,
            artifacts_subdir=artifacts_subdir,
            timeout_seconds=timeout_seconds,
            pull_mode=pull_mode,
            cpus=cpus,
            memory=memory,
            cache_volumes=tuple(cache_volumes),
        )

    @staticmethod
    def _sanitize_artifacts_subdir(value: str | None) -> str:
        candidate = str(value or "").strip().strip("/")
        if not candidate:
            return "container"
        parts = [
            segment for segment in candidate.split("/") if segment and segment != "."
        ]
        if any(segment == ".." for segment in parts):
            raise ValueError("task.container.artifactsSubdir may not contain '..'")
        normalized = "/".join(parts)
        return normalized or "container"

    @staticmethod
    def _container_command_summary(command: Sequence[str]) -> str:
        rendered = " ".join(str(item) for item in command)
        if len(rendered) <= 320:
            return rendered
        return f"{rendered[:317]}..."

    async def _ensure_container_image(
        self,
        *,
        image: str,
        pull_mode: str,
        cwd: Path,
        log_path: Path,
    ) -> None:
        if pull_mode == "always":
            await self._run_stage_command(
                [self._config.docker_binary, "pull", image],
                cwd=cwd,
                log_path=log_path,
            )
            return

        inspect_result = await self._run_stage_command(
            [self._config.docker_binary, "image", "inspect", image],
            cwd=cwd,
            log_path=log_path,
            check=False,
        )
        if inspect_result.returncode == 0:
            return
        await self._run_stage_command(
            [self._config.docker_binary, "pull", image],
            cwd=cwd,
            log_path=log_path,
        )

    def _build_container_run_command(
        self,
        *,
        job_id: UUID,
        repository: str,
        prepared: PreparedTaskWorkspace,
        container_spec: ContainerTaskSpec,
    ) -> tuple[list[str], str, str]:
        workspace_root = self._config.workdir
        if not workspace_root.is_absolute():
            raise ValueError(
                "MOONMIND_WORKDIR must be an absolute path for task.container execution"
            )

        mount_target = str(workspace_root)
        artifact_dir_in_container = str(
            prepared.artifacts_dir / container_spec.artifacts_subdir
        )
        workdir = container_spec.workdir or str(prepared.repo_dir)
        container_name = f"mm-task-{job_id}"

        command: list[str] = [
            self._config.docker_binary,
            "run",
            "--rm",
            "--name",
            container_name,
            "--label",
            f"moonmind.job_id={job_id}",
            "--label",
            f"moonmind.repository={repository}",
            "--label",
            "moonmind.runtime=container",
        ]

        if self._config.container_workspace_volume:
            command.extend(
                [
                    "--mount",
                    (
                        "type=volume,"
                        f"src={self._config.container_workspace_volume},"
                        f"dst={mount_target}"
                    ),
                ]
            )
        else:
            command.extend(
                [
                    "--mount",
                    (
                        "type=bind,"
                        f"src={workspace_root.resolve()},"
                        f"dst={mount_target}"
                    ),
                ]
            )

        for cache_volume in container_spec.cache_volumes:
            command.extend(
                [
                    "--mount",
                    (
                        "type=volume,"
                        f"src={cache_volume.name},"
                        f"dst={cache_volume.target}"
                    ),
                ]
            )

        if container_spec.cpus:
            command.extend(["--cpus", container_spec.cpus])
        if container_spec.memory:
            command.extend(["--memory", container_spec.memory])

        command.extend(["--workdir", workdir])

        run_env = {
            "ARTIFACT_DIR": artifact_dir_in_container,
            "JOB_ID": str(job_id),
            "REPOSITORY": repository,
            **container_spec.env,
        }
        for key in sorted(run_env):
            command.extend(["-e", f"{key}={run_env[key]}"])

        command.append(container_spec.image)
        command.extend(container_spec.command)
        return command, container_name, artifact_dir_in_container

    async def _run_container_execute_stage(
        self,
        *,
        job_id: UUID,
        canonical_payload: Mapping[str, Any],
        prepared: PreparedTaskWorkspace,
        container_spec: ContainerTaskSpec,
    ) -> WorkerExecutionResult:
        repository = str(canonical_payload.get("repository") or "").strip()
        if not repository:
            raise ValueError("repository is required for task.container execution")

        run_command: list[str] = []
        container_name = f"mm-task-{job_id}"
        artifact_dir_in_container = str(
            prepared.artifacts_dir / container_spec.artifacts_subdir
        )
        run_result: CommandResult | None = None
        timed_out = False
        error_message: str | None = None
        started_at = datetime.now(UTC)

        await self._emit_event(
            job_id=job_id,
            level="info",
            message="moonmind.task.container.started",
            payload={
                "image": container_spec.image,
                "command": self._container_command_summary(container_spec.command),
                "pullMode": container_spec.pull_mode,
                "timeoutSeconds": container_spec.timeout_seconds,
            },
        )

        try:
            await self._ensure_container_image(
                image=container_spec.image,
                pull_mode=container_spec.pull_mode,
                cwd=prepared.repo_dir,
                log_path=prepared.execute_log_path,
            )
            run_command, container_name, artifact_dir_in_container = (
                self._build_container_run_command(
                    job_id=job_id,
                    repository=repository,
                    prepared=prepared,
                    container_spec=container_spec,
                )
            )
            run_result = await asyncio.wait_for(
                self._run_stage_command(
                    run_command,
                    cwd=prepared.repo_dir,
                    log_path=prepared.execute_log_path,
                    check=False,
                ),
                timeout=float(container_spec.timeout_seconds),
            )
            if run_result.returncode != 0:
                detail = (run_result.stderr or run_result.stdout).strip()
                tail = detail.splitlines()[-1] if detail else ""
                error_message = (
                    f"container command failed ({run_result.returncode}): {tail}"
                    if tail
                    else f"container command failed ({run_result.returncode})"
                )
        except asyncio.TimeoutError:
            timed_out = True
            error_message = (
                f"container execution timed out after {container_spec.timeout_seconds}s"
            )
            with suppress(Exception):
                await self._run_stage_command(
                    [self._config.docker_binary, "stop", container_name],
                    cwd=prepared.repo_dir,
                    log_path=prepared.execute_log_path,
                    check=False,
                )
            run_result = CommandResult(
                command=tuple(run_command),
                returncode=124,
                stdout="",
                stderr=error_message,
            )
        except Exception as exc:
            error_message = str(exc)
            run_result = CommandResult(
                command=tuple(run_command),
                returncode=1,
                stdout="",
                stderr=error_message,
            )

        finished_at = datetime.now(UTC)
        duration_seconds = max(0.0, (finished_at - started_at).total_seconds())
        exit_code = run_result.returncode if run_result is not None else None
        succeeded = bool(run_result is not None and run_result.returncode == 0) and (
            not timed_out
        )

        metadata_dir = (
            prepared.artifacts_dir / container_spec.artifacts_subdir / "metadata"
        )
        metadata_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = metadata_dir / "run.json"
        metadata_payload = {
            "jobId": str(job_id),
            "repository": repository,
            "containerName": container_name,
            "image": container_spec.image,
            "command": list(container_spec.command),
            "commandSummary": self._container_command_summary(container_spec.command),
            "pullMode": container_spec.pull_mode,
            "workdir": container_spec.workdir or str(prepared.repo_dir),
            "artifactDir": artifact_dir_in_container,
            "timeoutSeconds": container_spec.timeout_seconds,
            "timedOut": timed_out,
            "exitCode": exit_code,
            "startedAt": started_at.isoformat(),
            "finishedAt": finished_at.isoformat(),
            "durationSeconds": duration_seconds,
            "error": error_message,
        }
        metadata_path.write_text(
            json.dumps(metadata_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        await self._emit_event(
            job_id=job_id,
            level="info" if succeeded else "error",
            message="moonmind.task.container.finished",
            payload={
                "containerName": container_name,
                "image": container_spec.image,
                "command": self._container_command_summary(container_spec.command),
                "timedOut": timed_out,
                "exitCode": exit_code,
                "durationSeconds": duration_seconds,
                "artifactsSubdir": container_spec.artifacts_subdir,
            },
        )

        return WorkerExecutionResult(
            succeeded=succeeded,
            summary="container task execution completed" if succeeded else None,
            error_message=error_message,
            artifacts=(
                ArtifactUpload(
                    path=metadata_path,
                    name=f"{container_spec.artifacts_subdir}/metadata/run.json",
                    content_type="application/json",
                ),
            ),
        )

    def _compose_instruction_for_runtime(
        self,
        *,
        canonical_payload: Mapping[str, Any],
        selected_skill: str,
        runtime_mode: str,
    ) -> str:
        task_node = canonical_payload.get("task")
        task = task_node if isinstance(task_node, Mapping) else {}
        instruction = str(task.get("instructions") or "").strip()
        if selected_skill == "auto":
            return instruction

        skill_node = task.get("skill")
        skill = skill_node if isinstance(skill_node, Mapping) else {}
        raw_args = skill.get("args")
        args = dict(raw_args) if isinstance(raw_args, Mapping) else {}
        args_text = json.dumps(args, indent=2, sort_keys=True)
        return (
            f"Selected skill: {selected_skill}\n"
            f"Runtime adapter: {runtime_mode}\n"
            f"Skill arguments:\n{args_text}\n\n"
            "Execute the user task with the selected skill semantics:\n"
            f"{instruction}"
        )

    def _build_non_codex_runtime_command(
        self,
        *,
        runtime_mode: str,
        instruction: str,
        model: str | None,
        effort: str | None,
    ) -> list[str]:
        if runtime_mode == "gemini":
            command = [self._config.gemini_binary, "--prompt", instruction]
        elif runtime_mode == "claude":
            command = [self._config.claude_binary, "--print", instruction]
        else:
            raise ValueError(f"runtime adapter not implemented: {runtime_mode}")

        if model:
            command.extend(["--model", model])
        if effort:
            command.extend(["--effort", effort])
        return command

    def _resolve_runtime_overrides(
        self,
        *,
        canonical_payload: Mapping[str, Any],
        runtime_mode: str,
    ) -> tuple[str | None, str | None]:
        task_node = canonical_payload.get("task")
        task = task_node if isinstance(task_node, Mapping) else {}
        runtime_node = task.get("runtime")
        runtime = runtime_node if isinstance(runtime_node, Mapping) else {}
        model_override = str(runtime.get("model") or "").strip() or None
        effort_override = str(runtime.get("effort") or "").strip() or None

        if runtime_mode == "codex":
            return (
                model_override or self._config.default_codex_model,
                effort_override or self._config.default_codex_effort,
            )
        if runtime_mode == "gemini":
            return (
                model_override or self._config.default_gemini_model,
                effort_override or self._config.default_gemini_effort,
            )
        if runtime_mode == "claude":
            return (
                model_override or self._config.default_claude_model,
                effort_override or self._config.default_claude_effort,
            )
        return (model_override, effort_override)

    def _runtime_can_execute(self, runtime_mode: str) -> bool:
        worker_runtime = self._config.worker_runtime
        if worker_runtime == "universal":
            return runtime_mode in SUPPORTED_EXECUTION_RUNTIMES
        return runtime_mode == worker_runtime

    def _validate_required_job_policy(
        self,
        canonical_payload: Mapping[str, Any],
    ) -> str | None:
        """Fail-closed policy guard for repository + capability requirements."""

        repository = str(canonical_payload.get("repository") or "").strip()
        if not repository:
            return "repository is required for task execution"

        required_raw = canonical_payload.get("requiredCapabilities")
        if not isinstance(required_raw, list):
            return "requiredCapabilities must be a non-empty list"
        required = {
            str(item).strip().lower() for item in required_raw if str(item).strip()
        }
        if not required:
            return "requiredCapabilities must include at least one capability"

        available = {
            str(item).strip().lower()
            for item in self._config.worker_capabilities
            if str(item).strip()
        }
        missing = sorted(required - available)
        if missing:
            return f"worker is missing required capabilities: {', '.join(missing)}"
        return None

    async def _resolve_task_auth_context(
        self,
        *,
        canonical_payload: Mapping[str, Any],
        publish_mode: str,
    ) -> TaskAuthContext:
        """Resolve token-free auth refs with Vault-first and env-token fallback."""

        auth_node = canonical_payload.get("auth")
        auth = auth_node if isinstance(auth_node, Mapping) else {}
        repo_auth_ref = str(auth.get("repoAuthRef") or "").strip() or None
        publish_auth_ref = str(auth.get("publishAuthRef") or "").strip() or None

        repo_token, repo_source = await self._resolve_github_token(
            auth_ref=repo_auth_ref,
            purpose="repository",
        )
        repo_env = self._build_command_env(repo_token) if repo_token else None

        publish_source: str | None = None
        publish_env: dict[str, str] | None = None
        if publish_mode != "none":
            publish_ref = publish_auth_ref or repo_auth_ref
            publish_token, publish_source = await self._resolve_github_token(
                auth_ref=publish_ref,
                purpose="publish",
            )
            if publish_token:
                publish_env = self._build_command_env(publish_token)
            else:
                publish_env = repo_env
                publish_source = repo_source

        return TaskAuthContext(
            repo_auth_ref=repo_auth_ref,
            publish_auth_ref=publish_auth_ref,
            repo_auth_source=repo_source,
            publish_auth_source=publish_source,
            repo_command_env=repo_env,
            publish_command_env=publish_env,
        )

    async def _resolve_github_token(
        self,
        *,
        auth_ref: str | None,
        purpose: str,
    ) -> tuple[str | None, str]:
        """Resolve a GitHub token from Vault reference or env fallback."""

        if auth_ref:
            if self._vault_secret_resolver is None:
                raise RuntimeError(
                    f"{purpose} auth ref was provided but Vault resolver is not configured"
                )
            try:
                resolved = await self._vault_secret_resolver.resolve_github_auth(
                    auth_ref
                )
            except SecretReferenceError as exc:
                raise RuntimeError(f"{purpose} auth resolution failed: {exc}") from exc
            self._register_redaction_value(resolved.token)
            return (resolved.token, f"vault:{resolved.source_ref}")

        env_token = str(environ.get("GITHUB_TOKEN", "")).strip() or None
        if env_token:
            self._register_redaction_value(env_token)
            return (env_token, "env:GITHUB_TOKEN")
        return (None, "none")

    @staticmethod
    def _build_command_env(token: str) -> dict[str, str]:
        command_env = dict(environ)
        command_env["GITHUB_TOKEN"] = token
        command_env["GH_TOKEN"] = token
        command_env["GIT_TERMINAL_PROMPT"] = "0"
        return command_env

    def _register_redaction_value(self, value: str | None) -> None:
        candidate = str(value or "").strip()
        if candidate:
            self._dynamic_redaction_values.add(candidate)

    def _redact_text(self, text: str) -> str:
        redacted = self._secret_redactor.scrub(text)
        for value in sorted(self._dynamic_redaction_values, key=len, reverse=True):
            redacted = redacted.replace(value, "[REDACTED]")
        return redacted

    def _redact_payload(self, payload: Any) -> Any:
        if payload is None:
            return None
        if isinstance(payload, str):
            return self._redact_text(payload)
        if isinstance(payload, Mapping):
            return {
                str(key): self._redact_payload(value) for key, value in payload.items()
            }
        if isinstance(payload, list):
            return [self._redact_payload(item) for item in payload]
        if isinstance(payload, tuple):
            return tuple(self._redact_payload(item) for item in payload)
        return payload

    async def _heartbeat_loop(self, *, job_id: UUID, stop_event: asyncio.Event) -> None:
        """Send lease renewals while a job is actively executing."""

        interval_seconds = max(1.0, self._config.lease_seconds / 3.0)
        while not stop_event.is_set():
            await asyncio.sleep(interval_seconds)
            if stop_event.is_set():
                return
            try:
                await self._queue_client.heartbeat(
                    job_id=job_id,
                    worker_id=self._config.worker_id,
                    lease_seconds=self._config.lease_seconds,
                )
            except Exception:
                # Heartbeat errors are tolerated so terminal transition can still run.
                continue

    async def _upload_artifacts(
        self,
        *,
        job_id: UUID,
        artifacts: Sequence[ArtifactUpload],
    ) -> None:
        for artifact in artifacts:
            if not artifact.path.exists():
                continue
            if artifact.path.stat().st_size == 0:
                continue
            await self._queue_client.upload_artifact(
                job_id=job_id,
                worker_id=self._config.worker_id,
                artifact=artifact,
            )

    async def _emit_event(
        self,
        *,
        job_id: UUID,
        level: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Best-effort event emission for streaming-ish worker logs."""

        try:
            redacted_message = self._redact_text(message)
            redacted_payload = (
                self._redact_payload(payload) if payload is not None else None
            )
            await self._queue_client.append_event(
                job_id=job_id,
                worker_id=self._config.worker_id,
                level=level,
                message=redacted_message,
                payload=(
                    redacted_payload if isinstance(redacted_payload, dict) else None
                ),
            )
        except Exception:
            # Event publication failures should not break terminal job transitions.
            return


__all__ = [
    "ClaimedJob",
    "CodexWorker",
    "CodexWorkerConfig",
    "QueueApiClient",
    "QueueClientError",
]
