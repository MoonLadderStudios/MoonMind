from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

def _load_compose() -> dict:
    compose_path = REPO_ROOT / "docker-compose.yaml"
    return yaml.safe_load(compose_path.read_text(encoding="utf-8"))

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

def test_temporal_compose_topology_and_private_exposure():
    compose = _load_compose()
    services = compose["services"]

    # temporal-db is now a network alias on the postgres service, not its own service.
    assert "postgres" in services
    postgres_aliases = []
    for _net_name, net_cfg in (services["postgres"].get("networks", {}) or {}).items():
        if isinstance(net_cfg, dict):
            postgres_aliases.extend(net_cfg.get("aliases", []))
    assert "temporal-db" in postgres_aliases

    assert "temporal" in services
    assert "temporal-namespace-init" in services

    temporal_service = services["temporal"]
    # Temporal now publishes port 7233 via ports (host-accessible for local dev).
    assert temporal_service.get("ports") == ["7233:7233"]
    # Temporal sits on local-network with alias temporal-internal.
    temporal_networks = temporal_service.get("networks", {})
    if isinstance(temporal_networks, dict):
        assert "local-network" in temporal_networks
    elif isinstance(temporal_networks, list):
        assert "local-network" in temporal_networks

def test_api_host_port_mapping_and_optional_env_file_for_mm_969():
    compose = _load_compose()
    services = compose["services"]

    api_service = services["api"]
    assert api_service["ports"] == ["${MOONMIND_API_HOST_PORT:-7000}:8000"]

    healthcheck = " ".join(str(part) for part in api_service["healthcheck"]["test"])
    assert "http://localhost:8000/healthz" in healthcheck

    api_env = _env_map(api_service["environment"])
    assert api_env["MODEL_CONTEXT_PROTOCOL_PORT"] == "8000"

    assert services["postgres"]["environment"]["POSTGRES_PASSWORD"] == (
        "${POSTGRES_PASSWORD:-password}"
    )

    runtime_service_names = [
        "temporal-worker-workflow",
        "temporal-worker-workflow-merge-automation",
        "temporal-worker-agent-runtime",
    ]
    for service_name in runtime_service_names:
        service_env = _env_map(services[service_name]["environment"])
        assert service_env["MOONMIND_URL"] == "${MOONMIND_URL:-http://api:8000}"

    for service in services.values():
        for env_file in service.get("env_file", []):
            if isinstance(env_file, dict) and env_file.get("path") == ".env":
                assert env_file.get("required") is False

    env_template = (REPO_ROOT / ".env-template").read_text(encoding="utf-8")
    assert any(
        line.strip() == "MOONMIND_API_HOST_PORT=7000"
        for line in env_template.splitlines()
    )

