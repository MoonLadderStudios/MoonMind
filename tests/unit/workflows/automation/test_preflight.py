"""Historical login checks use the deployed agent host, not the API image."""

from unittest.mock import MagicMock

import pytest

import docker
from moonmind.config.settings import settings
from moonmind.workflows.automation import models
from moonmind.workflows.automation.preflight import run_codex_preflight_check


@pytest.mark.parametrize("override", [None, "registry.invalid/check:custom"])
def test_codex_login_check_uses_omnigent_host_image(monkeypatch, override):
    host_image = "registry.invalid/host@sha256:" + "a" * 64
    monkeypatch.setenv("OMNIGENT_SHARED_HOST_IMAGE_REF", host_image)
    monkeypatch.setattr(
        "moonmind.omnigent.bootstrap.store.load_resolved_state", lambda: None
    )
    monkeypatch.setattr(
        settings.workflow, "job_image", "registry.invalid/api:agent-free"
    )
    monkeypatch.setattr(settings.workflow, "codex_login_check_image", override)
    client = MagicMock()
    container = client.containers.run.return_value
    container.wait.return_value = {"StatusCode": 0}
    container.logs.return_value = b"Logged in\n"
    monkeypatch.setattr(docker, "from_env", lambda: client)

    result = run_codex_preflight_check(volume_name="existing_auth_volume")

    assert result.status == models.CodexPreflightStatus.PASSED
    assert client.containers.run.call_args.args == (override or host_image,)
    assert client.containers.run.call_args.kwargs["volumes"] == {
        "existing_auth_volume": {"bind": "/home/app/.codex", "mode": "ro"}
    }
    client.close.assert_called_once()
