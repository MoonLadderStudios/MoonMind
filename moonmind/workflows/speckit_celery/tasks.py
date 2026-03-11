"""Spec Kit tasks module (compatibility shim - Celery removed).

This module provides the non-Celery utilities that are still needed by other
parts of the codebase after Celery was removed from MoonMind in PR #683.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
from dataclasses import dataclass
from typing import Optional

from moonmind.workflows.speckit_celery import models

logger = logging.getLogger(__name__)

# Task name constants (preserved for compatibility)
TASK_DISCOVER = "discover_next_phase"
TASK_SUBMIT = "submit_codex_job"
TASK_PUBLISH = "apply_and_publish"
TASK_SEQUENCE: tuple[str, ...] = (TASK_DISCOVER, TASK_SUBMIT, TASK_PUBLISH)


@dataclass
class CodexPreflightResult:
    """Outcome of a Codex login status check."""

    status: models.CodexPreflightStatus
    message: str
    volume: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""


class _MetricsEmitter:
    """Best-effort StatsD emitter used for workflow task instrumentation."""

    def __init__(self) -> None:
        prefix = (
            os.getenv("WORKFLOW_METRICS_PREFIX")
            or os.getenv("SPEC_WORKFLOW_METRICS_PREFIX")
            or "moonmind.spec_workflow"
        )
        self._prefix = prefix.rstrip(".")
        host = os.getenv("WORKFLOW_METRICS_HOST") or os.getenv(
            "SPEC_WORKFLOW_METRICS_HOST", os.getenv("STATSD_HOST")
        )
        port = os.getenv("WORKFLOW_METRICS_PORT") or os.getenv(
            "SPEC_WORKFLOW_METRICS_PORT", os.getenv("STATSD_PORT", "8125")
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
            logger.warning("StatsD socket creation failed: %s", exc)
            self._configured = False

    def _send(self, metric: str) -> None:
        if not self._enabled or not self._socket or not self._address:
            return
        try:
            self._socket.sendto(metric.encode("utf-8"), self._address)
        except OSError:
            self._failure_count += 1

    def increment(self, name: str, value: int = 1, tags: dict | None = None) -> None:
        self._send(f"{self._prefix}.{name}:{value}|c")

    def timing(self, name: str, ms: float, tags: dict | None = None) -> None:
        self._send(f"{self._prefix}.{name}:{ms:.3f}|ms")


def _run_codex_preflight_check(
    *, volume_name: str | None = None, timeout: int = 60
) -> CodexPreflightResult:
    """Execute the Codex login status check by running a subprocess."""

    volume = volume_name or ""
    if not volume:
        return CodexPreflightResult(
            status=models.CodexPreflightStatus.FAILED,
            message="No Codex auth volume specified.",
            volume=volume,
        )

    try:
        cmd = ["codex", "login", "--check"]
        if volume:
            cmd += ["--volume", volume]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        exit_code = result.returncode
        stdout = result.stdout or ""
        stderr = result.stderr or ""
    except FileNotFoundError:
        return CodexPreflightResult(
            status=models.CodexPreflightStatus.FAILED,
            message="Codex CLI not found — authentication check unavailable.",
            volume=volume,
        )
    except subprocess.TimeoutExpired:
        return CodexPreflightResult(
            status=models.CodexPreflightStatus.FAILED,
            message=f"Codex login status check timed out after {timeout} seconds.",
            volume=volume,
        )

    if exit_code == 0:
        return CodexPreflightResult(
            status=models.CodexPreflightStatus.PASSED,
            message="Codex authentication check passed.",
            volume=volume,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )
    return CodexPreflightResult(
        status=models.CodexPreflightStatus.FAILED,
        message=f"Codex authentication check failed (exit code {exit_code}).",
        volume=volume,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
    )


def run_codex_preflight_check(
    *, volume_name: str | None = None, timeout: int = 60
) -> CodexPreflightResult:
    """Public wrapper to execute the Codex login status check."""

    return _run_codex_preflight_check(timeout=timeout, volume_name=volume_name)


__all__ = [
    "TASK_DISCOVER",
    "TASK_PUBLISH",
    "TASK_SEQUENCE",
    "TASK_SUBMIT",
    "CodexPreflightResult",
    "_MetricsEmitter",
    "run_codex_preflight_check",
]
