"""Celery tasks for Gemini CLI operations."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from celery.utils.log import get_task_logger

from moonmind.workflows.speckit_celery import celery_app
from moonmind.workflows.speckit_celery.utils import verify_cli_is_executable

logger = get_task_logger(__name__)

GEMINI_QUEUE = os.getenv("GEMINI_CELERY_QUEUE", "gemini")


@celery_app.task(name="gemini_generate", queue=GEMINI_QUEUE)
def gemini_generate(prompt: str, model: str | None = None) -> dict[str, Any]:
    """Invoke Gemini CLI to generate content."""
    logger.info("Starting Gemini generation")

    try:
        gemini_path = verify_cli_is_executable("gemini")
    except Exception as exc:
        logger.error("Gemini CLI verification failed: %s", exc)
        return {"status": "failed", "error": str(exc)}

    command = [gemini_path, "--prompt", prompt, "--output-format", "json"]
    if model:
        command.extend(["--model", model])

    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
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
