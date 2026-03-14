"""Shared StatsD metrics emitter for MoonMind workflow instrumentation."""

from __future__ import annotations

import logging
import os
import re
import socket
import time
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)


class _MetricsEmitter:
    """Best-effort StatsD emitter used for workflow task instrumentation."""

    def __init__(self) -> None:
        prefix = (
            os.getenv("WORKFLOW_METRICS_PREFIX")
            or os.getenv("WORKFLOW_METRICS_PREFIX")
            or "moonmind.workflow"
        )
        self._prefix = prefix.rstrip(".")
        host = os.getenv("WORKFLOW_METRICS_HOST") or os.getenv(
            "WORKFLOW_METRICS_HOST", os.getenv("STATSD_HOST")
        )
        port = os.getenv("WORKFLOW_METRICS_PORT") or os.getenv(
            "WORKFLOW_METRICS_PORT", os.getenv("STATSD_PORT", "8125")
        )
        self._configured = bool(host)
        self._enabled = self._configured
        self._address: tuple[str, int] | None = None
        self._socket: socket.socket | None = None
        self._failure_count = 0
        self._disabled_until: float | None = None
        self._base_backoff = 5.0
        self._max_backoff = 60.0

        if self._configured:
            self._address = (str(host), int(port))
            self._open_socket()
            logger.info(
                "Spec workflow StatsD emitter configured",
                extra={"metrics_host": host, "metrics_prefix": self._prefix},
            )
        else:
            logger.debug(
                "Spec workflow StatsD emitter disabled (no host configured)",
                extra={"metrics_prefix": self._prefix},
            )

    @property
    def enabled(self) -> bool:
        return self._configured and self._enabled

    def _open_socket(self) -> None:
        if not self._address:
            return
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        except OSError as exc:
            self._socket = None
            self._enabled = False
            self._disabled_until = time.monotonic() + self._base_backoff
            logger.warning(
                "Failed to initialize Spec workflow metrics socket: %s", exc
            )

    def _close_socket(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            finally:
                self._socket = None

    @staticmethod
    def _format_tags(tags: Optional[Mapping[str, Any]]) -> str:
        if not tags:
            return ""
        parts: list[str] = []
        for key, raw_value in tags.items():
            if raw_value is None:
                continue
            safe_key = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(key))
            safe_value = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(raw_value))
            parts.append(f"{safe_key}:{safe_value}")
        if not parts:
            return ""
        return "|#" + ",".join(parts)

    def _send(self, metric: str) -> None:
        if not self._configured:
            return
        if self._disabled_until:
            if time.monotonic() < self._disabled_until:
                return
            self._disabled_until = None
            self._enabled = True
            self._open_socket()
        if not self._socket or not self._address or not self._enabled:
            return
        try:
            self._socket.sendto(metric.encode("utf-8"), self._address)
            self._failure_count = 0
        except OSError as exc:
            self._close_socket()
            self._failure_count += 1
            backoff = min(
                self._base_backoff * (2 ** (self._failure_count - 1)), self._max_backoff
            )
            self._disabled_until = time.monotonic() + backoff
            logger.warning(
                "Disabling Spec workflow metrics emission for %.1fs after socket error: %s",
                backoff,
                exc,
            )

    def increment(
        self, metric: str, *, value: int = 1, tags: Optional[Mapping[str, Any]] = None
    ) -> None:
        if not self.enabled:
            return
        formatted_tags = self._format_tags(tags)
        payload = f"{self._prefix}.{metric}:{value}|c{formatted_tags}"
        self._send(payload)

    def observe(
        self, metric: str, *, value: float, tags: Optional[Mapping[str, Any]] = None
    ) -> None:
        if not self.enabled:
            return
        formatted_tags = self._format_tags(tags)
        payload = f"{self._prefix}.{metric}:{value * 1000:.6f}|ms{formatted_tags}"
        self._send(payload)


__all__ = ["_MetricsEmitter"]
