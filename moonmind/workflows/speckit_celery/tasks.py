"""Celery tasks orchestrating Spec Kit workflows."""

from __future__ import annotations

import asyncio
import os
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Coroutine, Optional, TypeVar
from uuid import UUID

from celery.utils.log import get_task_logger

from api_service.db.base import get_async_session_context
from moonmind.config.settings import settings
from moonmind.workflows.adapters import (
    CodexClient,
    CodexDiffResult,
    CodexSubmissionResult,
    GitHubClient,
    GitHubPublishResult,
)
from moonmind.workflows.speckit_celery import celery_app, models
from moonmind.workflows.speckit_celery.repositories import SpecWorkflowRepository

logger = get_task_logger(__name__)


TASK_DISCOVER = "discover_next_phase"
TASK_SUBMIT = "submit_codex_job"
TASK_PUBLISH = "apply_and_publish"
TASK_SEQUENCE: tuple[str, ...] = (TASK_DISCOVER, TASK_SUBMIT, TASK_PUBLISH)

_TASK_PATTERN = re.compile(r"^- \[(?P<mark>[ xX])\] (?P<body>.+)$")
_TASK_BODY_PATTERN = re.compile(r"^(?P<identifier>\S+)(?P<title>\s+.*)?$")

T = TypeVar("T")


class CredentialValidationError(RuntimeError):
    """Raised when workflow credentials fail validation."""

    def __init__(self, audit: models.CredentialAuditResult, message: str) -> None:
        super().__init__(message)
        self.audit = audit


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
) -> models.SpecWorkflowTaskState:
    state = await repo.upsert_task_state(
        workflow_run_id=workflow_run_id,
        task_name=task_name,
        status=status,
        attempt=attempt,
        payload=payload,
        started_at=started_at,
        finished_at=finished_at,
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


def _resolve_artifacts_dir(run: models.SpecWorkflowRun) -> Path:
    if run.artifacts_path:
        return Path(run.artifacts_path)
    cfg = settings.spec_workflow
    base = Path(cfg.artifacts_root)
    return base / str(run.id)


def _base_context(run: models.SpecWorkflowRun) -> dict[str, Any]:
    return {
        "run_id": str(run.id),
        "feature_key": run.feature_key,
        "artifacts_path": str(_resolve_artifacts_dir(run)),
    }


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

    run_uuid = UUID(run_id)

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
                ),
                started_at=_now(),
                attempt=attempt,
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
                    ),
                    finished_at=finished,
                    attempt=attempt,
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
                ),
                finished_at=finished,
                attempt=attempt,
            )
            context["task"] = payload
            await session.commit()
            return context

    return _run_coro(_execute())


@celery_app.task(name=f"{models.SpecWorkflowRun.__tablename__}.{TASK_SUBMIT}")
def submit_codex_job(context: dict[str, Any]) -> dict[str, Any]:
    """Submit the discovered task to Codex Cloud and persist metadata."""

    run_uuid = UUID(context["run_id"])
    attempt = _prepare_retry_context(context)

    async def _execute() -> dict[str, Any]:
        async with get_async_session_context() as session:
            repo = SpecWorkflowRepository(session)
            run = await repo.get_run(run_uuid)
            if run is None:
                raise ValueError(f"Workflow run {context['run_id']} not found")

            await repo.update_run(
                run_uuid,
                phase=models.SpecWorkflowRunPhase.SUBMIT,
            )
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
                ),
                started_at=_now(),
                attempt=attempt,
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
                    ),
                    finished_at=finished,
                    attempt=attempt,
                )
                await session.commit()
                return context

            discovered = context.get("task") or {}
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

            await repo.update_run(
                run_uuid,
                codex_task_id=result.task_id,
                codex_logs_path=str(result.logs_path),
            )
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
                ),
                finished_at=finished,
                attempt=attempt,
            )
            await session.commit()
            return context

    return _run_coro(_execute())


@celery_app.task(name=f"{models.SpecWorkflowRun.__tablename__}.{TASK_PUBLISH}")
def apply_and_publish(context: dict[str, Any]) -> dict[str, Any]:
    """Retrieve the Codex patch and publish the resulting PR."""

    run_uuid = UUID(context["run_id"])
    attempt = _prepare_retry_context(context)

    async def _execute() -> dict[str, Any]:
        async with get_async_session_context() as session:
            repo = SpecWorkflowRepository(session)
            run = await repo.get_run(run_uuid)
            if run is None:
                raise ValueError(f"Workflow run {context['run_id']} not found")

            await repo.update_run(run_uuid, phase=models.SpecWorkflowRunPhase.APPLY)
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
                ),
                started_at=_now(),
                attempt=attempt,
            )
            await session.commit()

            if context.get("no_work"):
                finished = _now()
                await _update_task_state(
                    repo,
                    workflow_run_id=run_uuid,
                    task_name=TASK_PUBLISH,
                    status=models.SpecWorkflowTaskStatus.SKIPPED,
                    payload=_status_payload(
                        models.SpecWorkflowTaskStatus.SKIPPED,
                        message="Skipped publish because no work was required",
                        reason="no_work",
                    ),
                    finished_at=finished,
                    attempt=attempt,
                )
                await repo.update_run(
                    run_uuid,
                    status=models.SpecWorkflowRunStatus.SUCCEEDED,
                    phase=models.SpecWorkflowRunPhase.COMPLETE,
                    finished_at=finished,
                )
                await session.commit()
                return context

            artifacts_dir = _resolve_artifacts_dir(run)
            discovered = context.get("task") or {}
            codex_client = _build_codex_client()
            github_client = _build_github_client()

            try:
                diff: CodexDiffResult = codex_client.retrieve_patch(
                    task_id=context.get("codex_task_id", ""),
                    artifacts_dir=artifacts_dir,
                    task_identifier=discovered.get("taskId", ""),
                    task_summary=discovered.get("title", ""),
                )
                await repo.add_artifact(
                    workflow_run_id=run_uuid,
                    artifact_type=models.WorkflowArtifactType.CODEX_PATCH,
                    path=str(diff.patch_path),
                )

                await repo.update_run(
                    run_uuid,
                    phase=models.SpecWorkflowRunPhase.PUBLISH,
                    codex_patch_path=str(diff.patch_path),
                )

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
                }
            )
            await repo.update_run(
                run_uuid,
                status=models.SpecWorkflowRunStatus.SUCCEEDED,
                phase=models.SpecWorkflowRunPhase.COMPLETE,
                branch_name=publish.branch_name,
                pr_url=publish.pr_url,
                codex_patch_path=str(diff.patch_path),
                finished_at=finished,
            )
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
                    codexCredentialStatus=context.get(
                        "credential_audit_status", {}
                    ).get("codex"),
                    githubCredentialStatus=context.get(
                        "credential_audit_status", {}
                    ).get("github"),
                ),
                finished_at=finished,
                attempt=attempt,
            )
            await session.commit()
            return context

    return _run_coro(_execute())


__all__ = [
    "discover_next_phase",
    "submit_codex_job",
    "apply_and_publish",
]
