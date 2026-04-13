from pathlib import Path

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
