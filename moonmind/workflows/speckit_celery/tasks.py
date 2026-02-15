"""Celery tasks orchestrating Spec Kit workflows."""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Coroutine, Mapping, Optional, Sequence, TypeVar
from uuid import UUID

import docker
from celery import Task
from celery.utils.log import get_task_logger
from docker.errors import APIError, DockerException
from requests.exceptions import ReadTimeout

from api_service.db.base import get_async_session_context
from moonmind.config.settings import settings
from moonmind.workflows.adapters import (
    CodexClient,
    CodexDiffResult,
    CodexSubmissionResult,
    GitHubClient,
    GitHubPublishResult,
)
from moonmind.workflows.skills.registry import resolve_stage_execution
from moonmind.workflows.skills.runner import execute_stage
from moonmind.workflows.speckit_celery import celery_app, models
from moonmind.workflows.speckit_celery.celeryconfig import (
    CODEX_AFFINITY_HEADER,
    CODEX_QUEUE_HEADER,
    get_codex_shard_router,
)
from moonmind.workflows.speckit_celery.repositories import (
    SpecAutomationRepository,
    SpecWorkflowRepository,
)
from moonmind.workflows.speckit_celery.utils import (
    CliVerificationError,
    verify_cli_is_executable,
)

logger = get_task_logger(__name__)


TASK_DISCOVER = "discover_next_phase"
TASK_SUBMIT = "submit_codex_job"
TASK_PUBLISH = "apply_and_publish"
TASK_SEQUENCE: tuple[str, ...] = (TASK_DISCOVER, TASK_SUBMIT, TASK_PUBLISH)

_TASK_PATTERN = re.compile(r"^- \[(?P<mark>[ xX])\] (?P<body>.+)$")
_TASK_BODY_PATTERN = re.compile(r"^(?P<identifier>\S+)(?P<title>\s+.*)?$")

T = TypeVar("T")


_SPEC_KIT_CLI_LOCK = threading.Lock()
_SPEC_KIT_CLI_LOGGED = threading.Event()


