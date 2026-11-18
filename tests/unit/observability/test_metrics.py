import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
from moonmind.observability.metrics import FORBIDDEN_LABELS, REGISTRY, definition, normalize_labels

ROOT = Path(__file__).parents[3]

def test_registry_is_unique_bounded_and_documented():
    docs = (ROOT / "docs/Observability/MetricsAndDashboards.md").read_text()
    assert len({m.name for m in REGISTRY}) == len(REGISTRY)
    for metric in REGISTRY:
        assert metric.name.startswith("moonmind_") and metric.name in docs
        assert not FORBIDDEN_LABELS.intersection(metric.labels)
        assert metric.owner and metric.consumers

def test_registry_rejects_unknown_input_and_bounds_values():
    with pytest.raises(KeyError): definition("unknown")
    with pytest.raises(ValueError): normalize_labels("moonmind_api_requests", {"run_id": "x"})
    assert normalize_labels("moonmind_provider_requests", {"outcome": "success", "runtime_family": "new"})["runtime_family"] == "other"
    legacy_path = "moonmind_workspace_locator_legacy_path_usage"
    assert normalize_labels(legacy_path, {"operation": "apply_policy"}) == {"operation": "apply_policy"}
    assert normalize_labels(legacy_path, {"operation": "/tmp/workspace"}) == {"operation": "other"}

def test_dashboard_queries_registered_metrics_and_runbooks_exist():
    dashboard = json.loads((ROOT / "deploy/observability/grafana/dashboards/moonmind-overview.json").read_text())
    expressions = " ".join(target["expr"] for panel in dashboard["panels"] for target in panel["targets"])
    assert len(dashboard["panels"]) == 7
    for metric in REGISTRY:
        assert metric.name in expressions
    rules = (ROOT / "deploy/observability/prometheus/rules.yaml").read_text()
    assert "docs/Runbooks/Observability/ServiceHealth.md" in rules
    assert (ROOT / "docs/Runbooks/Observability/ServiceHealth.md").is_file()

_STARTUP_PROBE = """
import socket, sys
datagram_sockets = []
def audit(event, args):
    if event == "socket.__new__" and args[2] & 0xF == socket.SOCK_DGRAM:
        datagram_sockets.append(args[1:])
sys.addaudithook(audit)
import api_service.main
import moonmind.workflows.temporal.worker_runtime
print(datagram_sockets)
"""

def test_api_and_worker_startup_open_no_udp_socket_with_old_statsd_environment():
    """Old StatsD variables left in a deployment environment are inert."""
    env = dict(os.environ)
    for name in ("STATSD_HOST", "WORKFLOW_METRICS_HOST", "TASK_TEMPLATE_METRICS_HOST"):
        env[name] = "127.0.0.1"
    result = subprocess.run(
        [sys.executable, "-c", _STARTUP_PROBE], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300
    )
    assert result.returncode == 0, result.stderr[-4000:]
    assert result.stdout.strip().splitlines()[-1] == "[]"
