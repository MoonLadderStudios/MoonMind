import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from celery_worker.gemini_tasks import (
    GEMINI_QUEUE,
    gemini_generate,
    gemini_process_response,
)
from moonmind.workflows.speckit_celery import celery_app
from moonmind.workflows.speckit_celery.utils import CliVerificationError


@pytest.fixture(autouse=True)
def setup_celery(monkeypatch):
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)
    monkeypatch.setitem(celery_app.conf, "task_eager_propagates", True)
    monkeypatch.setitem(celery_app.conf, "result_backend", "cache+memory://")


def test_gemini_task_routing():
    """Verify that tasks are configured to use the Gemini queue."""
    assert gemini_generate.queue == GEMINI_QUEUE
    assert gemini_process_response.queue == GEMINI_QUEUE


@patch("celery_worker.gemini_tasks.verify_cli_is_executable")
@patch("celery_worker.gemini_tasks.subprocess.run")
@pytest.mark.parametrize(
    "task_args, expected_cmd",
    [
        (
            ("Hello", None),
            ["/usr/local/bin/gemini", "--prompt", "Hello", "--output-format", "json"],
        ),
        (
            ("Hello", "gemini-pro"),
            [
                "/usr/local/bin/gemini",
                "--prompt",
                "Hello",
                "--output-format",
                "json",
                "--model",
                "gemini-pro",
            ],
        ),
    ],
)
def test_gemini_generate_success(mock_run, mock_verify, task_args, expected_cmd):
    """Verify gemini_generate task executes the CLI correctly."""
    mock_verify.return_value = "/usr/local/bin/gemini"

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = '{"response": "Hello world", "stats": {}}'
    mock_run.return_value = mock_result

    # Execute task synchronously with mocked environment
    with patch.dict(
        os.environ,
        {"GEMINI_API_KEY": "test-key", "GEMINI_HOME": "/tmp/gemini"},
        clear=True,
    ):
        result = gemini_generate.apply(args=task_args).get()

    assert result["status"] == "success"
    assert result["result"]["response"] == "Hello world"

    mock_run.assert_called_once()
    call_args = mock_run.call_args
    assert call_args[0][0] == expected_cmd

    # Verify environment injection
    env_arg = call_args[1].get("env")
    assert env_arg is not None
    assert env_arg["GEMINI_API_KEY"] == "test-key"
    assert env_arg["GEMINI_HOME"] == "/tmp/gemini"


def test_gemini_generate_empty_prompt():
    """Verify an empty prompt fails fast."""

    result = gemini_generate.apply(args=("",)).get()

    assert result["status"] == "failed"
    assert result["error"] == "Prompt cannot be empty"


@patch("celery_worker.gemini_tasks.verify_cli_is_executable")
@patch("celery_worker.gemini_tasks.subprocess.run")
def test_gemini_generate_cli_verification_error(mock_run, mock_verify):
    """Verify CLI verification errors are surfaced."""

    mock_verify.side_effect = CliVerificationError("gemini missing", cli_path=None)

    result = gemini_generate.apply(args=("Hello",)).get()

    assert result["status"] == "failed"
    assert "gemini missing" in result["error"]


@patch("celery_worker.gemini_tasks.verify_cli_is_executable")
@patch("celery_worker.gemini_tasks.subprocess.run")
def test_gemini_generate_invalid_json(mock_run, mock_verify):
    """Verify invalid JSON output is handled."""

    mock_verify.return_value = "/usr/local/bin/gemini"

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "not-json"
    mock_result.stderr = ""
    mock_run.return_value = mock_result

    result = gemini_generate.apply(args=("Hello",)).get()

    assert result["status"] == "failed"
    assert result["error"] == "Invalid JSON output"


@patch("celery_worker.gemini_tasks.verify_cli_is_executable")
@patch("celery_worker.gemini_tasks.subprocess.run")
@pytest.mark.parametrize(
    "side_effect, expected_error_msg",
    [
        (
            subprocess.CalledProcessError(
                1, cmd=["gemini"], stderr="Error generating content"
            ),
            "Error generating content",
        ),
        (
            subprocess.TimeoutExpired(cmd=["gemini"], timeout=300),
            "Gemini CLI timed out",
        ),
        (ValueError("boom"), "boom"),
    ],
)
def test_gemini_generate_failure(
    mock_run, mock_verify, side_effect, expected_error_msg
):
    """Verify gemini_generate handles CLI failures."""
    mock_verify.return_value = "/usr/local/bin/gemini"

    mock_run.side_effect = side_effect

    result = gemini_generate.apply(args=("Hello",)).get()

    assert result["status"] == "failed"
    assert expected_error_msg in result["error"]


def test_gemini_process_response_success():
    """Verify gemini_process_response processes successful results."""
    input_data = {
        "status": "success",
        "result": {"response": "Hello world", "stats": {"tokens": 10}},
    }

    result = gemini_process_response.apply(args=(input_data,)).get()

    assert result["status"] == "processed"
    assert result["data"]["text"] == "Hello world"
    assert result["data"]["stats"]["tokens"] == 10


def test_gemini_process_response_failure():
    """Verify gemini_process_response handles failed generation results."""
    input_data = {"status": "failed", "error": "Something went wrong"}

    result = gemini_process_response.apply(args=(input_data,)).get()

    assert result["status"] == "skipped"
    assert result["reason"] == "Generation failed"


def test_gemini_worker_preflight_missing_api_key():
    """Verify worker fails to start if API key is missing."""
    # Ensure module is unloaded so we can re-import it
    if "celery_worker.gemini_worker" in sys.modules:
        del sys.modules["celery_worker.gemini_worker"]

    with patch.dict(os.environ, {}, clear=True):
        with patch(
            "moonmind.workflows.speckit_celery.utils.verify_cli_is_executable"
        ) as mock_verify:
            mock_verify.return_value = "/bin/gemini"
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.stdout = "gemini version 1.0.0"
                # Mock settings to avoid fallback
                with patch("moonmind.config.settings.settings") as mock_settings:
                    mock_settings.google.google_api_key = None

                    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is not set"):
                        import celery_worker.gemini_worker  # noqa: F401

    # Cleanup
    if "celery_worker.gemini_worker" in sys.modules:
        del sys.modules["celery_worker.gemini_worker"]


def test_gemini_worker_preflight_invalid_gemini_home():
    """Verify worker fails to start if GEMINI_HOME is invalid."""
    if "celery_worker.gemini_worker" in sys.modules:
        del sys.modules["celery_worker.gemini_worker"]

    with patch.dict(
        os.environ,
        {"GEMINI_API_KEY": "fake-key", "GEMINI_HOME": "/non/existent/path"},
        clear=True,
    ):
        with patch(
            "moonmind.workflows.speckit_celery.utils.verify_cli_is_executable"
        ) as mock_verify:
            mock_verify.return_value = "/bin/gemini"
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.stdout = "gemini version 1.0.0"

                with pytest.raises(
                    RuntimeError, match="GEMINI_HOME directory does not exist"
                ):
                    import celery_worker.gemini_worker  # noqa: F401

    if "celery_worker.gemini_worker" in sys.modules:
        del sys.modules["celery_worker.gemini_worker"]
