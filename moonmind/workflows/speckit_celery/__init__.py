"""Celery application configuration for the Spec Kit workflow."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any, Iterable

from celery import Celery

from moonmind.config.settings import settings
from moonmind.workflows.speckit_celery.celeryconfig import (
    build_task_router,
    get_codex_shard_router,
)

CELERY_NAMESPACE = "moonmind.workflows.speckit_celery"
_TASK_IMPORT = "moonmind.workflows.speckit_celery.tasks"
_ORCHESTRATOR_EXPORTS = frozenset(
    {
        "TriggeredWorkflow",
        "WorkflowConflictError",
        "WorkflowRetryError",
        "retry_spec_workflow_run",
        "trigger_spec_workflow_run",
    }
)

if TYPE_CHECKING:
    from moonmind.workflows.speckit_celery.orchestrator import (
        TriggeredWorkflow,
        WorkflowConflictError,
        WorkflowRetryError,
        retry_spec_workflow_run,
        trigger_spec_workflow_run,
    )


def _merge_imports(existing: Iterable[str]) -> list[str]:
    imports = {item for item in existing if item}
    imports.add(_TASK_IMPORT)
    return sorted(imports)


def create_celery_app() -> Celery:
    """Instantiate a Celery application configured for Spec Kit workflows."""

    app = Celery(CELERY_NAMESPACE)
    shard_router = get_codex_shard_router()
    app.conf.update(
        broker_url=(
            settings.spec_workflow.celery_broker_url or settings.celery.broker_url
        ),
        result_backend=(
            settings.spec_workflow.celery_result_backend
            or settings.celery.result_backend
        ),
        task_default_queue=settings.celery.default_queue,
        task_default_exchange=settings.celery.default_exchange,
        task_default_routing_key=settings.celery.default_routing_key,
        task_serializer=settings.celery.task_serializer,
        result_serializer=settings.celery.result_serializer,
        accept_content=list(settings.celery.accept_content),
        imports=_merge_imports(settings.celery.imports),
        task_acks_late=settings.celery.task_acks_late,
        task_acks_on_failure_or_timeout=settings.celery.task_acks_on_failure_or_timeout,
        task_reject_on_worker_lost=settings.celery.task_reject_on_worker_lost,
        worker_prefetch_multiplier=settings.celery.worker_prefetch_multiplier,
        result_extended=settings.celery.result_extended,
        result_expires=settings.celery.result_expires,
        task_queues=shard_router.build_queues(include_default=True),
        task_routes=build_task_router(shard_router),
    )
    return app


celery_app = create_celery_app()


def __getattr__(name: str) -> Any:
    """Lazily load orchestrator helpers to avoid import-order cycles."""

    if name in _ORCHESTRATOR_EXPORTS:
        orchestrator = import_module("moonmind.workflows.speckit_celery.orchestrator")
        return getattr(orchestrator, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# Keep exports alphabetized for readability and easy scanning.
__all__ = [
    "CELERY_NAMESPACE",
    "TriggeredWorkflow",
    "WorkflowConflictError",
    "WorkflowRetryError",
    "celery_app",
    "create_celery_app",
    "retry_spec_workflow_run",
    "trigger_spec_workflow_run",
]
