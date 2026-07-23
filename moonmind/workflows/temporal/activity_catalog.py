"""Canonical Temporal activity catalog and worker topology helpers."""

from __future__ import annotations

from dataclasses import dataclass

from moonmind.config.settings import TemporalSettings, settings
from moonmind.workflows.codex_session_timeouts import (
    SEND_TURN_ACTIVITY_HEARTBEAT_TIMEOUT_SECONDS,
    SEND_TURN_ACTIVITY_SCHEDULE_TO_CLOSE_SECONDS,
    SEND_TURN_ACTIVITY_START_TO_CLOSE_SECONDS,
)
from moonmind.workflows.skills.skill_plan_contracts import (
    SkillDefinition,
    SkillPolicies,
)
from moonmind.workflows.temporal.hard_switch_cutover import (
    resolve_user_workflow_start_contract,
)

WORKFLOW_FLEET = "workflow"
ARTIFACTS_FLEET = "artifacts"
LLM_FLEET = "llm"
SANDBOX_FLEET = "sandbox"
INTEGRATIONS_FLEET = "integrations"
AGENT_RUNTIME_FLEET = "agent_runtime"
DEPLOYMENT_FLEET = "deployment"

WORKFLOW_TASK_QUEUE = "mm.workflow"
ARTIFACTS_TASK_QUEUE = "mm.activity.artifacts"
LLM_TASK_QUEUE = "mm.activity.llm"
SANDBOX_TASK_QUEUE = "mm.activity.sandbox"
INTEGRATIONS_TASK_QUEUE = "mm.activity.integrations"
AGENT_RUNTIME_TASK_QUEUE = "mm.activity.agent_runtime"
DEPLOYMENT_TASK_QUEUE = "mm.activity.deployment"


def get_workflow_task_queue(
    temporal_settings: TemporalSettings | None = None,
) -> str:
    """Resolve the currently configured workflow-fleet task queue lazily."""

    return resolve_user_workflow_start_contract(
        temporal_settings or settings.temporal
    ).task_queue


def get_workflow_poll_task_queues(
    temporal_settings: TemporalSettings | None = None,
) -> tuple[str, ...]:
    """Resolve workflow queues the workflow fleet must poll.

    New workflow starts use the hard-switch start contract queue.  In-flight
    histories may still contain pre-patch child-workflow commands recorded on
    TEMPORAL_WORKFLOW_TASK_QUEUE, so the workflow fleet must keep polling that
    queue until those histories have drained.
    """

    cfg = temporal_settings or settings.temporal
    start_queue = get_workflow_task_queue(cfg)
    replay_queue = (
        str(cfg.workflow_task_queue).strip()
        if cfg.workflow_task_queue is not None
        else ""
    )
    merge_queue = str(cfg.merge_automation_workflow_task_queue).strip()
    if replay_queue and replay_queue != start_queue and start_queue != merge_queue:
        return (start_queue, replay_queue)
    return (start_queue,)


class TemporalActivityCatalogError(ValueError):
    """Raised when Temporal activity routing metadata is invalid."""


@dataclass(frozen=True, slots=True)
class TemporalActivityTimeouts:
    """Default timeout policy for one activity type."""

    start_to_close_seconds: int
    schedule_to_close_seconds: int
    heartbeat_timeout_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.start_to_close_seconds <= 0:
            raise TemporalActivityCatalogError(
                "start_to_close_seconds must be greater than zero"
            )
        if self.schedule_to_close_seconds < self.start_to_close_seconds:
            raise TemporalActivityCatalogError(
                "schedule_to_close_seconds must be >= start_to_close_seconds"
            )
        if (
            self.heartbeat_timeout_seconds is not None
            and self.heartbeat_timeout_seconds <= 0
        ):
            raise TemporalActivityCatalogError(
                "heartbeat_timeout_seconds must be greater than zero"
            )


@dataclass(frozen=True, slots=True)
class TemporalActivityRetries:
    """Default retry policy for one activity type.

    See docs/Temporal/ErrorTaxonomy.md for error classification rules.
    """

    max_attempts: int
    max_interval_seconds: int
    non_retryable_error_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_attempts <= 0:
            raise TemporalActivityCatalogError("max_attempts must be greater than zero")
        if self.max_interval_seconds <= 0:
            raise TemporalActivityCatalogError(
                "max_interval_seconds must be greater than zero"
            )


@dataclass(frozen=True, slots=True)
class TemporalActivityDefinition:
    """Stable routing contract for one activity type."""

    activity_type: str
    family: str
    capability_class: str
    task_queue: str
    fleet: str
    timeouts: TemporalActivityTimeouts
    retries: TemporalActivityRetries
    heartbeat_required: bool = False


