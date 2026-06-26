"""Tests for the local development environment bring-up path."""

from __future__ import annotations

import ast
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


def _module_assignment_value(path: Path, name: str):
    module = ast.parse(path.read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name and node.value is not None:
                try:
                    return ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    pass
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                try:
                    return ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    pass
    return None


def _network_names(service_config: dict) -> set[str]:
    networks = service_config.get("networks", {})
    if isinstance(networks, list):
        return {str(network) for network in networks}
    if isinstance(networks, dict):
        return {str(network) for network in networks}
    return set()


def _network_aliases(service_config: dict, network_name: str) -> set[str]:
    networks = service_config.get("networks", {})
    if not isinstance(networks, dict):
        return set()
    network_config = networks.get(network_name)
    if not isinstance(network_config, dict):
        return set()
    return {str(alias) for alias in network_config.get("aliases", [])}


def _env_map(environment: object) -> dict[str, str]:
    if isinstance(environment, dict):
        return {str(k): str(v) for k, v in environment.items()}
    if not isinstance(environment, list):
        return {}
    mapped: dict[str, str] = {}
    for item in environment:
        text = str(item)
        if "=" not in text:
            continue
        key, value = text.split("=", 1)
        mapped[key] = value
    return mapped


def test_alembic_revision_ids_fit_default_version_column():
    """Alembic's default version table stores revision ids in VARCHAR(32)."""
    migration_dir = Path("api_service/migrations/versions")
    assert migration_dir.exists(), "Alembic versions directory is missing"

    for migration_path in migration_dir.glob("*.py"):
        if migration_path.name == "__init__.py":
            continue
        revision = _module_assignment_value(migration_path, "revision")
        assert isinstance(revision, str), (
            f"{migration_path} must declare a string Alembic revision id"
        )
        assert len(revision) <= 32, (
            f"{migration_path} revision id is too long for alembic_version.version_num: "
            f"{revision!r}"
        )


def test_init_db_repairs_known_orphaned_revision_stamps():
    """Local databases may be stamped with revision ids that were shortened."""
    entrypoint_path = Path("init_db/init_db_entrypoint.sh")
    assert entrypoint_path.exists(), "init-db entrypoint is missing"

    entrypoint = entrypoint_path.read_text(encoding="utf-8")

    assert "312_workflow_execution_source_mapping_cutover" in entrypoint
    assert "312_source_mapping_cutover" in entrypoint
    assert "313_finish_summary_projection_fields" in entrypoint
    assert "313_finish_summary_fields" in entrypoint
    assert "316_provider_profile_activation_state" in entrypoint
    assert "316_provider_profile_auth_state" in entrypoint


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

        assert service_config.get("init") is True, (
            f"Worker fleet '{fleet}' must run with an init process so child "
            "agent/tool processes are reaped and shutdown signals are forwarded"
        )

        # Verify the command is polling, not sleep
        env_vars = service_config.get("environment", [])
        command_var = next(
            (env for env in env_vars if env.startswith("TEMPORAL_WORKER_COMMAND=")), ""
        )
        assert (
            "sleep" not in command_var
        ), f"Worker '{fleet}' must not be configured to sleep. Found: {command_var}"


def test_sandbox_worker_uses_internal_egress_network_for_mm_785():
    """MM-785: sandbox workers must not retain unrestricted outbound networking."""
    compose_path = Path("docker-compose.yaml")
    assert (
        compose_path.exists()
    ), "docker-compose.yaml must exist at the repository root"

    compose_data = yaml.safe_load(compose_path.read_text())
    services = compose_data.get("services", {})
    networks = compose_data.get("networks", {})

    sandbox_network = networks.get("sandbox-egress-network")
    assert isinstance(
        sandbox_network, dict
    ), "sandbox-egress-network must be declared in docker-compose.yaml"
    assert sandbox_network.get("internal") is True, (
        "sandbox-egress-network must be an internal Docker network so sandbox "
        "workers do not have default outbound internet egress"
    )

    sandbox_worker = services.get("temporal-worker-sandbox")
    assert isinstance(
        sandbox_worker, dict
    ), "temporal-worker-sandbox service is missing from docker-compose.yaml"
    assert _network_names(sandbox_worker) == {"sandbox-egress-network"}

    temporal_service = services.get("temporal")
    assert isinstance(temporal_service, dict), "temporal service is missing"
    minio_service = services.get("minio")
    assert isinstance(minio_service, dict), "minio service is missing"
    postgres_service = services.get("postgres")
    assert isinstance(postgres_service, dict), "postgres service is missing"
    proxy_service = services.get("sandbox-egress-proxy")
    assert isinstance(
        proxy_service, dict
    ), "sandbox-egress-proxy service is missing"

    assert "temporal-internal" in _network_aliases(
        temporal_service,
        "sandbox-egress-network",
    )
    assert "moonmind-temporal-artifacts-s3" in _network_aliases(
        minio_service,
        "sandbox-egress-network",
    )
    assert "moonmind-api-db" in _network_aliases(
        postgres_service,
        "sandbox-egress-network",
    )
    assert _network_names(proxy_service) == {
        "local-network",
        "sandbox-egress-network",
    }
    assert proxy_service.get("expose") == ["3128"]

    sandbox_env = _env_map(sandbox_worker.get("environment"))
    assert sandbox_env["HTTPS_PROXY"] == (
        "${MOONMIND_SANDBOX_HTTPS_PROXY:-http://sandbox-egress-proxy:3128}"
    )
    assert sandbox_env["HTTP_PROXY"] == (
        "${MOONMIND_SANDBOX_HTTP_PROXY:-http://sandbox-egress-proxy:3128}"
    )
    assert "moonmind-api-db" in sandbox_env["NO_PROXY"]

    squid_config = Path("docker/sandbox-egress-proxy/squid.conf").read_text(
        encoding="utf-8"
    )
    expected_proxy_domains = {
        "." + "".join(parts)
        for parts in [
            ("github", ".com"),
            ("anthropic", ".com"),
        ]
    }
    assert "http_access deny all" in squid_config
    assert expected_proxy_domains <= set(squid_config.split())


def test_api_service_runs_with_container_init():
    """API service supervises subprocess-capable routes and should reap children."""
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
    assert api_service.get("init") is True, (
        "api service must run with an init process so child subprocesses "
        "are reaped and shutdown signals are forwarded"
    )


def test_api_service_mounts_agent_runtime_workspace_volume():
    """Dashboard observability must read managed-run records from agent_workspaces."""
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

    assert _has_volume_mount(
        api_service,
        "agent_workspaces",
        "/work/agent_jobs",
    ), "api service must mount agent_workspaces at /work/agent_jobs so observability APIs can read managed-run records"


def test_moonmind_application_services_use_deployment_image_variable():
    """Deployment updates must be able to change the app image without YAML edits."""
    compose_path = Path("docker-compose.yaml")
    assert (
        compose_path.exists()
    ), "docker-compose.yaml must exist at the repository root"

    compose_data = yaml.safe_load(compose_path.read_text())
    services = compose_data.get("services", {})
    application_services = [
        "api",
        "init-db",
        "temporal-worker-workflow",
        "temporal-worker-artifacts",
        "temporal-worker-llm",
        "temporal-worker-sandbox",
        "temporal-worker-agent-runtime",
        "temporal-worker-integrations",
    ]

    for service_name in application_services:
        service_config = services.get(service_name)
        assert isinstance(service_config, dict), (
            f"{service_name} service is missing from docker-compose.yaml"
        )
        assert service_config.get("image") == (
            "${MOONMIND_IMAGE:-ghcr.io/moonladderstudios/moonmind:latest}"
        ), f"{service_name} must use MOONMIND_IMAGE for deployment updates"


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
