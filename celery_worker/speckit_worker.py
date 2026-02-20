"""Celery worker entrypoint for Spec Kit workflows."""

from __future__ import annotations

import logging
import os
import re
import subprocess
from importlib import import_module
from pathlib import Path

from celery_worker.runtime_mode import resolve_worker_runtime
from celery_worker.startup_checks import (
    validate_embedding_runtime_profile,
    validate_shared_skills_mirror,
)
from moonmind.config.settings import settings
from moonmind.workflows.skills.registry import (
    configured_stage_skills,
    configured_stage_skills_require_speckit,
)
from moonmind.workflows.speckit_celery import celery_app as speckit_celery_app
from moonmind.workflows.speckit_celery import models as speckit_models
from moonmind.workflows.speckit_celery import tasks as speckit_tasks
from moonmind.workflows.speckit_celery.utils import (
    CliVerificationError,
    verify_cli_is_executable,
)

logger = logging.getLogger(__name__)

_SELF_HEAL_ENV_DEFAULTS: dict[str, str] = {
    "STEP_MAX_ATTEMPTS": "3",
    "STEP_TIMEOUT_SECONDS": "900",
    "STEP_IDLE_TIMEOUT_SECONDS": "300",
    "STEP_NO_PROGRESS_LIMIT": "2",
    "JOB_SELF_HEAL_MAX_RESETS": "1",
}

# Register Gemini tasks so all shared-queue workers recognize task names.
try:
    import_module("celery_worker.gemini_tasks")
except ImportError as exc:  # pragma: no cover - import failure should abort start
    logger.critical(
        "Failed to import celery_worker.gemini_tasks at worker startup: %s",
        exc,
    )
    raise RuntimeError("Gemini task registration failed") from exc

codex_config = None
_codex_config_import_error: ImportError | None = None
try:  # pragma: no branch - optional import gated by runtime mode
    from api_service.scripts import ensure_codex_config as codex_config
except ImportError as exc:  # pragma: no cover - handled when codex policy is required
    _codex_config_import_error = exc


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

    if codex_config is None:
        logger.critical(
            "Codex config enforcement requested, but script is unavailable: %s",
            _codex_config_import_error,
        )
        raise RuntimeError("Codex config enforcement script unavailable") from (
            _codex_config_import_error
        )

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

    _log_cli_version(
        "codex", label="Codex", failure_message="Codex CLI health check failed"
    )


def _log_gemini_cli_version() -> None:
    """Emit a startup log confirming the bundled Gemini CLI version."""

    _log_cli_version(
        "gemini",
        label="Gemini",
        failure_message="Gemini CLI health check failed",
    )


def _log_claude_cli_version() -> None:
    """Emit a startup log confirming the bundled Claude CLI version."""

    _log_cli_version(
        "claude",
        label="Claude",
        failure_message="Claude CLI health check failed",
    )


def _log_speckit_cli_version() -> None:
    """Emit a startup log confirming the bundled Spec Kit CLI version."""

    _log_cli_version(
        "speckit",
        label="Spec Kit",
        failure_message="Spec Kit CLI health check failed",
    )


def _configure_self_heal_env_defaults() -> None:
    """Ensure self-heal budget env vars have documented defaults."""

    applied: dict[str, str] = {}
    for key, value in _SELF_HEAL_ENV_DEFAULTS.items():
        if key not in os.environ or not os.environ[key].strip():
            os.environ[key] = value
            applied[key] = value

    if applied:
        logger.info(
            "Applied worker self-heal budget defaults",
            extra={"self_heal_defaults": applied},
        )


def _log_cli_version(cli_name: str, *, label: str, failure_message: str) -> None:
    """Emit a startup log confirming the bundled CLI version."""

    try:
        cli_path = verify_cli_is_executable(cli_name)
    except CliVerificationError as exc:
        logger.critical(str(exc), extra={f"{cli_name}_path": exc.cli_path})
        raise RuntimeError(str(exc)) from exc

    try:
        result = subprocess.run(
            [cli_path, "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        logger.critical(
            "Failed to execute '%s --version': %s",
            cli_name,
            exc,
            extra={f"{cli_name}_path": cli_path},
        )
        raise RuntimeError(failure_message) from exc

    raw_output = result.stdout.strip() or result.stderr.strip()
    version_match = re.search(r"\d+\.\d+\.\d+", raw_output)
    version_output = version_match.group(0) if version_match else "unknown"
    logger.info(
        "%s CLI detected at %s (version: %s)",
        label,
        cli_path,
        version_output,
        extra={
            f"{cli_name}_path": cli_path,
            f"{cli_name}_version": version_output,
        },
    )


def _configure_worker_runtime() -> str:
    """Resolve runtime mode and publish normalized runtime environment values."""

    runtime, ai_cli = resolve_worker_runtime(default_runtime="codex")
    os.environ["MOONMIND_WORKER_RUNTIME"] = runtime
    os.environ["MOONMIND_AI_CLI"] = ai_cli
    logger.info(
        "Worker runtime mode resolved: %s",
        runtime,
        extra={"worker_runtime": runtime, "worker_ai_cli": ai_cli},
    )
    return runtime


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
        legacy_mirror_root=settings.spec_workflow.skills_legacy_mirror_root,
        repo_root=settings.spec_workflow.repo_root,
        strict=settings.spec_workflow.skills_validate_local_mirror,
        logger=logger,
    )


celery_app = speckit_celery_app

# Celery uses the module-level ``app`` attribute as the default application target
# when running ``celery -A celery_worker.speckit_worker worker``.
app = celery_app

_runtime_mode = _configure_worker_runtime()
_configure_self_heal_env_defaults()
if _runtime_mode in {"codex", "universal"}:
    _enforce_codex_approval_policy()
else:
    logger.info(
        "Skipping Codex approval policy enforcement for runtime mode '%s'",
        _runtime_mode,
        extra={"worker_runtime": _runtime_mode},
    )
_log_codex_cli_version()
_log_gemini_cli_version()
_log_claude_cli_version()
if configured_stage_skills_require_speckit():
    _log_speckit_cli_version()
else:
    logger.info(
        "Skipping Spec Kit CLI health check because configured stage skills do not require Speckit.",
        extra={"configured_stage_skills": configured_stage_skills()},
    )
_log_queue_configuration()
_validate_embedding_profile()
_validate_shared_skills_profile()
if _runtime_mode in {"codex", "universal"}:
    _run_codex_preflight_check()
else:
    logger.info(
        "Skipping Codex pre-flight check for runtime mode '%s'",
        _runtime_mode,
        extra={"worker_runtime": _runtime_mode},
    )


if __name__ == "__main__":  # pragma: no cover - CLI execution path
    celery_app.start()
