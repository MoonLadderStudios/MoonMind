"""Tests for the local development environment bring-up path."""

from __future__ import annotations

from pathlib import Path

import yaml


def _has_volume_mount(service_config: dict, source: str, target: str) -> bool:
    volumes = service_config.get("volumes", [])
    assert isinstance(volumes, list), "service volumes must be a list"

    for volume in volumes:
        if isinstance(volume, str):
            parts = volume.split(":")
            if len(parts) >= 2 and parts[0] == source and parts[1] == target:
                return True
        elif isinstance(volume, dict):
            if volume.get("source") == source and volume.get("target") == target:
                return True
    return False


def test_docker_compose_has_temporal_worker_auto_start_configured():
    """Verify that temporal workers are configured to start by default without sleeping."""
    compose_path = Path("docker-compose.yaml")
    assert (
        compose_path.exists()
    ), "docker-compose.yaml must exist at the repository root"

    compose_data = yaml.safe_load(compose_path.read_text())
    services = compose_data.get("services", {})

    worker_fleets = [
        "temporal-worker-workflow",
        "temporal-worker-artifacts",
        "temporal-worker-llm",
        "temporal-worker-sandbox",
        "temporal-worker-agent-runtime",
        "temporal-worker-integrations",
    ]

    for fleet in worker_fleets:
        assert (
            fleet in services
        ), f"Worker fleet '{fleet}' is missing from docker-compose.yaml"
        service_config = services[fleet]

        # Check that it is not hidden behind an optional profile
        # So it starts when users run `docker compose up`
        assert (
            "profiles" not in service_config
        ), f"Worker fleet '{fleet}' should not have profiles so it starts by default"

        # Verify the command is polling, not sleep
        env_vars = service_config.get("environment", [])
        command_var = next(
            (env for env in env_vars if env.startswith("TEMPORAL_WORKER_COMMAND=")), ""
        )
        assert (
            "sleep" not in command_var
        ), f"Worker '{fleet}' must not be configured to sleep. Found: {command_var}"


def test_api_service_mounts_agent_runtime_workspace_volume():
    """Mission Control observability must read managed-run records from agent_workspaces."""
    compose_path = Path("docker-compose.yaml")
    assert (
        compose_path.exists()
    ), "docker-compose.yaml must exist at the repository root"

    compose_data = yaml.safe_load(compose_path.read_text())
    services = compose_data.get("services", {})
    api_service = services.get("api")
    assert isinstance(
        api_service, dict
    ), "api service is missing from docker-compose.yaml"

    volumes = api_service.get("volumes", [])
    assert isinstance(volumes, list), "api service volumes must be a list"

    found_mount = False
    for v in volumes:
        if isinstance(v, str):
            parts = v.split(":")
            if (
                len(parts) >= 2
                and parts[0] == "agent_workspaces"
                and parts[1] == "/work/agent_jobs"
            ):
                found_mount = True
                break
        elif isinstance(v, dict):
            if (
                v.get("source") == "agent_workspaces"
                and v.get("target") == "/work/agent_jobs"
            ):
                found_mount = True
                break

    assert (
        found_mount
    ), "api service must mount agent_workspaces at /work/agent_jobs so observability APIs can read managed-run records"


def test_agent_runtime_worker_mounts_agent_skill_catalog():
    """Selected managed-session skills resolve from the deployment skill catalog."""
    compose_path = Path("docker-compose.yaml")
    assert (
        compose_path.exists()
    ), "docker-compose.yaml must exist at the repository root"

    compose_data = yaml.safe_load(compose_path.read_text())
    services = compose_data.get("services", {})
    service_config = services.get("temporal-worker-agent-runtime")
    assert isinstance(
        service_config, dict
    ), "temporal-worker-agent-runtime service is missing from docker-compose.yaml"

    assert _has_volume_mount(service_config, "./.agents", "/app/.agents"), (
        "temporal-worker-agent-runtime must mount ./.agents at /app/.agents so "
        "selected agent skills can be materialized for managed sessions"
    )


def test_agent_workspaces_init_avoids_recursive_permission_repair():
    """Workspace init should only normalize hot directories, not recurse the whole volume."""
    compose_path = Path("docker-compose.yaml")
    assert (
        compose_path.exists()
    ), "docker-compose.yaml must exist at the repository root"

    compose_data = yaml.safe_load(compose_path.read_text())
    services = compose_data.get("services", {})
    init_service = services.get("agent-workspaces-init")
    assert isinstance(
        init_service, dict
    ), "agent-workspaces-init service is missing from docker-compose.yaml"

    command = init_service.get("command", "")
    assert "set -e" in command
    assert "chown -R" not in command
    assert "$$dir" in command
    assert '"$dir"' not in command
    for expected_dir in (
        "/work/agent_jobs",
        "/work/agent_jobs/artifacts",
        "/work/agent_jobs/managed_runs",
        "/work/agent_jobs/managed_sessions",
        "/work/agent_jobs/workspaces",
    ):
        assert expected_dir in command
