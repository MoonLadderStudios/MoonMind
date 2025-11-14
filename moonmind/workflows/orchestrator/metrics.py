"""StatsD instrumentation utilities for the orchestrator worker."""

from __future__ import annotations

import atexit
import logging
import os
import socket
import threading
import time
from contextlib import contextmanager
from typing import Iterator, Optional

logger = logging.getLogger(__name__)


class MetricsClient:
    """Lightweight StatsD client that degrades gracefully when misconfigured."""

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        prefix: str | None = None,
    ) -> None:
        env_host = host or os.getenv("ORCHESTRATOR_STATSD_HOST") or os.getenv("STATSD_HOST")
        env_port = port or os.getenv("ORCHESTRATOR_STATSD_PORT") or os.getenv("STATSD_PORT")
        env_prefix = prefix or os.getenv(
            "ORCHESTRATOR_METRICS_PREFIX", "moonmind.orchestrator"
        )

        self._prefix = env_prefix.rstrip(".")
        self._address: tuple[str, int] | None = None
        self._socket: socket.socket | None = None
        self._lock = threading.Lock()
        self._enabled = False
        self._disabled_until: float | None = None
        self._initial_backoff_seconds = 5.0
        self._backoff_seconds = self._initial_backoff_seconds
        self._max_backoff_seconds = 60.0

        if env_host:
            self._address = (str(env_host), int(env_port or 8125))
            self._initialize_socket()
        else:
            logger.debug("Orchestrator metrics disabled; no StatsD host configured")

    @property
    def enabled(self) -> bool:
        """Return ``True`` when metrics emission is active."""

        if self._address is None:
            return False
        if self._disabled_until is not None and time.monotonic() >= self._disabled_until:
            self._disabled_until = None
            self._initialize_socket()
        return self._enabled and self._socket is not None

    def increment(self, metric: str, value: int = 1) -> None:
        """Emit a counter increment for ``metric``."""

        self._send(metric, f"{int(value)}|c")

    def gauge(self, metric: str, value: float | int) -> None:
        """Emit a gauge value for ``metric``."""

        self._send(metric, f"{value}|g")

    def timing(self, metric: str, milliseconds: float) -> None:
        """Emit a timing measurement for ``metric``."""

        self._send(metric, f"{milliseconds:.4f}|ms")

    @contextmanager
    def timer(self, metric: str) -> Iterator[None]:
        """Context manager that records elapsed milliseconds for ``metric``."""

        start = time.perf_counter()
        try:
            yield
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            self.timing(metric, duration_ms)

    def _send(self, metric: str, payload: str) -> None:
        if not self.enabled:
            return

        message = f"{self._prefix}.{metric}:{payload}".encode("utf-8")
        with self._lock:
            if not self.enabled:
                return
            assert self._socket is not None and self._address is not None
            try:
                self._socket.sendto(message, self._address)
                self._backoff_seconds = self._initial_backoff_seconds
            except OSError as exc:
                logger.warning(
                    "Failed to emit orchestrator metric %s due to %s",
                    metric,
                    exc.__class__.__name__,
                )
                self._schedule_retry()

    def close(self) -> None:
        """Close the underlying socket if it is open."""

        with self._lock:
            if self._socket is not None:
                self._socket.close()
                self._socket = None
            self._enabled = False
            self._disabled_until = None

    def _initialize_socket(self) -> None:
        if self._address is None:
            return
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        except OSError as exc:
            logger.warning(
                "Failed to initialize orchestrator metrics socket due to %s",
                exc.__class__.__name__,
            )
            self._schedule_retry()
            return
        self._enabled = True
        self._backoff_seconds = self._initial_backoff_seconds

    def _schedule_retry(self) -> None:
        if self._socket is not None:
            self._socket.close()
        self._socket = None
        self._enabled = False
        self._disabled_until = time.monotonic() + self._backoff_seconds
        self._backoff_seconds = min(
            self._backoff_seconds * 2, self._max_backoff_seconds
        )


_metrics: Optional[MetricsClient] = None


def get_metrics_client() -> MetricsClient:
    """Return a module-level metrics client instance."""

    global _metrics
    if _metrics is None:
        _metrics = MetricsClient()
        atexit.register(_metrics.close)
    return _metrics


__all__ = ["MetricsClient", "get_metrics_client"]
