"""Celery worker entrypoint for Gemini workflows."""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

from moonmind.config.settings import settings
from moonmind.workflows.speckit_celery import celery_app as speckit_celery_app
from moonmind.workflows.speckit_celery.utils import (
    CliVerificationError,
    verify_cli_is_executable,
)

logger = logging.getLogger(__name__)


def _log_gemini_cli_version() -> None:
    """Emit a startup log confirming the bundled Gemini CLI version."""

    try:
        gemini_path = verify_cli_is_executable("gemini")
    except CliVerificationError as exc:
        logger.critical(str(exc), extra={"gemini_path": exc.cli_path})
        raise RuntimeError(str(exc)) from exc

    try:
        result = subprocess.run(
            [gemini_path, "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        logger.critical(
            "Failed to execute 'gemini --version': %s",
            exc,
            extra={"gemini_path": gemini_path},
        )
        raise RuntimeError("Gemini CLI health check failed") from exc

    raw_output = result.stdout.strip() or result.stderr.strip()
    version_match = re.search(r"\d+\.\d+\.\d+", raw_output)
    version_output = version_match.group(0) if version_match else "unknown"
    logger.info(
        "Gemini CLI detected at %s (version: %s)",
        gemini_path,
        version_output,
        extra={"gemini_path": gemini_path, "gemini_version": version_output},
    )


def _log_queue_configuration() -> tuple[str, ...]:
    """Emit a log describing the Celery queues/QoS bindings for this worker."""

    gemini_queue = os.environ.get("GEMINI_CELERY_QUEUE", "gemini")
    queue_names = (gemini_queue,)

    queue_csv = ",".join(queue_names)
    os.environ.setdefault("CELERY_QUEUES", queue_csv)
    logger.info(
        "Gemini worker consuming Celery queues: %s",
        queue_csv,
        extra={
            "celery_queues": queue_names,
            "celery_prefetch": speckit_celery_app.conf.worker_prefetch_multiplier,
            "celery_reject_on_worker_lost": speckit_celery_app.conf.task_reject_on_worker_lost,
        },
    )
    return queue_names


def _run_gemini_preflight_check() -> None:
    """Validate Gemini authentication before accepting Celery tasks."""

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        if settings.google.google_api_key:
            api_key = settings.google.google_api_key

    if not api_key:
        logger.critical("GEMINI_API_KEY is not set.")
        raise RuntimeError("GEMINI_API_KEY is not set.")

    logger.info("Gemini pre-flight check completed.")


celery_app = speckit_celery_app
app = celery_app

_log_gemini_cli_version()
_log_queue_configuration()
_run_gemini_preflight_check()


if __name__ == "__main__":  # pragma: no cover - CLI execution path
    celery_app.start()
