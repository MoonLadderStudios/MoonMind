import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
MOUNTED_TOOL_PATH = (
    "/opt/moonmind-tools/bin:"
    "${OMNIGENT_HOST_BASE_PATH:-/opt/venv/bin:/usr/local/bin:/usr/local/sbin:"
    "/usr/bin:/usr/sbin:/bin:/sbin}"
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


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
    compose_path = REPO_ROOT / "docker-compose.yaml"
    return yaml.load(
        compose_path.read_text(encoding="utf-8"),
        Loader=UniqueKeySafeLoader,
    )


def _render_codex_host_compose() -> dict:
    _require_docker_compose()
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            "docker-compose.yaml",
            "--profile",
            "omnigent-host-codex",
            "config",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _require_docker_compose() -> None:
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

def _render_omnigent_compose_env(extra_env: dict[str, str]) -> dict[str, str]:
    _require_docker_compose()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
    }
    for name in ("DOCKER_CONFIG", "DOCKER_CONTEXT", "DOCKER_HOST"):
        if name in os.environ:
            env[name] = os.environ[name]
    env.update(extra_env)

    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            "docker-compose.yaml",
            "config",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    return {
        str(key): str(value)
        for key, value in config["services"]["omnigent"]["environment"].items()
    }

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


def test_omnigent_hosts_use_versioned_read_only_tool_bundle():
    compose = _load_compose()
    services = compose["services"]
    initializer = services["omnigent-tools-init"]
    tool_manifest = json.loads(
        (REPO_ROOT / "services/omnigent/tools/manifest.lock.json").read_text(
            encoding="utf-8"
        )
    )

    configured_version = initializer["environment"]["MOONMIND_GH_VERSION"]
    compose_default_version = configured_version.removesuffix("}").rsplit(":-", 1)[1]
    assert tool_manifest["bundleVersion"] == f"gh-{compose_default_version}-1"
    assert tool_manifest["tools"][0]["version"] == compose_default_version

    assert initializer["image"] == (
        "${OMNIGENT_GH_IMAGE:-serversideup/github-cli:alpine-2.76.2}"
    )
    assert initializer["user"] == "0:0"
    assert initializer["restart"] == "no"
    assert initializer["environment"] == {
        "MOONMIND_GH_SOURCE": "/usr/local/bin/gh",
        "MOONMIND_GH_VERSION": "${OMNIGENT_GH_VERSION:-2.76.2}",
    }
    assert initializer["volumes"] == [
        "omnigent-tools:/output",
        "./services/omnigent/scripts:/opt/moonmind:ro",
    ]
    assert compose["volumes"]["omnigent-tools"]["name"] == (
        "moonmind-omnigent-tools-gh-${OMNIGENT_GH_VERSION:-2.76.2}"
    )

    for service_name in ("omnigent-host", "omnigent-host-claude", "omnigent-host-codex"):
        host = services[service_name]
        environment = _env_map(host["environment"])
        assert environment["PATH"].startswith("/opt/moonmind-tools/bin:")
        assert host["depends_on"]["omnigent-tools-init"] == {
            "condition": "service_completed_successfully"
        }
        assert "omnigent-tools:/opt/moonmind-tools:ro" in host["volumes"]
        assert (
            "./services/omnigent/scripts/moonmind-tools.sh:"
            "/etc/profile.d/moonmind-tools.sh:ro"
        ) in host["volumes"]

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

