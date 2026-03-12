"""Celery configuration for the mm-orchestrator worker.

Provides minimal defaults; override via environment variables or
``ORCHESTRATOR_*`` prefixed env vars at container startup.
"""

from __future__ import annotations

import os

_QUEUE = os.getenv("ORCHESTRATOR_CELERY_QUEUE", "orchestrator.run")
_BROKER = os.getenv("ORCHESTRATOR_BROKER_URL", "amqp://guest:guest@rabbitmq:5672//")
_BACKEND = os.getenv("ORCHESTRATOR_RESULT_BACKEND", "") or None

broker_url: str = _BROKER
result_backend: str | None = _BACKEND

imports = ("moonmind.workflows.orchestrator.tasks",)

# Ensure the worker processes only one orchestrator run at a time.
worker_prefetch_multiplier = int(os.getenv("ORCHESTRATOR_PREFETCH", "1"))

task_acks_late = True
task_acks_on_failure_or_timeout = True
task_default_queue = _QUEUE
task_default_routing_key = _QUEUE

# Kombu queue definition
try:
    from kombu import Queue as _Queue

    task_queues = (_Queue(_QUEUE),)
except ImportError:
    task_queues = ()
