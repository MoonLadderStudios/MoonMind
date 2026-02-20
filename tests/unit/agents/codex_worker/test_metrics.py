"""Unit tests for the worker metrics adapter."""

from __future__ import annotations

from moonmind.agents.codex_worker.metrics import WorkerMetrics


class FakeEmitter:
    def __init__(self) -> None:
        self.enabled = True
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def increment(
        self, metric: str, *, value: int = 1, tags: dict[str, object] | None = None
    ) -> None:
        self.calls.append(("increment", metric, {"value": value, "tags": tags or {}}))

    def observe(
        self, metric: str, *, value: float, tags: dict[str, object] | None = None
    ) -> None:
        self.calls.append(("observe", metric, {"value": value, "tags": tags or {}}))


def test_record_self_heal_attempt_emits_counter() -> None:
    """Self-heal attempt metrics should forward tags to the emitter."""

    emitter = FakeEmitter()
    metrics = WorkerMetrics(emitter=emitter)

    metrics.record_self_heal_attempt(
        step_index=2,
        attempt=1,
        failure_class="transient_runtime",
        strategy="soft_reset",
        outcome="triggered",
    )

    assert emitter.calls == [
        (
            "increment",
            "task.self_heal.attempts_total",
            {
                "value": 1,
                "tags": {
                    "step": 2,
                    "attempt": 1,
                    "class": "transient_runtime",
                    "strategy": "soft_reset",
                    "outcome": "triggered",
                },
            },
        )
    ]


def test_record_step_duration_emits_timer() -> None:
    """Step duration metrics should use observe with milliseconds conversion."""

    emitter = FakeEmitter()
    metrics = WorkerMetrics(emitter=emitter)

    metrics.record_step_duration(step_index=0, attempt=3, duration_seconds=1.25)

    assert emitter.calls == [
        (
            "observe",
            "task.step.duration_seconds",
            {"value": 1.25, "tags": {"step": 0, "attempt": 3}},
        )
    ]
