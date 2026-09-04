"""Resolve execution evidence according to the configured evidence policy."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from moonmind.omnigent.settings import omnigent_evidence_policy

logger = logging.getLogger(__name__)


def resolve_execution_evidence(
    plan_payload: Any,
    *,
    policy: str | None = None,
    now=None,
) -> tuple[dict[str, Any], Literal["supported", "deployment_qualified"]]:
    """Resolve evidence for a plan according to policy.

    Returns (evidence_dict, support_tier).
    Raises ValueError if no admissible evidence is found.
    """
    selected_policy = (policy or omnigent_evidence_policy()).lower()
    # Try protected first if policy is protected or either
    if selected_policy in {"protected", "either"}:
        try:
            from moonmind.omnigent.execution_support_evidence import (
                load_protected_execution_support_evidence,
            )

            evidence = load_protected_execution_support_evidence(
                plan_payload, now=now
            )
            return evidence, "supported"
        except Exception:
            if selected_policy == "protected":
                raise
            # fall through to deployment for either
    if selected_policy in {"deployment", "either"}:
        try:
            from moonmind.omnigent.deployment_evidence import load_deployment_evidence

            evidence = load_deployment_evidence(plan_payload, now=now)
            return evidence, "deployment_qualified"
        except Exception as exc:
            # If policy is either and protected already failed, bubble deployment failure.
            # The underlying reason is the only actionable part of this failure,
            # so it travels in the message and not just the exception chain.
            raise ValueError(
                f"no admissible execution evidence for the current policy: {exc}"
            ) from exc
    raise ValueError(f"unknown evidence policy: {selected_policy}")


@dataclass(frozen=True)
class SupportEvidenceFreshness:
    """What support evidence backs one exact combination, and how fresh it is.

    ``tier`` is empty when no entry was found at all. ``expired`` is reported
    separately from a missing entry so a readiness decision can name the exact
    actionable reason instead of collapsing "never qualified" and "qualification
    lapsed" into one message.
    """

    tier: str = ""
    evidence_ref: str = ""
    age_seconds: float | None = None
    expired: bool = False


def _freshness(
    *,
    tier: str,
    entry: Any,
    evidence_ref: str,
    max_age_seconds: float,
    now: datetime,
) -> SupportEvidenceFreshness:
    age = max(0.0, (now - entry.generated_at).total_seconds())
    return SupportEvidenceFreshness(
        tier=tier,
        # A found entry always reports a non-empty ref so a readiness gate can
        # never read "qualified but unnamed" as "never qualified".
        evidence_ref=evidence_ref or tier,
        age_seconds=age,
        expired=(entry.expires_at <= now or age > max_age_seconds),
    )


def _protected_freshness(
    support_identity: Any, now: datetime
) -> SupportEvidenceFreshness | None:
    from moonmind.omnigent.execution_support_evidence import (
        MAX_EXECUTION_SUPPORT_EVIDENCE_AGE,
        find_protected_evidence_entry,
    )
    from moonmind.omnigent.harness_platform.support import (
        compute_support_combination_key,
    )

    entry = find_protected_evidence_entry(
        compute_support_combination_key(support_identity)
    )
    if entry is None:
        return None
    return _freshness(
        tier="supported",
        entry=entry,
        evidence_ref=entry.protected_run_ref,
        max_age_seconds=MAX_EXECUTION_SUPPORT_EVIDENCE_AGE.total_seconds(),
        now=now,
    )


def _deployment_freshness(
    support_identity: Any, now: datetime
) -> SupportEvidenceFreshness | None:
    from moonmind.omnigent.deployment_evidence import (
        MAX_DEPLOYMENT_EVIDENCE_AGE,
        find_deployment_evidence_entry,
    )

    entry = find_deployment_evidence_entry(support_identity)
    if entry is None:
        return None
    return _freshness(
        tier="deployment_qualified",
        entry=entry,
        evidence_ref=entry.compatibility_generation,
        max_age_seconds=MAX_DEPLOYMENT_EVIDENCE_AGE.total_seconds(),
        now=now,
    )


def resolve_support_evidence_freshness(
    support_identity: Any,
    *,
    policy: str | None = None,
    now: datetime | None = None,
) -> SupportEvidenceFreshness:
    """Report the support evidence backing one exact combination.

    This is an *observation* boundary for the runtime-provider rollout readiness
    gate (MoonLadderStudios/MoonMind#3833 required work 9). It consults the same
    tiers, in the same order, and on the same matching identity as
    :func:`resolve_execution_evidence` -- the protected document by exact support
    combination key, the deployment document by deployment qualification key --
    and, exactly like admission under the ``either`` policy, it continues to the
    next tier when the preferred one is unusable. A rollout demotion therefore
    never disagrees with what admission will accept.

    When every allowed tier found only lapsed evidence, the first tier's lapse is
    reported so the denial reason is "stale" rather than "missing".

    It never raises and never admits anything: admission authority stays with
    :func:`resolve_execution_evidence`, which fails closed.
    """

    selected_policy = (policy or omnigent_evidence_policy()).lower()
    observed_at = now or datetime.now(UTC)
    probes = []
    if selected_policy in {"protected", "either"}:
        probes.append(_protected_freshness)
    if selected_policy in {"deployment", "either"}:
        probes.append(_deployment_freshness)

    lapsed: SupportEvidenceFreshness | None = None
    for probe in probes:
        try:
            observed = probe(support_identity, observed_at)
        except Exception:
            logger.debug(
                "support evidence probe failed",
                exc_info=True,
                extra={"probe": probe.__name__},
            )
            continue
        if observed is None:
            continue
        if not observed.expired:
            return observed
        lapsed = lapsed or observed
    return lapsed or SupportEvidenceFreshness()


def evidence_policy_allows_deployment(*, policy: str | None = None) -> bool:
    p = (policy or omnigent_evidence_policy()).lower()
    return p in {"deployment", "either"}


def evidence_policy_requires_protected(*, policy: str | None = None) -> bool:
    p = (policy or omnigent_evidence_policy()).lower()
    return p == "protected"


__all__ = [
    "SupportEvidenceFreshness",
    "resolve_execution_evidence",
    "resolve_support_evidence_freshness",
    "evidence_policy_allows_deployment",
    "evidence_policy_requires_protected",
]
