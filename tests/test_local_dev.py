"""Tests for the local development environment bring-up path."""

from __future__ import annotations

import ast
from pathlib import Path

import yaml


class UniqueKeySafeLoader(yaml.SafeLoader):
    """PyYAML loader that rejects duplicate mapping keys."""


def _construct_mapping_with_unique_keys(loader, node, deep=False):
    seen_keys = set()
    for key_node, _value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen_keys:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key ({key!r})",
                key_node.start_mark,
            )
        seen_keys.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_with_unique_keys,
)


def _load_compose() -> dict:
    compose_path = Path("docker-compose.yaml")
    assert (
        compose_path.exists()
    ), "docker-compose.yaml must exist at the repository root"

    return yaml.load(
        compose_path.read_text(encoding="utf-8"),
        Loader=UniqueKeySafeLoader,
    )


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


def test_agent_workspaces_volume_is_docker_managed_named_volume():
    """agent_workspaces must stay under Docker's data root, not a host bind path."""
    compose_data = _load_compose()
    volumes = compose_data.get("volumes", {})
    volume_config = volumes.get("agent_workspaces")
    assert isinstance(
        volume_config, dict
    ), "agent_workspaces volume is missing from docker-compose.yaml"

    assert volume_config.get("name") == (
        "${MOONMIND_AGENT_WORKSPACES_VOLUME_NAME:-agent_workspaces}"
    ), "agent_workspaces must remain the deployment-configurable named volume"
    assert not volume_config.get("external"), (
        "agent_workspaces must be created by the MoonMind compose project, not "
        "borrowed from an externally configured host path"
    )
    assert "driver_opts" not in volume_config, (
        "agent_workspaces must not configure local driver bind/device options; "
        "place Docker on a data disk by configuring the daemon data-root"
    )


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