def test_managed_runtime_cleanup_defaults_match_api_and_agent_runtime_worker():
    compose = _load_compose()
    api_env = _env_map(compose["services"]["api"]["environment"])
    worker_env = _env_map(
        compose["services"]["temporal-worker-agent-runtime"]["environment"]
    )
    expected = {
        "MOONMIND_AGENT_RUNTIME_STORE": "${MOONMIND_AGENT_RUNTIME_STORE:-/work/agent_jobs}",
        "MOONMIND_AGENT_RUNTIME_ARTIFACTS": "${MOONMIND_AGENT_RUNTIME_ARTIFACTS:-/work/agent_jobs/artifacts}",
        "MOONMIND_MANAGED_RUNTIME_JANITOR_ENABLED": "${MOONMIND_MANAGED_RUNTIME_JANITOR_ENABLED:-true}",
        "MOONMIND_MANAGED_RUNTIME_JANITOR_DRY_RUN": "${MOONMIND_MANAGED_RUNTIME_JANITOR_DRY_RUN:-false}",
        "MOONMIND_MANAGED_RUNTIME_WORKSPACE_RETENTION_DAYS": "${MOONMIND_MANAGED_RUNTIME_WORKSPACE_RETENTION_DAYS:-30}",
        "MOONMIND_MANAGED_RUNTIME_ARTIFACT_RETENTION_DAYS": "${MOONMIND_MANAGED_RUNTIME_ARTIFACT_RETENTION_DAYS:-90}",
        "MOONMIND_MANAGED_RUNTIME_RECORD_RETENTION_DAYS": "${MOONMIND_MANAGED_RUNTIME_RECORD_RETENTION_DAYS:-}",
        "MOONMIND_MANAGED_RUNTIME_JANITOR_GRACE_SECONDS": "${MOONMIND_MANAGED_RUNTIME_JANITOR_GRACE_SECONDS:-3600}",
        "MOONMIND_MANAGED_RUNTIME_JANITOR_MAX_DELETE_PATHS": "${MOONMIND_MANAGED_RUNTIME_JANITOR_MAX_DELETE_PATHS:-100}",
        "MOONMIND_MANAGED_RUNTIME_JANITOR_MAX_DELETE_BYTES": "${MOONMIND_MANAGED_RUNTIME_JANITOR_MAX_DELETE_BYTES:-}",
        "MOONMIND_MANAGED_RUNTIME_JANITOR_LOCK_PATH": "${MOONMIND_MANAGED_RUNTIME_JANITOR_LOCK_PATH:-/work/agent_jobs/.janitor.lock}",
    }

    assert {key: api_env[key] for key in expected} == expected
    assert {key: worker_env[key] for key in expected} == expected

def test_documented_compose_startup_config_succeeds_without_env_file(tmp_path):
    _require_docker_compose()

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

def test_omnigent_trusted_origins_render_local_and_public_defaults():
    local_env = _render_omnigent_compose_env(
        {
            "OMNIGENT_PORT": "8010",
            "HOSTNAME": "cs30",
            "COMPUTERNAME": "ASUS-LAPTOP",
        }
    )

    assert local_env["OMNIGENT_ACCOUNTS_BASE_URL"] == "http://localhost:8010"
    assert local_env["OMNIGENT_WS_ALLOWED_ORIGINS"] == (
        "http://localhost:8010,http://127.0.0.1:8010,"
        "http://host.docker.internal:8010,http://cs30:8010,"
        "http://ASUS-LAPTOP:8010"
    )

    public_env = _render_omnigent_compose_env(
        {"OMNIGENT_ACCOUNTS_BASE_URL": "https://omnigent.example.test"}
    )

    assert public_env["OMNIGENT_ACCOUNTS_BASE_URL"] == "https://omnigent.example.test"
    assert public_env["OMNIGENT_WS_ALLOWED_ORIGINS"] == (
        "https://omnigent.example.test"
    )

    explicit_env = _render_omnigent_compose_env(
        {
            "OMNIGENT_ACCOUNTS_BASE_URL": "https://omnigent.example.test",
            "OMNIGENT_WS_ALLOWED_ORIGINS": (
                "https://omnigent.example.test,https://admin.example.test"
            ),
        }
    )

    assert explicit_env["OMNIGENT_WS_ALLOWED_ORIGINS"] == (
        "https://omnigent.example.test,https://admin.example.test"
    )


