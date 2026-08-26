import json
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

def test_dashboard_queries_registered_metrics_and_runbooks_exist():
    dashboard = json.loads((ROOT / "deploy/observability/grafana/dashboards/moonmind-overview.json").read_text())
    expressions = " ".join(target["expr"] for panel in dashboard["panels"] for target in panel["targets"])
    assert len(dashboard["panels"]) == 7
    for metric in REGISTRY:
        assert metric.name in expressions
    rules = (ROOT / "deploy/observability/prometheus/rules.yaml").read_text()
    assert "docs/Runbooks/Observability/ServiceHealth.md" in rules
    assert (ROOT / "docs/Runbooks/Observability/ServiceHealth.md").is_file()
