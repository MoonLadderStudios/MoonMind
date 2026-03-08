from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]


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

    assert "temporal-db" in services
    assert "temporal" in services
    assert "temporal-namespace-init" in services

    temporal_service = services["temporal"]
    assert "ports" not in temporal_service
    assert temporal_service.get("expose") == ["7233"]
    assert set(temporal_service.get("networks", [])) == {
        "temporal-network",
        "local-network",
    }

    temporal_db = services["temporal-db"]
    assert "ports" not in temporal_db
    assert temporal_db.get("expose") == ["5432"]
    assert temporal_db.get("networks") == ["temporal-network"]

    networks = compose["networks"]
    assert networks["temporal-network"]["internal"] is True


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

    namespace_env = _env_map(services["temporal-namespace-init"]["environment"])
    assert namespace_env["TEMPORAL_NAMESPACE"] == "${TEMPORAL_NAMESPACE:-moonmind}"
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
    assert api_env["TEMPORAL_NAMESPACE"] == "${TEMPORAL_NAMESPACE:-moonmind}"

    namespace_init_env = _env_map(services["temporal-namespace-init"]["environment"])
    assert (
        namespace_init_env["TEMPORAL_ADDRESS"] == "${TEMPORAL_ADDRESS:-temporal-internal:7233}"
    )
    assert namespace_init_env["TEMPORAL_NAMESPACE"] == "${TEMPORAL_NAMESPACE:-moonmind}"
