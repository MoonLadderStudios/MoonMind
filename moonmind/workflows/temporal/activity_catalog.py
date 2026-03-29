"""Canonical Temporal activity catalog and worker topology helpers."""

from __future__ import annotations

from dataclasses import dataclass

from moonmind.config.settings import TemporalSettings, settings
from moonmind.workflows.skills.skill_plan_contracts import (
    SkillDefinition,
    SkillPolicies,
)

WORKFLOW_FLEET = "workflow"
ARTIFACTS_FLEET = "artifacts"
LLM_FLEET = "llm"
SANDBOX_FLEET = "sandbox"
INTEGRATIONS_FLEET = "integrations"
AGENT_RUNTIME_FLEET = "agent_runtime"

WORKFLOW_TASK_QUEUE = "mm.workflow"
ARTIFACTS_TASK_QUEUE = "mm.activity.artifacts"
LLM_TASK_QUEUE = "mm.activity.llm"
SANDBOX_TASK_QUEUE = "mm.activity.sandbox"
INTEGRATIONS_TASK_QUEUE = "mm.activity.integrations"
AGENT_RUNTIME_TASK_QUEUE = "mm.activity.agent_runtime"


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
    
    # See docs/Temporal/ErrorTaxonomy.md
    NON_RETRYABLE_ERRORS = ("INVALID_INPUT", "ProfileResolutionError", "UnsupportedStatus")

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
            activity_type="artifact.read",
            family="artifact",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
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
            activity_type="plan.generate",
            family="plan",
            capability_class="llm",
            task_queue=cfg.activity_llm_task_queue,
            fleet=LLM_FLEET,
            timeouts=TemporalActivityTimeouts(600, 600),
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
            activity_type="provider_profile.reset_manager",
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
            activity_type="oauth_session.ensure_volume",
            family="oauth_session",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(60, 120),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="oauth_session.start_auth_runner",
            family="oauth_session",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(120, 240),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="oauth_session.stop_auth_runner",
            family="oauth_session",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
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
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
            timeouts=TemporalActivityTimeouts(60, 120),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=60),
        ),
        TemporalActivityDefinition(
            activity_type="oauth_session.verify_cli_fingerprint",
            family="oauth_session",
            capability_class="artifacts",
            task_queue=cfg.activity_artifacts_task_queue,
            fleet=ARTIFACTS_FLEET,
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
            activity_type="integration.get_activity_route",
            family="integration",
            capability_class="workflow",
            task_queue=cfg.workflow_task_queue,
            fleet=WORKFLOW_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="integration.resolve_external_adapter",
            family="integration",
            capability_class="workflow",
            task_queue=cfg.workflow_task_queue,
            fleet=WORKFLOW_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
        ),
        TemporalActivityDefinition(
            activity_type="integration.external_adapter_execution_style",
            family="integration",
            capability_class="workflow",
            task_queue=cfg.workflow_task_queue,
            fleet=WORKFLOW_FLEET,
            timeouts=TemporalActivityTimeouts(30, 60),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=30),
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
            activity_type="agent_runtime.publish_artifacts",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 240, heartbeat_timeout_seconds=30),
            retries=_activity_retries(max_attempts=3, max_interval_seconds=120),
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
            activity_type="agent_runtime.cancel",
            family="agent_runtime",
            capability_class="agent_runtime",
            task_queue=cfg.activity_agent_runtime_task_queue,
            fleet=AGENT_RUNTIME_FLEET,
            timeouts=TemporalActivityTimeouts(60, 120),
            retries=_activity_retries(max_attempts=2, max_interval_seconds=60),
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
            task_queues=(cfg.workflow_task_queue,),
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
                "integration:codex_cloud",
                "integration:openclaw",
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
            capabilities=("agent_runtime",),
            privileges=("isolated_process_execution", "auth_volume_mounts"),
            scaling_notes="Long-lived supervised runtime executions.",
            activity_types=tuple(
                list(
                    entry.activity_type
                    for entry in activities
                    if entry.fleet == AGENT_RUNTIME_FLEET
                )
            ),
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
            "artifact.read",
            "plan.generate",
            "artifact.write_complete",
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
    "manifest_ingest_activity_routes",
    "skill_policy_as_route",
]
