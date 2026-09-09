"""Render service availability through the API and planning configuration boundary."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from api_service.api.routers import container_jobs
from moonmind.config.settings import FeatureFlagsSettings
from moonmind.workflows.temporal import worker_runtime

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("enabled", [None, "", "true", "false"])
def test_rendered_service_flag_matches_api_and_planner(tmp_path, monkeypatch, enabled):
    compose = tmp_path / "docker-compose.yaml"
    compose.write_bytes((ROOT / "docker-compose.yaml").read_bytes())
    env_file = tmp_path / ".env"
    env_file.write_text(
        "" if enabled is None else f"MOONMIND_CONTAINER_JOBS_ENABLED={enabled}\n",
        encoding="utf-8",
    )
    render_env = {
        name: os.environ[name]
        for name in (
            "PATH",
            "HOME",
            "USERPROFILE",
            "SYSTEMROOT",
            "SystemRoot",
            "TEMP",
            "TMP",
        )
        if name in os.environ
    }
    rendered = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "moonmind-test-container-readiness",
            "--env-file",
            str(env_file),
            "-f",
            str(compose),
            "config",
            "--format",
            "json",
        ],
        cwd=tmp_path,
        env=render_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert rendered.returncode == 0, rendered.stderr
    services = json.loads(rendered.stdout)["services"]
    expected = enabled != "false"
    for name in ("api", "temporal-worker-llm", "temporal-worker-agent-runtime"):
        flag = services[name]["environment"].get("MOONMIND_CONTAINER_JOBS_ENABLED")
        assert flag is not None, f"{name} must receive the deployment's service flag"
        flags = FeatureFlagsSettings(
            _env_file=None, MOONMIND_CONTAINER_JOBS_ENABLED=flag
        )
        assert flags.container_jobs_enabled is expected
        monkeypatch.setattr(worker_runtime.settings, "feature_flags", flags)
        assert container_jobs.container_jobs_ready() is expected
        # Only exercise the service availability dimension here. Existing
        # backend tests own malformed, disabled, daemon and policy matrices.
        monkeypatch.setenv("MOONMIND_CONTAINER_BACKEND_ENABLED", "true")
        blockers = worker_runtime._required_capability_blockers(
            parameters={"requiredCapabilities": ["docker"]}, task_payload={}
        )
        service_blockers = [
            item for item in blockers if item["check"] == "container_job_service"
        ]
        assert bool(service_blockers) is not expected