@dataclass(frozen=True, slots=True)
class TemporalWorkerFleet:
    """Worker fleet metadata used for routing and operational validation."""

    fleet: str
    task_queues: tuple[str, ...]
    capabilities: tuple[str, ...]
    privileges: tuple[str, ...]
    scaling_notes: str
    activity_types: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TemporalActivityRoute:
    """Resolved routing decision for one activity invocation."""

    activity_type: str
    task_queue: str
    fleet: str
    capability_class: str
    timeouts: TemporalActivityTimeouts
    retries: TemporalActivityRetries
    heartbeat_required: bool = False


def _activity_retries(
    *, max_attempts: int, max_interval_seconds: int, non_retryable: tuple[str, ...] = ()
) -> TemporalActivityRetries:
    return TemporalActivityRetries(
        max_attempts=max_attempts,
        max_interval_seconds=max_interval_seconds,
        non_retryable_error_codes=non_retryable,
    )


def _skill_route_family(required_capabilities: tuple[str, ...]) -> tuple[str, str]:
    categories: set[str] = set()
    integration_caps = [
        capability
        for capability in required_capabilities
        if capability.startswith("integration:")
    ]
    if "artifacts" in required_capabilities:
        categories.add("artifacts")
    if "llm" in required_capabilities:
        categories.add("llm")
    if "sandbox" in required_capabilities:
        categories.add("sandbox")
    if "docker_workload" in required_capabilities:
        categories.add("docker_workload")
    if "deployment_control" in required_capabilities:
        categories.add("deployment_control")
    if integration_caps:
        categories.add("integrations")

    if not categories:
        raise TemporalActivityCatalogError(
            "Skill definition must declare one routing capability"
        )
    if len(categories) > 1:
        raise TemporalActivityCatalogError(
            "Skill definition declares incompatible routing capabilities: "
            + ", ".join(sorted(categories))
        )

    category = next(iter(categories))
    if category == "artifacts":
        return ARTIFACTS_FLEET, "artifacts"
    if category == "llm":
        return LLM_FLEET, "llm"
    if category == "sandbox":
        return SANDBOX_FLEET, "sandbox"
    if category == "docker_workload":
        return AGENT_RUNTIME_FLEET, "docker_workload"
    if category == "deployment_control":
        return DEPLOYMENT_FLEET, "deployment_control"
    return INTEGRATIONS_FLEET, integration_caps[0]


