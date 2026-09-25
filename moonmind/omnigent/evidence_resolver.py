"""Resolve execution evidence according to the configured evidence policy."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from moonmind.omnigent.settings import omnigent_evidence_policy

logger = logging.getLogger(__name__)


def resolve_execution_evidence(
    plan_payload: Any,
    *,
    policy: str | None = None,
    now=None,
    require_evidence: bool | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Resolve evidence for a plan according to policy.

    MoonLadderStudios/MoonMind#4560: ordinary admission is
    certificate-independent. When the trusted settings boundary selects
    ordinary mode (omitted/blank/``either``), missing, expired, malformed,
    or unavailable optional certificates never veto execution: this returns
    ``(None, "uncertified")`` instead of raising, and the caller persists an
    explicitly uncertified ordinary admission -- never a fabricated passing
    certificate, never a ``supported``/``deployment_qualified`` claim.

    When strict certification is explicitly selected (``protected`` or
    ``deployment``), this fails closed exactly as before. ``require_evidence``
    overrides the settings-derived default; when None it is derived from
    :func:`omnigent_requires_certification`, which reads only the trusted
    deployment settings -- never workflow-authored input.

    Returns (evidence_dict_or_None, support_tier).
    Raises ValueError if no admissible evidence is found under strict mode.
    """

    from moonmind.omnigent.settings import omnigent_requires_certification

    selected_policy = (policy or omnigent_evidence_policy()).lower()
    strict = (
        require_evidence
        if require_evidence is not None
        else omnigent_requires_certification()
    )
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
            if not strict:
                # Ordinary admission: an invalid optional report remains
                # invalid and authorizes nothing, but it never vetoes
                # ordinary execution either. Report uncertified truthfully.
                logger.debug(
                    "optional execution evidence unavailable; admitting ordinary "
                    "uncertified execution",
                    exc_info=True,
                )
                return None, "uncertified"
            # If policy is either and protected already failed, bubble deployment failure.
            # The underlying reason is the only actionable part of this failure,
            # so it travels in the message and not just the exception chain.
            raise ValueError(
                f"no admissible execution evidence for the current policy: {exc}"
            ) from exc
    if not strict:
        return None, "uncertified"
    raise ValueError(f"unknown evidence policy: {selected_policy}")


@dataclass(frozen=True)
class SupportEvidenceFreshness:
    """What support evidence backs one exact combination, and how fresh it is.

    ``tier`` is empty when no entry was found at all. ``expired`` is reported
    separately from a missing entry so a readiness decision can name the exact
    actionable reason instead of collapsing "never qualified" and "qualification
    lapsed" into one message. ``status`` carries the recorded row outcome
    (MoonLadderStudios/MoonMind#3885) so a blocked or unavailable protected row
    is likewise distinguishable from an absent one.
    """

    tier: str = ""
    evidence_ref: str = ""
    age_seconds: float | None = None
    expired: bool = False
    #: The recorded row outcome. Empty means the tier does not record one.
    status: str = ""

    @property
    def usable(self) -> bool:
        """Whether this evidence would also satisfy admission.

        Admission accepts only a current, passing row, so a lapsed row and a
        recorded non-pass row are both unusable here. Reporting either as
        usable would make the rollout gate disagree with what execution will
        actually accept.
        """

        return not self.expired and self.status in {"", "passed"}


