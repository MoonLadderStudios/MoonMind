"""Shared rollout admission for every Omnigent authoring path.

Source issue: MoonLadderStudios/MoonMind#3833.

Every authoring surface (Workflow Create, presets, schedules, edit/rerun,
retry-as-fresh, Checkpoint Branch, remediation, linked continuation, API/MCP
submissions) funnels through Omnigent plan compilation. This module is the one
shared backend selection/admission boundary those compile sites call: it builds
the exact :class:`RolloutCombination` from trusted planner inputs, admits it
through the deployment-owned rollout policy, records low-cardinality
telemetry, and returns the frozen rollout triple persisted with the execution
plan.

Pre-promotion behavior: when no deployment-owned policy is configured
(``MOONMIND_OMNIGENT_ROLLOUT_POLICY_REF`` unset), admission is skipped and the
plan persists null rollout authority, preserving historical canonical form.
Once a policy is configured, admission is fail-closed: unknown, stale, or
non-admissible combinations raise instead of substituting another runtime.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.runtime_provider_rollout import (
    ROLLOUT_POLICY_ENV,
    AuthoringSurface,
    RolloutAdmissionError,
    RolloutCombination,
    RolloutPolicy,
    load_rollout_policy,
    record_rollout_decision,
    resolve_default_target,
    select_authoring_target,
)


def rollout_policy_configured(
    *, env: Mapping[str, Any] | None = None
) -> bool:
    """Whether the deployment owns a rollout policy document ref."""

    values = os.environ if env is None else env
    return bool(str(values.get(ROLLOUT_POLICY_ENV, "") or "").strip())


def rollout_surface_from_parameters(
    parameters: Mapping[str, Any] | None,
) -> AuthoringSurface:
    """Resolve the authoring surface for telemetry/admission from a request.

    Callers pass ``parameters["omnigent"]["authoringSurface"]`` when the
    submission originates from a known surface (preset, schedule, rerun,
    branch, remediation, ...). Unknown or absent values map to ``API`` so no
    surface can bypass the shared boundary by omitting the hint.
    """

    omnigent = (parameters or {}).get("omnigent")
    raw = str(
        omnigent.get("authoringSurface") or "" if isinstance(omnigent, Mapping) else ""
    ).strip()
    if raw:
        try:
            return AuthoringSurface(raw)
        except ValueError:
            pass
    return AuthoringSurface.API


def owner_cohort_from_parameters(
    parameters: Mapping[str, Any] | None,
) -> str | None:
    """Return the exact canary owner cohort carried by a request, if any."""

    omnigent = (parameters or {}).get("omnigent")
    if not isinstance(omnigent, Mapping):
        return None
    cohort = str(omnigent.get("ownerCohort") or "").strip()
    return cohort or None


def build_rollout_combination(
    *,
    harness_implementation: str,
    agent_profile_ref: str,
    provider_runtime: str,
    provider_class: str,
    host_class_ref: str,
    runtime_pack: str,
    credential_materializer: str,
    launch_policy_ref: str,
    host_mode: str,
    architecture: str,
    model_config_class: str,
    execution_realizer: str,
    support_evidence_ref: str,
    owner_cohort: str | None = None,
) -> RolloutCombination:
    """Build the exact rollout combination from trusted planner inputs.

    Every dimension is an exact versioned ref or class -- never a display
    name or runtime substring.
    """

    return RolloutCombination.model_validate(
        {
            "harnessImplementation": harness_implementation,
            "agentProfileClass": agent_profile_ref,
            "providerRuntime": provider_runtime,
            "providerClass": provider_class,
            "hostClass": host_class_ref,
            "runtimePack": runtime_pack,
            "credentialMaterializer": credential_materializer,
            "launchPolicy": launch_policy_ref,
            "hostMode": host_mode,
            "architecture": architecture,
            "modelConfigClass": model_config_class,
            "executionRealizer": execution_realizer,
            "supportEvidenceRef": support_evidence_ref,
            "ownerCohort": owner_cohort,
        }
    )


def admit_rollout_for_plan(
    *,
    parameters: Mapping[str, Any] | None,
    combination: RolloutCombination,
    policy: RolloutPolicy | None = None,
    explicit: bool = True,
) -> tuple[str | None, int | None, str | None]:
    """Admit one compiled plan through the rollout policy.

    Returns the frozen ``(policyVersion, generation, state)`` triple for the
    execution plan, or ``(None, None, None)`` when no deployment-owned policy
    is configured (pre-promotion: historical canonical form is preserved).

    Denial raises :class:`HarnessPlatformError` with the exact unavailable
    reason; it never substitutes another runtime.
    """

    surface = rollout_surface_from_parameters(parameters)
    owner_cohort = owner_cohort_from_parameters(parameters)
    if policy is None:
        if not rollout_policy_configured():
            return None, None, None
        try:
            policy = load_rollout_policy()
        except RolloutAdmissionError as exc:
            raise HarnessPlatformError(
                f"Omnigent rollout policy unavailable: {exc}",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            ) from exc
    try:
        admitted = select_authoring_target(
            policy=policy,
            surface=surface,
            combination=combination,
            explicit=explicit,
            owner_cohort=owner_cohort or combination.owner_cohort,
        )
    except RolloutAdmissionError as exc:
        record_rollout_decision(
            harness=_telemetry_harness(combination.harness_implementation),
            realizer_ref=combination.execution_realizer,
            decision="denied",
            surface=surface,
        )
        raise HarnessPlatformError(
            f"Omnigent rollout admission denied: {exc}",
            code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
        ) from exc
    record_rollout_decision(
        harness=_telemetry_harness(combination.harness_implementation),
        realizer_ref=combination.execution_realizer,
        decision=(
            "admitted_default"
            if admitted.default_selection
            else "admitted_explicit"
        ),
        surface=surface,
    )
    return (
        admitted.rollout_policy_version,
        admitted.rollout_generation,
        admitted.rollout_state.value,
    )


def resolve_rollout_default_for_intention(
    *,
    product_intention: str,
    surface: AuthoringSurface | str,
    combination_template: Mapping[str, Any],
    owner_cohort: str | None = None,
    policy: RolloutPolicy | None = None,
) -> Any:
    """Resolve the promoted Omnigent default for a product intention.

    Production entrypoint for Workflow Create default promotion: a
    ``preferred`` or ``new_work_default`` row preselects the qualified
    Omnigent Agent Profile target while the submitted canonical identity
    remains ``external/omnigent``. Anything else fails closed with the exact
    reason. Denial raises :class:`HarnessPlatformError`.
    """

    if policy is None:
        try:
            policy = load_rollout_policy()
        except RolloutAdmissionError as exc:
            raise HarnessPlatformError(
                f"Omnigent rollout policy unavailable: {exc}",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            ) from exc
    parsed_surface = (
        surface if isinstance(surface, AuthoringSurface) else AuthoringSurface(surface)
    )
    try:
        admitted = resolve_default_target(
            policy=policy,
            product_intention=product_intention,
            surface=parsed_surface,
            combination_template=combination_template,
            owner_cohort=owner_cohort,
        )
    except RolloutAdmissionError as exc:
        record_rollout_decision(
            harness="unknown",
            realizer_ref="generic-omnigent-host@1",
            decision="denied",
            surface=parsed_surface,
        )
        raise HarnessPlatformError(
            f"Omnigent rollout default unavailable: {exc}",
            code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
        ) from exc
    return admitted


def _telemetry_harness(harness_implementation: str) -> str:
    lowered = (harness_implementation or "").strip().lower()
    for harness_id in ("codex-native", "claude-native", "opencode-native"):
        if lowered == harness_id or lowered.startswith(
            harness_id + "@"
        ) or lowered.startswith(harness_id + ":"):
            return harness_id
    return "unknown"


__all__ = [
    "admit_rollout_for_plan",
    "build_rollout_combination",
    "owner_cohort_from_parameters",
    "resolve_rollout_default_for_intention",
    "rollout_policy_configured",
    "rollout_surface_from_parameters",
]
