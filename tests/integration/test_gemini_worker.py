import subprocess
from unittest.mock import MagicMock, patch

import pytest

from celery_worker.gemini_tasks import (
    GEMINI_QUEUE,
    gemini_generate,
    gemini_process_response,
)
from moonmind.workflows.speckit_celery import celery_app


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
def test_gemini_generate_success(mock_run, mock_verify):
    """Verify gemini_generate task executes the CLI correctly."""
    mock_verify.return_value = "/usr/local/bin/gemini"

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = '{"response": "Hello world", "stats": {}}'
    mock_run.return_value = mock_result

    # Execute task synchronously
    result = gemini_generate.apply(args=("Hello",)).get()

    assert result["status"] == "success"
    assert result["result"]["response"] == "Hello world"

    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert args[0] == "/usr/local/bin/gemini"
    assert "--prompt" in args
    assert "Hello" in args
    assert "--output-format" in args
    assert "json" in args


@patch("celery_worker.gemini_tasks.verify_cli_is_executable")
@patch("celery_worker.gemini_tasks.subprocess.run")
def test_gemini_generate_failure(mock_run, mock_verify):
    """Verify gemini_generate handles CLI failures."""
    mock_verify.return_value = "/usr/local/bin/gemini"

    mock_run.side_effect = subprocess.CalledProcessError(
        1, cmd=["gemini"], stderr="Error generating content"
    )

    result = gemini_generate.apply(args=("Hello",)).get()

    assert result["status"] == "failed"
    assert "Error generating content" in result["error"]


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
