"""Codex preflight check helpers for workflow automation.

Extracted from moonmind.workflows.agentkit_celery.tasks during the Celery removal.
This module has no Celery dependency.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from moonmind.config.settings import settings
from moonmind.workflows.automation import models

logger = logging.getLogger(__name__)

_PREFLIGHT_OUTPUT_ERROR_MARKERS = re.compile(
    r"(?:not logged in|authentication required|unauthorized|401|403|login expired)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class CodexPreflightResult:
    """Outcome of the Codex login status verification."""

    status: models.CodexPreflightStatus
    message: Optional[str] = None
    volume: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None


def _summarize_preflight_output(stdout: str, stderr: str) -> Optional[str]:
    """Return a one-line summary extracted from preflight output."""

    for line in (stderr or stdout or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return None


def _run_codex_preflight_check(
    *, timeout: int = 60, volume_name: str | None = None
) -> CodexPreflightResult:
    """Execute ``command -v rg && codex login status`` using the configured auth volume."""

    volume = volume_name or settings.workflow.codex_volume_name
    if not volume:
        logger.info(
            "Skipping Codex pre-flight check because no auth volume is configured",
        )
        return CodexPreflightResult(
            status=models.CodexPreflightStatus.SKIPPED,
            message="Codex auth volume not configured for this worker.",
        )

    try:
        import docker
        from docker.errors import APIError, DockerException
        from requests.exceptions import ReadTimeout
    except ImportError as exc:
        logger.warning("Docker SDK not available for preflight check: %s", exc)
        return CodexPreflightResult(
            status=models.CodexPreflightStatus.FAILED,
            message="Docker SDK unavailable for Codex preflight check.",
            volume=volume,
        )

    image = (
        settings.workflow.codex_login_check_image
        or settings.workflow.job_image
    )
    stdout = ""
    stderr = ""
    exit_code: Optional[int] = None
    client = None

    try:
        client = docker.from_env()
        container = client.containers.run(
            image,
            command=["bash", "-lc", "command -v rg && codex login status"],
            environment={"HOME": "/home/app"},
            volumes={volume: {"bind": "/home/app/.codex", "mode": "ro"}},
            detach=True,
            auto_remove=True,
            tty=False,
        )
        try:
            wait_result = container.wait(timeout=timeout)
            exit_code = int(wait_result.get("StatusCode", 1))
        except ReadTimeout:
            try:
                container.stop(timeout=5)
            except DockerException as stop_exc:
                logger.debug(
                    "Codex pre-flight container stop failed after timeout: %s",
                    stop_exc,
                    extra={"codex_volume": volume},
                )
            message = (
                f"Codex login status check timed out after {timeout} seconds for "
                f"volume '{volume}'."
            )
            logger.warning(
                "Codex pre-flight check timed out",
                extra={"codex_volume": volume, "timeout_seconds": timeout},
            )
            return CodexPreflightResult(
                status=models.CodexPreflightStatus.FAILED,
                message=message,
                volume=volume,
            )
        try:
            stdout_bytes = container.logs(stdout=True, stderr=False) or b""
            stderr_bytes = container.logs(stdout=False, stderr=True) or b""
        except (APIError, DockerException):
            stdout_bytes = b""
            stderr_bytes = b""
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
    except DockerException as exc:
        message = f"Unable to execute Codex login status for volume '{volume}': {exc}"
        logger.warning(
            "Codex pre-flight check failed to start",
            extra={"codex_volume": volume, "error": str(exc)},
        )
        return CodexPreflightResult(
            status=models.CodexPreflightStatus.FAILED,
            message=message,
            volume=volume,
        )
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # pragma: no cover - defensive
                pass

    summary = _summarize_preflight_output(stdout, stderr)
    if exit_code == 0:
        message = summary or "Codex login status check passed."
        logger.info(
            "Codex pre-flight check passed",
            extra={
                "codex_volume": volume,
                "codex_preflight_exit_code": exit_code,
                "codex_preflight_summary": summary,
            },
        )
        return CodexPreflightResult(
            status=models.CodexPreflightStatus.PASSED,
            message=message,
            volume=volume,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )

    remediation = (
        f"Codex login status failed for volume '{volume}'. "
        "Re-authenticate this shard using `codex login --device-auth` and retry."
    )
    if summary:
        remediation = f"{remediation} Details: {summary}"

    logger.warning(
        "Codex pre-flight check failed",
        extra={
            "codex_volume": volume,
            "codex_preflight_exit_code": exit_code,
            "codex_preflight_summary": summary,
        },
    )
    return CodexPreflightResult(
        status=models.CodexPreflightStatus.FAILED,
        message=remediation,
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


__all__ = ["CodexPreflightResult", "run_codex_preflight_check"]
