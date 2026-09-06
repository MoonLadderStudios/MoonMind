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

from datetime import UTC, datetime
from typing import Any, Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    effective_rule_state,
    load_runtime_provider_rollout_policy,
    native_interactive_chat_allowed,
    state_admits_execution,
    state_admits_new_authoring,
)
from moonmind.omnigent.settings import generic_host_capacity

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


#: How the advertised concurrency peak was bounded. A closed, low-cardinality
#: vocabulary so the operator view names the limit rather than a number alone.
ADVERTISED_CONCURRENCY_LIMITS: tuple[str, ...] = (
    "unqualified",
    "qualified_level",
    "operator_ceiling",
)


class AdvertisedConcurrencyView(BaseModel):
    """The concurrency peak one exact combination may advertise.

    MoonLadderStudios/MoonMind#3885: a deployment must not advertise a peak it
    never validated. ``validatedLevel`` is the level the protected concurrency
    record observed for *this* combination; ``advertisedLevel`` is that level
    or the operator's lower configured ceiling, never more. A combination with
    no current passing concurrency evidence advertises ``0`` -- unqualified is
    not implicitly one, because nothing observed it.

    This view never rewrites the configured ceiling: ``operatorCeiling`` is
    reported as configured so the operator can see which of the two bounds is
    binding.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    advertised_level: int = Field(alias="advertisedLevel", ge=0)
    validated_level: int = Field(alias="validatedLevel", ge=0)
    operator_ceiling: int = Field(alias="operatorCeiling", ge=0)
    limited_by: str = Field(alias="limitedBy")

    @model_validator(mode="after")
    def validate_bounds(self) -> "AdvertisedConcurrencyView":
        if self.limited_by not in ADVERTISED_CONCURRENCY_LIMITS:
            raise ValueError(f"unknown advertised concurrency limit: {self.limited_by}")
        if self.advertised_level > self.validated_level:
            raise ValueError(
                "an advertised concurrency level cannot exceed the validated level"
            )
        if self.advertised_level > self.operator_ceiling:
            raise ValueError(
                "an advertised concurrency level cannot exceed the operator ceiling"
            )
        return self


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
    advertised_concurrency: AdvertisedConcurrencyView = Field(
        alias="advertisedConcurrency"
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


def _compatibility_path_status(rule: RolloutRule, state: RolloutState | None = None) -> str:
    if rule.path_class is RuntimeProviderPathClass.generic_omnigent:
        return "not_a_compatibility_path"
    effective = state if state is not None else rule.state
    if not state_admits_execution(effective):
        return "disabled"
    if not state_admits_new_authoring(effective):
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


def _advertised_concurrency(
    *,
    evidence: Any,
    operator_ceiling: int,
    now: datetime,
) -> AdvertisedConcurrencyView:
    """Report the peak this combination may advertise, bounded by its evidence.

    This is the production consumer of
    :func:`~moonmind.omnigent.execution_support_evidence.advertised_concurrency_ceiling`
    (MoonLadderStudios/MoonMind#3885). Without one, a deployment configured for
    ``N=16`` advertised sixteen while only ``N=2`` had ever been observed.

    Freshness is not decided here. The ceiling helper asks admission whether the
    row is still admissible, so an expired or over-age row advertises nothing
    and this projection cannot drift into a more permissive rule than the
    authority it is reporting on.
    """

    from moonmind.omnigent.execution_support_evidence import (
        advertised_concurrency_ceiling,
    )

    validated = advertised_concurrency_ceiling(evidence, now=now)
    advertised = advertised_concurrency_ceiling(
        evidence, operator_ceiling=operator_ceiling, now=now
    )
    if validated == 0:
        limited_by = "unqualified"
    elif advertised < validated:
        limited_by = "operator_ceiling"
    else:
        limited_by = "qualified_level"
    return AdvertisedConcurrencyView(
        advertisedLevel=advertised,
        validatedLevel=validated,
        operatorCeiling=operator_ceiling,
        limitedBy=limited_by,
    )


def _evidence_matches_rule(entry: Any, rule: RolloutRule) -> bool:
    """Return whether evidence belongs to the rule's exact combination.

    Compares every rule-pinned dimension that has a direct support-identity
    counterpart instead of only Host Class and realizer, so evidence from a
    different architecture, pack, materializer, launch policy, or harness
    implementation cannot attach to this row (MoonLadderStudios/MoonMind#3988).
    """

    identity = entry.support_identity
    selector = rule.selector
    checks: list[bool] = [
        identity.hostClassRef
        == _rule_dimension(rule, "host_class_ref", identity.hostClassRef),
        identity.executionRealizerRef
        == _rule_dimension(
            rule, "execution_realizer_ref", identity.executionRealizerRef
        ),
    ]
    pinned = {
        "harness_implementation_ref": "harnessImplementationRef",
        "launch_policy_ref": "launchPolicyRef",
        "architecture": "architecture",
    }
    for dimension, field in pinned.items():
        expected = selector.get(dimension, ANY_DIMENSION)
        if expected != ANY_DIMENSION and str(getattr(identity, field)) != str(
            expected
        ):
            return False
    materializer = selector.get("credential_materializer_ref", ANY_DIMENSION)
    if materializer != ANY_DIMENSION and str(materializer) not in set(
        getattr(identity, "materializerRefs", ()) or ()
    ):
        return False
    return all(checks)


def _newest_matching(
    entries: Iterable[Any], *, rule: RolloutRule,
) -> Any | None:
    matches = [entry for entry in entries if _evidence_matches_rule(entry, rule)]
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
        ExecutionSupportRowStatus,
        ProtectedExecutionSupportEvidence,
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
            # Parse structurally valid entries without the admission freshness
            # check so expired evidence is retained and reported as expired in
            # the projection instead of disappearing
            # (MoonLadderStudios/MoonMind#3988).
            entry = ProtectedExecutionSupportEvidence.model_validate(item)
        except Exception:
            continue
        if entry.status is not ExecutionSupportRowStatus.passed:
            # MoonLadderStudios/MoonMind#3885: the index now records failed,
            # skipped, blocked, unavailable, and partial rows so an operator can
            # see what happened. This projection reports what *backs* a rule, so
            # a non-passing row is not evidence here — and, being the newest
            # entry, it would otherwise displace the last real pass.
            continue
        parsed.append(entry)
    return (tuple(parsed), True)


def _load_shared_outcome_series() -> tuple[tuple[str, dict[str, str], int], ...]:
    """Load worker-published outcome counters shared across processes.

    ``counter_series()`` only sees the current process's aggregates, while the
    lifecycle recorders run in the workflow worker process and this projection
    runs in the API service process. The worker persists its bounded aggregate
    series to the JSON file named by
    ``MOONMIND_OMNIGENT_MIGRATION_OUTCOME_COUNTS``; this merges that shared
    series with the local one so the Compose topology reports both halves
    (MoonLadderStudios/MoonMind#3988).
    """

    import json
    import os
    from pathlib import Path

    configured = str(
        os.getenv("MOONMIND_OMNIGENT_MIGRATION_OUTCOME_COUNTS", "")
    ).strip()
    if not configured:
        return ()
    try:
        raw = json.loads(Path(configured).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    items = raw.get("counters") if isinstance(raw, Mapping) else None
    if not isinstance(items, list):
        return ()
    series: list[tuple[str, dict[str, str], int]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "")
        labels = item.get("labels")
        try:
            value = int(item.get("value") or 0)
        except (TypeError, ValueError):
            continue
        if not name or not isinstance(labels, Mapping):
            continue
        series.append((name, {str(k): str(v) for k, v in labels.items()}, value))
    return tuple(series)


def _outcome_counts(harness_class: str) -> MigrationOutcomeCounts:
    launch: dict[str, int] = {}
    denials: dict[str, int] = {}
    fallback: dict[str, int] = {}
    followup: dict[str, int] = {}
    cleanup: dict[str, int] = {}
    selected: dict[str, int] = {}
    merged: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {}
    for name, labels, value in (
        *control_plane_metrics.counter_series(),
        *_load_shared_outcome_series(),
    ):
        key = (name, tuple(sorted(labels.items())))
        merged[key] = merged.get(key, 0) + value
    for (name, label_items), value in merged.items():
        labels = dict(label_items)
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
    # The operator's configured aggregate ceiling, read once and never
    # rewritten: this projection reports which bound is binding, it does not
    # change a configured capacity value.
    operator_ceiling = generic_host_capacity()

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
            rule=rule,
        )
        protected = _newest_matching(
            protected_entries,
            rule=rule,
        )
        applicable = _applicable_controls(rule)
        # Project the effective post-rollback state so the operator view
        # matches active admission during a rollback (MoonLadderStudios/MoonMind#3988).
        effective_state = effective_rule_state(rule, active)[0]
        rows.append(
            RuntimeProviderMigrationRow(
                targetId=rule.target_id,
                label=rule.label,
                description=rule.description,
                pathClass=(
                    rule.path_class or RuntimeProviderPathClass.generic_omnigent
                ),
                rolloutState=effective_state,
                rolloutGeneration=rule.generation,
                defaultStatus=_default_status(effective_state),
                compatibilityPathStatus=_compatibility_path_status(
                    rule, effective_state
                ),
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
                advertisedConcurrency=_advertised_concurrency(
                    evidence=protected,
                    operator_ceiling=operator_ceiling,
                    now=observed_at,
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
    "ADVERTISED_CONCURRENCY_LIMITS",
    "RUNTIME_PROVIDER_MIGRATION_STATUS_VERSION",
    "AdvertisedConcurrencyView",
    "MigrationEvidenceView",
    "MigrationOutcomeCounts",
    "RuntimeProviderMigrationRow",
    "RuntimeProviderMigrationStatus",
    "build_runtime_provider_migration_status",
]