class TemporalActivityCatalog:
    """Default activity catalog + worker fleet topology for Temporal workers."""

    def __init__(
        self,
        *,
        activities: tuple[TemporalActivityDefinition, ...],
        fleets: tuple[TemporalWorkerFleet, ...],
    ) -> None:
        self._activities = activities
        self._fleets = fleets
        self._activity_by_type = {entry.activity_type: entry for entry in activities}
        self._fleet_by_name = {entry.fleet: entry for entry in fleets}
        self._validate()

    @property
    def activities(self) -> tuple[TemporalActivityDefinition, ...]:
        return self._activities

    @property
    def fleets(self) -> tuple[TemporalWorkerFleet, ...]:
        return self._fleets

    def resolve_activity(self, activity_type: str) -> TemporalActivityRoute:
        try:
            entry = self._activity_by_type[activity_type]
        except KeyError as exc:
            raise TemporalActivityCatalogError(
                f"Unknown Temporal activity type '{activity_type}'"
            ) from exc
        return TemporalActivityRoute(
            activity_type=entry.activity_type,
            task_queue=entry.task_queue,
            fleet=entry.fleet,
            capability_class=entry.capability_class,
            timeouts=entry.timeouts,
            retries=entry.retries,
            heartbeat_required=entry.heartbeat_required,
        )

    def resolve_skill(self, definition: SkillDefinition) -> TemporalActivityRoute:
        activity_type = definition.executor.activity_type
        if activity_type not in {"mm.skill.execute", "mm.tool.execute"}:
            route = self.resolve_activity(activity_type)
        else:
            fleet, capability_class = _skill_route_family(
                definition.required_capabilities
            )
            fleet_entry = self._fleet_by_name[fleet]
            route = TemporalActivityRoute(
                activity_type=activity_type,
                task_queue=fleet_entry.task_queues[0],
                fleet=fleet,
                capability_class=capability_class,
                timeouts=TemporalActivityTimeouts(
                    start_to_close_seconds=definition.policies.timeouts.start_to_close_seconds,
                    schedule_to_close_seconds=definition.policies.timeouts.schedule_to_close_seconds,
                ),
                retries=TemporalActivityRetries(
                    max_attempts=definition.policies.retries.max_attempts,
                    max_interval_seconds=max(
                        definition.policies.timeouts.start_to_close_seconds,
                        min(
                            definition.policies.timeouts.schedule_to_close_seconds,
                            300,
                        ),
                    ),
                    non_retryable_error_codes=definition.policies.retries.non_retryable_error_codes,
                ),
                heartbeat_required=False,
            )

        return TemporalActivityRoute(
            activity_type=route.activity_type,
            task_queue=route.task_queue,
            fleet=route.fleet,
            capability_class=route.capability_class,
            timeouts=TemporalActivityTimeouts(
                start_to_close_seconds=definition.policies.timeouts.start_to_close_seconds,
                schedule_to_close_seconds=definition.policies.timeouts.schedule_to_close_seconds,
                heartbeat_timeout_seconds=route.timeouts.heartbeat_timeout_seconds,
            ),
            retries=TemporalActivityRetries(
                max_attempts=definition.policies.retries.max_attempts,
                max_interval_seconds=route.retries.max_interval_seconds,
                non_retryable_error_codes=definition.policies.retries.non_retryable_error_codes,
            ),
            heartbeat_required=route.heartbeat_required,
        )

    def _validate(self) -> None:
        if len(self._activity_by_type) != len(self._activities):
            raise TemporalActivityCatalogError(
                "Activity type definitions must be unique"
            )
        if len(self._fleet_by_name) != len(self._fleets):
            raise TemporalActivityCatalogError("Fleet definitions must be unique")

        task_queue_to_fleet: dict[str, str] = {}
        for fleet in self._fleets:
            for queue_name in fleet.task_queues:
                existing = task_queue_to_fleet.get(queue_name)
                if existing is not None and existing != fleet.fleet:
                    raise TemporalActivityCatalogError(
                        f"Task queue '{queue_name}' is assigned to multiple fleets"
                    )
                task_queue_to_fleet[queue_name] = fleet.fleet

        for activity in self._activities:
            fleet = self._fleet_by_name.get(activity.fleet)
            if fleet is None:
                raise TemporalActivityCatalogError(
                    f"Activity '{activity.activity_type}' references unknown fleet '{activity.fleet}'"
                )
            if activity.task_queue not in fleet.task_queues:
                raise TemporalActivityCatalogError(
                    f"Activity '{activity.activity_type}' routes to queue '{activity.task_queue}' "
                    f"outside fleet '{activity.fleet}'"
                )


