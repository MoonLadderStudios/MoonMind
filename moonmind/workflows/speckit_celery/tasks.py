"""Celery tasks orchestrating Spec Kit workflows."""

from __future__ import annotations

import asyncio
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

_TASK_PATTERN = re.compile(r"^- \[(?P<mark>[ xX])\] (?P<body>.+)$")
_TASK_BODY_PATTERN = re.compile(r"^(?P<identifier>\S+)(?P<title>\s+.*)?$")

T = TypeVar("T")


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
        except BaseException as exc:  # pragma: no cover - propagate errors
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
    payload: Optional[dict[str, Any]] = None,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
) -> models.SpecWorkflowTaskState:
    state = await repo.upsert_task_state(
        workflow_run_id=workflow_run_id,
        task_name=task_name,
        status=status,
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
) -> None:
    finished = _now()
    await _update_task_state(
        repo,
        workflow_run_id=run_id,
        task_name=task_name,
        status=models.SpecWorkflowTaskStatus.FAILED,
        payload={"code": f"{task_name}_failed", "message": message},
        finished_at=finished,
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


@celery_app.task(name=f"{models.SpecWorkflowRun.__tablename__}.{TASK_DISCOVER}")
def discover_next_phase(
    run_id: str,
    *,
    feature_key: Optional[str] = None,
    force_phase: Optional[str] = None,
) -> dict[str, Any]:
    """Locate the next unchecked task in the Spec Kit tasks document."""

    run_uuid = UUID(run_id)

    async def _execute() -> dict[str, Any]:
        async with get_async_session_context() as session:
            repo = SpecWorkflowRepository(session)
            run = await repo.get_run(run_uuid)
            if run is None:
                raise ValueError(f"Workflow run {run_id} not found")

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
                started_at=_now(),
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
                )
                await session.commit()
                raise

            finished = _now()
            context = _base_context(run)
            context.update({"force_phase": force_phase})

            if discovered is None:
                message = "All tasks in tasks.md are already complete."
                await _update_task_state(
                    repo,
                    workflow_run_id=run_uuid,
                    task_name=TASK_DISCOVER,
                    status=models.SpecWorkflowTaskStatus.SUCCEEDED,
                    payload={"status": "no_work", "message": message},
                    finished_at=finished,
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
                payload=payload,
                finished_at=finished,
            )
            context["task"] = payload
            await session.commit()
            return context

    return _run_coro(_execute())


@celery_app.task(name=f"{models.SpecWorkflowRun.__tablename__}.{TASK_SUBMIT}")
def submit_codex_job(context: dict[str, Any]) -> dict[str, Any]:
    """Submit the discovered task to Codex Cloud and persist metadata."""

    run_uuid = UUID(context["run_id"])

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
            await _update_task_state(
                repo,
                workflow_run_id=run_uuid,
                task_name=TASK_SUBMIT,
                status=models.SpecWorkflowTaskStatus.RUNNING,
                started_at=_now(),
            )
            await session.commit()

            if context.get("no_work"):
                finished = _now()
                await _update_task_state(
                    repo,
                    workflow_run_id=run_uuid,
                    task_name=TASK_SUBMIT,
                    status=models.SpecWorkflowTaskStatus.SKIPPED,
                    payload={"status": "skipped", "reason": "no_work"},
                    finished_at=finished,
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
                    repo, run_id=run_uuid, task_name=TASK_SUBMIT, message=str(exc)
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
                payload={"codexTaskId": result.task_id, "summary": result.summary},
                finished_at=finished,
            )
            await session.commit()
            return context

    return _run_coro(_execute())


@celery_app.task(name=f"{models.SpecWorkflowRun.__tablename__}.{TASK_PUBLISH}")
def apply_and_publish(context: dict[str, Any]) -> dict[str, Any]:
    """Retrieve the Codex patch and publish the resulting PR."""

    run_uuid = UUID(context["run_id"])

    async def _execute() -> dict[str, Any]:
        async with get_async_session_context() as session:
            repo = SpecWorkflowRepository(session)
            run = await repo.get_run(run_uuid)
            if run is None:
                raise ValueError(f"Workflow run {context['run_id']} not found")

            await repo.update_run(run_uuid, phase=models.SpecWorkflowRunPhase.APPLY)
            await _update_task_state(
                repo,
                workflow_run_id=run_uuid,
                task_name=TASK_PUBLISH,
                status=models.SpecWorkflowTaskStatus.RUNNING,
                started_at=_now(),
            )
            await session.commit()

            if context.get("no_work"):
                finished = _now()
                await _update_task_state(
                    repo,
                    workflow_run_id=run_uuid,
                    task_name=TASK_PUBLISH,
                    status=models.SpecWorkflowTaskStatus.SKIPPED,
                    payload={"status": "skipped", "reason": "no_work"},
                    finished_at=finished,
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
                    run_uuid, phase=models.SpecWorkflowRunPhase.PUBLISH
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
                    repo, run_id=run_uuid, task_name=TASK_PUBLISH, message=str(exc)
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
                finished_at=finished,
            )
            await _update_task_state(
                repo,
                workflow_run_id=run_uuid,
                task_name=TASK_PUBLISH,
                status=models.SpecWorkflowTaskStatus.SUCCEEDED,
                payload={
                    "branch": publish.branch_name,
                    "prUrl": publish.pr_url,
                    "patchPath": str(diff.patch_path),
                },
                finished_at=finished,
            )
            await session.commit()
            return context

    return _run_coro(_execute())


__all__ = [
    "discover_next_phase",
    "submit_codex_job",
    "apply_and_publish",
]
