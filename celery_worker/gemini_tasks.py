"""Celery tasks for Gemini CLI operations."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from celery.utils.log import get_task_logger

from celery_worker.runtime_mode import (
    is_invalid_gemini_cli_auth_mode,
    resolve_gemini_cli_auth_mode,
    resolve_worker_queue,
    summarize_untrusted_auth_mode_value,
)
from moonmind.config.settings import settings
from moonmind.workflows.speckit_celery import celery_app
from moonmind.workflows.speckit_celery.utils import (
    CliVerificationError,
    verify_cli_is_executable,
)

logger = get_task_logger(__name__)

GEMINI_QUEUE = resolve_worker_queue(
    default_queue=settings.celery.default_queue,
    legacy_queue_env="GEMINI_CELERY_QUEUE",
)


def _resolve_gemini_cli_auth_mode() -> str:
    """Resolve Gemini CLI auth mode for subprocess execution."""

    mode, raw = resolve_gemini_cli_auth_mode()
    if is_invalid_gemini_cli_auth_mode(raw):
        logger.warning(
            "Unknown MOONMIND_GEMINI_CLI_AUTH_MODE value (%s); defaulting to api_key.",
            summarize_untrusted_auth_mode_value(raw),
            extra={"gemini_cli_auth_mode_invalid": True},
        )
    return mode


@celery_app.task(name="gemini_generate", queue=GEMINI_QUEUE)
def gemini_generate(prompt: str, model: str | None = None) -> dict[str, Any]:
@celery_app.task(name="gemini_generate", queue=GEMINI_QUEUE)
def gemini_generate(prompt: str, model: str | None = None) -> dict[str, Any]:
    """Invoke Gemini CLI to generate content."""
    logger.info("Starting Gemini generation")

    if not prompt:
        logger.error("Prompt must not be empty")
        return {"status": "failed", "error": "Prompt cannot be empty"}

    try:
        gemini_path = verify_cli_is_executable("gemini")
    except CliVerificationError as exc:
        logger.error("Gemini CLI verification failed: %s", exc)
        return {"status": "failed", "error": str(exc)}

    command = [gemini_path, "--prompt", prompt, "--output-format", "json"]
    if model:
        command.extend(["--model", model])

    # Prepare environment with auth and config
    env = os.environ.copy()
    auth_mode = _resolve_gemini_cli_auth_mode()
    auth_mode = _resolve_gemini_cli_auth_mode()
    if auth_mode == "oauth":
        env.pop("GEMINI_API_KEY", None)
        env.pop("GOOGLE_API_KEY", None)
    else:
        api_key = (
            os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or settings.google.google_api_key
        )
        if api_key:
            env["GEMINI_API_KEY"] = api_key

    gemini_home = os.environ.get("GEMINI_HOME")
    if gemini_home:
        env["GEMINI_HOME"] = gemini_home

    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        logger.info("Gemini CLI execution successful")

        # Parse stdout as JSON
        try:
            output_json = json.loads(result.stdout)
            return {"status": "success", "result": output_json}
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse Gemini CLI output: %s", exc)
            return {
                "status": "failed",
                "error": "Invalid JSON output",
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

    except subprocess.TimeoutExpired as exc:
        logger.error("Gemini CLI execution timed out: %s", exc)
        return {
            "status": "failed",
            "error": "Gemini CLI timed out",
            "stdout": exc.stdout.decode(errors="ignore") if exc.stdout else "",
            "stderr": exc.stderr.decode(errors="ignore") if exc.stderr else "",
        }
    except subprocess.CalledProcessError as exc:
        logger.error(
            "Gemini CLI execution failed with exit code %s: %s",
            exc.returncode,
            exc.stderr,
        )
        return {
            "status": "failed",
            "error": exc.stderr,
            "exit_code": exc.returncode,
        }
    except Exception as exc:
        logger.exception("Unexpected error during Gemini generation")
        return {"status": "failed", "error": str(exc)}


@celery_app.task(name="gemini_process_response", queue=GEMINI_QUEUE)
def gemini_process_response(result: dict[str, Any]) -> dict[str, Any]:
    """Process the response from Gemini CLI."""
    logger.info("Processing Gemini response")

    if result.get("status") != "success":
        logger.warning("Gemini generation failed, nothing to process")
        return {"status": "skipped", "reason": "Generation failed"}

    gemini_output = result.get("result", {})
    response_text = gemini_output.get("response")
    stats = gemini_output.get("stats")

    processed = {
        "text": response_text,
        "stats": stats,
    }

    logger.info("Gemini response processed successfully")
    return {"status": "processed", "data": processed}
