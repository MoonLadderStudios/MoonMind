"""Worker-topology bootstrap helpers for Temporal activity fleets."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any, Sequence

from moonmind.config.settings import AppSettings, TemporalSettings, settings
from moonmind.workflows.temporal.activity_catalog import (
    AGENT_RUNTIME_FLEET,
    ARTIFACTS_FLEET,
    INTEGRATIONS_FLEET,
    LLM_FLEET,
    SANDBOX_FLEET,
    WORKFLOW_FLEET,
    TemporalActivityCatalog,
    TemporalActivityCatalogError,
    TemporalWorkerFleet,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalActivityBinding,
    build_activity_bindings,
)

ALLOWED_TEMPORAL_WORKER_FLEETS = (
    WORKFLOW_FLEET,
    ARTIFACTS_FLEET,
    LLM_FLEET,
    SANDBOX_FLEET,
    INTEGRATIONS_FLEET,
    AGENT_RUNTIME_FLEET,
)

_FLEET_SERVICE_NAMES = {
    WORKFLOW_FLEET: "temporal-worker-workflow",
    ARTIFACTS_FLEET: "temporal-worker-artifacts",
    LLM_FLEET: "temporal-worker-llm",
    SANDBOX_FLEET: "temporal-worker-sandbox",
    INTEGRATIONS_FLEET: "temporal-worker-integrations",
    AGENT_RUNTIME_FLEET: "temporal-worker-agent-runtime",
}
_FLEET_RESOURCE_CLASSES = {
    WORKFLOW_FLEET: "light",
    ARTIFACTS_FLEET: "io_bound",
    LLM_FLEET: "rate_limited",
    SANDBOX_FLEET: "cpu_mem_heavy",
    INTEGRATIONS_FLEET: "rate_limited",
    AGENT_RUNTIME_FLEET: "cpu_mem_heavy",
}
_FLEET_EGRESS_POLICIES = {
    WORKFLOW_FLEET: "temporal-only",
    ARTIFACTS_FLEET: "artifact-store-only",
    LLM_FLEET: "llm-provider-only",
    SANDBOX_FLEET: "restricted-sandbox-egress",
    INTEGRATIONS_FLEET: "provider-api-only",
    AGENT_RUNTIME_FLEET: "restricted-sandbox-egress",
}
_FLEET_FORBIDDEN_CAPABILITIES = {
    WORKFLOW_FLEET: ("artifacts", "llm", "sandbox", "integration:jules", "agent_runtime"),
    ARTIFACTS_FLEET: ("llm", "sandbox", "integration:jules", "agent_runtime"),
    LLM_FLEET: ("sandbox", "integration:jules", "agent_runtime"),
    SANDBOX_FLEET: ("llm", "integration:jules", "agent_runtime"),
    INTEGRATIONS_FLEET: ("sandbox", "agent_runtime"),
    AGENT_RUNTIME_FLEET: ("llm", "integration:jules"),
}
REGISTERED_TEMPORAL_WORKFLOW_TYPES = (
    "MoonMind.Run",
    "MoonMind.ManifestIngest",
    "MoonMind.AuthProfileManager",
)


class TemporalWorkerBootstrapError(ValueError):
    """Raised when the worker topology cannot be resolved safely."""


def list_registered_workflow_types() -> tuple[str, ...]:
    """Return the workflow types owned by the workflow fleet."""

    return REGISTERED_TEMPORAL_WORKFLOW_TYPES


@dataclass(frozen=True, slots=True)
class TemporalWorkerTopology:
    """Operational topology for one Temporal worker fleet."""

    fleet: str
    service_name: str
    task_queues: tuple[str, ...]
    capabilities: tuple[str, ...]
    privileges: tuple[str, ...]
    required_secrets: tuple[str, ...]
    forbidden_capabilities: tuple[str, ...]
    activity_types: tuple[str, ...]
    concurrency_limit: int | None
    resource_class: str
    egress_policy: str

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


def normalize_worker_fleet(fleet: str) -> str:
    """Return a canonical fleet name or fail closed."""

    normalized = str(fleet or "").strip().lower()
    if normalized not in ALLOWED_TEMPORAL_WORKER_FLEETS:
        raise TemporalWorkerBootstrapError(
            "Unknown Temporal worker fleet "
            f"'{fleet}'. Expected one of: {', '.join(ALLOWED_TEMPORAL_WORKER_FLEETS)}"
        )
    return normalized


def _artifact_secrets(app_settings: AppSettings) -> tuple[str, ...]:
    if app_settings.workflow.temporal_artifact_backend == "s3":
        return (
            "TEMPORAL_ARTIFACT_S3_ENDPOINT",
            "TEMPORAL_ARTIFACT_S3_BUCKET",
            "TEMPORAL_ARTIFACT_S3_ACCESS_KEY_ID",
            "TEMPORAL_ARTIFACT_S3_SECRET_ACCESS_KEY",
        )
    return ()


def _required_secrets_for_fleet(
    fleet: str, *, app_settings: AppSettings
) -> tuple[str, ...]:
    if fleet == ARTIFACTS_FLEET:
        return _artifact_secrets(app_settings)
    if fleet == LLM_FLEET:
        return (
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "ANTHROPIC_API_KEY",
        )
    if fleet == INTEGRATIONS_FLEET:
        return ("JULES_API_URL", "JULES_API_KEY")
    return ()


def _concurrency_limit_for_fleet(
    fleet: str, *, temporal_settings: TemporalSettings
) -> int | None:
    return {
        WORKFLOW_FLEET: temporal_settings.workflow_worker_concurrency,
        ARTIFACTS_FLEET: temporal_settings.artifacts_worker_concurrency,
        LLM_FLEET: temporal_settings.llm_worker_concurrency,
        SANDBOX_FLEET: temporal_settings.sandbox_worker_concurrency,
        INTEGRATIONS_FLEET: temporal_settings.integrations_worker_concurrency,
        AGENT_RUNTIME_FLEET: temporal_settings.agent_runtime_worker_concurrency,
    }[fleet]


def _fleet_entry(
    catalog: TemporalActivityCatalog, *, fleet: str
) -> TemporalWorkerFleet:
    normalized = normalize_worker_fleet(fleet)
    for entry in catalog.fleets:
        if entry.fleet == normalized:
            return entry
    raise TemporalWorkerBootstrapError(
        f"Temporal catalog does not define worker fleet '{normalized}'"
    )


def build_worker_topology(
    *,
    fleet: str,
    catalog: TemporalActivityCatalog | None = None,
    temporal_settings: TemporalSettings | None = None,
    app_settings: AppSettings | None = None,
) -> TemporalWorkerTopology:
    """Build the least-privilege topology contract for one worker fleet."""

    app_cfg = app_settings or settings
    temporal_cfg = temporal_settings or app_cfg.temporal
    resolved_catalog = catalog or build_default_activity_catalog(temporal_cfg)
    entry = _fleet_entry(resolved_catalog, fleet=fleet)
    normalized = entry.fleet
    activity_types = entry.activity_types
    if normalized == WORKFLOW_FLEET:
        activity_types = ()

    return TemporalWorkerTopology(
        fleet=normalized,
        service_name=_FLEET_SERVICE_NAMES[normalized],
        task_queues=entry.task_queues,
        capabilities=entry.capabilities,
        privileges=entry.privileges,
        required_secrets=_required_secrets_for_fleet(
            normalized,
            app_settings=app_cfg,
        ),
        forbidden_capabilities=_FLEET_FORBIDDEN_CAPABILITIES[normalized],
        activity_types=activity_types,
        concurrency_limit=_concurrency_limit_for_fleet(
            normalized,
            temporal_settings=temporal_cfg,
        ),
        resource_class=_FLEET_RESOURCE_CLASSES[normalized],
        egress_policy=_FLEET_EGRESS_POLICIES[normalized],
    )


def build_all_worker_topologies(
    *,
    catalog: TemporalActivityCatalog | None = None,
    temporal_settings: TemporalSettings | None = None,
    app_settings: AppSettings | None = None,
) -> tuple[TemporalWorkerTopology, ...]:
    """Return topology metadata for every canonical fleet."""

    resolved_catalog = catalog or build_default_activity_catalog(
        temporal_settings or (app_settings or settings).temporal
    )
    return tuple(
        build_worker_topology(
            fleet=fleet,
            catalog=resolved_catalog,
            temporal_settings=temporal_settings,
            app_settings=app_settings,
        )
        for fleet in ALLOWED_TEMPORAL_WORKER_FLEETS
    )


def build_worker_activity_bindings(
    *,
    fleet: str,
    catalog: TemporalActivityCatalog | None = None,
    artifact_activities: Any | None = None,
    plan_activities: Any | None = None,
    skill_activities: Any | None = None,
    sandbox_activities: Any | None = None,
    integration_activities: Any | None = None,
) -> tuple[TemporalActivityBinding, ...]:
    """Resolve activity handlers for exactly one activity fleet."""

    normalized = normalize_worker_fleet(fleet)
    if normalized == WORKFLOW_FLEET:
        return ()

    resolved_catalog = catalog or build_default_activity_catalog()
    return build_activity_bindings(
        resolved_catalog,
        artifact_activities=artifact_activities,
        plan_activities=plan_activities,
        skill_activities=skill_activities,
        sandbox_activities=sandbox_activities,
        integration_activities=integration_activities,
        fleets=(normalized,),
    )


def describe_configured_worker(
    *,
    temporal_settings: TemporalSettings | None = None,
    app_settings: AppSettings | None = None,
    catalog: TemporalActivityCatalog | None = None,
) -> TemporalWorkerTopology:
    """Resolve the topology described by current worker env."""

    app_cfg = app_settings or settings
    temporal_cfg = temporal_settings or app_cfg.temporal
    return build_worker_topology(
        fleet=temporal_cfg.worker_fleet,
        catalog=catalog,
        temporal_settings=temporal_cfg,
        app_settings=app_cfg,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="moonmind-temporal-worker-bootstrap")
    parser.add_argument(
        "--fleet",
        help="Temporal worker fleet to resolve. Defaults to TEMPORAL_WORKER_FLEET.",
    )
    parser.add_argument(
        "--describe-json",
        action="store_true",
        help="Emit the resolved worker topology as JSON and exit.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint used by the shared worker launcher."""

    args = _build_parser().parse_args(argv)
    temporal_cfg = settings.temporal
    if args.fleet:
        temporal_cfg = temporal_cfg.model_copy(update={"worker_fleet": args.fleet})

    try:
        topology = describe_configured_worker(temporal_settings=temporal_cfg)
    except (TemporalActivityCatalogError, TemporalWorkerBootstrapError) as exc:
        raise SystemExit(str(exc)) from exc

    payload = topology.to_payload()
    if args.describe_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(
            f"{payload['service_name']} [{payload['fleet']}] "
            f"queues={','.join(payload['task_queues'])} "
            f"concurrency={payload['concurrency_limit']}"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "ALLOWED_TEMPORAL_WORKER_FLEETS",
    "TemporalWorkerBootstrapError",
    "TemporalWorkerTopology",
    "build_all_worker_topologies",
    "build_worker_activity_bindings",
    "build_worker_topology",
    "describe_configured_worker",
    "main",
    "normalize_worker_fleet",
]
