"""Celery configuration for the mm-orchestrator worker."""

from __future__ import annotations

import os
from typing import Any, Mapping

from kombu import Queue

from moonmind.config.settings import settings

_DEFAULT_QUEUE = os.getenv("ORCHESTRATOR_CELERY_QUEUE", "orchestrator.run")
_DEFAULT_EXCHANGE = os.getenv(
    "ORCHESTRATOR_CELERY_EXCHANGE", settings.celery.default_exchange
)
_DEFAULT_ROUTING_KEY = os.getenv("ORCHESTRATOR_CELERY_ROUTING_KEY", _DEFAULT_QUEUE)

broker_url: str = os.getenv("ORCHESTRATOR_BROKER_URL", settings.celery.broker_url)
result_backend: str | None = (
    os.getenv("ORCHESTRATOR_RESULT_BACKEND", settings.celery.result_backend or "")
    or None
)

imports = ("moonmind.workflows.orchestrator.tasks",)

# Ensure the worker only pulls one orchestrator run at a time.
worker_prefetch_multiplier = int(os.getenv("ORCHESTRATOR_PREFETCH", "1"))

task_acks_late = True
task_acks_on_failure_or_timeout = True
task_reject_on_worker_lost = True
task_default_queue = _DEFAULT_QUEUE
task_default_exchange = _DEFAULT_EXCHANGE
task_default_routing_key = _DEFAULT_ROUTING_KEY
task_queues = (
    Queue(
        _DEFAULT_QUEUE,
        exchange=_DEFAULT_EXCHANGE,
        routing_key=_DEFAULT_ROUTING_KEY,
        durable=True,
    ),
)
accept_content = settings.celery.accept_content
task_serializer = settings.celery.task_serializer
result_serializer = settings.celery.result_serializer
result_extended = True
result_expires = settings.celery.result_expires


def build_task_headers(**overrides: Any) -> Mapping[str, Any]:
    """Return default task header values for orchestrator submissions."""

    headers: dict[str, Any] = {
        "orchestrator": {
            "queue": _DEFAULT_QUEUE,
        }
    }
    headers.update(overrides)
    return headers


__all__ = [
    "broker_url",
    "result_backend",
    "imports",
    "worker_prefetch_multiplier",
    "task_acks_late",
    "task_acks_on_failure_or_timeout",
    "task_reject_on_worker_lost",
    "task_default_queue",
    "task_default_exchange",
    "task_default_routing_key",
    "task_queues",
    "accept_content",
    "task_serializer",
    "result_serializer",
    "result_extended",
    "result_expires",
    "build_task_headers",
]