def build_default_activity_catalog(
    temporal_settings: TemporalSettings | None = None,
) -> TemporalActivityCatalog:
    """Return the canonical Temporal activity catalog for MoonMind."""

    cfg = temporal_settings or settings.temporal
    workflow_task_queue = get_workflow_task_queue(cfg)
    workflow_poll_task_queues = get_workflow_poll_task_queues(cfg)

    # See docs/Temporal/ErrorTaxonomy.md
    NON_RETRYABLE_ERRORS = (
        "INVALID_INPUT",
        "ProfileResolutionError",
        "UnsupportedStatus",
    )

    activities = (
        TemporalActivityDefinition(
            activity_type="artifact.create",
            family="artifact",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(60, 120),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="artifact.write_complete",
            family="artifact",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(60, 120),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="artifact.publish_report_bundle",
            family="artifact",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(60, 120),
            retries=_activity_retries(max_attempts=1, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="artifact.read",
            family="artifact",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="execution.dependency_status_snapshot",
            family="execution",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="execution.record_terminal_state",
            family="execution",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="resilience.compile_policy",
            family="execution",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(
                max_attempts=3,
                max_interval_seconds=30,
                non_retryable=NON_RETRYABLE_ERRORS,
            ),
        ),
        TemporalActivityDefinition(
            activity_type="execution.notify_completion",
            family="execution",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=1, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="artifact.list_for_execution",
            family="artifact",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="artifact.compute_preview",
            family="artifact",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(60, 120),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="artifact.link",
            family="artifact",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="artifact.pin",
            family="artifact",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="artifact.unpin",
            family="artifact",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="artifact.lifecycle_sweep",
            family="artifact",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="step_checkpoint.create",
            family="step_checkpoint",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(60, 120),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="step_checkpoint.validate",
            family="step_checkpoint",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(60, 120),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="manifest.compile",
            family="manifest",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(300, 600),
            retries=_activity_retries(max_attempts=5, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="manifest.write_summary",
            family="manifest",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(300, 600),
            retries=_activity_retries(max_attempts=5, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="plan.generate",
            family="plan",
            capability_class="llm",
            task_queue=cfg.activity_llm_task_queue,
            fleet=LLM_FLEET,
            timeouts=TemporalActivityTimeouts(300, 900),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=120),
        ),
        TemporalActivityDefinition(
            activity_type="plan.validate",
            family="plan",
            capability_class="llm",
            task_queue=cfg.activity_llm_task_queue,
            fleet=LLM_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300),
            retries=_activity_retries(
                max_attempts=2,
                max_interval_seconds=60,
                non_retryable=NON_RETRYABLE_ERRORS,
            ),
        ),
        TemporalActivityDefinition(
            activity_type="mm.skill.execute",
            family="skill",
            capability_class="by_capability",
            task_queue=cfg.activity_llm_task_queue,
            fleet=LLM_FLEET,
            timeouts=TemporalActivityTimeouts(300, 1800),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=120),
        ),
        TemporalActivityDefinition(
            activity_type="sandbox.checkout_repo",
            family="sandbox",
            capability_class="sandbox",
            task_queue=cfg.activity_sandbox_task_queue,
            fleet=SANDBOX_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300, heartbeat_timeout_seconds=30),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=300),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="sandbox.apply_patch",
            family="sandbox",
            capability_class="sandbox",
            task_queue=cfg.activity_sandbox_task_queue,
            fleet=SANDBOX_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300, heartbeat_timeout_seconds=30),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=300),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="sandbox.run_command",
            family="sandbox",
            capability_class="sandbox",
            task_queue=cfg.activity_sandbox_task_queue,
            fleet=SANDBOX_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300, heartbeat_timeout_seconds=30),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=300),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="sandbox.run_tests",
            family="sandbox",
            capability_class="sandbox",
            task_queue=cfg.activity_sandbox_task_queue,
            fleet=SANDBOX_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300, heartbeat_timeout_seconds=30),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=300),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.capture_workspace_checkpoint",
            family="agent_runtime",
            capability_class="managed_workspace_checkpoint_capture",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(300, 600, heartbeat_timeout_seconds=30),
            retries=_activity_retries(
                max_attempts=3,
                max_interval_seconds=120,
                non_retryable=(
                    "WORKSPACE_AUTHORITY_MISMATCH",
                    "WORKSPACE_IDENTITY_MISMATCH",
                    "WORKSPACE_LOCATOR_UNSUPPORTED",
                    "CHECKPOINT_KIND_INCOMPATIBLE",
                    "CHECKPOINT_CAPTURE_POLICY_INVALID",
                    "CHECKPOINT_CAPTURE_LIMIT_EXCEEDED",
                    "CHECKPOINT_CAPABILITY_DIGEST_MISMATCH",
                    "CHECKPOINT_IDEMPOTENCY_CONFLICT",
                ),
            ),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="workspace.capture_checkpoint",
            family="workspace",
            capability_class="sandbox",
            task_queue=cfg.activity_sandbox_task_queue,
            fleet=SANDBOX_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300, heartbeat_timeout_seconds=30),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=300),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="workspace.apply_checkpoint",
            family="workspace",
            capability_class="sandbox",
            task_queue=cfg.activity_sandbox_task_queue,
            fleet=SANDBOX_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300, heartbeat_timeout_seconds=30),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=300),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="workspace.apply_policy",
            family="workspace",
            capability_class="sandbox",
            task_queue=cfg.activity_sandbox_task_queue,
            fleet=SANDBOX_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300, heartbeat_timeout_seconds=30),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=300),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="workspace.classify_git_effect",
            family="workspace",
            capability_class="sandbox",
            task_queue=cfg.activity_sandbox_task_queue,
            fleet=SANDBOX_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300, heartbeat_timeout_seconds=30),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=300),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="provider_profile.list",
            family="provider_profile",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="provider_profile.ensure_manager",
            family="provider_profile",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="provider_profile.acquire_credential_maintenance_lease",
            family="provider_profile",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(
                1800, 1860, heartbeat_timeout_seconds=30
            ),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="provider_profile.reset_manager",
            family="provider_profile",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="provider_profile.manager_state",
            family="provider_profile",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="provider_profile.verify_lease_holders",
            family="provider_profile",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="provider_profile.sync_slot_leases",
            family="provider_profile",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="provider_profile.pending_request_order",
            family="provider_profile",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="oauth_session.prepare_credential_maintenance",
            family="oauth_session",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(120, 180),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="oauth_session.revalidate_bound_host",
            family="oauth_session",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(180, 240),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="oauth_session.ensure_volume",
            family="oauth_session",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 120),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="oauth_session.start_auth_runner",
            family="oauth_session",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(120, 240),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="oauth_session.stop_auth_runner",
            family="oauth_session",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 120),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=15),
        ),
        TemporalActivityDefinition(
            activity_type="oauth_session.update_terminal_session",
            family="oauth_session",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(15, 30),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=15),
        ),
        TemporalActivityDefinition(
            activity_type="oauth_session.update_status",
            family="oauth_session",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(15, 30),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=15),
        ),
        TemporalActivityDefinition(
            activity_type="oauth_session.verify_volume",
            family="oauth_session",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 120),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="oauth_session.verify_cli_fingerprint",
            family="oauth_session",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 120),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="oauth_session.register_profile",
            family="oauth_session",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="oauth_session.mark_failed",
            family="oauth_session",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(15, 30),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=15),
        ),
        TemporalActivityDefinition(
            activity_type="oauth_session.cleanup_stale",
            family="oauth_session",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(60, 120),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="integration.jules.start",
            family="integration",
            capability_class="integration:jules",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=120),
        ),
        TemporalActivityDefinition(
            activity_type="integration.jules.status",
            family="integration",
            capability_class="integration:jules",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(60, 120),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=120),
        ),
        TemporalActivityDefinition(
            activity_type="integration.jules.fetch_result",
            family="integration",
            capability_class="integration:jules",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=120),
        ),
        TemporalActivityDefinition(
            activity_type="integration.jules.cancel",
            family="integration",
            capability_class="integration:jules",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(60, 120),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="integration.jules.send_message",
            family="integration",
            capability_class="integration:jules",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=120),
        ),
        TemporalActivityDefinition(
            activity_type="integration.jules.list_activities",
            family="integration",
            capability_class="integration:jules",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(60, 120),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="integration.jules.answer_question",
            family="integration",
            capability_class="integration:jules",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(300, 600),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=120),
        ),
        TemporalActivityDefinition(
            activity_type="integration.jules.get_auto_answer_config",
            family="integration",
            capability_class="integration:jules",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(10, 30),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=10),
        ),
        TemporalActivityDefinition(
            activity_type="integration.codex_cloud.start",
            family="integration",
            capability_class="integration:codex_cloud",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=120),
        ),
        TemporalActivityDefinition(
            activity_type="integration.codex_cloud.status",
            family="integration",
            capability_class="integration:codex_cloud",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(60, 120),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=120),
        ),
        TemporalActivityDefinition(
            activity_type="integration.codex_cloud.fetch_result",
            family="integration",
            capability_class="integration:codex_cloud",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=120),
        ),
        TemporalActivityDefinition(
            activity_type="integration.codex_cloud.cancel",
            family="integration",
            capability_class="integration:codex_cloud",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(60, 120),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="integration.openclaw.execute",
            family="integration",
            capability_class="integration:openclaw",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(300, 700, heartbeat_timeout_seconds=120),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=300),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="integration.omnigent.execute",
            family="integration",
            capability_class="integration:omnigent",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(
                3600, 3700, heartbeat_timeout_seconds=120
            ),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=300),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="integration.omnigent.profile_bound_execute",
            family="integration",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(
                3600, 3700, heartbeat_timeout_seconds=120
            ),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=300),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="integration.omnigent.oauth_host_janitor",
            family="integration",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(300, 600),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        # ---- General-purpose repo operations (provider-agnostic) ----
        TemporalActivityDefinition(
            activity_type="repo.create_pr",
            family="repo",
            capability_class="integration:jules",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="repo.merge_pr",
            family="repo",
            capability_class="integration:jules",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="merge_automation.evaluate_readiness",
            family="merge_automation",
            capability_class="integration:github",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="merge_automation.complete_post_merge_jira",
            family="merge_automation",
            capability_class="integration:jira",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="merge_automation.complete_post_merge_github",
            family="merge_automation",
            capability_class="integration:github",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="pr_resolver.resolve_selector",
            family="pr_resolver",
            capability_class="integration:github",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="pr_resolver.read_snapshot",
            family="pr_resolver",
            capability_class="integration:github",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300),
            retries=_activity_retries(max_attempts=5, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="pr_resolver.classify_gate",
            family="pr_resolver",
            capability_class="integration:github",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="pr_resolver.finalize_merge",
            family="pr_resolver",
            capability_class="integration:github",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="pr_resolver.verify_remote_head",
            family="pr_resolver",
            capability_class="integration:github",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300),
            retries=_activity_retries(max_attempts=5, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="pr_resolver.verify_merged",
            family="pr_resolver",
            capability_class="integration:github",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300),
            retries=_activity_retries(max_attempts=5, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="worker.verify_workflow_capability",
            family="worker_readiness",
            capability_class="integration:internal_readiness",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(15, 30),
            retries=_activity_retries(max_attempts=1, max_interval_seconds=1),
        ),
        TemporalActivityDefinition(
            activity_type="pr_resolver.write_terminal_result",
            family="pr_resolver",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300),
            retries=_activity_retries(max_attempts=5, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="integration.resolve_adapter_metadata",
            family="integration",
            capability_class="workflow",
            task_queue=workflow_task_queue,
            fleet=WORKFLOW_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="memory.evaluate_proposals",
            family="memory",
            capability_class="integrations",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=1, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="memory.apply_policy",
            family="memory",
            capability_class="integrations",
            task_queue=cfg.activity_integrations_task_queue,
            fleet=INTEGRATIONS_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=1, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.build_launch_context",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 180),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.launch",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 240, heartbeat_timeout_seconds=30),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=120),
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.launch_session",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(300, 1200, heartbeat_timeout_seconds=120),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=120),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.load_session_snapshot",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(30, 90),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.publish_artifacts",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 240, heartbeat_timeout_seconds=30),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=120),
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.session_status",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 180, heartbeat_timeout_seconds=30),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=30),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.prepare_turn_instructions",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 180),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.send_turn",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(
                SEND_TURN_ACTIVITY_START_TO_CLOSE_SECONDS,
                SEND_TURN_ACTIVITY_SCHEDULE_TO_CLOSE_SECONDS,
                heartbeat_timeout_seconds=SEND_TURN_ACTIVITY_HEARTBEAT_TIMEOUT_SECONDS,
            ),
            retries=_activity_retries(
                max_attempts=5,
                max_interval_seconds=600,
                non_retryable=("CodexPermanentTurnError",),
            ),
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.steer_turn",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 180, heartbeat_timeout_seconds=30),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=60),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.interrupt_turn",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 180, heartbeat_timeout_seconds=30),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=60),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.clear_session",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 180, heartbeat_timeout_seconds=30),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=60),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.ensure_docker_sidecar",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 180, heartbeat_timeout_seconds=30),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=60),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.terminate_session",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 180, heartbeat_timeout_seconds=30),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=60),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.fetch_session_summary",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 180),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.publish_session_artifacts",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 240, heartbeat_timeout_seconds=30),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.publish_bridge_events",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 180),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.reconcile_managed_sessions",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300, heartbeat_timeout_seconds=30),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=60),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.cleanup_managed_runtime_files",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(1800, 1800, heartbeat_timeout_seconds=30),
            retries=_activity_retries(max_attempts=1, max_interval_seconds=60),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.status",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 180, heartbeat_timeout_seconds=30),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=30),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.fetch_result",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 180),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.restore_workspace_checkpoint",
            family="agent_runtime",
            capability_class="managed_workspace_checkpoint_restore",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(900, 1200, heartbeat_timeout_seconds=60),
            retries=_activity_retries(
                max_attempts=3,
                max_interval_seconds=60,
                non_retryable=(
                    "CHECKPOINT_RESTORE_UNSUPPORTED",
                    "CHECKPOINT_KIND_INCOMPATIBLE",
                    "CHECKPOINT_SOURCE_IDENTITY_MISMATCH",
                    "CHECKPOINT_DESTINATION_IDENTITY_MISMATCH",
                    "CHECKPOINT_ARTIFACT_UNAUTHORIZED",
                    "CHECKPOINT_ARCHIVE_CORRUPTED",
                    "CHECKPOINT_MANIFEST_CORRUPTED",
                    "CHECKPOINT_ENTRY_DIGEST_MISMATCH",
                    "CHECKPOINT_BASE_COMMIT_MISMATCH",
                    "CHECKPOINT_PATH_ESCAPE",
                    "CHECKPOINT_SYMLINK_ESCAPE",
                    "CHECKPOINT_SPECIAL_FILE_UNSUPPORTED",
                    "CHECKPOINT_RESTORE_IDEMPOTENCY_CONFLICT",
                    "CHECKPOINT_CAPABILITY_DIGEST_MISMATCH",
                ),
            ),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.publish_terminal_checkpoint",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 240),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.evaluate_terminal_evidence",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 180),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="agent_runtime.cancel",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 120),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="workload.run",
            family="workload",
            capability_class="docker_workload",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(3600, 3900),
            retries=_activity_retries(
                max_attempts=1,
                max_interval_seconds=300,
                non_retryable=NON_RETRYABLE_ERRORS,
            ),
        ),
        *(
            TemporalActivityDefinition(
                activity_type=f"container_job.{name}",
                family="container_job",
                capability_class="docker_workload",
                task_queue=cfg.activity_agent_runtime_task_queue,
                fleet=AGENT_RUNTIME_FLEET,
                timeouts=TemporalActivityTimeouts(
                    300 if name in {"acquire_image", "create_container"} else 60,
                    300,
                ),
                retries=_activity_retries(
                    max_attempts=(
                        1
                        if name
                        in {
                            "create_container",
                            "start_container",
                            "stop_container",
                            "remove_container",
                        }
                        else 3
                    ),
                    max_interval_seconds=30,
                    non_retryable=NON_RETRYABLE_ERRORS,
                ),
            )
            for name in (
                "submit",
                "status",
                "cancel",
                "resolve_workspace",
                "acquire_image",
                "create_container",
                "start_container",
                "observe_container",
                "reconcile_container",
                "stop_container",
                "remove_container",
                "publish_evidence",
                "project_status",
                "repair_projection",
                "cleanup",
            )
        ),
        TemporalActivityDefinition(
            activity_type="security.pentest.execute",
            family="security",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(
                28800,
                32400,
                heartbeat_timeout_seconds=300,
            ),
            retries=_activity_retries(
                max_attempts=1,
                max_interval_seconds=300,
                non_retryable=(
                    "INVALID_SCOPE",
                    "PERMISSION_DENIED",
                    "UNAPPROVED_TARGET",
                    "UNSUPPORTED_PROFILE",
                    "NON_IDEMPOTENT_OPERATION",
                ),
            ),
            heartbeat_required=True,
        ),
        TemporalActivityDefinition(
            activity_type="proposal.generate",
            family="proposal",
            capability_class="llm",
            task_queue=cfg.activity_llm_task_queue,
            fleet=LLM_FLEET,
            timeouts=TemporalActivityTimeouts(300, 600),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=120),
        ),
        TemporalActivityDefinition(
            activity_type="proposal.submit",
            family="proposal",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="agent_skill.resolve",
            family="agent_skill",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 120),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="agent_skill.build_prompt_index",
            family="agent_skill",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 120),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="agent_skill.materialize",
            family="agent_skill",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 120),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="agent_skill.query_on_demand",
            family="agent_skill",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=1, max_interval_seconds=10),
        ),
        TemporalActivityDefinition(
            activity_type="agent_skill.request_on_demand",
            family="agent_skill",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=1, max_interval_seconds=10),
        ),
        TemporalActivityDefinition(
            activity_type="step.review",
            family="review",
            capability_class="llm",
            task_queue=cfg.activity_llm_task_queue,
            fleet=LLM_FLEET,
            timeouts=TemporalActivityTimeouts(120, 300),
            retries=_activity_retries(
                max_attempts=2,
                max_interval_seconds=60,
                non_retryable=NON_RETRYABLE_ERRORS,
            ),
        ),
    )

    fleets = (
        TemporalWorkerFleet(
            fleet=WORKFLOW_FLEET,
            task_queues=workflow_poll_task_queues,
            capabilities=("workflow",),
            privileges=("temporal",),
            scaling_notes="Workflow code only; no side effects.",
        ),
        TemporalWorkerFleet(
            fleet=ARTIFACTS_FLEET,
            task_queues=(cfg.activity_artifacts_task_queue,),
            capabilities=("artifacts",),
            privileges=("artifact_store",),
            scaling_notes="IO-bound fleet for artifact bytes and metadata.",
            activity_types=tuple(
                list(
                    entry.activity_type
                    for entry in activities
                    if entry.fleet == ARTIFACTS_FLEET
                )
                + ["mm.skill.execute", "mm.tool.execute"]
            ),
        ),
        TemporalWorkerFleet(
            fleet=LLM_FLEET,
            task_queues=(cfg.activity_llm_task_queue,),
            capabilities=("llm",),
            privileges=("llm_provider_secrets",),
            scaling_notes="Rate-limited by provider quotas.",
            activity_types=tuple(
                list(
                    entry.activity_type
                    for entry in activities
                    if entry.fleet == LLM_FLEET
                )
                + ["mm.tool.execute"]
            ),
        ),
        TemporalWorkerFleet(
            fleet=SANDBOX_FLEET,
            task_queues=(cfg.activity_sandbox_task_queue,),
            capabilities=("sandbox",),
            privileges=("isolated_process_execution",),
            scaling_notes="CPU and memory heavy; enforce strict concurrency limits.",
            activity_types=tuple(
                list(
                    entry.activity_type
                    for entry in activities
                    if entry.fleet == SANDBOX_FLEET
                )
                + ["mm.skill.execute", "mm.tool.execute"]
            ),
        ),
        TemporalWorkerFleet(
            fleet=INTEGRATIONS_FLEET,
            task_queues=(cfg.activity_integrations_task_queue,),
            capabilities=(
                "integration:jules",
                "integration:jira",
                "integration:codex_cloud",
                "integration:openclaw",
                "integration:omnigent",
            ),
            privileges=("provider_tokens",),
            scaling_notes="Protect with rate limiting and circuit breakers.",
            activity_types=tuple(
                list(
                    entry.activity_type
                    for entry in activities
                    if entry.fleet == INTEGRATIONS_FLEET
                )
                + ["mm.skill.execute", "mm.tool.execute"]
            ),
        ),
        TemporalWorkerFleet(
            fleet=AGENT_RUNTIME_FLEET,
            task_queues=(cfg.activity_agent_runtime_task_queue,),
            capabilities=("agent_runtime", "docker_workload"),
            privileges=(
                "isolated_process_execution",
                "auth_volume_mounts",
                "docker_proxy",
                "agent_workspaces_volume",
            ),
            scaling_notes="Long-lived supervised runtime executions and bounded Docker workload launches.",
            activity_types=tuple(
                list(
                    entry.activity_type
                    for entry in activities
                    if entry.fleet == AGENT_RUNTIME_FLEET
                )
            ),
        ),
        TemporalWorkerFleet(
            fleet=DEPLOYMENT_FLEET,
            task_queues=(cfg.activity_deployment_task_queue,),
            capabilities=("deployment_control", "docker_admin"),
            privileges=("docker_proxy", "deployment_state_write"),
            scaling_notes=(
                "Singleton deployment-control runner for audited Docker Compose "
                "stack updates."
            ),
            activity_types=("mm.tool.execute",),
        ),
    )

    return TemporalActivityCatalog(activities=activities, fleets=fleets)


