"""Unit tests for Gemini Celery task auth-mode behavior."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from celery_worker.gemini_tasks import gemini_generate
from moonmind.workflows.speckit_celery import celery_app


@pytest.fixture(autouse=True)
def setup_celery(monkeypatch):
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)
    monkeypatch.setitem(celery_app.conf, "task_eager_propagates", True)
    monkeypatch.setitem(celery_app.conf, "result_backend", "cache+memory://")


@patch("celery_worker.gemini_tasks.verify_cli_is_executable")
@patch("celery_worker.gemini_tasks.subprocess.run")
def test_gemini_generate_resolves_auth_mode_per_task_call(mock_run, mock_verify):
    """Auth mode should be read on each invocation, not cached at import time."""

    mock_verify.return_value = "/usr/local/bin/gemini"
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = '{"response": "ok", "stats": {}}'
    mock_result.stderr = ""
    mock_run.return_value = mock_result

    with patch.dict(
        os.environ,
        {
            "MOONMIND_GEMINI_CLI_AUTH_MODE": "api_key",
            "GEMINI_API_KEY": "api-key-a",
        },
        clear=True,
    ):
        result = gemini_generate.apply(args=("hello",)).get()
    assert result["status"] == "success"

    with patch.dict(
        os.environ,
        {
            "MOONMIND_GEMINI_CLI_AUTH_MODE": "oauth",
            "GEMINI_API_KEY": "api-key-b",
            "GOOGLE_API_KEY": "google-key-b",
        },
        clear=True,
    ):
        result = gemini_generate.apply(args=("hello",)).get()
    assert result["status"] == "success"

    first_env = mock_run.call_args_list[0].kwargs["env"]
    second_env = mock_run.call_args_list[1].kwargs["env"]

    assert first_env["GEMINI_API_KEY"] == "api-key-a"
    assert "GEMINI_API_KEY" not in second_env
    assert "GOOGLE_API_KEY" not in second_env
