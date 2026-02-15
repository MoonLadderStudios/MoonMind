"""Celery worker entrypoint for Gemini workflows."""

from __future__ import annotations

import logging
import os
import re
import subprocess
from importlib import import_module

from celery_worker.startup_checks import (
    validate_embedding_runtime_profile,
    validate_shared_skills_mirror,
)
from moonmind.config.settings import settings
from moonmind.workflows.speckit_celery import celery_app as speckit_celery_app
from moonmind.workflows.speckit_celery.utils import (
    CliVerificationError,
    verify_cli_is_executable,
)

logger = logging.getLogger(__name__)

# Register Gemini tasks via import side effect
import_module("celery_worker.gemini_tasks")


def _log_gemini_cli_version() -> None:
    """Emit a startup log confirming the bundled Gemini CLI version."""

    try:
        gemini_path = verify_cli_is_executable("gemini")
    except CliVerificationError as exc:
        logger.critical(str(exc), extra={"gemini_path": exc.cli_path})
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
    version_match = re.search(r"\d+\.\d+\.\d+", raw_output)
    version_output = version_match.group(0) if version_match else "unknown"
    logger.info(
        "Gemini CLI detected at %s (version: %s)",
        gemini_path,
        version_output,
        extra={"gemini_path": gemini_path, "gemini_version": version_output},
    )


def _log_speckit_cli_version() -> None:
    """Emit a startup log confirming Speckit capability is available."""

    try:
        speckit_path = verify_cli_is_executable("speckit")
    except CliVerificationError as exc:
        logger.critical(str(exc), extra={"speckit_path": exc.cli_path})
        raise RuntimeError(str(exc)) from exc

    try:
        result = subprocess.run(
            [speckit_path, "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        logger.critical(
            "Failed to execute 'speckit --version': %s",
            exc,
            extra={"speckit_path": speckit_path},
        )
        raise RuntimeError("Spec Kit CLI health check failed") from exc

    raw_output = result.stdout.strip() or result.stderr.strip()
    version_match = re.search(r"\d+\.\d+\.\d+", raw_output)
    version_output = version_match.group(0) if version_match else "unknown"
    logger.info(
        "Spec Kit CLI detected at %s (version: %s)",
        speckit_path,
        version_output,
        extra={"speckit_path": speckit_path, "speckit_version": version_output},
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


def _validate_embedding_profile() -> None:
    """Validate embedding startup prerequisites for the worker runtime."""

    validate_embedding_runtime_profile(
        worker_name="gemini",
        default_provider=settings.default_embedding_provider,
        default_model=settings.google.google_embedding_model,
        google_api_key=os.environ.get("GOOGLE_API_KEY")
        or settings.google.google_api_key,
        gemini_api_key=os.environ.get("GEMINI_API_KEY"),
        logger=logger,
    )


def _run_gemini_preflight_check() -> None:
    """Validate Gemini authentication before accepting Celery tasks."""

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        if settings.google.google_api_key:
            api_key = settings.google.google_api_key

    if not api_key:
        logger.critical("GEMINI_API_KEY is not set.")
        raise RuntimeError("GEMINI_API_KEY is not set.")

    gemini_home = os.environ.get("GEMINI_HOME")
    if gemini_home:
        if not os.path.isdir(gemini_home):
            logger.critical(
                "GEMINI_HOME is set to '%s' but it is not a directory.", gemini_home
            )
            raise RuntimeError(f"GEMINI_HOME directory does not exist: {gemini_home}")
        logger.info("Gemini pre-flight check: GEMINI_HOME=%s", gemini_home)
    else:
        logger.warning("GEMINI_HOME is not set; persistent config may not be active.")

    logger.info("Gemini pre-flight check completed.")


def _validate_shared_skills_profile() -> None:
    """Validate local shared-skills mirror when strict mode is configured."""

    validate_shared_skills_mirror(
        worker_name="gemini",
        mirror_root=settings.spec_workflow.skills_local_mirror_root,
        strict=settings.spec_workflow.skills_validate_local_mirror,
        logger=logger,
    )


celery_app = speckit_celery_app

# Celery uses the module-level ``app`` attribute as the default application target
# when running ``celery -A celery_worker.gemini_worker worker``.
app = celery_app

_log_gemini_cli_version()
_log_speckit_cli_version()
_log_queue_configuration()
_validate_embedding_profile()
_validate_shared_skills_profile()
_run_gemini_preflight_check()


if __name__ == "__main__":  # pragma: no cover - CLI execution path
    celery_app.start()
