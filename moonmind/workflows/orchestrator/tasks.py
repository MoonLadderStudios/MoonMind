"""Stub Celery application for the MoonMind orchestrator worker."""

from __future__ import annotations

import os
from typing import Final

from celery import Celery

app = Celery("moonmind.workflows.orchestrator")
app.config_from_object("moonmind.workflows.orchestrator.celeryconfig")

_DEFAULT_QUEUE: Final[str] = app.conf.get(
    "task_default_queue", os.getenv("ORCHESTRATOR_CELERY_QUEUE", "orchestrator.run")
)
app.conf.update(
    task_default_queue=_DEFAULT_QUEUE,
    task_default_routing_key=app.conf.get(
        "task_default_routing_key", _DEFAULT_QUEUE
    ),
)


@app.task(name="moonmind.workflows.orchestrator.tasks.health_check")
def health_check() -> str:
    """Basic health-check task to verify the worker boots successfully."""

    return "ok"


__all__ = ["app", "health_check"]
