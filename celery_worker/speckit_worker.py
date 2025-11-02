"""Celery worker entrypoint for Spec Kit workflows."""

from __future__ import annotations

from celery import Celery

from moonmind.config.settings import settings


def create_celery_app() -> Celery:
    """Create a Celery app configured for Spec Kit workflows."""
    app = Celery("moonmind.workflows.speckit_celery")
    app.conf.update(
        broker_url=settings.celery.broker_url,
        result_backend=settings.celery.result_backend,
        task_default_queue=settings.celery.default_queue,
        task_default_exchange=settings.celery.default_exchange,
        task_default_routing_key=settings.celery.default_routing_key,
        task_serializer=settings.celery.task_serializer,
        result_serializer=settings.celery.result_serializer,
        accept_content=list(settings.celery.accept_content),
        imports=list(settings.celery.imports),
        task_acks_late=settings.celery.task_acks_late,
        task_acks_on_failure_or_timeout=(
            settings.celery.task_acks_on_failure_or_timeout
        ),
        task_reject_on_worker_lost=settings.celery.task_reject_on_worker_lost,
        worker_prefetch_multiplier=settings.celery.worker_prefetch_multiplier,
        result_extended=settings.celery.result_extended,
        result_expires=settings.celery.result_expires,
    )

    return app


celery_app = create_celery_app()

# Celery uses the module-level ``app`` attribute as the default application target
# when running ``celery -A celery_worker.speckit_worker worker``.
app = celery_app


if __name__ == "__main__":  # pragma: no cover - CLI execution path
    celery_app.start()
