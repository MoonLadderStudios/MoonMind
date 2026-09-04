"""Operator-visible runtime-provider migration status projection.

Source issue: MoonLadderStudios/MoonMind#3833 (required work 10).

One view answers, per exact support combination: what its rollout state and
generation are, whether it is currently a default, which exact Agent Profile
class, Host Class, runtime pack, materializer, launch policy, and realizer it
names, what deterministic and protected-live evidence backs it, how old that
evidence is, when it last passed a protected (canary) run, what recent bounded
outcomes were observed, which rollback controls apply, and whether it is a
compatibility path.

The projection deliberately excludes credentials, provider-session ids, raw host
paths, host image digests, and internal endpoint authority: a migration status
reader needs support state, not launch authority.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field

from moonmind.omnigent.control_plane import metrics as control_plane_metrics
from moonmind.omnigent.runtime_provider_rollout import (
    ANY_DIMENSION,
    NOT_APPLICABLE,
    RolloutRule,
    RolloutState,
    RuntimeProviderPathClass,
    RuntimeProviderRollbackControl,
    RuntimeProviderRolloutPolicy,
    _rollback_blocks,
    load_runtime_provider_rollout_policy,
    native_interactive_chat_allowed,
    state_admits_execution,
    state_admits_new_authoring,
)

logger = logging.getLogger(__name__)

RUNTIME_PROVIDER_MIGRATION_STATUS_VERSION = (
    "moonmind.omnigent-runtime-provider-migration-status.v1"
)


class MigrationEvidenceView(BaseModel):
    """Non-sensitive evidence provenance for one combination."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    tier: str
    evidence_ref: str = Field(alias="evidenceRef")
    support_combination_key: str = Field(alias="supportCombinationKey")
    generated_at: datetime = Field(alias="generatedAt")
    expires_at: datetime = Field(alias="expiresAt")
    age_seconds: int = Field(alias="ageSeconds", ge=0)
    expired: bool


class MigrationOutcomeCounts(BaseModel):
    """Bounded recent-outcome counters for one harness class."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    launch_readiness: dict[str, int] = Field(
        default_factory=dict, alias="launchReadiness"
    )
    support_evidence_denials: dict[str, int] = Field(
        default_factory=dict, alias="supportEvidenceDenials"
    )
    fallback_denials: dict[str, int] = Field(
        default_factory=dict, alias="fallbackDenials"
    )
    followup_availability: dict[str, int] = Field(
        default_factory=dict, alias="followupAvailability"
    )
    cleanup_outcomes: dict[str, int] = Field(
        default_factory=dict, alias="cleanupOutcomes"
    )
    selected_paths: dict[str, int] = Field(
        default_factory=dict, alias="selectedPaths"
    )


class RuntimeProviderMigrationRow(BaseModel):
    """One combination's operator-visible migration status."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    target_id: str = Field(alias="targetId")
    label: str
    description: str = ""
    path_class: RuntimeProviderPathClass = Field(alias="pathClass")
    rollout_state: RolloutState = Field(alias="rolloutState")
    rollout_generation: int = Field(alias="rolloutGeneration")
    default_status: str = Field(alias="defaultStatus")
    compatibility_path_status: str = Field(alias="compatibilityPathStatus")
    harness_id: str | None = Field(default=None, alias="harnessId")
    agent_profile_compatibility_class: str = Field(
        alias="agentProfileCompatibilityClass"
    )
    host_class_ref: str = Field(alias="hostClassRef")
    runtime_pack_ref: str = Field(alias="runtimePackRef")
    credential_materializer_ref: str = Field(alias="credentialMaterializerRef")
    launch_policy_ref: str = Field(alias="launchPolicyRef")
    host_mode: str = Field(alias="hostMode")
    architectures: tuple[str, ...] = ()
    model_configuration_class: str = Field(alias="modelConfigurationClass")
    execution_realizer_ref: str = Field(alias="executionRealizerRef")
    deterministic_evidence: MigrationEvidenceView | None = Field(
        default=None, alias="deterministicEvidence"
    )
    protected_evidence: MigrationEvidenceView | None = Field(
        default=None, alias="protectedEvidence"
    )
    last_successful_canary_at: datetime | None = Field(
        default=None, alias="lastSuccessfulCanaryAt"
    )
    recent_outcomes: MigrationOutcomeCounts = Field(alias="recentOutcomes")
    applicable_rollback_controls: tuple[str, ...] = Field(
        default=(), alias="applicableRollbackControls"
    )
    active_rollback_controls: tuple[str, ...] = Field(
        default=(), alias="activeRollbackControls"
    )
    rollback_available: bool = Field(alias="rollbackAvailable")


