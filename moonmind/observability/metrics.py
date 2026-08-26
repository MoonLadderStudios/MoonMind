"""Canonical bounded metric registry used by MoonMind exporters."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping

FORBIDDEN_LABELS = frozenset({"workflow_id", "run_id", "step_id", "session_id", "user", "repository", "branch", "artifact_id"})
BOUNDED_VALUES = {
    "component": frozenset({"api", "worker", "temporal", "provider", "omnigent", "artifact", "operator"}),
    "outcome": frozenset({"success", "failure", "canceled", "timeout", "denied", "unknown"}),
    "runtime_family": frozenset({"omnigent", "codex", "claude", "other", "unknown"}),
}

@dataclass(frozen=True)
class MetricDefinition:
    name: str
    kind: str
    unit: str
    labels: tuple[str, ...]
    owner: str
    consumers: tuple[str, ...]

REGISTRY = (
    MetricDefinition("moonmind_api_requests", "counter", "requests", ("outcome",), "api", ("overview", "api-slo")),
    MetricDefinition("moonmind_api_request_duration_seconds", "histogram", "seconds", ("outcome",), "api", ("overview", "api-slo")),
    MetricDefinition("moonmind_workflow_started", "counter", "workflows", ("outcome",), "workflows", ("overview", "workflow-slo")),
    MetricDefinition("moonmind_workflow_duration_seconds", "histogram", "seconds", ("outcome",), "workflows", ("workflow", "workflow-slo")),
    MetricDefinition("moonmind_task_schedule_to_start_seconds", "histogram", "seconds", ("component",), "temporal", ("temporal", "queue-slo")),
    MetricDefinition("moonmind_provider_requests", "counter", "requests", ("outcome", "runtime_family"), "profiles", ("providers", "provider-slo")),
    MetricDefinition("moonmind_omnigent_session_start_seconds", "histogram", "seconds", ("outcome",), "runtime", ("omnigent", "session-slo")),
    MetricDefinition("moonmind_omnigent_event_lag_seconds", "gauge", "seconds", ("runtime_family",), "runtime", ("omnigent", "freshness-slo")),
    MetricDefinition("moonmind_artifact_operations", "counter", "operations", ("outcome",), "artifacts", ("artifacts", "artifact-slo")),
    MetricDefinition("moonmind_observability_stream_lag_seconds", "histogram", "seconds", ("component",), "operator", ("artifacts", "logs-slo")),
    MetricDefinition("moonmind_policy_decisions", "counter", "decisions", ("outcome",), "security", ("safety",)),
    MetricDefinition("moonmind_usage_attribution_coverage_ratio", "gauge", "ratio", ("runtime_family",), "profiles", ("providers", "attribution-slo")),
)

def definition(name: str) -> MetricDefinition:
    for metric in REGISTRY:
        if metric.name == name:
            return metric
    raise KeyError(f"unknown MoonMind metric: {name}")

def normalize_labels(metric_name: str, labels: Mapping[str, str]) -> dict[str, str]:
    metric = definition(metric_name)
    unknown = set(labels) - set(metric.labels)
    if unknown:
        raise ValueError(f"unknown labels for {metric_name}: {sorted(unknown)}")
    result = {}
    for key in metric.labels:
        value = labels.get(key, "unknown")
        allowed = BOUNDED_VALUES[key]
        result[key] = value if value in allowed else "other"
    return result