def manifest_ingest_activity_routes(
    catalog: TemporalActivityCatalog | None = None,
) -> tuple[TemporalActivityRoute, ...]:
    """Return the canonical activity routes used by manifest ingest."""

    resolved_catalog = catalog or build_default_activity_catalog()
    return tuple(
        resolved_catalog.resolve_activity(activity_type)
        for activity_type in (
            "manifest.compile",
            "manifest.write_summary",
        )
    )


def skill_policy_as_route(
    *,
    activity_type: str,
    task_queue: str,
    fleet: str,
    capability_class: str,
    policies: SkillPolicies,
    heartbeat_required: bool = False,
    heartbeat_timeout_seconds: int | None = None,
    max_interval_seconds: int = 300,
) -> TemporalActivityRoute:
    """Build a routing record from one skill definition policy block."""

    return TemporalActivityRoute(
        activity_type=activity_type,
        task_queue=task_queue,
        fleet=fleet,
        capability_class=capability_class,
        timeouts=TemporalActivityTimeouts(
            start_to_close_seconds=policies.timeouts.start_to_close_seconds,
            schedule_to_close_seconds=policies.timeouts.schedule_to_close_seconds,
            heartbeat_timeout_seconds=heartbeat_timeout_seconds,
        ),
        retries=TemporalActivityRetries(
            max_attempts=policies.retries.max_attempts,
            max_interval_seconds=max_interval_seconds,
            non_retryable_error_codes=policies.retries.non_retryable_error_codes,
        ),
        heartbeat_required=heartbeat_required,
    )


__all__ = [
    "AGENT_RUNTIME_FLEET",
    "AGENT_RUNTIME_TASK_QUEUE",
    "ARTIFACTS_FLEET",
    "ARTIFACTS_TASK_QUEUE",
    "DEPLOYMENT_FLEET",
    "DEPLOYMENT_TASK_QUEUE",
    "INTEGRATIONS_FLEET",
    "INTEGRATIONS_TASK_QUEUE",
    "LLM_FLEET",
    "LLM_TASK_QUEUE",
    "SANDBOX_FLEET",
    "SANDBOX_TASK_QUEUE",
    "TemporalActivityCatalog",
    "TemporalActivityCatalogError",
    "TemporalActivityDefinition",
    "TemporalActivityRetries",
    "TemporalActivityRoute",
    "TemporalActivityTimeouts",
    "TemporalWorkerFleet",
    "WORKFLOW_FLEET",
    "WORKFLOW_TASK_QUEUE",
    "build_default_activity_catalog",
    "get_workflow_task_queue",
    "get_workflow_poll_task_queues",
    "manifest_ingest_activity_routes",
    "skill_policy_as_route",
]