def _log_spec_kit_cli_availability() -> None:
    """Log the resolved Spec Kit CLI path and version once per worker."""

    if _SPEC_KIT_CLI_LOGGED.is_set():
        return

    if getattr(settings.spec_workflow, "test_mode", False):
        with _SPEC_KIT_CLI_LOCK:
            if _SPEC_KIT_CLI_LOGGED.is_set():
                return

            logger.debug(
                "Skipping Spec Kit CLI verification in test mode",
                extra={"speckit_path": None},
            )
            _SPEC_KIT_CLI_LOGGED.set()
        return

    if bool(getattr(celery_app.conf, "task_always_eager", False)):
        with _SPEC_KIT_CLI_LOCK:
            if _SPEC_KIT_CLI_LOGGED.is_set():
                return

            logger.info(
                "Skipping Spec Kit CLI verification in eager mode",
                extra={"speckit_path": None},
            )
            _SPEC_KIT_CLI_LOGGED.set()
        return

    with _SPEC_KIT_CLI_LOCK:
        if _SPEC_KIT_CLI_LOGGED.is_set():
            return

        try:
            speckit_path = verify_cli_is_executable("speckit")
        except CliVerificationError as exc:
            logger.critical(
                "Spec Kit CLI is unavailable: %s",
                exc,
                extra={"speckit_path": exc.cli_path},
            )
            raise RuntimeError(str(exc)) from exc

        try:
            result = subprocess.run(
                [speckit_path, "--version"],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            extra = {"speckit_path": speckit_path}
            if isinstance(exc, subprocess.CalledProcessError):
                extra["stdout"] = exc.stdout
                extra["stderr"] = exc.stderr
            logger.critical(
                "Failed to execute 'speckit --version': %s",
                exc,
                extra=extra,
            )
            raise RuntimeError("Spec Kit CLI health check failed") from exc

        raw_output = result.stdout.strip() or result.stderr.strip()
        version_match = re.search(r"\d+\.\d+\.\d+", raw_output)
        version = version_match.group(0) if version_match else (raw_output or "unknown")

        logger.info(
            "Spec Kit CLI detected at %s (version: %s)",
            speckit_path,
            version,
            extra={"speckit_path": speckit_path, "speckit_version": version},
        )

        _SPEC_KIT_CLI_LOGGED.set()


class CredentialValidationError(RuntimeError):
    """Raised when workflow credentials fail validation."""

    def __init__(self, audit: models.CredentialAuditResult, message: str) -> None:
        super().__init__(message)
        self.audit = audit


@dataclass(slots=True)
class CodexPreflightResult:
    """Outcome of the Codex login status verification."""

    status: models.CodexPreflightStatus
    message: Optional[str] = None
    volume: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None


def _run_coro(coro: Coroutine[Any, Any, T]) -> T:
    """Execute an async coroutine from sync Celery tasks safely."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except Exception as exc:  # pragma: no cover - propagate errors
            result["error"] = exc

    thread = threading.Thread(target=_runner, name="spec-workflow-task")
    thread.start()
    thread.join()

    if "error" in result:
        raise result["error"]

    return result["value"]


_SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|secret|token|key|credential|auth|cookie|session)", re.IGNORECASE
)
_REDACTED = "***REDACTED***"


def _sanitize_for_log(value: Any, *, _field: str | None = None) -> Any:
    """Return a log-friendly representation of the provided value."""

    if _field and _SENSITIVE_KEY_PATTERN.search(_field):
        return _REDACTED
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (Path, UUID)):
        return str(value)
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            sanitized[key_str] = _sanitize_for_log(item, _field=key_str)
        return sanitized
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_for_log(item) for item in value]
    return str(value)


def _derive_codex_affinity_key(context: Mapping[str, Any]) -> str:
    existing = context.get("codex_affinity_key")
    if existing:
        return str(existing)

    repository = context.get("repository") or settings.spec_workflow.github_repository
    feature_key = context.get("feature_key")
    task_info = context.get("task")
    task_identifier: str | None = None
    if isinstance(task_info, Mapping):
        raw_identifier = task_info.get("taskId")
        if raw_identifier:
            task_identifier = str(raw_identifier)

    parts = [
        str(value).strip()
        for value in (repository, feature_key, task_identifier)
        if value
    ]
    if not parts:
        run_id = context.get("run_id")
        if not run_id:
            raise ValueError("Workflow context is missing a run identifier for routing")
        return str(run_id)
    return "|".join(parts)


_PERSISTABLE_QUEUE_KEY = "_persistable_codex_queue"


async def _maybe_include_codex_queue(
    repo: SpecWorkflowRepository, context: Mapping[str, Any], updates: dict[str, Any]
) -> None:
    """Attach the Codex queue to ``updates`` when a shard record exists."""

    cache = context.get(_PERSISTABLE_QUEUE_KEY)
    if cache is not None:
        if cache:
            updates.setdefault("codex_queue", cache)
        return

    queue_value = context.get("codex_queue")
    queue_name = str(queue_value).strip() if queue_value else ""
    if not queue_name:
        if isinstance(context, dict):
            context[_PERSISTABLE_QUEUE_KEY] = None
        return

    if await repo.codex_shard_exists(queue_name):
        updates.setdefault("codex_queue", queue_name)
        if isinstance(context, dict):
            context[_PERSISTABLE_QUEUE_KEY] = queue_name
        return

    if isinstance(context, dict):
        context[_PERSISTABLE_QUEUE_KEY] = None
    logger.warning(
        "Skipping codex_queue persistence for run %s because shard metadata for queue %s is missing",
        context.get("run_id"),
        queue_name,
    )


class SpecWorkflowTask(Task):
    """Base Celery task providing shared Spec workflow behavior."""

    abstract = True


class CodexShardTask(SpecWorkflowTask):
    """Celery task that ensures deterministic Codex shard routing."""

    abstract = True

    def apply_async(
        self,
        args: tuple[Any, ...] | None = None,
        kwargs: dict[str, Any] | None = None,
        task_id: str | None = None,
        producer: Any = None,
        link: Any = None,
        link_error: Any = None,
        shadow: str | None = None,
        **options: Any,
    ):
        args = args or ()
        kwargs = kwargs or {}
        mutable_args = list(args)
        mutable_kwargs = dict(kwargs)
        routing_options = dict(options)

        context = None
        context_source: tuple[str, int | str] | None = None
        if mutable_args and isinstance(mutable_args[0], dict):
            context = mutable_args[0]
            context_source = ("args", 0)
        elif isinstance(mutable_kwargs.get("context"), dict):
            context = mutable_kwargs["context"]
            context_source = ("kwargs", "context")

        if isinstance(context, dict):
            router = get_codex_shard_router()
            affinity = _derive_codex_affinity_key(context)
            headers = dict(routing_options.get("headers") or {})
            queue_name = (
                str(context.get("codex_queue"))
                if context.get("codex_queue")
                else str(headers.get(CODEX_QUEUE_HEADER) or "")
            )
            if not queue_name:
                queue_name = router.queue_for_key(affinity)
            shard_index = router.shard_for_key(affinity)

            context.setdefault("codex_affinity_key", affinity)
            context.setdefault("codex_queue", queue_name)
            context.setdefault("codex_shard_index", shard_index)

            headers.setdefault(CODEX_AFFINITY_HEADER, affinity)
            headers.setdefault(CODEX_QUEUE_HEADER, queue_name)
            routing_options["headers"] = headers

            routing_options.setdefault("queue", queue_name)
            routing_options.setdefault("routing_key", queue_name)

            if context_source == ("args", 0):
                mutable_args[0] = context
            elif context_source == ("kwargs", "context"):
                mutable_kwargs["context"] = context

        return super().apply_async(
            args=tuple(mutable_args),
            kwargs=mutable_kwargs,
            task_id=task_id,
            producer=producer,
            link=link,
            link_error=link_error,
            shadow=shadow,
            **routing_options,
        )


class _MetricsEmitter:
    """Best-effort StatsD emitter used for workflow task instrumentation."""

    def __init__(self) -> None:
        prefix = os.getenv("SPEC_WORKFLOW_METRICS_PREFIX", "moonmind.spec_workflow")
        self._prefix = prefix.rstrip(".")
        host = os.getenv("SPEC_WORKFLOW_METRICS_HOST", os.getenv("STATSD_HOST"))
        port = os.getenv("SPEC_WORKFLOW_METRICS_PORT", os.getenv("STATSD_PORT", "8125"))
        self._configured = bool(host)
        self._enabled = self._configured
        self._address: tuple[str, int] | None = None
        self._socket: socket.socket | None = None
        self._failure_count = 0
        self._disabled_until: float | None = None
        self._base_backoff = 5.0
        self._max_backoff = 60.0

        if self._configured:
            self._address = (str(host), int(port))
            self._open_socket()
            logger.info(
                "Spec workflow StatsD emitter configured",
                extra={"metrics_host": host, "metrics_prefix": self._prefix},
            )
        else:
            logger.debug(
                "Spec workflow StatsD emitter disabled (no host configured)",
                extra={"metrics_prefix": self._prefix},
            )

    @property
    def enabled(self) -> bool:
        return self._configured and self._enabled

    def _open_socket(self) -> None:
        if not self._address:
            return
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        except OSError as exc:
            self._socket = None
            self._enabled = False
            self._disabled_until = time.monotonic() + self._base_backoff
            logger.warning("Failed to initialize Spec workflow metrics socket: %s", exc)

    def _close_socket(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                # Best-effort cleanup: errors during close are non-fatal and can be ignored.
                pass
            finally:
                self._socket = None

    @staticmethod
    def _format_tags(tags: Optional[Mapping[str, Any]]) -> str:
        if not tags:
            return ""
        parts: list[str] = []
        for key, raw_value in tags.items():
            if raw_value is None:
                continue
            safe_key = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(key))
            safe_value = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(raw_value))
            parts.append(f"{safe_key}:{safe_value}")
        if not parts:
            return ""
        return "|#" + ",".join(parts)

    def _send(self, metric: str) -> None:
        if not self._configured:
            return
        if self._disabled_until:
            if time.monotonic() < self._disabled_until:
                return
            self._disabled_until = None
            self._enabled = True
            self._open_socket()
        if not self._socket or not self._address or not self._enabled:
            return
        try:
            self._socket.sendto(metric.encode("utf-8"), self._address)
            self._failure_count = 0
        except OSError as exc:
            self._close_socket()
            self._failure_count += 1
            backoff = min(
                self._base_backoff * (2 ** (self._failure_count - 1)), self._max_backoff
            )
            self._disabled_until = time.monotonic() + backoff
            logger.warning(
                "Disabling Spec workflow metrics emission for %.1fs after socket error: %s",
                backoff,
                exc,
            )

    def increment(
        self, metric: str, *, value: int = 1, tags: Optional[Mapping[str, Any]] = None
    ) -> None:
        if not self.enabled:
            return
        formatted_tags = self._format_tags(tags)
        payload = f"{self._prefix}.{metric}:{value}|c{formatted_tags}"
        self._send(payload)

    def observe(
        self, metric: str, *, value: float, tags: Optional[Mapping[str, Any]] = None
    ) -> None:
        if not self.enabled:
            return
        formatted_tags = self._format_tags(tags)
        payload = f"{self._prefix}.{metric}:{value * 1000:.6f}|ms{formatted_tags}"
        self._send(payload)


class TaskObserver:
    """Helper to emit structured logs and metrics for Celery task execution."""

    def __init__(
        self,
        *,
        task_name: str,
        run_id: str,
        attempt: int,
        metrics: _MetricsEmitter,
    ) -> None:
        self._task_name = task_name
        self._run_id = run_id
        self._attempt = attempt
        self._metrics = metrics
        self._started_at: float | None = None

    def _base_extra(self, **overrides: Any) -> dict[str, Any]:
        payload = {
            "run_id": self._run_id,
            "task": self._task_name,
            "attempt": self._attempt,
            "metrics_enabled": self._metrics.enabled,
        }
        for key, value in overrides.items():
            if value is not None:
                payload[key] = value
        return payload

    def _metric_tags(self, **extras: Any) -> dict[str, Any]:
        tags = {"task": self._task_name, "attempt": self._attempt}
        tags.update({key: value for key, value in extras.items() if value is not None})
        return tags

    def started(self, **details: Any) -> None:
        self._started_at = time.perf_counter()
        sanitized = _sanitize_for_log(details)
        self._metrics.increment(
            "task_start",
            tags=self._metric_tags(status="running", retry=details.get("retry")),
        )
        log_extra = self._base_extra(event="task_started", details=sanitized)
        logger.info(
            "Spec workflow task %s started for run %s (attempt %s) | details=%s",
            self._task_name,
            self._run_id,
            self._attempt,
            sanitized,
            extra=log_extra,
        )

    def succeeded(self, summary: Optional[Mapping[str, Any]] = None) -> None:
        duration = None
        if self._started_at is not None:
            duration = time.perf_counter() - self._started_at
        tags = self._metric_tags(status="success")
        self._metrics.increment("task_success", tags=tags)
        if duration is not None:
            self._metrics.observe("task_duration", value=duration, tags=tags)
        sanitized_summary = _sanitize_for_log(summary) if summary is not None else {}
        log_extra = self._base_extra(
            event="task_succeeded",
            duration_ms=duration * 1000 if duration is not None else None,
            summary=sanitized_summary,
        )
        if duration is not None:
            logger.info(
                "Spec workflow task %s succeeded for run %s (attempt %s) in %.3fs | summary=%s",
                self._task_name,
                self._run_id,
                self._attempt,
                duration,
                sanitized_summary,
                extra=log_extra,
            )
        else:
            logger.info(
                "Spec workflow task %s succeeded for run %s (attempt %s) | summary=%s",
                self._task_name,
                self._run_id,
                self._attempt,
                sanitized_summary,
                extra=log_extra,
            )

    def failed(
        self, exc: BaseException, *, details: Optional[Mapping[str, Any]] = None
    ) -> None:
        duration = None
        if self._started_at is not None:
            duration = time.perf_counter() - self._started_at
        tags = self._metric_tags(status="failure")
        self._metrics.increment("task_failure", tags=tags)
        if duration is not None:
            self._metrics.observe("task_duration", value=duration, tags=tags)
        error_details = {
            "type": exc.__class__.__name__,
            "message": str(exc),
        }
        sanitized_error = _sanitize_for_log(error_details)
        sanitized_details = _sanitize_for_log(details) if details is not None else None
        log_extra = self._base_extra(
            event="task_failed",
            duration_ms=duration * 1000 if duration is not None else None,
            error=sanitized_error,
            details=sanitized_details,
        )
        if duration is not None:
            logger.error(
                "Spec workflow task %s failed for run %s (attempt %s) after %.3fs | error=%s | details=%s",
                self._task_name,
                self._run_id,
                self._attempt,
                duration,
                sanitized_error,
                sanitized_details,
                extra=log_extra,
            )
        else:
            logger.error(
                "Spec workflow task %s failed for run %s (attempt %s) | error=%s | details=%s",
                self._task_name,
                self._run_id,
                self._attempt,
                sanitized_error,
                sanitized_details,
                extra=log_extra,
            )


_METRICS = _MetricsEmitter()


@dataclass(slots=True)
class DiscoveredTask:
    """Represents the next unchecked Spec task discovered from tasks.md."""

    identifier: str
    title: str
    phase: Optional[str]
    line_number: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "taskId": self.identifier,
            "title": self.title,
            "phase": self.phase,
            "lineNumber": self.line_number,
        }


@dataclass(slots=True)
class AgentConfigurationSnapshot:
    """Snapshot of the agent configuration applied to a Spec Automation run."""

    backend: str
    version: str
    prompt_pack_version: Optional[str]
    runtime_env: dict[str, str]


def _normalize_runtime_env_keys(value: Any) -> tuple[str, ...]:
    """Coerce ``value`` into a tuple of distinct, non-empty key names."""

    if value is None:
        return ()
    if isinstance(value, Mapping):
        raise ValueError("agent_runtime_env_keys override must not be a mapping")
    if isinstance(value, str):
        candidates: Sequence[object] = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        candidates = value
    else:
        candidates = (value,)

    normalized: list[str] = []
    for item in candidates:
        text = str(item).strip()
        if not text:
            continue
        normalized.append(text)

    return tuple(dict.fromkeys(normalized))


def select_agent_configuration(
    overrides: Optional[Mapping[str, Any]] = None,
) -> AgentConfigurationSnapshot:
    """Determine the agent configuration for an automation run."""

    cfg = settings.spec_workflow
    overrides = overrides or {}

    backend = str(overrides.get("agent_backend") or cfg.agent_backend or "").strip()
    if not backend:
        raise ValueError("Agent backend selection must not be blank")

    allowed = cfg.allowed_agent_backends
    if allowed and backend not in allowed:
        allowed_display = ", ".join(allowed)
        raise ValueError(
            f"Agent backend '{backend}' is not permitted; allowed: {allowed_display}"
        )

    version = str(overrides.get("agent_version") or cfg.agent_version or "").strip()
    if not version:
        raise ValueError("Agent version must be provided for auditability")

    prompt_pack = overrides.get("prompt_pack_version")
    if prompt_pack is None:
        prompt_pack_value = cfg.prompt_pack_version
    else:
        prompt_pack_value = str(prompt_pack).strip() or None

    runtime_keys_override = overrides.get("agent_runtime_env_keys")
    if runtime_keys_override is None:
        runtime_keys = tuple(dict.fromkeys(cfg.agent_runtime_env_keys))
    else:
        runtime_keys = _normalize_runtime_env_keys(runtime_keys_override)

    runtime_env: dict[str, str] = {}
    for key in runtime_keys:
        key_str = str(key).strip()
        if not key_str:
            continue
        value = os.getenv(key_str)
        if value is not None:
            runtime_env[key_str] = value

    override_env = overrides.get("agent_runtime_env")
    if isinstance(override_env, Mapping):
        for key, value in override_env.items():
            key_str = str(key).strip()
            if not key_str:
                continue
            if value is None:
                runtime_env.pop(key_str, None)
            else:
                runtime_env[key_str] = str(value)

    return AgentConfigurationSnapshot(
        backend=backend,
        version=version,
        prompt_pack_version=prompt_pack_value,
        runtime_env=runtime_env,
    )


async def persist_agent_configuration(
    repo: SpecAutomationRepository,
    *,
    run_id: UUID,
    snapshot: AgentConfigurationSnapshot,
) -> models.SpecAutomationAgentConfiguration:
    """Persist the agent configuration snapshot for a run and log the outcome."""

    redacted_env = {key: _REDACTED for key in snapshot.runtime_env}

    record = await repo.upsert_agent_configuration(
        run_id=run_id,
        agent_backend=snapshot.backend,
        agent_version=snapshot.version,
        prompt_pack_version=snapshot.prompt_pack_version,
        runtime_env=redacted_env,
    )

    logger.info(
        "Recorded agent configuration for Spec Automation run",
        extra={
            "run_id": str(run_id),
            "agent": _sanitize_for_log(
                {
                    "backend": snapshot.backend,
                    "version": snapshot.version,
                    "promptPackVersion": snapshot.prompt_pack_version,
                    "runtimeEnv": redacted_env,
                }
            ),
        },
    )
    return record


def _now() -> datetime:
    return datetime.now(UTC)


def _resolve_tasks_file(feature_key: str) -> Path:
    cfg = settings.spec_workflow
    tasks_root = Path(cfg.tasks_root)
    if not tasks_root.is_absolute():
        tasks_root = Path(cfg.repo_root) / tasks_root
    tasks_root = tasks_root.resolve()
    tasks_file = (tasks_root / feature_key / "tasks.md").resolve()
    try:
        tasks_file.relative_to(tasks_root)
    except ValueError as exc:
        raise ValueError("Invalid feature_key leading to path traversal") from exc
    return tasks_file


def _parse_next_task(tasks_file: Path) -> Optional[DiscoveredTask]:
    if not tasks_file.exists():
        raise FileNotFoundError(f"Spec task file not found: {tasks_file}")

    current_phase: Optional[str] = None
    with tasks_file.open("r", encoding="utf-8") as handle:
        for idx, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if line.startswith("## "):
                current_phase = line.lstrip("# ")
                continue

            match = _TASK_PATTERN.match(line)
            if not match:
                continue

            if match.group("mark").lower() == "x":
                continue

            body = match.group("body")
            body_match = _TASK_BODY_PATTERN.match(body)
            if not body_match:
                continue

            identifier = body_match.group("identifier")
            title = (body_match.group("title") or "").strip()
            return DiscoveredTask(
                identifier=identifier,
                title=title,
                phase=current_phase,
                line_number=idx,
            )
    return None


async def _update_task_state(
    repo: SpecWorkflowRepository,
    *,
    workflow_run_id: UUID,
    task_name: str,
    status: models.SpecWorkflowTaskStatus,
    attempt: int = 1,
    payload: Optional[dict[str, Any]] = None,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
    message: Optional[str] = None,
    artifact_paths: Optional[Sequence[str]] = None,
) -> models.SpecWorkflowTaskState:
    state = await repo.upsert_task_state(
        workflow_run_id=workflow_run_id,
        task_name=task_name,
        status=status,
        attempt=attempt,
        payload=payload,
        started_at=started_at,
        finished_at=finished_at,
        message=message,
        artifact_paths=artifact_paths,
    )
    return state


async def _persist_failure(
    repo: SpecWorkflowRepository,
    *,
    run_id: UUID,
    task_name: str,
    message: str,
    attempt: int = 1,
) -> None:
    finished = _now()
    await _update_task_state(
        repo,
        workflow_run_id=run_id,
        task_name=task_name,
        status=models.SpecWorkflowTaskStatus.FAILED,
        payload=_status_payload(
            models.SpecWorkflowTaskStatus.FAILED,
            message=message,
            code=f"{task_name}_failed",
        ),
        finished_at=finished,
        attempt=attempt,
        message=message,
    )
    await repo.update_run(
        run_id,
        status=models.SpecWorkflowRunStatus.FAILED,
        finished_at=finished,
    )


def _build_codex_client() -> CodexClient:
    cfg = settings.spec_workflow
    return CodexClient(
        environment=cfg.codex_environment,
        model=cfg.codex_model,
        profile=cfg.codex_profile,
        test_mode=cfg.test_mode,
    )


def _build_github_client() -> GitHubClient:
    cfg = settings.spec_workflow
    return GitHubClient(
        repository=cfg.github_repository,
        token=cfg.github_token,
        test_mode=cfg.test_mode,
    )


def _summarize_preflight_output(stdout: str, stderr: str) -> Optional[str]:
    """Collapse Codex pre-flight output into a short human-readable summary."""

    combined = (stderr or "").strip() or (stdout or "").strip()
    if not combined:
        return None
    condensed = re.sub(r"\s+", " ", combined)
    if len(condensed) > 512:
        return condensed[:509] + "..."
    return condensed


def _poll_for_codex_diff(
    codex_client: CodexClient,
    *,
    task_id: str,
    artifacts_dir: Path,
    task_identifier: str,
    task_summary: str,
    poll_interval: float = 1.5,
    timeout: float = 30.0,
) -> CodexDiffResult:
    """Poll Codex for a diff until it is available or the timeout elapses."""

    deadline = time.monotonic() + max(timeout, poll_interval)
    last_error: Exception | None = None

    while True:
        try:
            return codex_client.retrieve_patch(
                task_id=task_id,
                artifacts_dir=artifacts_dir,
                task_identifier=task_identifier,
                task_summary=task_summary,
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            last_error = exc
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Timed out while polling Codex for diff availability"
                ) from exc
            time.sleep(poll_interval)

    raise RuntimeError("Unexpected Codex polling exit") from last_error


def _write_apply_output(artifacts_dir: Path, diff: CodexDiffResult) -> Path:
    """Persist apply/poll metadata alongside the patch artifact."""

    apply_payload = {
        "description": diff.description,
        "hasChanges": diff.has_changes,
        "patchPath": str(diff.patch_path),
    }
    output_path = artifacts_dir / "apply_output.json"
    output_path.write_text(json.dumps(apply_payload, indent=2), encoding="utf-8")
    return output_path


def _write_run_summary(artifacts_dir: Path, context: Mapping[str, Any]) -> Path:
    """Persist a compact JSON summary of the workflow outcome."""

    summary_payload = {
        "runId": context.get("run_id"),
        "featureKey": context.get("feature_key"),
        "task": context.get("task"),
        "codexTaskId": context.get("codex_task_id"),
        "codexQueue": context.get("codex_queue"),
        "skillExecution": context.get("skill_execution"),
        "branch": context.get("branch_name"),
        "pullRequestUrl": context.get("pr_url"),
        "codexPatchPath": context.get("codex_patch_path"),
        "codexLogsPath": context.get("codex_logs_path"),
        "noWork": bool(context.get("no_work")),
    }
    summary_path = artifacts_dir / "run_summary.json"
    summary_path.write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")
    return summary_path


def _run_codex_preflight_check(
    *, timeout: int = 60, volume_name: str | None = None
) -> CodexPreflightResult:
    """Execute ``codex login status`` using the configured auth volume."""

    volume = volume_name or settings.spec_workflow.codex_volume_name
    if not volume:
        logger.info(
            "Skipping Codex pre-flight check because no auth volume is configured",
        )
        return CodexPreflightResult(
            status=models.CodexPreflightStatus.SKIPPED,
            message="Codex auth volume not configured for this worker.",
        )

    image = (
        settings.spec_workflow.codex_login_check_image
        or settings.spec_workflow.job_image
    )
    stdout = ""
    stderr = ""
    exit_code: Optional[int] = None
    client: Optional[docker.DockerClient] = None

    try:
        client = docker.from_env()
        container = client.containers.run(
            image,
            command=["bash", "-lc", "codex login status"],
            environment={"HOME": "/home/app"},
            volumes={volume: {"bind": "/home/app/.codex", "mode": "ro"}},
            detach=True,
            auto_remove=True,
            tty=False,
        )
        try:
            wait_result = container.wait(timeout=timeout)
            exit_code = int(wait_result.get("StatusCode", 1))
        except ReadTimeout:
            try:
                container.stop(timeout=5)
            except DockerException as stop_exc:  # pragma: no cover - defensive
                logger.debug(
                    "Codex pre-flight container stop failed after timeout: %s",
                    stop_exc,
                    extra={"codex_volume": volume},
                )
            message = (
                f"Codex login status check timed out after {timeout} seconds for "
                f"volume '{volume}'."
            )
            logger.warning(
                "Codex pre-flight check timed out",
                extra={"codex_volume": volume, "timeout_seconds": timeout},
            )
            return CodexPreflightResult(
                status=models.CodexPreflightStatus.FAILED,
                message=message,
                volume=volume,
            )
        try:
            stdout_bytes = container.logs(stdout=True, stderr=False) or b""
            stderr_bytes = container.logs(stdout=False, stderr=True) or b""
        except (APIError, DockerException):
            stdout_bytes = b""
            stderr_bytes = b""
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
    except DockerException as exc:
        message = f"Unable to execute Codex login status for volume '{volume}': {exc}"
        logger.warning(
            "Codex pre-flight check failed to start",
            extra={"codex_volume": volume, "error": str(exc)},
        )
        return CodexPreflightResult(
            status=models.CodexPreflightStatus.FAILED,
            message=message,
            volume=volume,
        )
    finally:
        if client is not None:
            try:
                client.close()
            except DockerException as exc:  # pragma: no cover - defensive logging
                logger.debug(
                    "Failed to close Docker client after Codex pre-flight check: %s",
                    exc,
                    extra={"codex_volume": volume},
                )

    summary = _summarize_preflight_output(stdout, stderr)
    if exit_code == 0:
        message = summary or "Codex login status check passed."
        logger.info(
            "Codex pre-flight check passed",
            extra={
                "codex_volume": volume,
                "codex_preflight_exit_code": exit_code,
                "codex_preflight_summary": summary,
            },
        )
        return CodexPreflightResult(
            status=models.CodexPreflightStatus.PASSED,
            message=message,
            volume=volume,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )

    remediation = (
        f"Codex login status failed for volume '{volume}'. "
        "Re-authenticate this shard using `codex login --device-auth` and retry."
    )
    if summary:
        remediation = f"{remediation} Details: {summary}"

    logger.warning(
        "Codex pre-flight check failed",
        extra={
            "codex_volume": volume,
            "codex_preflight_exit_code": exit_code,
            "codex_preflight_summary": summary,
        },
    )
    return CodexPreflightResult(
        status=models.CodexPreflightStatus.FAILED,
        message=remediation,
        volume=volume,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
    )


def run_codex_preflight_check(
    *, volume_name: str | None = None, timeout: int = 60
) -> CodexPreflightResult:
    """Public wrapper to execute the Codex login status check."""

    return _run_codex_preflight_check(timeout=timeout, volume_name=volume_name)


def _resolve_artifacts_dir(run: models.SpecWorkflowRun) -> Path:
    if run.artifacts_path:
        return Path(run.artifacts_path)
    cfg = settings.spec_workflow
    base = Path(cfg.artifacts_root)
    return base / str(run.id)


def _base_context(run: models.SpecWorkflowRun) -> dict[str, Any]:
    context: dict[str, Any] = {
        "run_id": str(run.id),
        "feature_key": run.feature_key,
        "artifacts_path": str(_resolve_artifacts_dir(run)),
    }
    repository = run.repository or settings.spec_workflow.github_repository
    if repository:
        context["repository"] = repository
    codex_volume = run.codex_volume or settings.spec_workflow.codex_volume_name
    if codex_volume:
        context["codex_volume"] = codex_volume
    return context


def _status_payload(
    status: models.SpecWorkflowTaskStatus,
    *,
    message: Optional[str] = None,
    code: Optional[str] = None,
    **extras: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": status.value}
    if message:
        payload["message"] = message
    if code:
        payload["code"] = code
    for key, value in extras.items():
        if value is not None:
            payload[key] = value
    return payload


def _set_stage_execution_payload(
    context: dict[str, Any],
    stage_name: str,
    *,
    selected_skill: str,
    execution_path: str,
    used_skills: bool,
    used_fallback: bool,
    shadow_mode_requested: bool,
) -> None:
    """Persist stage execution metadata into the workflow context."""

    execution = context.setdefault("skill_execution", {})
    if isinstance(execution, dict):
        execution[stage_name] = {
            "selectedSkill": selected_skill,
            "executionPath": execution_path,
            "usedSkills": used_skills,
            "usedFallback": used_fallback,
            "shadowModeRequested": shadow_mode_requested,
        }


def _stage_execution_payload(
    context: Mapping[str, Any], stage_name: str
) -> dict[str, Any]:
    """Return stage execution metadata for status payload decoration."""

    execution = context.get("skill_execution")
    if not isinstance(execution, Mapping):
        return {}
    entry = execution.get(stage_name)
    if not isinstance(entry, Mapping):
        return {}
    return {
        "selectedSkill": entry.get("selectedSkill"),
        "executionPath": entry.get("executionPath"),
        "usedSkills": entry.get("usedSkills"),
        "usedFallback": entry.get("usedFallback"),
        "shadowModeRequested": entry.get("shadowModeRequested"),
    }


def _merge_notes(*notes: Optional[str]) -> Optional[str]:
    parts = [note.strip() for note in notes if note and note.strip()]
    if not parts:
        return None
    return "\n\n".join(parts)


def _prepare_retry_context(context: dict[str, Any]) -> int:
    """Normalize attempt metadata on the task context and return the attempt number."""

    attempt = int(context.get("attempt", 1))
    context["attempt"] = attempt
    context["retry"] = bool(context.get("retry")) or attempt > 1
    return attempt


def _resolve_resume_path(artifacts_dir: Path, raw_path: str | Path) -> Path:
    """Return an absolute artifact path scoped within the run directory."""

    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = artifacts_dir / candidate
    return candidate


def _apply_resume_token(context: dict[str, Any], *, artifacts_dir: Path) -> None:
    """Merge resume token metadata into the task context when present."""

    token = context.get("resume_token") if isinstance(context, dict) else None
    if isinstance(token, Mapping):
        field_map = {
            "codex_task_id": "codexTaskId",
            "codex_logs_path": "codexLogsPath",
            "codex_patch_path": "codexPatchPath",
            "branch_name": "branchName",
            "pr_url": "prUrl",
        }
        for target, source in field_map.items():
            if not context.get(target) and token.get(source):
                context[target] = token.get(source)

    for path_key in (
        "codex_logs_path",
        "codex_patch_path",
        "apply_output_path",
        "github_response_path",
    ):
        raw = context.get(path_key)
        if not raw:
            continue
        context[path_key] = str(_resolve_resume_path(artifacts_dir, raw))


async def _ensure_credentials_validated(
    repo: SpecWorkflowRepository,
    *,
    session,
    context: dict[str, Any],
    workflow_run_id: UUID,
    task_name: str,
    attempt: int,
) -> None:
    """Validate workflow credentials once and persist audit results in the context."""

    if context.get("credentials_validated"):
        return

    try:
        audit_result = await _validate_credentials(
            repo,
            workflow_run_id=workflow_run_id,
            notes=context.get("retry_notes"),
        )
    except CredentialValidationError as exc:
        logger.warning(
            "Credential validation failed for run %s: %s",
            context["run_id"],
            exc,
        )
        await _persist_failure(
            repo,
            run_id=workflow_run_id,
            task_name=task_name,
            message=str(exc),
            attempt=attempt,
        )
        await session.commit()
        raise

    context["credentials_validated"] = True
    context["credential_audit_status"] = {
        "codex": audit_result.codex_status.value,
        "github": audit_result.github_status.value,
    }


async def _validate_credentials(
    repo: SpecWorkflowRepository,
    *,
    workflow_run_id: UUID,
    notes: Optional[str] = None,
) -> models.CredentialAuditResult:
    cfg = settings.spec_workflow
    codex_status = models.CodexCredentialStatus.VALID
    github_status = models.GitHubCredentialStatus.VALID
    issues: list[str] = []

    if not cfg.test_mode:
        if not cfg.codex_environment or not cfg.codex_model:
            codex_status = models.CodexCredentialStatus.INVALID
            issues.append("Codex environment or model is not configured")
        if not (cfg.github_token or os.getenv("GITHUB_TOKEN")):
            github_status = models.GitHubCredentialStatus.INVALID
            issues.append("GitHub token is not configured for publishing")

    system_note = None
    if issues:
        system_note = "Credential validation detected issues:\n" + "\n".join(
            f"- {issue}" for issue in issues
        )

    combined_notes = _merge_notes(notes, system_note)

    await repo.upsert_credential_audit(
        workflow_run_id=workflow_run_id,
        codex_status=codex_status,
        github_status=github_status,
        notes=combined_notes,
    )

    result = models.CredentialAuditResult(
        codex_status=codex_status, github_status=github_status, notes=combined_notes
    )

    if not result.is_valid():
        reason = "; ".join(issues)
        raise CredentialValidationError(
            result,
            message=f"Credential validation failed: {reason}",
        )

    return result


@celery_app.task(name=f"{models.SpecWorkflowRun.__tablename__}.{TASK_DISCOVER}")
def discover_next_phase(
    run_id: str,
    *,
    feature_key: Optional[str] = None,
    force_phase: Optional[str] = None,
    attempt: int = 1,
    retry_notes: Optional[str] = None,
) -> dict[str, Any]:
    """Locate the next unchecked task in the Spec Kit tasks document."""

    _log_spec_kit_cli_availability()

    stage_context: dict[str, Any] = {
        "feature_key": feature_key,
        "force_phase": force_phase,
        "attempt": attempt,
        "retry": attempt > 1,
    }
    decision = resolve_stage_execution(
        stage_name=TASK_DISCOVER,
        run_id=run_id,
        context=stage_context,
    )
    _set_stage_execution_payload(
        stage_context,
        TASK_DISCOVER,
        selected_skill=decision.selected_skill,
        execution_path=decision.execution_path,
        used_skills=decision.use_skills,
        used_fallback=False,
        shadow_mode_requested=decision.shadow_mode,
    )

    run_uuid = UUID(run_id)
    observer = TaskObserver(
        task_name=TASK_DISCOVER,
        run_id=run_id,
        attempt=attempt,
        metrics=_METRICS,
    )
    observer.started(
        feature_key=feature_key,
        force_phase=force_phase,
        retry_notes=bool(retry_notes),
        retry=attempt > 1,
        selected_skill=decision.selected_skill,
        execution_path=decision.execution_path,
    )

    async def _execute() -> dict[str, Any]:
        async with get_async_session_context() as session:
            repo = SpecWorkflowRepository(session)
            run = await repo.get_run(run_uuid)
            if run is None:
                raise ValueError(f"Workflow run {run_id} not found")

            await repo.ensure_task_state_placeholders(
                workflow_run_id=run_uuid,
                task_names=TASK_SEQUENCE,
                attempt=attempt,
            )

            started = run.started_at or _now()
            await repo.update_run(
                run_uuid,
                status=models.SpecWorkflowRunStatus.RUNNING,
                phase=models.SpecWorkflowRunPhase.DISCOVER,
                started_at=started,
            )
            await _update_task_state(
                repo,
                workflow_run_id=run_uuid,
                task_name=TASK_DISCOVER,
                status=models.SpecWorkflowTaskStatus.RUNNING,
                payload=_status_payload(
                    models.SpecWorkflowTaskStatus.RUNNING,
                    message="Discovering next Spec Kit task",
                    **_stage_execution_payload(stage_context, TASK_DISCOVER),
                ),
                started_at=_now(),
                attempt=attempt,
                message="Discovering next Spec Kit task",
            )
            await session.commit()

            try:
                effective_feature = feature_key or run.feature_key
                tasks_file = _resolve_tasks_file(effective_feature)
                discovered = _parse_next_task(tasks_file)
            except FileNotFoundError as exc:
                logger.warning("Discovery task failed for run %s: %s", run_id, exc)
                await _persist_failure(
                    repo,
                    run_id=run_uuid,
                    task_name=TASK_DISCOVER,
                    message=str(exc),
                    attempt=attempt,
                )
                await session.commit()
                raise
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception(
                    "Discovery task failed unexpectedly for run %s", run_id
                )
                await _persist_failure(
                    repo,
                    run_id=run_uuid,
                    task_name=TASK_DISCOVER,
                    message=str(exc),
                    attempt=attempt,
                )
                await session.commit()
                raise

            finished = _now()
            context = _base_context(run)
            context.update({"force_phase": force_phase, "attempt": attempt})
            context["skill_execution"] = dict(
                stage_context.get("skill_execution", {})  # type: ignore[arg-type]
            )
            if retry_notes is not None:
                context["retry_notes"] = retry_notes
            context["retry"] = attempt > 1

            if discovered is None:
                message = "All tasks in tasks.md are already complete."
                await _update_task_state(
                    repo,
                    workflow_run_id=run_uuid,
                    task_name=TASK_DISCOVER,
                    status=models.SpecWorkflowTaskStatus.SUCCEEDED,
                    payload=_status_payload(
                        models.SpecWorkflowTaskStatus.SUCCEEDED,
                        message=message,
                        result="no_work",
                        **_stage_execution_payload(context, TASK_DISCOVER),
                    ),
                    finished_at=finished,
                    attempt=attempt,
                    message=message,
                )
                await repo.update_run(
                    run_uuid,
                    status=models.SpecWorkflowRunStatus.SUCCEEDED,
                    phase=models.SpecWorkflowRunPhase.COMPLETE,
                    finished_at=finished,
                )
                context.update({"no_work": True, "message": message})
                await session.commit()
                return context

            payload = discovered.to_payload()
            await _update_task_state(
                repo,
                workflow_run_id=run_uuid,
                task_name=TASK_DISCOVER,
                status=models.SpecWorkflowTaskStatus.SUCCEEDED,
                payload=_status_payload(
                    models.SpecWorkflowTaskStatus.SUCCEEDED,
                    message="Discovered next task",
                    **payload,
                    **_stage_execution_payload(context, TASK_DISCOVER),
                ),
                finished_at=finished,
                attempt=attempt,
                message="Discovered next task",
            )
            context["task"] = payload
            await session.commit()
            return context

    try:
        outcome = execute_stage(
            stage_name=TASK_DISCOVER,
            run_id=run_id,
            context=stage_context,
            execute_direct=lambda: _run_coro(_execute()),
        )
        context = outcome.result
    except Exception as exc:
        observer.failed(
            exc,
            details={
                "run_id": run_id,
                "attempt": attempt,
                "feature_key": feature_key,
                "force_phase": force_phase,
                "selected_skill": decision.selected_skill,
                "execution_path": decision.execution_path,
            },
        )
        raise

    if isinstance(context, dict):
        _set_stage_execution_payload(
            context,
            TASK_DISCOVER,
            selected_skill=outcome.selected_skill,
            execution_path=outcome.execution_path,
            used_skills=outcome.used_skills,
            used_fallback=outcome.used_fallback,
            shadow_mode_requested=outcome.shadow_mode_requested,
        )

    summary: dict[str, Any] = {}
    if isinstance(context, dict):
        summary["no_work"] = bool(context.get("no_work"))
        task_payload = context.get("task")
        if isinstance(task_payload, dict):
            summary["task"] = {
                "taskId": task_payload.get("taskId"),
                "phase": task_payload.get("phase"),
            }
        summary.update(_stage_execution_payload(context, TASK_DISCOVER))
    observer.succeeded(summary)
    return context


@celery_app.task(
    base=CodexShardTask, name=f"{models.SpecWorkflowRun.__tablename__}.{TASK_SUBMIT}"
)
def submit_codex_job(context: dict[str, Any]) -> dict[str, Any]:
    """Submit the discovered task to Codex Cloud and persist metadata."""

    _log_spec_kit_cli_availability()

    run_uuid = UUID(context["run_id"])
    attempt = _prepare_retry_context(context)
    decision = resolve_stage_execution(
        stage_name=TASK_SUBMIT,
        run_id=context["run_id"],
        context=context,
    )
    _set_stage_execution_payload(
        context,
        TASK_SUBMIT,
        selected_skill=decision.selected_skill,
        execution_path=decision.execution_path,
        used_skills=decision.use_skills,
        used_fallback=False,
        shadow_mode_requested=decision.shadow_mode,
    )
    observer = TaskObserver(
        task_name=TASK_SUBMIT,
        run_id=context["run_id"],
        attempt=attempt,
        metrics=_METRICS,
    )
    observer.started(
        feature_key=context.get("feature_key"),
        retry=context.get("retry"),
        codex_task_id=context.get("codex_task_id"),
        codex_queue=context.get("codex_queue"),
        codex_volume=context.get("codex_volume"),
        selected_skill=decision.selected_skill,
        execution_path=decision.execution_path,
    )

    async def _execute() -> dict[str, Any]:
        async with get_async_session_context() as session:
            repo = SpecWorkflowRepository(session)
            run = await repo.get_run(run_uuid)
            if run is None:
                raise ValueError(f"Workflow run {context['run_id']} not found")

            updates = {"phase": models.SpecWorkflowRunPhase.SUBMIT}
            await _maybe_include_codex_queue(repo, context, updates)
            await repo.update_run(run_uuid, **updates)
            await _ensure_credentials_validated(
                repo,
                session=session,
                context=context,
                workflow_run_id=run_uuid,
                task_name=TASK_SUBMIT,
                attempt=attempt,
            )
            await _update_task_state(
                repo,
                workflow_run_id=run_uuid,
                task_name=TASK_SUBMIT,
                status=models.SpecWorkflowTaskStatus.RUNNING,
                payload=_status_payload(
                    models.SpecWorkflowTaskStatus.RUNNING,
                    message="Submitting task to Codex",
                    codexCredentialStatus=context.get(
                        "credential_audit_status", {}
                    ).get("codex"),
                    githubCredentialStatus=context.get(
                        "credential_audit_status", {}
                    ).get("github"),
                    **_stage_execution_payload(context, TASK_SUBMIT),
                ),
                started_at=_now(),
                attempt=attempt,
                message="Submitting task to Codex",
            )
            await session.commit()

            if context.get("no_work"):
                finished = _now()
                await _update_task_state(
                    repo,
                    workflow_run_id=run_uuid,
                    task_name=TASK_SUBMIT,
                    status=models.SpecWorkflowTaskStatus.SKIPPED,
                    payload=_status_payload(
                        models.SpecWorkflowTaskStatus.SKIPPED,
                        message="Skipped because discovery found no remaining work",
                        reason="no_work",
                        **_stage_execution_payload(context, TASK_SUBMIT),
                    ),
                    finished_at=finished,
                    attempt=attempt,
                    message="Skipped because discovery found no remaining work",
                )
                await session.commit()
                return context

            discovered = context.get("task") or {}

            preflight = _run_codex_preflight_check()
            if preflight.volume:
                context["codex_volume"] = preflight.volume
            else:
                context.setdefault(
                    "codex_volume", settings.spec_workflow.codex_volume_name
                )
            context["codex_preflight_status"] = preflight.status.value
            context["codex_preflight_message"] = preflight.message
            context["codex_preflight_exit_code"] = preflight.exit_code
            if preflight.status is models.CodexPreflightStatus.FAILED:
                failure_message = preflight.message or (
                    "Codex login status check failed before submission."
                )
                finished = _now()
                await _update_task_state(
                    repo,
                    workflow_run_id=run_uuid,
                    task_name=TASK_SUBMIT,
                    status=models.SpecWorkflowTaskStatus.FAILED,
                    payload=_status_payload(
                        models.SpecWorkflowTaskStatus.FAILED,
                        message=failure_message,
                        code="codex_preflight_failed",
                        codexPreflightStatus=preflight.status.value,
                        codexVolume=context.get("codex_volume"),
                        **_stage_execution_payload(context, TASK_SUBMIT),
                    ),
                    finished_at=finished,
                    attempt=attempt,
                    message=failure_message,
                )
                updates = {
                    "status": models.SpecWorkflowRunStatus.FAILED,
                    "codex_preflight_status": preflight.status,
                    "codex_preflight_message": preflight.message,
                    "codex_volume": context.get("codex_volume"),
                    "finished_at": finished,
                }
                await _maybe_include_codex_queue(repo, context, updates)
                await repo.update_run(run_uuid, **updates)
                await session.commit()
                raise RuntimeError(failure_message)

            updates = {
                "codex_preflight_status": preflight.status,
                "codex_preflight_message": preflight.message,
                "codex_volume": (
                    preflight.volume
                    or context.get("codex_volume")
                    or settings.spec_workflow.codex_volume_name
                ),
            }
            await _maybe_include_codex_queue(repo, context, updates)
            await repo.update_run(run_uuid, **updates)
            await session.commit()

            client = _build_codex_client()
            artifacts_dir = _resolve_artifacts_dir(run)

            try:
                result: CodexSubmissionResult = client.submit(
                    feature_key=context["feature_key"],
                    task_identifier=discovered.get("taskId", ""),
                    task_summary=discovered.get("title", ""),
                    artifacts_dir=artifacts_dir,
                )
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception(
                    "Codex submission failed for run %s", context["run_id"]
                )
                await _persist_failure(
                    repo,
                    run_id=run_uuid,
                    task_name=TASK_SUBMIT,
                    message=str(exc),
                    attempt=attempt,
                )
                await session.commit()
                raise

            finished = _now()
            context.update(
                {
                    "codex_task_id": result.task_id,
                    "codex_logs_path": str(result.logs_path),
                }
            )

            updates = {
                "codex_task_id": result.task_id,
                "codex_logs_path": str(result.logs_path),
            }
            await _maybe_include_codex_queue(repo, context, updates)
            await repo.update_run(run_uuid, **updates)
            await repo.add_artifact(
                workflow_run_id=run_uuid,
                artifact_type=models.WorkflowArtifactType.CODEX_LOGS,
                path=str(result.logs_path),
            )
            await _update_task_state(
                repo,
                workflow_run_id=run_uuid,
                task_name=TASK_SUBMIT,
                status=models.SpecWorkflowTaskStatus.SUCCEEDED,
                payload=_status_payload(
                    models.SpecWorkflowTaskStatus.SUCCEEDED,
                    message="Codex job submitted",
                    codexTaskId=result.task_id,
                    summary=result.summary,
                    logsPath=str(result.logs_path),
                    codexCredentialStatus=context.get(
                        "credential_audit_status", {}
                    ).get("codex"),
                    githubCredentialStatus=context.get(
                        "credential_audit_status", {}
                    ).get("github"),
                    **_stage_execution_payload(context, TASK_SUBMIT),
                ),
                finished_at=finished,
                attempt=attempt,
                message="Codex job submitted",
                artifact_paths=[str(result.logs_path)],
            )
            await session.commit()
            return context

    try:
        outcome = execute_stage(
            stage_name=TASK_SUBMIT,
            run_id=context["run_id"],
            context=context,
            execute_direct=lambda: _run_coro(_execute()),
        )
        result = outcome.result
    except Exception as exc:
        observer.failed(exc, details=context)
        raise

    if isinstance(result, dict):
        _set_stage_execution_payload(
            result,
            TASK_SUBMIT,
            selected_skill=outcome.selected_skill,
            execution_path=outcome.execution_path,
            used_skills=outcome.used_skills,
            used_fallback=outcome.used_fallback,
            shadow_mode_requested=outcome.shadow_mode_requested,
        )

    summary: dict[str, Any] = {}
    if isinstance(result, dict):
        summary["codex_task_id"] = result.get("codex_task_id")
        summary["codex_logs_path"] = result.get("codex_logs_path")
        summary["retry"] = result.get("retry")
        summary["codex_queue"] = result.get("codex_queue")
        summary["codex_volume"] = result.get("codex_volume")
        summary["codex_preflight_status"] = result.get("codex_preflight_status")
        summary.update(_stage_execution_payload(result, TASK_SUBMIT))
    observer.succeeded(summary)
    return result


@celery_app.task(
    base=CodexShardTask, name=f"{models.SpecWorkflowRun.__tablename__}.{TASK_PUBLISH}"
)
def apply_and_publish(context: dict[str, Any]) -> dict[str, Any]:
    """Retrieve the Codex patch and publish the resulting PR."""

    _log_spec_kit_cli_availability()

    run_uuid = UUID(context["run_id"])
    attempt = _prepare_retry_context(context)
    decision = resolve_stage_execution(
        stage_name=TASK_PUBLISH,
        run_id=context["run_id"],
        context=context,
    )
    _set_stage_execution_payload(
        context,
        TASK_PUBLISH,
        selected_skill=decision.selected_skill,
        execution_path=decision.execution_path,
        used_skills=decision.use_skills,
        used_fallback=False,
        shadow_mode_requested=decision.shadow_mode,
    )
    observer = TaskObserver(
        task_name=TASK_PUBLISH,
        run_id=context["run_id"],
        attempt=attempt,
        metrics=_METRICS,
    )
    observer.started(
        feature_key=context.get("feature_key"),
        retry=context.get("retry"),
        codex_task_id=context.get("codex_task_id"),
        no_work=context.get("no_work"),
        codex_queue=context.get("codex_queue"),
        codex_volume=context.get("codex_volume"),
        selected_skill=decision.selected_skill,
        execution_path=decision.execution_path,
    )

    async def _execute() -> dict[str, Any]:
        async with get_async_session_context() as session:
            repo = SpecWorkflowRepository(session)
            run = await repo.get_run(run_uuid)
            if run is None:
                raise ValueError(f"Workflow run {context['run_id']} not found")

            updates = {"phase": models.SpecWorkflowRunPhase.APPLY}
            await _maybe_include_codex_queue(repo, context, updates)
            await repo.update_run(run_uuid, **updates)
            await _ensure_credentials_validated(
                repo,
                session=session,
                context=context,
                workflow_run_id=run_uuid,
                task_name=TASK_PUBLISH,
                attempt=attempt,
            )
            await _update_task_state(
                repo,
                workflow_run_id=run_uuid,
                task_name=TASK_PUBLISH,
                status=models.SpecWorkflowTaskStatus.RUNNING,
                payload=_status_payload(
                    models.SpecWorkflowTaskStatus.RUNNING,
                    message="Retrieving Codex diff and publishing to GitHub",
                    codexCredentialStatus=context.get(
                        "credential_audit_status", {}
                    ).get("codex"),
                    githubCredentialStatus=context.get(
                        "credential_audit_status", {}
                    ).get("github"),
                    **_stage_execution_payload(context, TASK_PUBLISH),
                ),
                started_at=_now(),
                attempt=attempt,
                message="Retrieving Codex diff and publishing to GitHub",
            )
            await session.commit()

            artifacts_dir = _resolve_artifacts_dir(run)
            _apply_resume_token(context, artifacts_dir=artifacts_dir)

            if context.get("no_work"):
                finished = _now()
                summary_path = _write_run_summary(artifacts_dir, context)
                await repo.add_artifact(
                    workflow_run_id=run_uuid,
                    artifact_type=models.WorkflowArtifactType.PR_PAYLOAD,
                    path=str(summary_path),
                )
                await _update_task_state(
                    repo,
                    workflow_run_id=run_uuid,
                    task_name=TASK_PUBLISH,
                    status=models.SpecWorkflowTaskStatus.SKIPPED,
                    payload=_status_payload(
                        models.SpecWorkflowTaskStatus.SKIPPED,
                        message="Skipped publish because no work was required",
                        reason="no_work",
                        **_stage_execution_payload(context, TASK_PUBLISH),
                    ),
                    finished_at=finished,
                    attempt=attempt,
                    message="Skipped publish because no work was required",
                    artifact_paths=[str(summary_path)],
                )
                updates = {
                    "status": models.SpecWorkflowRunStatus.SUCCEEDED,
                    "phase": models.SpecWorkflowRunPhase.COMPLETE,
                    "finished_at": finished,
                }
                await _maybe_include_codex_queue(repo, context, updates)
                await repo.update_run(run_uuid, **updates)
                await session.commit()
                context["run_summary_path"] = str(summary_path)
                return context
            discovered = context.get("task") or {}
            codex_client = _build_codex_client()
            github_client = _build_github_client()

            try:
                codex_task_id = context.get("codex_task_id", "")
                if context.get("retry") and context.get("codex_patch_path"):
                    patch_path = _resolve_resume_path(
                        artifacts_dir, str(context["codex_patch_path"])
                    )
                    diff = CodexDiffResult(
                        patch_path=patch_path,
                        description="resume_from_retry_context",
                        has_changes=True,
                    )
                else:
                    diff = _poll_for_codex_diff(
                        codex_client,
                        task_id=codex_task_id,
                        artifacts_dir=artifacts_dir,
                        task_identifier=discovered.get("taskId", ""),
                        task_summary=discovered.get("title", ""),
                    )
                await repo.add_artifact(
                    workflow_run_id=run_uuid,
                    artifact_type=models.WorkflowArtifactType.CODEX_PATCH,
                    path=str(diff.patch_path),
                )
                if context.get("apply_output_path"):
                    apply_output_path = _resolve_resume_path(
                        artifacts_dir, str(context["apply_output_path"])
                    )
                else:
                    apply_output_path = _write_apply_output(artifacts_dir, diff)
                    await repo.add_artifact(
                        workflow_run_id=run_uuid,
                        artifact_type=models.WorkflowArtifactType.APPLY_OUTPUT,
                        path=str(apply_output_path),
                    )

                updates = {
                    "phase": models.SpecWorkflowRunPhase.PUBLISH,
                    "codex_patch_path": str(diff.patch_path),
                }
                await _maybe_include_codex_queue(repo, context, updates)
                await repo.update_run(run_uuid, **updates)

                publish: GitHubPublishResult = github_client.publish(
                    feature_key=context["feature_key"],
                    task_identifier=discovered.get("taskId", ""),
                    patch_path=diff.patch_path,
                    artifacts_dir=artifacts_dir,
                )
                await repo.add_artifact(
                    workflow_run_id=run_uuid,
                    artifact_type=models.WorkflowArtifactType.GH_PR_RESPONSE,
                    path=str(publish.response_path),
                )
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception("Apply/publish failed for run %s", context["run_id"])
                await _persist_failure(
                    repo,
                    run_id=run_uuid,
                    task_name=TASK_PUBLISH,
                    message=str(exc),
                    attempt=attempt,
                )
                await session.commit()
                raise

            finished = _now()
            context.update(
                {
                    "branch_name": publish.branch_name,
                    "pr_url": publish.pr_url,
                    "codex_patch_path": str(diff.patch_path),
                    "github_response_path": str(publish.response_path),
                    "apply_output_path": str(apply_output_path),
                }
            )
            summary_path = _write_run_summary(artifacts_dir, context)
            await repo.add_artifact(
                workflow_run_id=run_uuid,
                artifact_type=models.WorkflowArtifactType.PR_PAYLOAD,
                path=str(summary_path),
            )
            updates = {
                "status": models.SpecWorkflowRunStatus.SUCCEEDED,
                "phase": models.SpecWorkflowRunPhase.COMPLETE,
                "branch_name": publish.branch_name,
                "pr_url": publish.pr_url,
                "codex_patch_path": str(diff.patch_path),
                "finished_at": finished,
            }
            await _maybe_include_codex_queue(repo, context, updates)
            await repo.update_run(run_uuid, **updates)
            await _update_task_state(
                repo,
                workflow_run_id=run_uuid,
                task_name=TASK_PUBLISH,
                status=models.SpecWorkflowTaskStatus.SUCCEEDED,
                payload=_status_payload(
                    models.SpecWorkflowTaskStatus.SUCCEEDED,
                    message="Pull request published",
                    branch=publish.branch_name,
                    prUrl=publish.pr_url,
                    patchPath=str(diff.patch_path),
                    responsePath=str(publish.response_path),
                    applyOutputPath=str(apply_output_path),
                    codexCredentialStatus=context.get(
                        "credential_audit_status", {}
                    ).get("codex"),
                    githubCredentialStatus=context.get(
                        "credential_audit_status", {}
                    ).get("github"),
                    resumed=context.get("resume_token") is not None,
                    **_stage_execution_payload(context, TASK_PUBLISH),
                ),
                finished_at=finished,
                attempt=attempt,
                message="Pull request published",
                artifact_paths=[
                    str(diff.patch_path),
                    str(publish.response_path),
                    str(apply_output_path),
                    str(summary_path),
                ],
            )
            await session.commit()
            context["run_summary_path"] = str(summary_path)
            return context

    try:
        outcome = execute_stage(
            stage_name=TASK_PUBLISH,
            run_id=context["run_id"],
            context=context,
            execute_direct=lambda: _run_coro(_execute()),
        )
        result = outcome.result
    except Exception as exc:
        observer.failed(exc, details=context)
        raise

    if isinstance(result, dict):
        _set_stage_execution_payload(
            result,
            TASK_PUBLISH,
            selected_skill=outcome.selected_skill,
            execution_path=outcome.execution_path,
            used_skills=outcome.used_skills,
            used_fallback=outcome.used_fallback,
            shadow_mode_requested=outcome.shadow_mode_requested,
        )

    summary: dict[str, Any] = {}
    if isinstance(result, dict):
        summary["no_work"] = bool(result.get("no_work"))
        summary["branch"] = result.get("branch_name")
        summary["pr_url"] = result.get("pr_url")
        summary["codex_patch_path"] = result.get("codex_patch_path")
        summary["codex_queue"] = result.get("codex_queue")
        summary["codex_volume"] = result.get("codex_volume")
        summary.update(_stage_execution_payload(result, TASK_PUBLISH))
    observer.succeeded(summary)
    return result


__all__ = [
    "discover_next_phase",
    "submit_codex_job",
    "apply_and_publish",
    "run_codex_preflight_check",
]