def test_documented_compose_startup_config_succeeds_without_env_file(tmp_path):
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is not available")

    compose_version = subprocess.run(
        ["docker", "compose", "version"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if compose_version.returncode != 0:
        pytest.skip("docker compose plugin is not available")

    env_path = REPO_ROOT / ".env"
    hidden_env_path = tmp_path / ".env"
    env_was_hidden = False
    if env_path.exists():
        shutil.move(str(env_path), str(hidden_env_path))
        env_was_hidden = True

    try:
        result = subprocess.run(
            ["docker", "compose", "-f", "docker-compose.yaml", "config"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        if env_was_hidden:
            shutil.move(str(hidden_env_path), str(env_path))

    assert result.returncode == 0, result.stderr

def test_merge_automation_workflow_worker_polls_dedicated_user_workflow_queue():
    compose = _load_compose()
    services = compose["services"]

    merge_worker_env = _env_map(
        services["temporal-worker-workflow-merge-automation"]["environment"]
    )
    assert merge_worker_env["TEMPORAL_WORKFLOW_TASK_QUEUE"] == (
        "${TEMPORAL_WORKFLOW_TASK_QUEUE:-mm.workflow}"
    )
    assert merge_worker_env["TEMPORAL_USER_WORKFLOW_V2_TASK_QUEUE"] == (
        "${TEMPORAL_MERGE_AUTOMATION_WORKFLOW_TASK_QUEUE:-mm.workflow.merge_automation}"
    )

def test_sandbox_worker_compose_egress_is_restricted_for_mm_785():
    compose = _load_compose()
    services = compose["services"]
    networks = compose["networks"]

    assert networks["sandbox-egress-network"]["internal"] is True
    assert _network_names(services["temporal-worker-sandbox"]) == {
        "sandbox-egress-network"
    }
    assert "temporal-internal" in _network_aliases(
        services["temporal"],
        "sandbox-egress-network",
    )
    assert "moonmind-temporal-artifacts-s3" in _network_aliases(
        services["minio"],
        "sandbox-egress-network",
    )
    assert "moonmind-api-db" in _network_aliases(
        services["postgres"],
        "sandbox-egress-network",
    )

    proxy_service = services["sandbox-egress-proxy"]
    assert _network_names(proxy_service) == {
        "local-network",
        "sandbox-egress-network",
    }
    assert proxy_service["expose"] == ["3128"]

    sandbox_env = _env_map(services["temporal-worker-sandbox"]["environment"])
    assert sandbox_env["HTTPS_PROXY"] == (
        "${MOONMIND_SANDBOX_HTTPS_PROXY:-http://sandbox-egress-proxy:3128}"
    )
    assert sandbox_env["HTTP_PROXY"] == (
        "${MOONMIND_SANDBOX_HTTP_PROXY:-http://sandbox-egress-proxy:3128}"
    )
    assert "moonmind-api-db" in sandbox_env["NO_PROXY"]
    assert services["temporal-worker-sandbox"]["depends_on"][
        "sandbox-egress-proxy"
    ]["condition"] == "service_started"

    squid_config = (
        REPO_ROOT / "docker" / "sandbox-egress-proxy" / "squid.conf"
    ).read_text(encoding="utf-8")
    expected_proxy_domains = {
        "." + "".join(parts)
        for parts in [
            ("github", ".com"),
            ("openai", ".com"),
        ]
    }
    assert "http_access deny all" in squid_config
    assert expected_proxy_domains <= set(squid_config.split())

def test_temporal_persistence_and_visibility_environment_defaults():
    compose = _load_compose()
    services = compose["services"]

    temporal_env = _env_map(services["temporal"]["environment"])
    assert temporal_env["DB"] == "postgres12"
    assert temporal_env["DBNAME"] == "${TEMPORAL_POSTGRES_DB:-temporal}"
    assert temporal_env["VISIBILITY_DBNAME"] == (
        "${TEMPORAL_VISIBILITY_DB:-temporal_visibility}"
    )
    assert temporal_env["NUM_HISTORY_SHARDS"] == "${TEMPORAL_NUM_HISTORY_SHARDS:-1}"
    assert temporal_env["DYNAMIC_CONFIG_FILE_PATH"] == (
        "/etc/temporal/dynamicconfig/development-sql.yaml"
    )
    assert temporal_env["SKIP_ADD_CUSTOM_SEARCH_ATTRIBUTES"] == "true"

    namespace_env = _env_map(services["temporal-namespace-init"]["environment"])
    assert namespace_env["TEMPORAL_NAMESPACE"] == "${TEMPORAL_NAMESPACE:-default}"
    assert namespace_env["TEMPORAL_RETENTION_MAX_STORAGE_GB"] == (
        "${TEMPORAL_RETENTION_MAX_STORAGE_GB:-100}"
    )
    assert namespace_env["TEMPORAL_RETENTION_ESTIMATED_GB_PER_DAY"] == (
        "${TEMPORAL_RETENTION_ESTIMATED_GB_PER_DAY:-1}"
    )

def test_local_compose_enables_temporal_workflow_editing_readiness():
    compose = _load_compose()
    services = compose["services"]

    api_env = _env_map(services["api"]["environment"])
    assert api_env["TEMPORAL_WORKFLOW_EDITING_ENABLED"] == (
        "${TEMPORAL_WORKFLOW_EDITING_ENABLED:-true}"
    )

    env_template = (REPO_ROOT / ".env-template").read_text(encoding="utf-8")
    assert any(
        line.strip() == "TEMPORAL_WORKFLOW_EDITING_ENABLED=true"
        for line in env_template.splitlines()
    )

def test_visibility_schema_rehearsal_service_is_wired():
    compose = _load_compose()
    services = compose["services"]
    rehearsal_service = services["temporal-visibility-rehearsal"]

    assert rehearsal_service["profiles"] == ["temporal-tools"]
    rehearsal_env = _env_map(rehearsal_service["environment"])
    assert rehearsal_env["TEMPORAL_VISIBILITY_DB"] == (
        "${TEMPORAL_VISIBILITY_DB:-temporal_visibility}"
    )
    assert rehearsal_env["TEMPORAL_NUM_HISTORY_SHARDS"] == (
        "${TEMPORAL_NUM_HISTORY_SHARDS:-1}"
    )
    assert rehearsal_env["TEMPORAL_SHARD_DECISION_ACK"] == (
        "${TEMPORAL_SHARD_DECISION_ACK:-}"
    )

def test_runtime_services_receive_temporal_namespace_and_address():
    compose = _load_compose()
    services = compose["services"]

    api_env = _env_map(services["api"]["environment"])
    assert api_env["TEMPORAL_ADDRESS"] == "${TEMPORAL_ADDRESS:-temporal-internal:7233}"
    assert api_env["TEMPORAL_NAMESPACE"] == "${TEMPORAL_NAMESPACE:-default}"

    namespace_init_env = _env_map(services["temporal-namespace-init"]["environment"])
    assert (
        namespace_init_env["TEMPORAL_ADDRESS"]
        == "${TEMPORAL_ADDRESS:-temporal-internal:7233}"
    )
    assert namespace_init_env["TEMPORAL_NAMESPACE"] == "${TEMPORAL_NAMESPACE:-default}"


def test_runtime_image_includes_agent_skill_sources():
    dockerfile = (REPO_ROOT / "api_service" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "COPY .agents /app/.agents/" in dockerfile


def test_omnigent_shared_postgres_compose_topology_for_mm_970():
    compose = _load_compose()
    services = compose["services"]

    assert "postgres" in services
    assert not any(
        service_name != "postgres" and "postgres" in service_name
        for service_name in services
    )

    init_service = services["omnigent-db-init"]
    assert init_service["restart"] == "no"
    assert init_service["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert _network_names(init_service) == {"local-network"}

    init_env = _env_map(init_service["environment"])
    assert init_env["OMNIGENT_POSTGRES_USER"] == (
        "${OMNIGENT_POSTGRES_USER:-omnigent}"
    )
    assert init_env["OMNIGENT_POSTGRES_PASSWORD"] == (
        "${OMNIGENT_POSTGRES_PASSWORD:-omnigent}"
    )
    assert init_env["OMNIGENT_POSTGRES_DB"] == "${OMNIGENT_POSTGRES_DB:-omnigent}"

    command_text = "\n".join(str(part) for part in init_service["command"])
    assert "SELECT 1 FROM pg_roles" in command_text
    assert "CREATE ROLE" in command_text
    assert "ALTER ROLE $$role_sql PASSWORD $$password_sql" in command_text
    assert "SELECT 1 FROM pg_database" in command_text
    assert "CREATE DATABASE" in command_text
    assert "GRANT ALL PRIVILEGES ON DATABASE" in command_text
    assert "DROP DATABASE" not in command_text
    assert "DROP ROLE" not in command_text

    omnigent_service = services["omnigent"]
    assert omnigent_service["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert (
        omnigent_service["depends_on"]["omnigent-db-init"]["condition"]
        == "service_completed_successfully"
    )
    assert omnigent_service["ports"] == ["${OMNIGENT_PORT:-8000}:8000"]
    assert _network_names(omnigent_service) == {"local-network"}
    assert "omnigent-data:/data" in omnigent_service["volumes"]
    assert "omnigent-data" in compose["volumes"]

    omnigent_env = _env_map(omnigent_service["environment"])
    assert omnigent_env["DATABASE_URL"] == (
        "postgresql://${OMNIGENT_POSTGRES_USER:-omnigent}:"
        "${OMNIGENT_POSTGRES_PASSWORD:-omnigent}@postgres:5432/"
        "${OMNIGENT_POSTGRES_DB:-omnigent}"
    )
    assert "POSTGRES_USER" not in omnigent_env
    assert "POSTGRES_PASSWORD" not in omnigent_env
    assert "POSTGRES_DB" not in omnigent_env


def test_omnigent_env_template_and_example_config_for_mm_970():
    env_template = (REPO_ROOT / ".env-template").read_text(encoding="utf-8")
    for expected_name in (
        "OMNIGENT_IMAGE",
        "OMNIGENT_IMAGE_TAG",
        "OMNIGENT_PORT",
        "OMNIGENT_POSTGRES_USER",
        "OMNIGENT_POSTGRES_PASSWORD",
        "OMNIGENT_POSTGRES_DB",
        "OMNIGENT_BUILTIN_ADMIN_EMAIL",
        "OMNIGENT_BUILTIN_ADMIN_PASSWORD",
        "OMNIGENT_BUILTIN_USER_EMAIL",
        "OMNIGENT_BUILTIN_USER_PASSWORD",
        "OMNIGENT_OIDC_ENABLED",
        "OMNIGENT_OIDC_ISSUER_URL",
        "OMNIGENT_OIDC_CLIENT_ID",
        "OMNIGENT_OIDC_CLIENT_SECRET",
        "OMNIGENT_OIDC_SCOPES",
        "OMNIGENT_CONFIG",
        "OMNIGENT_HOST_IMAGE",
        "OMNIGENT_HOST_IMAGE_TAG",
    ):
        assert f"{expected_name}=" in env_template

    config = yaml.safe_load(
        (REPO_ROOT / "deploy" / "omnigent" / "server-config.example.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert config["database"]["url_env"] == "DATABASE_URL"
    assert config["accounts"]["built_in"]["admin_email_env"] == (
        "OMNIGENT_BUILTIN_ADMIN_EMAIL"
    )
    assert config["sandbox"]["host_image_env"] == "OMNIGENT_HOST_IMAGE"
