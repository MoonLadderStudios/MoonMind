"""StatsD adapter for Codex worker self-heal instrumentation."""

from __future__ import annotations

from typing import Any, Mapping, MutableMapping

from moonmind.workflows.speckit_celery.tasks import _MetricsEmitter


class WorkerMetrics:
    """Thin wrapper around the shared Spec workflow StatsD emitter."""

    def __init__(self, emitter: _MetricsEmitter | None = None) -> None:
        self._emitter = emitter or _MetricsEmitter()

    @property
    def enabled(self) -> bool:
        return self._emitter.enabled

    def record_self_heal_attempt(
        self,
        *,
        step_index: int,
        attempt: int,
        failure_class: str | None,
        strategy: str | None,
        outcome: str,
    ) -> None:
        """Increment attempt counters for a self-heal attempt."""

        tags = self._step_tags(
            step_index=step_index,
            attempt=attempt,
            extras={
                "class": failure_class,
                "strategy": strategy,
                "outcome": outcome,
            },
        )
        self._emitter.increment("task.self_heal.attempts_total", tags=tags)

    def record_self_heal_recovered(
        self,
        *,
        step_index: int,
        attempt: int,
        failure_class: str | None,
        strategy: str | None,
    ) -> None:
        tags = self._step_tags(
            step_index=step_index,
            attempt=attempt,
            extras={"class": failure_class, "strategy": strategy},
        )
        self._emitter.increment("task.self_heal.recovered_total", tags=tags)

    def record_self_heal_exhausted(
        self,
        *,
        step_index: int,
        attempt: int,
        failure_class: str | None,
        strategy: str | None,
    ) -> None:
        tags = self._step_tags(
            step_index=step_index,
            attempt=attempt,
            extras={"class": failure_class, "strategy": strategy},
        )
        self._emitter.increment("task.self_heal.exhausted_total", tags=tags)

    def record_step_duration(
        self,
        *,
        step_index: int,
        attempt: int,
        duration_seconds: float,
    ) -> None:
        tags = self._step_tags(step_index=step_index, attempt=attempt)
        self._emitter.observe(
            "task.step.duration_seconds", value=duration_seconds, tags=tags
        )

    def record_wall_timeout(
        self,
        *,
        step_index: int,
        attempt: int,
    ) -> None:
        tags = self._step_tags(step_index=step_index, attempt=attempt)
        self._emitter.increment("task.step.wall_timeout_total", tags=tags)

    def record_idle_timeout(
        self,
        *,
        step_index: int,
        attempt: int,
    ) -> None:
        tags = self._step_tags(step_index=step_index, attempt=attempt)
        self._emitter.increment("task.step.idle_timeout_total", tags=tags)

    def record_no_progress_trip(
        self,
        *,
        step_index: int,
        attempt: int,
    ) -> None:
        tags = self._step_tags(step_index=step_index, attempt=attempt)
        self._emitter.increment("task.step.no_progress_total", tags=tags)

    def _step_tags(
        self,
        *,
        step_index: int | None = None,
        attempt: int | None = None,
        extras: Mapping[str, Any] | None = None,
    ) -> MutableMapping[str, Any]:
        tags: MutableMapping[str, Any] = {}
        if step_index is not None:
            tags["step"] = step_index
        if attempt is not None:
            tags["attempt"] = attempt
        if extras:
            for key, value in extras.items():
                if value is not None:
                    tags[key] = value
        return tags


__all__ = ["WorkerMetrics"]
