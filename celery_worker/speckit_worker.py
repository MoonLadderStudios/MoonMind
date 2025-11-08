"""Celery worker entrypoint for Spec Kit workflows."""

from __future__ import annotations

import logging
import subprocess
import re

from moonmind.workflows.speckit_celery import celery_app as speckit_celery_app
from moonmind.workflows.speckit_celery.utils import (
    CliVerificationError,
    verify_cli_is_executable,
)

logger = logging.getLogger(__name__)


def _log_codex_cli_version() -> None:
    """Emit a startup log confirming the bundled Codex CLI version."""

    try:
        codex_path = verify_cli_is_executable("codex")
    except CliVerificationError as exc:
        logger.critical(str(exc), extra={"codex_path": exc.cli_path})
        raise RuntimeError(str(exc)) from exc

    try:
        result = subprocess.run(
            [codex_path, "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        logger.critical(
            "Failed to execute 'codex --version': %s",
            exc,
            extra={"codex_path": codex_path},
        )
        raise RuntimeError("Codex CLI health check failed") from exc

    raw_output = result.stdout.strip() or result.stderr.strip()
    version_match = re.search(r"\d+\.\d+\.\d+", raw_output)
    version_output = version_match.group(0) if version_match else "unknown"
    logger.info(
        "Codex CLI detected at %s (version: %s)",
        codex_path,
        version_output,
        extra={"codex_path": codex_path, "codex_version": version_output},
    )


celery_app = speckit_celery_app

# Celery uses the module-level ``app`` attribute as the default application target
# when running ``celery -A celery_worker.speckit_worker worker``.
app = celery_app

_log_codex_cli_version()


if __name__ == "__main__":  # pragma: no cover - CLI execution path
    celery_app.start()