class RuntimeProviderMigrationStatus(BaseModel):
    """The complete operator-visible migration view."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    schema_version: str = Field(
        RUNTIME_PROVIDER_MIGRATION_STATUS_VERSION, alias="schemaVersion"
    )
    policy_version: str = Field(alias="policyVersion")
    policy_generation: int = Field(alias="policyGeneration")
    observed_at: datetime = Field(alias="observedAt")
    active_rollback_controls: tuple[str, ...] = Field(
        default=(), alias="activeRollbackControls"
    )
    native_interactive_chat_allowed: bool = Field(
        alias="nativeInteractiveChatAllowed"
    )
    evidence_sources_available: dict[str, bool] = Field(
        default_factory=dict, alias="evidenceSourcesAvailable"
    )
    combinations: tuple[RuntimeProviderMigrationRow, ...] = ()


def _default_status(state: RolloutState) -> str:
    if state is RolloutState.new_work_default:
        return "default_for_new_work"
    if state is RolloutState.preferred:
        return "preferred"
    if state is RolloutState.canary:
        return "canary"
    if state is RolloutState.direct_compatibility_only:
        return "compatibility_path"
    if state is RolloutState.explicit_only:
        return "explicit_only"
    if state is RolloutState.retired_for_new_work:
        return "retired_for_new_work"
    return "unavailable"


def _compatibility_path_status(rule: RolloutRule) -> str:
    if rule.path_class is RuntimeProviderPathClass.generic_omnigent:
        return "not_a_compatibility_path"
    if not state_admits_execution(rule.state):
        return "disabled"
    if not state_admits_new_authoring(rule.state):
        return "retired_for_new_work"
    return "active_compatibility"


def _rule_dimension(rule: RolloutRule, name: str, fallback: str) -> str:
    value = rule.selector.get(name, ANY_DIMENSION)
    return fallback if value == ANY_DIMENSION else str(value)


def _registered_host_class_ref(harness_id: str | None) -> str:
    if not harness_id:
        return NOT_APPLICABLE
    try:
        from moonmind.omnigent.harness_platform.harness_registry import (
            harness_registration,
        )

        return harness_registration(harness_id).hostClassRef
    except Exception:
        return NOT_APPLICABLE


def _registered_materializer_ref(harness_id: str | None) -> str:
    if not harness_id:
        return NOT_APPLICABLE
    try:
        from moonmind.omnigent.harness_platform.harness_registry import (
            harness_registration,
        )

        return harness_registration(harness_id).materializerRef
    except Exception:
        return NOT_APPLICABLE


def _registered_runtime_pack_ref(harness_id: str | None) -> str:
    if not harness_id:
        return NOT_APPLICABLE
    try:
        from moonmind.omnigent.harness_platform.runtime_packs import (
            pack_ref_for_harness,
        )

        return pack_ref_for_harness(harness_id)
    except Exception:
        return NOT_APPLICABLE


def _host_class_architectures(host_class_ref: str) -> tuple[str, ...]:
    """Return the architectures a deployment-owned Host Class declares."""

    if host_class_ref == NOT_APPLICABLE:
        return ()
    try:
        from moonmind.omnigent.harness_platform.host_classes import (
            DEFAULT_HOST_CLASS_TEMPLATES,
        )
    except Exception:  # pragma: no cover - defensive
        return ()
    template = next(
        (item for item in DEFAULT_HOST_CLASS_TEMPLATES if item.ref == host_class_ref),
        None,
    )
    return tuple(template.architectures) if template is not None else ()


def _evidence_view(
    *,
    tier: str,
    evidence: Any,
    now: datetime,
) -> MigrationEvidenceView:
    generated_at = evidence.generated_at
    expires_at = evidence.expires_at
    age = max(0, int((now - generated_at).total_seconds()))
    ref = str(
        getattr(evidence, "protected_run_ref", "")
        or getattr(evidence, "compatibility_generation", "")
        or tier
    )
    return MigrationEvidenceView(
        tier=tier,
        evidenceRef=ref,
        supportCombinationKey=evidence.support_combination_key,
        generatedAt=generated_at,
        expiresAt=expires_at,
        ageSeconds=age,
        expired=expires_at <= now,
    )


def _newest_matching(
    entries: Iterable[Any], *, host_class_ref: str, realizer_ref: str
) -> Any | None:
    matches = [
        entry
        for entry in entries
        if entry.support_identity.hostClassRef == host_class_ref
        and entry.support_identity.executionRealizerRef == realizer_ref
    ]
    if not matches:
        return None
    return max(matches, key=lambda entry: entry.generated_at)


def _load_deterministic_entries() -> tuple[tuple[Any, ...], bool]:
    try:
        from moonmind.omnigent.deployment_evidence import (
            load_deployment_evidence_entries,
        )

        return (tuple(load_deployment_evidence_entries()), True)
    except Exception:
        return ((), False)


def _load_protected_entries() -> tuple[tuple[Any, ...], bool]:
    import json
    import os
    from pathlib import Path

    from moonmind.omnigent.execution_support_evidence import (
        validate_protected_execution_support_evidence,
    )

    configured = str(
        os.getenv("MOONMIND_OMNIGENT_EXECUTION_SUPPORT_EVIDENCE", "")
    ).strip()
    if not configured:
        return ((), False)
    try:
        raw = json.loads(Path(configured).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ((), False)
    candidates = raw.get("entries") if isinstance(raw, Mapping) else None
    items = candidates if isinstance(candidates, list) else [raw]
    parsed: list[Any] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        try:
            parsed.append(validate_protected_execution_support_evidence(item))
        except Exception:
            continue
    return (tuple(parsed), True)


def _outcome_counts(harness_class: str) -> MigrationOutcomeCounts:
    launch: dict[str, int] = {}
    denials: dict[str, int] = {}
    fallback: dict[str, int] = {}
    followup: dict[str, int] = {}
    cleanup: dict[str, int] = {}
    selected: dict[str, int] = {}
    for name, labels, value in control_plane_metrics.counter_series():
        if labels.get("harness_class") != harness_class:
            continue
        if name == control_plane_metrics.MIGRATION_LAUNCH_READINESS:
            launch[labels.get("readiness", "unknown")] = value
        elif name == control_plane_metrics.MIGRATION_SUPPORT_EVIDENCE_DENIAL:
            denials[labels.get("denial_reason", "unknown")] = value
        elif name == control_plane_metrics.MIGRATION_FALLBACK_DENIED:
            fallback[labels.get("denial_reason", "unknown")] = value
        elif name == control_plane_metrics.MIGRATION_FOLLOWUP_AVAILABILITY:
            key = (
                f"{labels.get('followup_kind', 'unknown')}:"
                f"{labels.get('availability', 'unknown')}"
            )
            followup[key] = value
        elif name == control_plane_metrics.MIGRATION_CLEANUP_OUTCOME:
            cleanup[labels.get("cleanup_outcome", "unknown")] = value
        elif name == control_plane_metrics.MIGRATION_SELECTED_PATH:
            key = (
                f"{labels.get('realizer_class', 'unknown')}:"
                f"{labels.get('selection_source', 'unknown')}"
            )
            selected[key] = value
    return MigrationOutcomeCounts(
        launchReadiness=launch,
        supportEvidenceDenials=denials,
        fallbackDenials=fallback,
        followupAvailability=followup,
        cleanupOutcomes=cleanup,
        selectedPaths=selected,
    )


def _applicable_controls(rule: RolloutRule) -> tuple[str, ...]:
    applicable = [
        control
        for control in RuntimeProviderRollbackControl
        if _rollback_blocks(
            control, harness_id=rule.harness_id, path_class=rule.path_class
        )
    ]
    if rule.path_class is RuntimeProviderPathClass.generic_omnigent or (
        rule.legacy_default_restorable
    ):
        applicable.append(
            RuntimeProviderRollbackControl.restore_legacy_or_direct_default
        )
    return tuple(dict.fromkeys(str(control) for control in applicable))


def build_runtime_provider_migration_status(
    *,
    policy: RuntimeProviderRolloutPolicy | None = None,
    now: datetime | None = None,
) -> RuntimeProviderMigrationStatus:
    """Compile the operator-visible runtime-provider migration view."""

    active = policy or load_runtime_provider_rollout_policy()
    observed_at = now or datetime.now(UTC)
    deterministic_entries, deterministic_available = _load_deterministic_entries()
    protected_entries, protected_available = _load_protected_entries()
    active_controls = tuple(str(item) for item in active.rollback_controls)

    rows: list[RuntimeProviderMigrationRow] = []
    for rule in active.rules:
        harness_id = rule.harness_id
        host_class_ref = _rule_dimension(
            rule, "host_class_ref", _registered_host_class_ref(harness_id)
        )
        realizer_ref = _rule_dimension(
            rule, "execution_realizer_ref", NOT_APPLICABLE
        )
        architectures = _host_class_architectures(host_class_ref)
        deterministic = _newest_matching(
            deterministic_entries,
            host_class_ref=host_class_ref,
            realizer_ref=realizer_ref,
        )
        protected = _newest_matching(
            protected_entries,
            host_class_ref=host_class_ref,
            realizer_ref=realizer_ref,
        )
        applicable = _applicable_controls(rule)
        rows.append(
            RuntimeProviderMigrationRow(
                targetId=rule.target_id,
                label=rule.label,
                description=rule.description,
                pathClass=(
                    rule.path_class or RuntimeProviderPathClass.generic_omnigent
                ),
                rolloutState=rule.state,
                rolloutGeneration=rule.generation,
                defaultStatus=_default_status(rule.state),
                compatibilityPathStatus=_compatibility_path_status(rule),
                harnessId=harness_id,
                agentProfileCompatibilityClass=_rule_dimension(
                    rule,
                    "agent_profile_compatibility_class",
                    "moonmind.omnigent-agent-profile.v2"
                    if harness_id
                    else NOT_APPLICABLE,
                ),
                hostClassRef=host_class_ref,
                runtimePackRef=_rule_dimension(
                    rule,
                    "runtime_pack_ref",
                    _registered_runtime_pack_ref(harness_id),
                ),
                credentialMaterializerRef=_rule_dimension(
                    rule,
                    "credential_materializer_ref",
                    _registered_materializer_ref(harness_id),
                ),
                launchPolicyRef=_rule_dimension(
                    rule, "launch_policy_ref", "selected-per-plan"
                ),
                hostMode=_rule_dimension(rule, "host_mode", "selected-per-plan"),
                architectures=architectures,
                modelConfigurationClass=_rule_dimension(
                    rule, "model_configuration_class", "per-run"
                ),
                executionRealizerRef=realizer_ref,
                deterministicEvidence=(
                    _evidence_view(
                        tier="deployment_qualified",
                        evidence=deterministic,
                        now=observed_at,
                    )
                    if deterministic is not None
                    else None
                ),
                protectedEvidence=(
                    _evidence_view(
                        tier="protected_live",
                        evidence=protected,
                        now=observed_at,
                    )
                    if protected is not None
                    else None
                ),
                # A passing protected-live run *is* the canary evidence for a
                # combination; there is no second canary log to reconcile.
                lastSuccessfulCanaryAt=(
                    protected.generated_at if protected is not None else None
                ),
                recentOutcomes=_outcome_counts(
                    control_plane_metrics.harness_class_for(harness_id)
                ),
                applicableRollbackControls=applicable,
                activeRollbackControls=tuple(
                    control for control in applicable if control in active_controls
                ),
                rollbackAvailable=bool(applicable),
            )
        )

    return RuntimeProviderMigrationStatus(
        schemaVersion=RUNTIME_PROVIDER_MIGRATION_STATUS_VERSION,
        policyVersion=active.policy_version,
        policyGeneration=active.generation,
        observedAt=observed_at,
        activeRollbackControls=active_controls,
        nativeInteractiveChatAllowed=native_interactive_chat_allowed(active),
        evidenceSourcesAvailable={
            "deploymentQualified": deterministic_available,
            "protectedLive": protected_available,
        },
        combinations=tuple(rows),
    )


__all__ = [
    "RUNTIME_PROVIDER_MIGRATION_STATUS_VERSION",
    "MigrationEvidenceView",
    "MigrationOutcomeCounts",
    "RuntimeProviderMigrationRow",
    "RuntimeProviderMigrationStatus",
    "build_runtime_provider_migration_status",
]
