"""Celery routing helpers for Spec Kit Codex shard queues."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Tuple

from kombu import Queue

from moonmind.config.settings import settings

CODEX_QUEUE_PREFIX = "codex-"
CODEX_QUEUE_HEADER = "codex-queue"
CODEX_AFFINITY_HEADER = "codex-affinity"


def _ensure_positive(value: int, *, field: str) -> int:
    if value <= 0:
        raise ValueError(f"{field} must be a positive integer; received {value!r}")
    return value


def _hash_affinity_key(key: str) -> int:
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


@dataclass(frozen=True, slots=True)
class CodexShardRouter:
    """Utility for deterministic Codex queue selection."""

    shard_count: int

    def __post_init__(self) -> None:
        _ensure_positive(self.shard_count, field="shard_count")

    @property
    def default_exchange(self) -> str:
        return settings.celery.default_exchange

    @property
    def default_queue(self) -> str:
        return settings.celery.default_queue

    def queue_name(self, shard_index: int) -> str:
        if shard_index < 0 or shard_index >= self.shard_count:
            raise ValueError(
                "shard_index must be between 0 and shard_count - 1; "
                f"received {shard_index!r}"
            )
        return f"{CODEX_QUEUE_PREFIX}{shard_index}"

    def queue_names(self) -> Tuple[str, ...]:
        return tuple(self.queue_name(index) for index in range(self.shard_count))

    def shard_for_key(self, affinity_key: str) -> int:
        if not affinity_key:
            raise ValueError("affinity_key must be a non-empty string")
        hashed = _hash_affinity_key(affinity_key)
        return hashed % self.shard_count

    def queue_for_key(self, affinity_key: str) -> str:
        return self.queue_name(self.shard_for_key(affinity_key))

    def build_queues(self, *, include_default: bool = False) -> Tuple[Queue, ...]:
        queues: list[Queue] = []
        if include_default:
            queues.append(
                Queue(
                    self.default_queue,
                    exchange=self.default_exchange,
                    routing_key=settings.celery.default_routing_key,
                    durable=True,
                )
            )
        for name in self.queue_names():
            queues.append(
                Queue(
                    name,
                    exchange=self.default_exchange,
                    routing_key=name,
                    durable=True,
                )
            )
        return tuple(queues)


def get_codex_shard_router(shard_count: int | None = None) -> CodexShardRouter:
    count = shard_count or settings.spec_workflow.codex_shards
    return CodexShardRouter(shard_count=count)


def iter_codex_queue_names(shard_count: int | None = None) -> Iterable[str]:
    router = get_codex_shard_router(shard_count)
    return router.queue_names()


def build_task_router(
    router: CodexShardRouter,
) -> Tuple[Callable[[str, tuple, dict, Mapping[str, Any]], Mapping[str, Any]], ...]:
    def _route_task(
        name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        options: Mapping[str, Any],
        task: Any = None,
        **_extra: Any,
    ) -> Mapping[str, Any]:
        queue = options.get("queue")
        routing_key = options.get("routing_key")
        if queue:
            return {
                "queue": queue,
                "routing_key": routing_key or queue,
            }

        headers = options.get("headers") or {}
        header_queue = headers.get(CODEX_QUEUE_HEADER)
        if header_queue:
            return {"queue": header_queue, "routing_key": header_queue}

        affinity = headers.get(CODEX_AFFINITY_HEADER) or kwargs.get(
            CODEX_AFFINITY_HEADER
        )

        if affinity and _is_codex_task(name):
            queue_name = router.queue_for_key(str(affinity))
            return {"queue": queue_name, "routing_key": queue_name}

        return {
            "queue": settings.celery.default_queue,
            "routing_key": settings.celery.default_routing_key,
        }

    return (_route_task,)


def _is_codex_task(task_name: str) -> bool:
    return task_name.endswith(".submit_codex_job") or task_name.endswith(
        ".apply_and_publish"
    )


__all__ = [
    "CODEX_QUEUE_PREFIX",
    "CODEX_QUEUE_HEADER",
    "CODEX_AFFINITY_HEADER",
    "CodexShardRouter",
    "get_codex_shard_router",
    "iter_codex_queue_names",
    "build_task_router",
]
