"""Celery worker entrypoint for Spec Kit workflows."""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

from celery_worker.startup_checks import (
    validate_embedding_runtime_profile,
    validate_shared_skills_mirror,
)
from moonmind.config.settings import settings
from moonmind.workflows.speckit_celery import celery_app as speckit_celery_app
from moonmind.workflows.speckit_celery import models as speckit_models
from moonmind.workflows.speckit_celery import tasks as speckit_tasks
from moonmind.workflows.speckit_celery.utils import (
    CliVerificationError,
    verify_cli_is_executable,
)

logger = logging.getLogger(__name__)

try:  # pragma: no branch - import guard required for health enforcement
    from api_service.scripts import ensure_codex_config as codex_config
except ImportError as exc:  # pragma: no cover - import failure should abort start
    logger.critical(
        "Codex config enforcement script missing from image: %s",
        exc,
    )
    raise RuntimeError("Codex config enforcement script unavailable") from exc


def _assert_policy_locked(
    result: codex_config.CodexConfigResult, *, context: dict[str, str]
) -> None:
    approval_policy = result.config.get("approval_policy")
    if approval_policy != "never":
        logger.critical(
            "Codex approval policy is %r (expected 'never')",
            approval_policy,
            extra={
                **context,
                "codex_config_path": str(result.path),
                "approval_policy": approval_policy,
            },
        )
        raise RuntimeError("Codex approval policy is not enforced")

    logger.info(
        "Codex approval policy enforced at %s",
        result.path,
        extra={
            **context,
            "codex_config_path": str(result.path),
            "approval_policy": approval_policy,
        },
    )


def _enforce_codex_approval_policy() -> Path:
    """Run the merge script and confirm the policy is locked to ``never``."""

    try:
        home_result = codex_config.ensure_codex_config()
    except codex_config.CodexConfigError as exc:
        logger.critical(
            "Failed to enforce Codex approval policy in worker home: %s",
            exc,
        )
        raise RuntimeError("Codex approval policy enforcement failed") from exc

    _assert_policy_locked(home_result, context={"target": "worker_home"})

    volume_name = settings.spec_workflow.codex_volume_name
    if volume_name:
        volume_mount = Path(os.environ.get("CODEX_VOLUME_PATH", "/var/lib/codex-auth"))
        if not volume_mount.exists():
            logger.critical(
                "Codex auth volume %s is not mounted at %s",
                volume_name,
                volume_mount,
                extra={
                    "codex_volume_name": volume_name,
                    "codex_volume_mount": str(volume_mount),
                },
            )
            raise RuntimeError("Codex auth volume is unavailable")

        target_path = volume_mount / codex_config.CONFIG_FILENAME
        try:
            volume_result = codex_config.ensure_codex_config(target_path=target_path)
        except codex_config.CodexConfigError as exc:
            logger.critical(
                "Failed to enforce Codex approval policy on volume %s: %s",
                volume_name,
                exc,
                extra={
                    "codex_volume_name": volume_name,
                    "codex_volume_mount": str(volume_mount),
                },
            )
            raise RuntimeError("Codex approval policy enforcement failed") from exc

        _assert_policy_locked(
            volume_result,
            context={
                "target": "codex_volume",
                "codex_volume_name": volume_name,
                "codex_volume_mount": str(volume_mount),
            },
        )

        os.environ.setdefault("CODEX_CONFIG_HOME", str(volume_mount))
        os.environ.setdefault(
            "CODEX_CONFIG_PATH", str(volume_mount / codex_config.CONFIG_FILENAME)
        )
        logger.info(
            "Codex auth volume configured",
            extra={
                "codex_volume_name": volume_name,
                "codex_volume_mount": str(volume_mount),
                "codex_config_home": os.environ.get("CODEX_CONFIG_HOME"),
                "codex_config_path": os.environ.get("CODEX_CONFIG_PATH"),
            },
        )

    return home_result.path


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


def _log_speckit_cli_version() -> None:
    """Emit a startup log confirming the bundled Spec Kit CLI version."""

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

    task_queues = getattr(speckit_celery_app.conf, "task_queues", None) or ()
    queue_names = tuple(queue.name for queue in task_queues)
    if not queue_names:
        fallback_queue = (
            settings.spec_workflow.codex_queue or settings.celery.default_queue
        )
        queue_names = (fallback_queue,)

    queue_csv = ",".join(queue_names)
    os.environ.setdefault("CELERY_QUEUES", queue_csv)
    logger.info(
        "SpecKit worker consuming Celery queues: %s",
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
        worker_name="codex",
        default_provider=settings.default_embedding_provider,
        default_model=settings.google.google_embedding_model,
        google_api_key=os.environ.get("GOOGLE_API_KEY")
        or settings.google.google_api_key,
        gemini_api_key=os.environ.get("GEMINI_API_KEY"),
        logger=logger,
    )


def _run_codex_preflight_check() -> None:
    """Validate Codex authentication before accepting Celery tasks."""

    try:
        result = speckit_tasks.run_codex_preflight_check()
    except Exception as exc:  # pragma: no cover - startup should fail fast
        logger.critical("Codex pre-flight execution failed: %s", exc)
        raise RuntimeError("Codex pre-flight check failed to execute") from exc

    if result.status is speckit_models.CodexPreflightStatus.FAILED:
        logger.critical(
            "Codex pre-flight failed for volume %s",
            result.volume,
            extra={
                "codex_volume": result.volume,
                "codex_preflight_status": result.status.value,
                "codex_preflight_exit_code": result.exit_code,
                "codex_preflight_message": result.message,
            },
        )
        raise RuntimeError(result.message or "Codex pre-flight failed")

    log_message = result.message or "Codex pre-flight check completed"
    logger.info(
        log_message,
        extra={
            "codex_volume": result.volume,
            "codex_preflight_status": result.status.value,
            "codex_preflight_exit_code": result.exit_code,
        },
    )


def _validate_shared_skills_profile() -> None:
    """Validate local shared-skills mirror when strict mode is configured."""

    validate_shared_skills_mirror(
        worker_name="codex",
        mirror_root=settings.spec_workflow.skills_local_mirror_root,
        strict=settings.spec_workflow.skills_validate_local_mirror,
        logger=logger,
    )


celery_app = speckit_celery_app

# Celery uses the module-level ``app`` attribute as the default application target
# when running ``celery -A celery_worker.speckit_worker worker``.
app = celery_app

_enforce_codex_approval_policy()
_log_codex_cli_version()
_log_speckit_cli_version()
_log_queue_configuration()
_validate_embedding_profile()
_validate_shared_skills_profile()
_run_codex_preflight_check()


if __name__ == "__main__":  # pragma: no cover - CLI execution path
    celery_app.start()
