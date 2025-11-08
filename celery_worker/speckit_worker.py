"""Celery worker entrypoint for Spec Kit workflows."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

from moonmind.workflows.speckit_celery import celery_app as speckit_celery_app

logger = logging.getLogger(__name__)


def _log_codex_cli_version() -> None:
    """Emit a startup log confirming the bundled Codex CLI version."""

    codex_path = shutil.which("codex")
    if not codex_path or not os.access(codex_path, os.X_OK):
        message = (
            "Codex CLI is not available on PATH; rebuild the automation image to"
            " include the bundled CLI."
        )
        logger.critical(message, extra={"codex_path": codex_path})
        raise RuntimeError(message)

    try:
        result = subprocess.run(
            ["codex", "--version"],
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
    version_output = raw_output.splitlines()[0] if raw_output else "unknown"
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