def test_workflow_worker_service_supervises_normal_and_merge_automation_roles():
    compose = _load_compose()
    services = compose["services"]

    assert "temporal-worker-workflow" in services
    assert "temporal-worker-workflow-merge-automation" not in services

    workflow_worker = services["temporal-worker-workflow"]
    assert workflow_worker["entrypoint"] == [
        "python",
        "/app/services/temporal/scripts/start-workflow-worker-group.py",
    ]

    workflow_env = _env_map(workflow_worker["environment"])
    assert workflow_env["TEMPORAL_WORKFLOW_TASK_QUEUE"] == (
        "${TEMPORAL_WORKFLOW_TASK_QUEUE:-mm.workflow}"
    )
    assert workflow_env["TEMPORAL_USER_WORKFLOW_V2_TASK_QUEUE"] == (
        "${TEMPORAL_USER_WORKFLOW_V2_TASK_QUEUE:-mm.workflow.user.v2}"
    )
    assert workflow_env["TEMPORAL_MERGE_AUTOMATION_WORKFLOW_TASK_QUEUE"] == (
        "${TEMPORAL_MERGE_AUTOMATION_WORKFLOW_TASK_QUEUE:-mm.workflow.merge_automation}"
    )
    assert workflow_env["TEMPORAL_WORKFLOW_WORKER_CONCURRENCY"] == (
        "${TEMPORAL_WORKFLOW_WORKER_CONCURRENCY:-8}"
    )
    assert workflow_env["TEMPORAL_MERGE_AUTOMATION_WORKFLOW_WORKER_CONCURRENCY"] == (
        "${TEMPORAL_MERGE_AUTOMATION_WORKFLOW_WORKER_CONCURRENCY:-2}"
    )
    assert workflow_env["TEMPORAL_WORKER_VERSIONING_ENABLED"] == (
        "${TEMPORAL_WORKER_VERSIONING_ENABLED:-false}"
    )
    assert workflow_env["MOONMIND_DEPLOYMENT_MODE"] == (
        "${MOONMIND_DEPLOYMENT_MODE:-development}"
    )
    assert workflow_env["TEMPORAL_WORKFLOW_READINESS_URL"] == (
        "${TEMPORAL_WORKFLOW_READINESS_URL:-http://temporal-worker-workflow:8080/readyz}"
    )
    healthcheck = " ".join(
        str(part) for part in workflow_worker["healthcheck"]["test"]
    )
    assert "http://localhost:8080/readyz" in healthcheck


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


def test_omnigent_host_profile_service_is_wired_for_mm_971():
    # MM-971 carries the optional host-service slice from source issue MM-968.
    compose = _load_compose()
    services = compose["services"]
    volumes = compose["volumes"]

    assert "omnigent" in services
    assert "omnigent-host" in services

    server_service = services["omnigent"]
    assert "profiles" not in server_service
    assert server_service["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert (
        server_service["depends_on"]["omnigent-db-init"]["condition"]
        == "service_completed_successfully"
    )
    assert _network_names(server_service) == {"local-network"}

    host_service = services["omnigent-host"]
    assert host_service["profiles"] == ["omnigent-host"]
    assert host_service["image"] == (
        "${OMNIGENT_HOST_IMAGE_REF:-${OMNIGENT_HOST_IMAGE:-ghcr.io/omnigent-ai/omnigent-host}:"
        "${OMNIGENT_HOST_IMAGE_TAG:-latest}}"
    )
    assert host_service["entrypoint"] == ["/opt/moonmind/start-host-with-projections.sh"]
    assert host_service["depends_on"]["omnigent"]["condition"] == "service_started"
    assert _network_names(host_service) == {"local-network"}

    host_env = _env_map(host_service["environment"])
    assert host_env["OPENAI_API_KEY"] == "${OPENAI_API_KEY:-}"
    assert "CODEX_HOME" not in host_env
    assert host_env["ANTHROPIC_API_KEY"] == "${ANTHROPIC_API_KEY:-}"
    assert host_env["GEMINI_API_KEY"] == "${GEMINI_API_KEY:-}"
    assert host_env["GOOGLE_API_KEY"] == "${GOOGLE_API_KEY:-}"

    host_volumes = {
        volume for volume in host_service["volumes"] if isinstance(volume, str)
    }
    assert "omnigent-host-state:/root/.omnigent" in host_volumes
    assert (
        "${OMNIGENT_RUN_WORKSPACE:-./omnigent_workspaces/run}:/workspaces/run"
        in host_volumes
    )
    assert "codex_auth_volume:/root/.codex" not in host_volumes
    assert "omnigent-host-artifacts:/artifacts" in host_volumes
    assert "omnigent-host-cache:/root/.cache" in host_volumes
    assert "omnigent-host-state" in volumes
    assert "codex_auth_volume" in volumes
    assert "omnigent-server-state" not in volumes


