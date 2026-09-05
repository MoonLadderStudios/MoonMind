"""One shared runtime-target selection and admission boundary.

Source issue: MoonLadderStudios/MoonMind#3833 (required work 2, 3, 4, 5, 9).

Every authoring and follow-up surface that chooses an agent runtime resolves it
here: Workflow Create, presets and preset expansion, schedules and recurring
occurrences, edit, rerun, retry as a fresh execution, Checkpoint Branch create /
continue / fork, remediation authoring, linked continuation, and any API or MCP
submission. No surface reconstructs a default from environment variables or a
hard-coded runtime map.

The boundary answers three separate questions and keeps them separate:

``resolve_runtime_target_catalog``
    Which explicit target identities exist, what each is labeled, whether it is
    a compatibility path, and what its rollout state and generation are.
``resolve_default_runtime_id``
    Which canonical runtime id new work should preselect, derived from the
    versioned rollout policy rather than from a literal.
``resolve_runtime_target_selection``
    What one surface actually gets for one submission, including whether the
    selection was authored, defaulted, or preserved from recorded authority, and
    the exact reason when the resulting target is unavailable.

An unavailable target never silently becomes a different runtime. The selection
carries ``available=False`` and ``replacement_required=True`` with an exact
reason so the caller can surface explicit valid alternatives instead.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from moonmind.omnigent.runtime_provider_rollout import (
    ANY_DIMENSION,
    RolloutReason,
    RolloutRule,
    RolloutState,
    RuntimeProviderPathClass,
    RuntimeProviderRolloutPolicy,
    effective_rule_state,
    load_runtime_provider_rollout_policy,
    rollout_state_rank,
    state_admits_new_authoring,
)

from .runtime_defaults import (
    DEFAULT_WORKFLOW_RUNTIME,
    normalize_runtime_id,
    resolve_default_workflow_runtime,
)

logger = logging.getLogger(__name__)

#: Canonical product identity for every Omnigent-backed target.
OMNIGENT_RUNTIME_ID = "omnigent"


class AuthoringSurface(StrEnum):
    """Closed vocabulary of surfaces that may choose an agent runtime.

    A source-kind difference changes policy and evidence. It never creates a
    second default resolver.
    """

    workflow_create = "workflow_create"
    preset_expansion = "preset_expansion"
    schedule = "schedule"
    schedule_occurrence = "schedule_occurrence"
    edit = "edit"
    rerun = "rerun"
    retry_as_fresh_execution = "retry_as_fresh_execution"
    checkpoint_branch = "checkpoint_branch"
    remediation = "remediation"
    linked_continuation = "linked_continuation"
    api_submission = "api_submission"
    mcp_submission = "mcp_submission"
    worker_normalization = "worker_normalization"
    dashboard_config = "dashboard_config"


#: Surfaces that continue recorded authority instead of authoring a new target.
#: They preserve the recorded plan and realizer unless the caller explicitly
#: asks to upgrade to a currently qualified target.
_RECORDED_AUTHORITY_SURFACES = frozenset(
    {
        AuthoringSurface.schedule_occurrence,
        AuthoringSurface.edit,
        AuthoringSurface.rerun,
        AuthoringSurface.linked_continuation,
    }
)


class SelectionSource(StrEnum):
    """Where the resolved target came from."""

    authored = "authored"
    rollout_default = "rollout_default"
    recorded = "recorded"
    configured_default = "configured_default"


class RuntimeTargetView(BaseModel):
    """One explicit, operator-visible runtime target identity."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    target_id: str = Field(alias="targetId")
    label: str
    runtime_id: str = Field(alias="runtimeId")
    harness_id: str | None = Field(default=None, alias="harnessId")
    execution_realizer_ref: str | None = Field(
        default=None, alias="executionRealizerRef"
    )
    path_class: RuntimeProviderPathClass = Field(alias="pathClass")
    rollout_state: RolloutState = Field(alias="rolloutState")
    rollout_generation: int = Field(alias="rolloutGeneration")
    policy_version: str = Field(alias="policyVersion")
    policy_generation: int = Field(alias="policyGeneration")
    default_eligible: bool = Field(alias="defaultEligible")
    explicit_selection_allowed: bool = Field(alias="explicitSelectionAllowed")
    compatibility_path: bool = Field(alias="compatibilityPath")
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")


