"""Default core-worker topology and the boundaries retained by consolidation."""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
FLEETS = ("workflow", "artifacts", "llm", "integrations")


def test_default_install_has_twelve_persistent_services():
    services = yaml.safe_load((ROOT / "docker-compose.yaml").read_text())["services"]
    profiled = {name for name, spec in services.items() if spec.get("profiles")}
    init = {
        dependency
        for spec in services.values()
        for dependency, condition in spec.get("depends_on", {}).items()
        if condition.get("condition") == "service_completed_successfully"
    }
    persistent = set(services) - profiled - init
    assert len(persistent) == 12, sorted(persistent)
    assert {"temporal-worker-core", "controller"} <= persistent
    assert not {f"temporal-worker-{fleet}" for fleet in FLEETS} & set(services)


def test_core_retains_fleet_configuration_and_process_boundaries():
    services = yaml.safe_load((ROOT / "docker-compose.yaml").read_text())["services"]
    core = services["temporal-worker-core"]
    env = dict(entry.split("=", 1) for entry in core["environment"])
    assert tuple(json.loads(env["TEMPORAL_WORKER_FLEETS"])) == FLEETS
    assert env["TEMPORAL_WORKER_FLEET"] == "artifacts"
    for key, default in {
        "TEMPORAL_WORKFLOW_TASK_QUEUE": "mm.workflow",
        "TEMPORAL_USER_WORKFLOW_V2_TASK_QUEUE": "mm.workflow.user.v2",
        "TEMPORAL_MERGE_AUTOMATION_WORKFLOW_TASK_QUEUE": "mm.workflow.merge_automation",
        "TEMPORAL_ACTIVITY_ARTIFACTS_TASK_QUEUE": "mm.activity.artifacts",
        "TEMPORAL_ACTIVITY_LLM_TASK_QUEUE": "mm.activity.llm",
        "TEMPORAL_ACTIVITY_INTEGRATIONS_TASK_QUEUE": "mm.activity.integrations",
        "TEMPORAL_WORKFLOW_WORKER_CONCURRENCY": "8",
        "TEMPORAL_MERGE_AUTOMATION_WORKFLOW_WORKER_CONCURRENCY": "2",
        "TEMPORAL_ARTIFACTS_WORKER_CONCURRENCY": "8",
        "TEMPORAL_LLM_WORKER_CONCURRENCY": "4",
        "TEMPORAL_INTEGRATIONS_WORKER_CONCURRENCY": "4",
        "MOONMIND_INSTALLATION_ID": "",
    }.items():
        assert env[key] == "${" + key + ":-" + default + "}"
    assert core["labels"]["moonmind.journal-consumer"] == "true"
    assert core["stop_grace_period"] == "6m"
    assert core["healthcheck"]["start_period"] == "3m"
    assert "/readyz" in " ".join(core["healthcheck"]["test"])
    assert set(core["depends_on"]) == {"temporal", "temporal-namespace-init", "minio"}
    assert core["networks"] == ["control-plane-network"]
    assert "agent_workspaces:/work/agent_jobs" in core["volumes"]
    assert "moonmind_secrets:/app/var/secrets" in core["volumes"]
    assert any(
        "sandbox-egress-proxy" in volume and volume.endswith(":ro")
        for volume in core["volumes"]
    )
    assert not any("docker.sock" in volume for volume in core["volumes"])
    assert core["env_file"] == [{"path": ".env", "required": False}]
    for service in ("api", "temporal-worker-core", "temporal-worker-deployment-control"):
        environment = dict(
            entry.split("=", 1) for entry in services[service]["environment"]
        )
        assert environment["TEMPORAL_WORKFLOW_READINESS_URL"] == (
            "${TEMPORAL_WORKFLOW_READINESS_URL:-http://temporal-worker-core:8080/readyz}"
        )


def test_fleet_discovery_targets_the_hosting_service():
    from moonmind.workflows.temporal.workers import build_all_worker_topologies

    topologies = {topology.fleet: topology for topology in build_all_worker_topologies()}
    assert {topologies[fleet].service_name for fleet in FLEETS} == {
        "temporal-worker-core"
    }
    assert topologies["agent_runtime"].service_name == "temporal-worker-agent-runtime"
    assert topologies["sandbox"].service_name == "temporal-worker-sandbox"
