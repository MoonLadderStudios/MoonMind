"""Telemetry helpers for RAG vector actions."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from moonmind.workflows.orchestrator.metrics import get_metrics_client

logger = logging.getLogger(__name__)


class VectorTelemetry:
    """Best-effort telemetry tracker that emits StatsD metrics and structured logs."""

    def __init__(self, *, run_id: Optional[str], job_id: Optional[str]) -> None:
        self._metrics = get_metrics_client()
        self._run_id = run_id
        self._job_id = job_id

    def record(self, event: str, *, count: int = 1, **extra: Any) -> None:
        metric_base = f"rag.{event}"
        self._metrics.increment(f"{metric_base}.count", value=count)
        metadata = {"event": event, "run_id": self._run_id, "job_id": self._job_id}
        metadata.update(extra)
        logger.info("RAG telemetry", extra=metadata)

    def timing(self, event: str, *, milliseconds: float, **extra: Any) -> None:
        metric = f"rag.{event}.latency_ms"
        self._metrics.timing(metric, milliseconds)
        self.record(event, duration_ms=milliseconds, **extra)

    @contextmanager
    def timer(self, event: str, **extra: Any) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            self.timing(event, milliseconds=duration_ms, **extra)