def test_omnigent_claude_host_profile_uses_only_canonical_oauth_credentials():
    compose = _load_compose()
    host_service = compose["services"]["omnigent-host-claude"]

    assert host_service["profiles"] == ["omnigent-host-claude"]
    assert host_service["hostname"] == "omnigent-host-claude"
    assert host_service["image"] == (
        "${OMNIGENT_HOST_IMAGE_REF:-${OMNIGENT_HOST_IMAGE:-ghcr.io/omnigent-ai/omnigent-host}:"
        "${OMNIGENT_HOST_IMAGE_TAG:-latest}}"
    )
    assert host_service["entrypoint"] == ["/opt/moonmind/start-host-with-projections.sh"]
    assert host_service["user"] == "1000:1000"
    assert host_service["working_dir"] == "/home/app"
    assert "env_file" not in host_service

    host_env = _env_map(host_service["environment"])
    assert host_env == {
        "MOONMIND_ACTIVE_SKILLS_DIR": "/opt/moonmind-skills",
        "OMNIGENT_SERVER_URL": "http://omnigent:8000",
        "HOME": "/home/app",
        "PATH": MOUNTED_TOOL_PATH,
        "CLAUDE_HOME": "/home/app/.claude",
        "CLAUDE_VOLUME_PATH": "/home/app/.claude",
        "CLAUDE_CONFIG_DIR": "/home/app/.claude",
        "ANTHROPIC_API_KEY": "",
        "ANTHROPIC_AUTH_TOKEN": "",
        "CLAUDE_API_KEY": "",
        "CLAUDE_CODE_OAUTH_TOKEN": "",
        "OPENAI_API_KEY": "",
        "GEMINI_API_KEY": "",
        "GOOGLE_API_KEY": "",
    }

    host_volumes = {
        volume for volume in host_service["volumes"] if isinstance(volume, str)
    }
    assert "omnigent-host-claude-state:/home/app/.omnigent" in host_volumes
    assert "claude_auth_volume:/home/app/.claude" in host_volumes
    assert "./omnigent_workspaces:/workspaces" in host_volumes
    assert "omnigent-tools:/opt/moonmind-tools:ro" in host_volumes
    assert (
        "${OMNIGENT_ACTIVE_SKILLS_DIR:-./omnigent_workspaces/.moonmind/skills_active}:"
        "/opt/moonmind-skills:ro"
    ) in host_volumes
    assert (
        "${OMNIGENT_MOONMIND_WORKSPACE:-./omnigent_workspaces/MoonMind}:"
        "/workspaces/MoonMind:ro"
    ) in host_volumes
    assert "omnigent-host-claude-state" in compose["volumes"]

    assert host_service["depends_on"] == {
        "omnigent": {"condition": "service_started"},
        "claude-auth-init": {"condition": "service_completed_successfully"},
        "omnigent-tools-init": {"condition": "service_completed_successfully"},
    }
    assert _network_names(host_service) == {"local-network"}
    assert host_service["restart"] == "unless-stopped"
    assert host_service["healthcheck"] == {
        "test": [
            "CMD-SHELL",
            "test -d /home/app/.claude && test -w /home/app/.claude",
        ],
        "interval": "10s",
        "timeout": "5s",
        "retries": 3,
        "start_period": "10s",
    }


