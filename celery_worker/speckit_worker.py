"""Celery worker entrypoint for Spec Kit workflows."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

import toml

from moonmind.workflows.speckit_celery import celery_app as speckit_celery_app
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


def _enforce_codex_approval_policy() -> Path:
    """Run the merge script and confirm the policy is locked to ``never``."""

    try:
        config_path = codex_config.ensure_codex_config()
    except codex_config.CodexConfigError as exc:
        logger.critical(
            "Failed to enforce Codex approval policy: %s",
            exc,
        )
        raise RuntimeError("Codex approval policy enforcement failed") from exc

    try:
        resolved_config: dict[str, Any] = toml.load(config_path)
    except toml.TomlDecodeError as exc:
        logger.critical(
            "Codex config at %s is not valid TOML: %s",
            config_path,
            exc,
            extra={"codex_config_path": str(config_path)},
        )
        raise RuntimeError("Codex config is corrupt") from exc

    approval_policy = resolved_config.get("approval_policy")
    if approval_policy != "never":
        logger.critical(
            "Codex approval policy is %r (expected 'never')",
            approval_policy,
            extra={
                "codex_config_path": str(config_path),
                "approval_policy": approval_policy,
            },
        )
        raise RuntimeError("Codex approval policy is not enforced")

    logger.info(
        "Codex approval policy enforced at %s",
        config_path,
        extra={
            "codex_config_path": str(config_path),
            "approval_policy": approval_policy,
        },
    )
    return config_path


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

_enforce_codex_approval_policy()
_log_codex_cli_version()


if __name__ == "__main__":  # pragma: no cover - CLI execution path
    celery_app.start()
