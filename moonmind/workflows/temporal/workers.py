"""Worker-topology bootstrap helpers for Temporal activity fleets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from temporalio import activity, workflow

from pr_resolver_core import (
    IMPLEMENTATION_CONTRACT,
    RESOLVER_CORE_DIGEST,
    RESOLVER_CORE_VERSION,
)

from moonmind.config.settings import AppSettings, TemporalSettings, settings
from moonmind.workflows.temporal.activity_catalog import (
    AGENT_RUNTIME_FLEET,
    ARTIFACTS_FLEET,
    INTEGRATIONS_FLEET,
    LLM_FLEET,
    SANDBOX_FLEET,
    WORKFLOW_FLEET,
    DEPLOYMENT_FLEET,
    TemporalActivityCatalog,
    TemporalActivityCatalogError,
    TemporalWorkerFleet,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalActivityBinding,
    build_activity_bindings,
    validate_activity_catalog_runtime_bindings,
)
from moonmind.workflows.temporal.workflow_registry import (
    workflow_fleet_workflow_types,
)

ALLOWED_TEMPORAL_WORKER_FLEETS = (
    WORKFLOW_FLEET,
    ARTIFACTS_FLEET,
    LLM_FLEET,
    SANDBOX_FLEET,
    INTEGRATIONS_FLEET,
    AGENT_RUNTIME_FLEET,
    DEPLOYMENT_FLEET,
)

_FLEET_SERVICE_NAMES = {
    WORKFLOW_FLEET: "temporal-worker-workflow",
    ARTIFACTS_FLEET: "temporal-worker-artifacts",
    LLM_FLEET: "temporal-worker-llm",
    SANDBOX_FLEET: "temporal-worker-sandbox",
    INTEGRATIONS_FLEET: "temporal-worker-integrations",
    AGENT_RUNTIME_FLEET: "temporal-worker-agent-runtime",
    DEPLOYMENT_FLEET: "temporal-worker-deployment-control",
}
_FLEET_RESOURCE_CLASSES = {
    WORKFLOW_FLEET: "light",
    ARTIFACTS_FLEET: "io_bound",
    LLM_FLEET: "rate_limited",
    SANDBOX_FLEET: "cpu_mem_heavy",
    INTEGRATIONS_FLEET: "rate_limited",
    AGENT_RUNTIME_FLEET: "cpu_mem_heavy",
    DEPLOYMENT_FLEET: "singleton_control",
}
_FLEET_EGRESS_POLICIES = {
    WORKFLOW_FLEET: "temporal-only",
    ARTIFACTS_FLEET: "artifact-store-only",
    LLM_FLEET: "llm-provider-only",
    SANDBOX_FLEET: "restricted-sandbox-egress",
    INTEGRATIONS_FLEET: "provider-api-only",
    AGENT_RUNTIME_FLEET: "restricted-sandbox-egress",
    DEPLOYMENT_FLEET: "docker-proxy-only",
}
_FLEET_FORBIDDEN_CAPABILITIES = {
    WORKFLOW_FLEET: (
        "artifacts",
        "llm",
        "sandbox",
        "integration:jules",
        "integration:openclaw",
        "integration:omnigent",
        "agent_runtime",
        "docker_workload",
    ),
    ARTIFACTS_FLEET: (
        "llm",
        "sandbox",
        "integration:jules",
        "integration:openclaw",
        "integration:omnigent",
        "agent_runtime",
        "docker_workload",
    ),
    LLM_FLEET: (
        "sandbox",
        "integration:jules",
        "integration:openclaw",
        "integration:omnigent",
        "agent_runtime",
        "docker_workload",
    ),
    SANDBOX_FLEET: (
        "llm",
        "integration:jules",
        "integration:openclaw",
        "integration:omnigent",
        "agent_runtime",
        "docker_workload",
    ),
    INTEGRATIONS_FLEET: ("sandbox", "agent_runtime", "docker_workload"),
    AGENT_RUNTIME_FLEET: (
        "sandbox",
        "llm",
        "integration:jules",
        "integration:openclaw",
        "integration:omnigent",
    ),
    DEPLOYMENT_FLEET: (
        "artifacts",
        "llm",
        "sandbox",
        "integration:jules",
        "integration:openclaw",
        "integration:omnigent",
        "agent_runtime",
        "docker_workload",
    ),
}


class TemporalWorkerBootstrapError(ValueError):
    """Raised when the worker topology cannot be resolved safely."""


def list_registered_workflow_types() -> tuple[str, ...]:
    """Return the workflow types owned by the workflow fleet."""

    return list_registered_workflow_types_for_settings(settings.temporal)


def list_registered_workflow_types_for_settings(
    temporal_settings: TemporalSettings,
) -> tuple[str, ...]:
    """Return workflow registrations for the configured user-workflow contract."""

    return workflow_fleet_workflow_types(temporal_settings)


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


@dataclass(frozen=True, slots=True)
class WorkerSpec:
    """One executable worker specification used by SDK wiring and diagnostics."""

    fleet: str
    task_queues: tuple[str, ...]
    workflows: tuple[type[Any], ...]
    activities: tuple[Callable[..., Any], ...]
    workflow_types: tuple[str, ...]
    activity_types: tuple[str, ...]
    registry_fingerprint: str
    build_id: str
    build_sha: str | None
    image_digest: str | None
    deployment_id: str
    versioning_enabled: bool
    deployment_mode: str
    immutable_release_identity: bool

    def readiness_payload(self) -> dict[str, Any]:
        return {
            "ready": True,
            "fleet": self.fleet,
            "buildId": self.build_id,
            "buildSha": self.build_sha,
            "imageDigest": self.image_digest,
            "deploymentId": self.deployment_id,
            "registryFingerprint": self.registry_fingerprint,
            "taskQueues": list(self.task_queues),
            "workflowTypes": list(self.workflow_types),
            "activityTypes": list(self.activity_types),
            "workerVersioningEnabled": self.versioning_enabled,
            "deploymentMode": self.deployment_mode,
            "immutableReleaseIdentity": self.immutable_release_identity,
            "resolverCore": {
                "contract": IMPLEMENTATION_CONTRACT,
                "version": RESOLVER_CORE_VERSION,
                "digest": RESOLVER_CORE_DIGEST,
            },
        }


def _workflow_type(workflow_class: type[Any]) -> str:
    return workflow._Definition.must_from_class(workflow_class).name


def _activity_type(handler: Callable[..., Any]) -> str:
    return activity._Definition.must_from_callable(handler).name


def _workflow_source_digest(workflow_class: type[Any]) -> str:
    module = __import__(workflow_class.__module__, fromlist=[workflow_class.__name__])
    path_value = getattr(module, "__file__", None)
    if not path_value:
        return f"{workflow_class.__module__}:{workflow_class.__qualname__}"
    path = Path(path_value)
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return f"{workflow_class.__module__}:{workflow_class.__qualname__}"


def build_worker_spec(
    *,
    topology: TemporalWorkerTopology,
    workflows: Sequence[type[Any]],
    activities: Sequence[Callable[..., Any]],
    environ: dict[str, str] | None = None,
) -> WorkerSpec:
    """Build the exact immutable worker identity passed to Temporal."""

    env = os.environ if environ is None else environ
    workflow_classes = tuple(workflows)
    activity_handlers = tuple(activities)
    workflow_types = tuple(_workflow_type(item) for item in workflow_classes)
    activity_types = tuple(_activity_type(item) for item in activity_handlers)
    fingerprint_input = {
        "workflowTypes": list(workflow_types),
        "workflowSourceDigests": [
            _workflow_source_digest(item) for item in workflow_classes
        ],
        "activityTypes": list(activity_types),
        "resolverCoreDigest": RESOLVER_CORE_DIGEST,
    }
    fingerprint = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(fingerprint_input, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
    )
    build_sha = str(env.get("MOONMIND_BUILD_SHA") or "").strip() or None
    image_digest = str(env.get("MOONMIND_IMAGE_DIGEST") or "").strip() or None
    build_id = build_sha or image_digest or fingerprint.split(":", 1)[1][:32]
    deployment_id = str(
        env.get("TEMPORAL_WORKER_DEPLOYMENT_NAME") or "moonmind-workflow-fleet"
    ).strip()
    versioning_enabled = str(
        env.get("TEMPORAL_WORKER_VERSIONING_ENABLED") or "false"
    ).strip().lower() in {"1", "true", "yes", "on"}
    deployment_mode = (
        str(env.get("MOONMIND_DEPLOYMENT_MODE") or "development").strip().lower()
    )
    immutable_release_identity = bool(build_sha or image_digest)
    if deployment_mode == "production" and not immutable_release_identity:
        raise TemporalWorkerBootstrapError(
            "production workflow workers require MOONMIND_BUILD_SHA or "
            "MOONMIND_IMAGE_DIGEST"
        )
    if deployment_mode == "production" and not versioning_enabled:
        raise TemporalWorkerBootstrapError(
            "production workflow workers require "
            "TEMPORAL_WORKER_VERSIONING_ENABLED=true"
        )
    return WorkerSpec(
        fleet=topology.fleet,
        task_queues=tuple(topology.task_queues),
        workflows=workflow_classes,
        activities=activity_handlers,
        workflow_types=workflow_types,
        activity_types=activity_types,
        registry_fingerprint=fingerprint,
        build_id=build_id,
        build_sha=build_sha,
        image_digest=image_digest,
        deployment_id=deployment_id,
        versioning_enabled=versioning_enabled,
        deployment_mode=deployment_mode,
        immutable_release_identity=immutable_release_identity,
    )


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
        DEPLOYMENT_FLEET: temporal_settings.deployment_worker_concurrency,
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
    validate_activity_catalog_runtime_bindings(resolved_catalog)
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
    manifest_activities: Any | None = None,
    skill_activities: Any | None = None,
    sandbox_activities: Any | None = None,
    integration_activities: Any | None = None,
    agent_runtime_activities: Any | None = None,
    proposal_activities: Any | None = None,
    review_activities: Any | None = None,
    agent_skills_activities: Any | None = None,
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
        manifest_activities=manifest_activities,
        skill_activities=skill_activities,
        sandbox_activities=sandbox_activities,
        integration_activities=integration_activities,
        agent_runtime_activities=agent_runtime_activities,
        proposal_activities=proposal_activities,
        review_activities=review_activities,
        agent_skills_activities=agent_skills_activities,
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
