"""Tests for the local development environment bring-up path."""

from __future__ import annotations

from pathlib import Path

import yaml


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