def _freshness(
    *,
    tier: str,
    entry: Any,
    evidence_ref: str,
    max_age_seconds: float,
    now: datetime,
) -> SupportEvidenceFreshness:
    age = max(0.0, (now - entry.generated_at).total_seconds())
    status = getattr(entry, "status", "")
    return SupportEvidenceFreshness(
        tier=tier,
        # A found entry always reports a non-empty ref so a readiness gate can
        # never read "qualified but unnamed" as "never qualified".
        evidence_ref=evidence_ref or tier,
        age_seconds=age,
        expired=(entry.expires_at <= now or age > max_age_seconds),
        status=str(getattr(status, "value", status) or ""),
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

    When every allowed tier found only unusable evidence, the first tier's
    result is reported so the denial reason is "stale" or the recorded non-pass
    status rather than "missing".

    It never raises and never admits anything: admission authority stays with
    :func:`resolve_execution_evidence`, which fails closed under explicit
    strict certification and admits explicitly uncertified ordinary execution
    otherwise.
    """

    selected_policy = (policy or omnigent_evidence_policy()).lower()
    observed_at = now or datetime.now(UTC)
    probes = []
    if selected_policy in {"protected", "either"}:
        probes.append(_protected_freshness)
    if selected_policy in {"deployment", "either"}:
        probes.append(_deployment_freshness)

    unusable: SupportEvidenceFreshness | None = None
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
        if observed.usable:
            return observed
        unusable = unusable or observed
    return unusable or SupportEvidenceFreshness()


def evidence_policy_allows_deployment(*, policy: str | None = None) -> bool:
    p = (policy or omnigent_evidence_policy()).lower()
    return p in {"deployment", "either"}


def evidence_policy_requires_protected(*, policy: str | None = None) -> bool:
    p = (policy or omnigent_evidence_policy()).lower()
    return p == "protected"


__all__ = [
    "RETAINED_CERTIFICATION_CONSUMERS",
    "SupportEvidenceFreshness",
    "resolve_execution_evidence",
    "resolve_support_evidence_freshness",
    "evidence_policy_allows_deployment",
    "evidence_policy_requires_protected",
]


#: Retained certificate machinery still wired as mandatory-for-strict
#: (MoonLadderStudios/MoonMind#4560 R12 removal audit).
#:
#: Each entry names the retained consumer, the strict/diagnostic use that
#: keeps it alive, and the exit condition that retires it. Ordinary
#: admission never consults these gates for a veto: ``resolve_execution_evidence``
#: returns ``(None, "uncertified")`` and the rollout/planner/bootstrap
#: ordinary paths treat freshness as advisory-only. The R3/R7/R8
#: ordinary-admission tests are the proof that no retained consumer
#: reinstates the veto. Real integrity and security checks stay; only the
#: historical-certificate prerequisite was removed from ordinary work.
RETAINED_CERTIFICATION_CONSUMERS: tuple[dict[str, str], ...] = (
    {
        "consumer": "moonmind.omnigent.bootstrap.qualification:run_qualification",
        "retained_use": (
            "strict certification report tail behind "
            "BootstrapController._publish_certification_report; "
            "diagnostic observation for ordinary readiness"
        ),
        "exit_condition": (
            "retire when explicit strict certification "
            "(protected/deployment) is removed, or when no strict or "
            "diagnostic consumer reads its report"
        ),
    },
    {
        "consumer": (
            "moonmind.omnigent.bootstrap.controller:"
            "materializer qualification sweep + deployment-evidence "
            "freshness reconciliation"
        ),
        "retained_use": (
            "mandatory sweep only under explicit strict certification; "
            "best-effort truthful observation otherwise"
        ),
        "exit_condition": (
            "remove the strict branch when strict certification is "
            "retired; keep the advisory observation while schedules "
            "report certification state"
        ),
    },
    {
        "consumer": (
            "moonmind.omnigent.deployment_evidence:"
            "validate_deployment_evidence expiry/max-age gates + "
            "assert_deployment_evidence_matches_plan host-image gate"
        ),
        "retained_use": (
            "strict admission enforcement and history-vs-new-effect "
            "diagnosis; ordinary admission never calls this module "
            "for a veto"
        ),
        "exit_condition": (
            "re-scope to diagnostic-only when strict certification is "
            "retired; host-image integrity stays with the runtime "
            "adapter/launch-preflight owners"
        ),
    },
    {
        "consumer": (
            "moonmind.omnigent.harness_platform.execution_plan:"
            "AdmissionAuthority strict validator (default strict)"
        ),
        "retained_use": (
            "preserves the in-flight interpretation of plans persisted "
            "before admissionMode existed; missing metadata never "
            "silently bypasses validation"
        ),
        "exit_condition": (
            "default may become ordinary only after every pre-upgrade "
            "strict plan has drained or migrated via "
            "reissue_ordinary_admission_for_saved_plan"
        ),
    },
    {
        "consumer": (
            "moonmind.omnigent.runtime_provider_rollout:_readiness_denials "
            "strict branch + harness_platform.planner freshness observation"
        ),
        "retained_use": (
            "strict row demotion for missing/stale/non-pass evidence; "
            "advisory observation for ordinary rows and operator "
            "migration views"
        ),
        "exit_condition": (
            "remove the strict demotion branch when strict "
            "certification is retired; keep the observation while "
            "readiness reporting names evidence state"
        ),
    },
    {
        "consumer": (
            "moonmind.workflows.temporal.activities."
            "omnigent_session_activities:_validate_plan_admission_authority "
            "strict branch"
        ),
        "retained_use": (
            "explicit strict new-effect enforcement at the plan-reader "
            "boundary; ordinary plans return before any certificate read"
        ),
        "exit_condition": (
            "remove when strict admission is retired and no strict "
            "plan remains loadable"
        ),
    },
)