def test_omnigent_codex_host_profile_uses_only_canonical_oauth_credentials():
    compose = _load_compose()
    host_service = compose["services"]["omnigent-host-codex"]
    expected_image = (
        "${OMNIGENT_HOST_IMAGE_REF:-${OMNIGENT_HOST_IMAGE:-ghcr.io/omnigent-ai/omnigent-host}:"
        "${OMNIGENT_HOST_IMAGE_TAG:-latest}}"
    )

    assert host_service["profiles"] == ["omnigent-host-codex"]
    assert host_service["hostname"] == "omnigent-host-codex"
    assert host_service["image"] == expected_image
    assert compose["services"]["omnigent-host-codex-init"]["image"] == expected_image
    assert host_service["user"] == "1000:1000"
    assert host_service["working_dir"] == "/home/app"
    assert "env_file" not in host_service
    assert _env_map(host_service["environment"]) == {
        "MOONMIND_ACTIVE_SKILLS_DIR": "/opt/moonmind-skills",
        "HOME": "/home/app",
        "PATH": MOUNTED_TOOL_PATH,
        "CODEX_HOME": "/home/app/.codex",
        "CODEX_CONFIG_HOME": "/home/app/.codex",
        "CODEX_CONFIG_PATH": "/home/app/.codex/config.toml",
        "CODEX_VOLUME_PATH": "/home/app/.codex",
        "CODEX_CREDENTIAL_GENERATION": "${CODEX_CREDENTIAL_GENERATION:-1}",
        "OMNIGENT_SERVER_URL": "http://omnigent:8000",
        "OMNIGENT_EXECUTION_TIMEOUT_SECONDS": "${OMNIGENT_HOST_TIMEOUT_SECONDS:-5400}",
        "OMNIGENT_EXECUTION_TIMEOUT_OWNER": "temporal_workflow",
        "OMNIGENT_CAPTURE_OWNER": "moonmind_bridge",
        "OMNIGENT_CAPTURE_RETENTION_DAYS": "${OMNIGENT_CAPTURE_RETENTION_DAYS:-30}",
    }
    assert host_service["stop_grace_period"] == "${OMNIGENT_HOST_STOP_GRACE_SECONDS:-20}s"
    entrypoint = host_service["entrypoint"]
    assert entrypoint[:2] == ["/usr/bin/env", "-u"]
    assert set(entrypoint[2::2]) == {
        "OPENAI_API_KEY",
        "CODEX_ACCESS_TOKEN",
        "OPENAI_BASE_URL",
        "MINIMAX_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    }
    assert set(entrypoint[1::2]) == {"-u"}
    assert {
        volume for volume in host_service["volumes"] if isinstance(volume, str)
    } == {
        "omnigent-host-codex-state:/home/app/.omnigent",
        "codex_auth_volume:/home/app/.codex",
        "omnigent-tools:/opt/moonmind-tools:ro",
        "${OMNIGENT_RUN_WORKSPACE:-./omnigent_workspaces/run}:/workspaces/run",
        "omnigent-host-artifacts:/artifacts",
        "omnigent-host-cache:/home/app/.cache",
        "./services/omnigent/scripts:/opt/moonmind:ro",
        "./services/omnigent/scripts/moonmind-tools.sh:/etc/profile.d/moonmind-tools.sh:ro",
        "omnigent-tools:/opt/moonmind-tools:ro",
        (
            "${OMNIGENT_ACTIVE_SKILLS_DIR:-./omnigent_workspaces/.moonmind/skills_active}:"
            "/opt/moonmind-skills:ro"
        ),
    }
    assert "omnigent-host-codex-state" in compose["volumes"]
    assert host_service["depends_on"] == {
        "omnigent": {"condition": "service_started"},
        "omnigent-host-codex-init": {
            "condition": "service_completed_successfully"
        },
        "omnigent-tools-init": {"condition": "service_completed_successfully"},
    }
    init_service = compose["services"]["omnigent-host-codex-init"]
    assert init_service["user"] == "0:0"
    assert init_service["depends_on"] == {
        "codex-auth-init": {"condition": "service_completed_successfully"}
    }
    assert init_service["entrypoint"] == [
        "/opt/moonmind/init-codex-oauth-host.sh"
    ]
    assert _network_names(host_service) == {"local-network"}


def test_canonical_omnigent_codex_host_uses_base_owned_oauth_volume():
    config = _render_codex_host_compose()
    host_service = config["services"]["omnigent-host-codex"]
    mounts = host_service["volumes"]
    oauth_mount = next(
        mount for mount in mounts if mount.get("target") == "/home/app/.codex"
    )

    assert oauth_mount["type"] == "volume"
    assert oauth_mount["source"] == "codex_auth_volume"
    assert host_service["working_dir"] == "/home/app"
    assert config["volumes"]["codex_auth_volume"]["name"] == "codex_auth_volume"
    assert config["volumes"]["codex_auth_volume"].get("external") is not True
    assert config["volumes"]["omnigent-host-codex-state"].get("name")


def test_oauth_hosts_have_no_platform_specific_compose_overlays():
    assert not (REPO_ROOT / "docker-compose.claude-host.yaml").exists()
    assert not (REPO_ROOT / "docker-compose.codex-host.yaml").exists()


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
    assert "COPY pr_resolver_core /app/pr_resolver_core/" in dockerfile


def test_python_test_runtime_is_provisioned_on_demand_outside_compose_startup():
    dockerfile = (REPO_ROOT / "api_service" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    test_stage = dockerfile.index("FROM runtime-dependencies AS test-runtime")
    production_stage = dockerfile.index("FROM runtime-dependencies AS runtime-base")
    omnigent_copy = dockerfile.index("COPY omnigent/omnigent /app/omnigent/omnigent/")

    assert test_stage < production_stage < omnigent_copy
    assert "USER app" in dockerfile[test_stage:production_stage]

    compose = _load_compose()
    services = compose["services"]
    worker = services["temporal-worker-agent-runtime"]
    worker_env = _env_map(worker["environment"])
    api_env = _env_map(services["api"]["environment"])

    assert all("build" not in service for service in services.values())
    assert "python-test-runtime-ready" not in services
    assert "python-test-runtime-ready" not in worker["depends_on"]
    assert worker_env["MOONMIND_CONTAINER_BACKEND_ENABLED"] == (
        "${MOONMIND_CONTAINER_BACKEND_ENABLED:-true}"
    )
    assert worker_env["MOONMIND_AGENT_WORKSPACES_VOLUME_NAME"] == (
        "${MOONMIND_AGENT_WORKSPACES_VOLUME_NAME:-agent_workspaces}"
    )
    assert worker_env["MOONMIND_PYTHON_TEST_IMAGE"] == (
        "${MOONMIND_PYTHON_TEST_IMAGE:-}"
    )
    assert worker_env["MOONMIND_PYTHON_TEST_IMAGE_MAX_AGE_SECONDS"] == (
        "${MOONMIND_PYTHON_TEST_IMAGE_MAX_AGE_SECONDS:-604800}"
    )
    assert worker_env["DOCKER_BUILDKIT"] == "1"
    assert api_env["MOONMIND_CONTAINER_JOBS_ENABLED"] == (
        "${MOONMIND_CONTAINER_JOBS_ENABLED:-true}"
    )
    assert worker_env["MOONMIND_CONTAINER_JOBS_ENABLED"] == (
        "${MOONMIND_CONTAINER_JOBS_ENABLED:-true}"
    )
    assert compose["volumes"]["agent_workspaces"]["name"] == (
        "${MOONMIND_AGENT_WORKSPACES_VOLUME_NAME:-agent_workspaces}"
    )
    assert services["docker-proxy"]["environment"]["BUILD"] == 1
    assert services["docker-proxy"]["environment"]["SESSION"] == 1

    test_compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.test.yaml").read_text(encoding="utf-8")
    )
    pytest_service = test_compose["services"]["pytest"]
    assert pytest_service["image"] == (
        "${MOONMIND_PYTHON_TEST_IMAGE:-moonmind-python-tests:local}"
    )
    assert pytest_service["build"]["target"] == "test-runtime"


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
    assert omnigent_service["image"] == (
        "${OMNIGENT_IMAGE_REF:-${OMNIGENT_IMAGE:-ghcr.io/omnigent-ai/omnigent-server}:"
        "${OMNIGENT_IMAGE_TAG:-latest}}"
    )
    assert omnigent_env["DATABASE_URL"] == (
        "postgresql://${OMNIGENT_POSTGRES_USER:-omnigent}:"
        "${OMNIGENT_POSTGRES_PASSWORD:-omnigent}@postgres:5432/"
        "${OMNIGENT_POSTGRES_DB:-omnigent}"
    )
    assert omnigent_env["ARTIFACT_DIR"] == "${OMNIGENT_ARTIFACT_DIR:-/data/artifacts}"
    assert omnigent_env["HOST"] == "0.0.0.0"
    assert omnigent_env["PORT"] == "8000"
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
    assert omnigent_env["OMNIGENT_ACCOUNTS_AUTO_OPEN"] == (
        "${OMNIGENT_ACCOUNTS_AUTO_OPEN:-0}"
    )
    assert omnigent_env["OMNIGENT_OIDC_ISSUER"] == "${OMNIGENT_OIDC_ISSUER:-}"
    assert "POSTGRES_USER" not in omnigent_env
    assert "POSTGRES_PASSWORD" not in omnigent_env
    assert "POSTGRES_DB" not in omnigent_env
    assert "OMNIGENT_BUILTIN_ADMIN_EMAIL" not in omnigent_env
    assert "OMNIGENT_OIDC_ENABLED" not in omnigent_env
    assert "OMNIGENT_OIDC_ISSUER_URL" not in omnigent_env


def test_omnigent_env_template_and_example_config_for_mm_970():
    env_template = (REPO_ROOT / ".env-template").read_text(encoding="utf-8")
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
        "COMPOSE_PROFILES",
    ):
        assert f"{expected_name}=" in env_template
    env_lines = env_template.splitlines()
    assert not any(line.startswith("COMPOSE_FILE=") for line in env_lines)
    assert not any(line.startswith("COMPOSE_PATH_SEPARATOR=") for line in env_lines)
    for removed_name in (
        "OMNIGENT_BUILTIN_ADMIN_EMAIL",
        "OMNIGENT_BUILTIN_ADMIN_PASSWORD",
        "OMNIGENT_BUILTIN_USER_EMAIL",
        "OMNIGENT_BUILTIN_USER_PASSWORD",
        "OMNIGENT_OIDC_ENABLED",
        "OMNIGENT_OIDC_ISSUER_URL",
    ):
        assert f"{removed_name}=" not in env_template

    config = yaml.safe_load(
        (REPO_ROOT / "deploy" / "omnigent" / "server-config.example.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert config["host"] == "0.0.0.0"
    assert config["port"] == 8000
    assert config["artifact_location"] == "/data/artifacts"
    assert config["admins"] == []
    assert config["allowed_domains"] == []
    assert "sandbox" not in config
