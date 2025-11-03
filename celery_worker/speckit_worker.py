"""Celery worker entrypoint for Spec Kit workflows."""

from __future__ import annotations

from moonmind.workflows.speckit_celery import celery_app as speckit_celery_app


celery_app = speckit_celery_app

# Celery uses the module-level ``app`` attribute as the default application target
# when running ``celery -A celery_worker.speckit_worker worker``.
app = celery_app


if __name__ == "__main__":  # pragma: no cover - CLI execution path
    celery_app.start()