class RuntimeTargetSelection(BaseModel):
    """The resolved runtime target for one submission from one surface."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    runtime_id: str = Field(alias="runtimeId")
    target_id: str | None = Field(default=None, alias="targetId")
    path_class: RuntimeProviderPathClass | None = Field(
        default=None, alias="pathClass"
    )
    rollout_state: RolloutState | None = Field(default=None, alias="rolloutState")
    rollout_generation: int | None = Field(default=None, alias="rolloutGeneration")
    policy_version: str = Field(alias="policyVersion")
    policy_generation: int = Field(alias="policyGeneration")
    surface: AuthoringSurface
    source: SelectionSource
    available: bool = True
    replacement_required: bool = Field(default=False, alias="replacementRequired")
    reason_code: RolloutReason | None = Field(default=None, alias="reasonCode")

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")


def _rule_runtime_id(rule: RolloutRule) -> str:
    """Return the canonical product runtime id for one rollout rule.

    Omnigent-backed rows always submit as ``external/omnigent``; only a direct
    compatibility row carries a provider runtime id as its product identity.
    """

    if rule.path_class is RuntimeProviderPathClass.direct_compatibility:
        provider_runtime = rule.selector.get("provider_runtime_id", ANY_DIMENSION)
        if provider_runtime == ANY_DIMENSION:
            raise ValueError(
                f"direct compatibility rule {rule.target_id!r} must pin "
                "provider_runtime_id"
            )
        return normalize_runtime_id(provider_runtime)
    return OMNIGENT_RUNTIME_ID


def _rule_realizer(rule: RolloutRule) -> str | None:
    ref = rule.selector.get("execution_realizer_ref", ANY_DIMENSION)
    return None if ref == ANY_DIMENSION else str(ref)


def _rule_view(
    rule: RolloutRule, policy: RuntimeProviderRolloutPolicy
) -> RuntimeTargetView:
    # The catalog reports the *effective* state so an active rollback control
    # removes a promoted target from every authoring surface at once.
    state, _controls, _reason = effective_rule_state(rule, policy)
    return RuntimeTargetView(
        targetId=rule.target_id,
        label=rule.label,
        runtimeId=_rule_runtime_id(rule),
        harnessId=rule.harness_id,
        executionRealizerRef=_rule_realizer(rule),
        pathClass=rule.path_class or RuntimeProviderPathClass.generic_omnigent,
        rolloutState=state,
        rolloutGeneration=rule.generation,
        policyVersion=policy.policy_version,
        policyGeneration=policy.generation,
        defaultEligible=state
        in {RolloutState.preferred, RolloutState.new_work_default},
        explicitSelectionAllowed=state_admits_new_authoring(state),
        compatibilityPath=(
            rule.path_class is not RuntimeProviderPathClass.generic_omnigent
        ),
        description=rule.description,
    )


def resolve_runtime_target_catalog(
    *,
    policy: RuntimeProviderRolloutPolicy | None = None,
    env: Mapping[str, Any] | None = None,
) -> tuple[RuntimeTargetView, ...]:
    """Return every registered target identity, most promoted first."""

    active = policy or load_runtime_provider_rollout_policy(env=env)
    views = [_rule_view(rule, active) for rule in active.rules]
    views.sort(
        key=lambda view: (-rollout_state_rank(view.rollout_state), view.target_id)
    )
    return tuple(views)


def _promoted_views(
    views: Sequence[RuntimeTargetView],
) -> tuple[RuntimeTargetView, ...]:
    return tuple(view for view in views if view.default_eligible)


def resolve_default_runtime_id(
    *,
    workflow_settings: Any = None,
    policy: RuntimeProviderRolloutPolicy | None = None,
    env: Mapping[str, Any] | None = None,
) -> str:
    """Return the canonical runtime id new work should preselect.

    The rollout policy decides. The configured
    ``settings.workflow.default_runtime`` is the tie-breaker used only when the
    policy promotes more than one runtime id, and the last-resort value when the
    policy promotes none — in that case the caller receives an unavailable
    selection rather than a silent substitution.
    """

    active = policy or load_runtime_provider_rollout_policy(env=env)
    views = resolve_runtime_target_catalog(policy=active)
    promoted = _promoted_views(views)
    configured = (
        resolve_default_workflow_runtime(workflow_settings)
        if workflow_settings is not None
        else DEFAULT_WORKFLOW_RUNTIME
    )
    if configured in {view.runtime_id for view in promoted}:
        return configured
    configured_views = _views_for_runtime(views, configured)
    if not configured_views or any(
        view.explicit_selection_allowed for view in configured_views
    ):
        # An explicit deployment configuration is an authored intention. The
        # rollout policy decides which *target* that runtime resolves to and
        # which runtime is recommended; it does not silently replace a
        # configured runtime that still has a selectable target, and it never
        # invents a target for an unregistered runtime id.
        return configured
    if promoted:
        return promoted[0].runtime_id
    return configured


def _views_for_runtime(
    views: Sequence[RuntimeTargetView], runtime_id: str
) -> tuple[RuntimeTargetView, ...]:
    return tuple(view for view in views if view.runtime_id == runtime_id)


def resolve_runtime_target_selection(
    *,
    surface: AuthoringSurface | str,
    requested_runtime: object = None,
    requested_target_id: str | None = None,
    recorded_target_id: str | None = None,
    recorded_runtime_id: str | None = None,
    upgrade_to_qualified_target: bool = False,
    workflow_settings: Any = None,
    policy: RuntimeProviderRolloutPolicy | None = None,
    env: Mapping[str, Any] | None = None,
    record_metrics: bool = True,
) -> RuntimeTargetSelection:
    """Resolve one surface's runtime target through the shared boundary.

    ``recorded_*`` inputs express immutable recorded authority. A continuation
    surface keeps them unless ``upgrade_to_qualified_target`` explicitly asks for
    a currently qualified target, so editing or rerunning historical work never
    silently replaces its harness, profile, policy, Host Class, runtime pack,
    materializer, or realizer.
    """

    active_surface = AuthoringSurface(str(surface))
    active = policy or load_runtime_provider_rollout_policy(env=env)
    views = resolve_runtime_target_catalog(policy=active)
    by_target = {view.target_id: view for view in views}

    def _selection(
        *,
        runtime_id: str,
        view: RuntimeTargetView | None,
        source: SelectionSource,
        available: bool,
        replacement_required: bool = False,
        reason: RolloutReason | None = None,
    ) -> RuntimeTargetSelection:
        selection = RuntimeTargetSelection(
            runtimeId=runtime_id,
            targetId=view.target_id if view else None,
            pathClass=view.path_class if view else None,
            rolloutState=view.rollout_state if view else None,
            rolloutGeneration=view.rollout_generation if view else None,
            policyVersion=active.policy_version,
            policyGeneration=active.generation,
            surface=active_surface,
            source=source,
            available=available,
            replacementRequired=replacement_required,
            reasonCode=reason,
        )
        # The dashboard-config projection is a read of the catalog, not a
        # submission, so it does not count as a migration selection.
        if record_metrics and active_surface is not AuthoringSurface.dashboard_config:
            _record_selection_metrics(
                selection,
                policy=active,
                harness_id=view.harness_id if view else None,
            )
        return selection

    # 1. Recorded authority wins on continuation surfaces.
    if (
        recorded_target_id
        and active_surface in _RECORDED_AUTHORITY_SURFACES
        and not upgrade_to_qualified_target
    ):
        recorded_view = by_target.get(recorded_target_id)
        if recorded_view is None:
            # The recorded selection is no longer registered. It stays visible
            # and requires an explicit replacement for new submission.
            return _selection(
                runtime_id=normalize_runtime_id(
                    recorded_runtime_id or DEFAULT_WORKFLOW_RUNTIME
                ),
                view=None,
                source=SelectionSource.recorded,
                available=False,
                replacement_required=True,
                reason=RolloutReason.combination_not_registered,
            )
        return _selection(
            runtime_id=recorded_view.runtime_id,
            view=recorded_view,
            source=SelectionSource.recorded,
            available=recorded_view.explicit_selection_allowed,
            replacement_required=not recorded_view.explicit_selection_allowed,
            reason=(
                None
                if recorded_view.explicit_selection_allowed
                else RolloutReason.rollout_disabled
            ),
        )

    # 2. An explicitly authored target identity.
    if requested_target_id:
        requested_view = by_target.get(requested_target_id)
        if requested_view is None:
            return _selection(
                runtime_id=normalize_runtime_id(
                    requested_runtime or DEFAULT_WORKFLOW_RUNTIME
                ),
                view=None,
                source=SelectionSource.authored,
                available=False,
                replacement_required=True,
                reason=RolloutReason.combination_not_registered,
            )
        return _selection(
            runtime_id=requested_view.runtime_id,
            view=requested_view,
            source=SelectionSource.authored,
            available=requested_view.explicit_selection_allowed,
            replacement_required=not requested_view.explicit_selection_allowed,
            reason=(
                None
                if requested_view.explicit_selection_allowed
                else RolloutReason.rollout_disabled
            ),
        )

    # 3. An authored runtime id (product intention) resolves to the most
    #    promoted registered target for that runtime.
    authored_runtime = str(requested_runtime or "").strip()
    if authored_runtime:
        runtime_id = normalize_runtime_id(authored_runtime)
        candidates = _views_for_runtime(views, runtime_id)
        selectable = [
            view for view in candidates if view.explicit_selection_allowed
        ]
        if not candidates:
            # Unregistered runtime ids remain authorable; the rollout policy
            # governs promotion, not the set of runtimes that exist.
            return _selection(
                runtime_id=runtime_id,
                view=None,
                source=SelectionSource.authored,
                available=True,
                reason=RolloutReason.combination_not_registered,
            )
        if not selectable:
            return _selection(
                runtime_id=runtime_id,
                view=candidates[0],
                source=SelectionSource.authored,
                available=False,
                replacement_required=True,
                reason=RolloutReason.rollout_disabled,
            )
        return _selection(
            runtime_id=runtime_id,
            view=selectable[0],
            source=SelectionSource.authored,
            available=True,
        )

    # 4. No authored intention: take the promoted default.
    default_runtime_id = resolve_default_runtime_id(
        workflow_settings=workflow_settings, policy=active
    )
    candidates = _views_for_runtime(views, default_runtime_id)
    promoted = [view for view in candidates if view.default_eligible]
    if promoted:
        return _selection(
            runtime_id=default_runtime_id,
            view=promoted[0],
            source=SelectionSource.rollout_default,
            available=True,
        )
    selectable = [view for view in candidates if view.explicit_selection_allowed]
    if selectable:
        # An explicitly configured deployment default the policy does not
        # promote is still selectable; it is reported as configured, not
        # recommended.
        return _selection(
            runtime_id=default_runtime_id,
            view=selectable[0],
            source=SelectionSource.configured_default,
            available=True,
        )
    if not candidates:
        # An unregistered configured runtime stays authorable: the rollout
        # policy governs promotion, not the set of runtimes that exist.
        return _selection(
            runtime_id=default_runtime_id,
            view=None,
            source=SelectionSource.configured_default,
            available=True,
            reason=RolloutReason.combination_not_registered,
        )
    return _selection(
        runtime_id=default_runtime_id,
        view=candidates[0],
        source=SelectionSource.configured_default,
        available=False,
        replacement_required=True,
        reason=RolloutReason.rollout_disabled,
    )


def _realizer_class(path_class: RuntimeProviderPathClass | None) -> str:
    return str(path_class) if path_class is not None else "unknown"


def _record_selection_metrics(
    selection: RuntimeTargetSelection,
    *,
    policy: RuntimeProviderRolloutPolicy,
    harness_id: str | None,
) -> None:
    """Emit the bounded migration telemetry for one resolved selection.

    Recording lives at the shared boundary so every authoring surface reports
    the migration identically. Metric recording is never authority: a telemetry
    failure must not change which target was selected.
    """

    try:
        from moonmind.omnigent.control_plane import metrics as control_plane_metrics

        control_plane_metrics.record_runtime_target_selection(
            harness_id=harness_id,
            realizer_class=_realizer_class(selection.path_class),
            selection_source=str(selection.source),
            rollout_state=(
                str(selection.rollout_state)
                if selection.rollout_state is not None
                else "unknown"
            ),
            available=selection.available,
            denial_reason=(
                str(selection.reason_code)
                if selection.reason_code is not None
                else None
            ),
        )
        if selection.target_id:
            rule = policy.rule(selection.target_id)
            if rule is not None:
                _state, controls, _reason = effective_rule_state(rule, policy)
                for control in controls:
                    control_plane_metrics.record_rollback_activation(str(control))
    except Exception:  # pragma: no cover - telemetry is never authority
        logger.warning(
            "runtime-provider selection telemetry recording failed",
            exc_info=True,
        )


def runtime_target_catalog_payload(
    *,
    policy: RuntimeProviderRolloutPolicy | None = None,
    env: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the operator-facing catalog projection for dashboard config."""

    active = policy or load_runtime_provider_rollout_policy(env=env)
    views = resolve_runtime_target_catalog(policy=active)
    return {
        "policyVersion": active.policy_version,
        "policyGeneration": active.generation,
        "defaultRuntimeId": resolve_default_runtime_id(policy=active),
        "targets": [view.as_dict() for view in views],
    }


__all__ = [
    "OMNIGENT_RUNTIME_ID",
    "AuthoringSurface",
    "RuntimeTargetSelection",
    "RuntimeTargetView",
    "SelectionSource",
    "resolve_default_runtime_id",
    "resolve_runtime_target_catalog",
    "resolve_runtime_target_selection",
    "runtime_target_catalog_payload",
]
