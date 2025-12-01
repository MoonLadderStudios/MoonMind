"""Celery worker entrypoint for Gemini workflows."""

from __future__ import annotations

import logging
import subprocess

# Register Gemini tasks
import celery_worker.gemini_tasks  # noqa: F401
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
        logger.warning(str(exc), extra={"gemini_path": exc.cli_path})
        # If the CLI is critical for this worker, we should raise.
        # Given it is a gemini worker, it is critical.
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
    logger.info(
        "Gemini CLI detected at %s (version: %s)",
        gemini_path,
        raw_output,
        extra={"gemini_path": gemini_path, "gemini_version": raw_output},
    )


celery_app = speckit_celery_app

# Celery uses the module-level ``app`` attribute as the default application target
# when running ``celery -A celery_worker.gemini_worker worker``.
app = celery_app

_log_gemini_cli_version()


if __name__ == "__main__":  # pragma: no cover - CLI execution path
    celery_app.start()