def test_omnigent_compose_uses_shared_postgres_for_mm_970():
    """MM-970: Omnigent runs beside MoonMind using the shared Postgres service."""
    compose_data = _load_compose()
    services = compose_data.get("services", {})

    assert "postgres" in services
    assert not any(
        service_name != "postgres" and "postgres" in service_name
        for service_name in services
    ), "Omnigent must reuse the existing postgres service, not add another one"

    init_service = services.get("omnigent-db-init")
    assert isinstance(
        init_service, dict
    ), "omnigent-db-init service is missing from docker-compose.yaml"
    assert init_service.get("restart") == "no"
    assert init_service["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert _network_names(init_service) == {"local-network"}

    init_env = _env_map(init_service.get("environment"))
    assert init_env["OMNIGENT_POSTGRES_USER"] == (
        "${OMNIGENT_POSTGRES_USER:-omnigent}"
    )
    assert init_env["OMNIGENT_POSTGRES_PASSWORD"] == (
        "${OMNIGENT_POSTGRES_PASSWORD:-omnigent}"
    )
    assert init_env["OMNIGENT_POSTGRES_DB"] == "${OMNIGENT_POSTGRES_DB:-omnigent}"
    assert "POSTGRES_USER" not in {
        key for key in init_env if key.startswith("OMNIGENT_")
    }

    command_text = "\n".join(str(part) for part in init_service.get("command", []))
    assert "SELECT 1 FROM pg_roles" in command_text
    assert "CREATE ROLE" in command_text
    assert "SELECT 1 FROM pg_database" in command_text
    assert "CREATE DATABASE" in command_text
    assert "DROP DATABASE" not in command_text
    assert "DROP ROLE" not in command_text
    assert "GRANT ALL PRIVILEGES ON DATABASE" in command_text

    omnigent_service = services.get("omnigent")
    assert isinstance(
        omnigent_service, dict
    ), "omnigent service is missing from docker-compose.yaml"
    assert omnigent_service["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert (
        omnigent_service["depends_on"]["omnigent-db-init"]["condition"]
        == "service_completed_successfully"
    )
    assert _network_names(omnigent_service) == {"local-network"}
    assert omnigent_service["ports"] == ["${OMNIGENT_PORT:-8000}:8000"]
    assert _has_volume_mount(omnigent_service, "omnigent-data", "/data")
    assert "omnigent-data" in compose_data.get("volumes", {})

    omnigent_env = _env_map(omnigent_service.get("environment"))
    assert omnigent_service["image"] == (
        "${OMNIGENT_IMAGE:-ghcr.io/omnigent-ai/omnigent-server}:"
        "${OMNIGENT_IMAGE_TAG:-latest}"
    )
    assert omnigent_env["DATABASE_URL"] == (
        "postgresql://${OMNIGENT_POSTGRES_USER:-omnigent}:"
        "${OMNIGENT_POSTGRES_PASSWORD:-omnigent}@postgres:5432/"
        "${OMNIGENT_POSTGRES_DB:-omnigent}"
    )
    assert omnigent_env["ARTIFACT_DIR"] == "${OMNIGENT_ARTIFACT_DIR:-/data/artifacts}"
    assert omnigent_env["HOST"] == "0.0.0.0"
    assert omnigent_env["PORT"] == "8000"
    assert omnigent_env["OMNIGENT_CONFIG"] == "${OMNIGENT_CONFIG:-}"
    assert omnigent_env["OMNIGENT_AUTH_ENABLED"] == "${OMNIGENT_AUTH_ENABLED:-1}"
    assert omnigent_env["OMNIGENT_AUTH_PROVIDER"] == "${OMNIGENT_AUTH_PROVIDER:-}"
    assert omnigent_env["OMNIGENT_ACCOUNTS_BASE_URL"] == (
        "${OMNIGENT_ACCOUNTS_BASE_URL:-http://localhost:${OMNIGENT_PORT:-8000}}"
    )
    assert omnigent_env["OMNIGENT_WS_ALLOWED_ORIGINS"] == (
        "${OMNIGENT_WS_ALLOWED_ORIGINS:-${OMNIGENT_ACCOUNTS_BASE_URL:-http://"
        "localhost:${OMNIGENT_PORT:-8000},http://127.0.0.1:${OMNIGENT_PORT:-8000},"
        "http://host.docker.internal:${OMNIGENT_PORT:-8000},http://${HOSTNAME:-localhost}:"
        "${OMNIGENT_PORT:-8000},http://${COMPUTERNAME:-localhost}:${OMNIGENT_PORT:-8000}}}"
    )
    assert omnigent_env["OMNIGENT_OIDC_ISSUER"] == "${OMNIGENT_OIDC_ISSUER:-}"
    assert "POSTGRES_USER" not in omnigent_env
    assert "POSTGRES_PASSWORD" not in omnigent_env
    assert "POSTGRES_DB" not in omnigent_env
    assert "OMNIGENT_BUILTIN_ADMIN_EMAIL" not in omnigent_env
    assert "OMNIGENT_OIDC_ENABLED" not in omnigent_env
    assert "OMNIGENT_OIDC_ISSUER_URL" not in omnigent_env


def test_omnigent_env_template_and_optional_config_for_mm_970():
    env_template = Path(".env-template").read_text(encoding="utf-8")
    for expected_name in (
        "OMNIGENT_IMAGE",
        "OMNIGENT_IMAGE_TAG",
        "OMNIGENT_PORT",
        "OMNIGENT_POSTGRES_USER",
        "OMNIGENT_POSTGRES_PASSWORD",
        "OMNIGENT_POSTGRES_DB",
        "OMNIGENT_ARTIFACT_DIR",
        "OMNIGENT_AUTH_ENABLED",
        "OMNIGENT_AUTH_PROVIDER",
        "OMNIGENT_ACCOUNTS_COOKIE_SECRET",
        "OMNIGENT_ACCOUNTS_BASE_URL",
        "OMNIGENT_WS_ALLOWED_ORIGINS",
        "OMNIGENT_ACCOUNTS_INIT_ADMIN_PASSWORD",
        "OMNIGENT_ACCOUNTS_SESSION_TTL_HOURS",
        "OMNIGENT_ACCOUNTS_INVITE_TTL_HOURS",
        "OMNIGENT_ACCOUNTS_MAGIC_TTL_MINUTES",
        "OMNIGENT_ACCOUNTS_AUTO_OPEN",
        "OMNIGENT_OIDC_ISSUER",
        "OMNIGENT_OIDC_CLIENT_ID",
        "OMNIGENT_OIDC_CLIENT_SECRET",
        "OMNIGENT_OIDC_COOKIE_SECRET",
        "OMNIGENT_OIDC_SCOPES",
        "OMNIGENT_OIDC_SESSION_TTL_HOURS",
        "OMNIGENT_OIDC_ALLOWED_DOMAINS",
        "OMNIGENT_OIDC_LOGOUT_REDIRECT_URI",
        "OMNIGENT_OIDC_ALLOW_INVITES",
        "OMNIGENT_DOMAIN",
        "OMNIGENT_CONFIG",
        "OMNIGENT_HOST_IMAGE",
        "OMNIGENT_HOST_IMAGE_TAG",
    ):
        assert f"{expected_name}=" in env_template
    for removed_name in (
        "OMNIGENT_BUILTIN_ADMIN_EMAIL",
        "OMNIGENT_BUILTIN_ADMIN_PASSWORD",
        "OMNIGENT_BUILTIN_USER_EMAIL",
        "OMNIGENT_BUILTIN_USER_PASSWORD",
        "OMNIGENT_OIDC_ENABLED",
        "OMNIGENT_OIDC_ISSUER_URL",
    ):
        assert f"{removed_name}=" not in env_template

    config_path = Path("deploy/omnigent/server-config.example.yaml")
    assert config_path.exists(), "Omnigent example server config is missing"

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["host"] == "0.0.0.0"
    assert config["port"] == 8000
    assert config["artifact_location"] == "/data/artifacts"
    assert config["admins"] == []
    assert config["allowed_domains"] == []
    assert "sandbox" not in config
