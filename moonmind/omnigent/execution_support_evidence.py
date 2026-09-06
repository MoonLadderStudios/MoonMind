"""Protected support authority for admitting immutable Omnigent plans.

This document is produced by a protected conformance run and materialized into
the API deployment.  The product planner may select a passing entry, but it may
not manufacture one from the plan it is trying to admit.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.concurrency_qualification import (
    ConcurrencyQualificationRecord,
)
from moonmind.omnigent.conformance import assert_secret_free
from moonmind.omnigent.harness_platform.support import (
    SupportClassification,
    SupportKeyPayload,
    compute_support_combination_key,
)


EXECUTION_SUPPORT_EVIDENCE_VERSION = (
    "moonmind.omnigent-protected-execution-support-evidence/v1"
)
EXECUTION_SUPPORT_EVIDENCE_ISSUER = (
    "moonmind-protected-omnigent-conformance@1"
)
MAX_EXECUTION_SUPPORT_EVIDENCE_AGE = timedelta(days=30)


class ExecutionSupportRowStatus(StrEnum):
    """Distinct outcomes one protected support row may record.

    MoonLadderStudios/MoonMind#3885 requires the protected index to record
    non-pass rows rather than omitting them: an operator must be able to tell a
    combination that failed from one that was deselected, refused by a release
    gate, or never had an environment to run in. Only :attr:`passed` is
    admissible — :func:`validate_protected_execution_support_evidence` refuses
    every other status, so widening what can be *recorded* never widens what
    can be *admitted*.
    """

    passed = "passed"
    failed = "failed"
    skipped = "skipped"
    blocked = "blocked"
    unavailable = "unavailable"
    partial = "partial"


class ProtectedExecutionSupportEvidence(BaseModel):
    """One independently-qualified exact execution combination."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    schema_version: Literal[EXECUTION_SUPPORT_EVIDENCE_VERSION] = Field(
        EXECUTION_SUPPORT_EVIDENCE_VERSION, alias="schemaVersion"
    )
    status: ExecutionSupportRowStatus = ExecutionSupportRowStatus.passed
    evidence_issuer: Literal[EXECUTION_SUPPORT_EVIDENCE_ISSUER] = Field(
        EXECUTION_SUPPORT_EVIDENCE_ISSUER,
        alias="evidenceIssuer",
    )
    source_commit: str = Field(
        alias="sourceCommit", pattern=r"^[0-9a-f]{7,64}$"
    )
    protected_run_ref: str = Field(alias="protectedRunRef", min_length=1, max_length=512)
    evidence_manifest_ref: str = Field(
        alias="evidenceManifestRef", min_length=1, max_length=512
    )
    evidence_manifest_digest: str = Field(
        alias="evidenceManifestDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    generated_at: datetime = Field(alias="generatedAt")
    expires_at: datetime = Field(alias="expiresAt")
    support_classification: SupportClassification = Field(
        alias="supportClassification"
    )
    support_combination_key: str = Field(alias="supportCombinationKey")
    support_identity: SupportKeyPayload = Field(alias="supportIdentity")
    host_image_ref: str = Field(
        alias="hostImageRef", pattern=r"^[^\s@]+@sha256:[0-9a-f]{64}$"
    )
    policy_snapshot_digest: str = Field(
        alias="policySnapshotDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    effective_launch_snapshot_digest: str = Field(
        alias="effectiveLaunchSnapshotDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    policy_gate_ref: str = Field(alias="policyGateRef", min_length=1, max_length=255)
    policy_qualified: bool = Field(True, alias="policyQualified")
    exact_artifacts_verified: bool = Field(True, alias="exactArtifactsVerified")
    #: The concurrency dimension of this combination, when the protected run
    #: also qualified concurrency. Absent means "no concurrency level is
    #: validated for this exact combination", which is not the same as 1.
    concurrency: ConcurrencyQualificationRecord | None = None
    feature_generation: str = Field(alias="featureGeneration", min_length=1)
    replay_compatibility_version: str = Field(
        alias="replayCompatibilityVersion", min_length=1
    )
    rollback_policy_version: str = Field(alias="rollbackPolicyVersion", min_length=1)

    @model_validator(mode="after")
    def validate_exact_authority(self) -> "ProtectedExecutionSupportEvidence":
        if self.generated_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("protected support evidence timestamps require timezones")
        if self.expires_at <= self.generated_at:
            raise ValueError("protected support evidence validity interval is invalid")
        if self.support_combination_key != compute_support_combination_key(
            self.support_identity
        ):
            raise ValueError("protected support combination key does not recompute")
        if self.support_classification not in {
            SupportClassification.fully_managed,
            SupportClassification.connected_host,
        }:
            raise ValueError("protected support classification is not admissible")
        if self.status is ExecutionSupportRowStatus.passed:
            if not (self.policy_qualified and self.exact_artifacts_verified):
                raise ValueError(
                    "a passing protected row must be policy qualified against "
                    "verified exact artifacts"
                )
        elif self.policy_qualified:
            # A recorded non-pass row exists so the outcome is visible, not so
            # it can carry admission authority. Letting it stay "qualified"
            # would make a blocked or unavailable environment look like a pass
            # to any reader that checks the flag instead of the status.
            raise ValueError(
                "a non-passing protected row cannot claim policy qualification"
            )
        if self.concurrency is not None and (
            self.concurrency.identity.support_combination_key
            != self.support_combination_key
        ):
            raise ValueError(
                "concurrency evidence belongs to a different support combination"
            )
        assert_secret_free(self.model_dump(mode="json", by_alias=True))
        return self


def validate_protected_execution_support_evidence(
    evidence: Mapping[str, Any],
    *,
    now: datetime | None = None,
    expected_source_commit: str | None = None,
) -> ProtectedExecutionSupportEvidence:
    """Validate provenance, freshness, and the closed protected schema."""

    parsed = ProtectedExecutionSupportEvidence.model_validate(evidence)
    if parsed.status is not ExecutionSupportRowStatus.passed:
        raise ValueError(
            "protected support evidence did not pass "
            f"(status={parsed.status.value})"
        )
    observed_at = now or datetime.now(UTC)
    if parsed.generated_at > observed_at:
        raise ValueError("protected support evidence is future-dated")
    if (
        observed_at - parsed.generated_at > MAX_EXECUTION_SUPPORT_EVIDENCE_AGE
        or parsed.expires_at <= observed_at
    ):
        raise ValueError("protected support evidence is stale or expired")
    if expected_source_commit and parsed.source_commit != expected_source_commit:
        raise ValueError("protected support evidence source commit is mismatched")
    return parsed


def assert_protected_evidence_matches_plan(
    evidence: ProtectedExecutionSupportEvidence,
    plan_payload: Any,
) -> None:
    """Fail closed unless protected evidence qualifies this exact plan."""

    support_identity = getattr(plan_payload, "supportIdentity", None)
    if support_identity is None:
        raise ValueError("execution plan lacks exact support identity")
    from moonmind.omnigent.session_supervisor_rollback import (
        SUPERVISOR_ROLLBACK_POLICY_VERSION,
    )
    from moonmind.schemas.omnigent_session_models import (
        OMNIGENT_SESSION_COMPATIBILITY_VERSION,
        OMNIGENT_SESSION_FEATURE_GENERATION,
    )

    admission = getattr(plan_payload, "admissionAuthority", None)
    expected = {
        "supportCombinationKey": plan_payload.supportCombinationKey,
        "supportIdentity": support_identity.model_dump(mode="json", by_alias=True),
        "hostImageRef": plan_payload.hostImageRef,
        "policySnapshotDigest": plan_payload.policySnapshotDigest,
        "effectiveLaunchSnapshotDigest": plan_payload.effectiveLaunchSnapshotDigest,
        "featureGeneration": getattr(
            admission, "featureGeneration", OMNIGENT_SESSION_FEATURE_GENERATION
        ),
        "replayCompatibilityVersion": getattr(
            admission,
            "replayCompatibilityVersion",
            OMNIGENT_SESSION_COMPATIBILITY_VERSION,
        ),
        "rollbackPolicyVersion": getattr(
            admission, "rollbackPolicyVersion", SUPERVISOR_ROLLBACK_POLICY_VERSION
        ),
    }
    actual = {
        "supportCombinationKey": evidence.support_combination_key,
        "supportIdentity": evidence.support_identity.model_dump(
            mode="json", by_alias=True
        ),
        "hostImageRef": evidence.host_image_ref,
        "policySnapshotDigest": evidence.policy_snapshot_digest,
        "effectiveLaunchSnapshotDigest": evidence.effective_launch_snapshot_digest,
        "featureGeneration": evidence.feature_generation,
        "replayCompatibilityVersion": evidence.replay_compatibility_version,
        "rollbackPolicyVersion": evidence.rollback_policy_version,
    }
    if actual != expected:
        raise ValueError("protected support evidence conflicts with the execution plan")


def protected_evidence_is_admissible(
    evidence: ProtectedExecutionSupportEvidence | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Whether this row still carries admission authority on its own terms.

    Admission also matches a row against the plan it is admitting; this answers
    only the half that is about the row itself — its recorded outcome and its
    freshness window. It answers it by asking
    :func:`validate_protected_execution_support_evidence`, so a consumer that
    advertises something on the strength of a row cannot drift into a second,
    more permissive freshness rule and advertise what admission will refuse.
    """

    if evidence is None:
        return False
    try:
        validate_protected_execution_support_evidence(
            evidence.model_dump(mode="json", by_alias=True), now=now
        )
    except ValueError:
        return False
    return True


def advertised_concurrency_ceiling(
    evidence: ProtectedExecutionSupportEvidence | None,
    *,
    operator_ceiling: int | None = None,
    now: datetime | None = None,
) -> int:
    """Return the concurrency level this exact combination may advertise.

    Readiness advertises the *validated* level for the exact deployment
    combination, or a lower operator ceiling — never a configured value that
    was never observed. Missing concurrency evidence advertises ``0``: an
    unqualified combination is not implicitly qualified at 1, because nothing
    observed it.

    A row admission would refuse — a non-pass outcome, an expired document, or
    one older than :data:`MAX_EXECUTION_SUPPORT_EVIDENCE_AGE` — advertises ``0``
    for the same reason: the concurrency dimension inherits the authority of the
    row that carries it and has none of its own.
    """

    if evidence is None or evidence.concurrency is None:
        return 0
    if not protected_evidence_is_admissible(evidence, now=now):
        return 0
    return evidence.concurrency.advertised_concurrency_level(operator_ceiling)


def _protected_evidence_candidates(path: str | Path | None) -> list[Any]:
    """Return the published protected entries, unvalidated."""

    configured = str(
        path
        or os.getenv("MOONMIND_OMNIGENT_EXECUTION_SUPPORT_EVIDENCE", "")
    ).strip()
    if not configured:
        raise ValueError("protected Omnigent execution support evidence is not configured")
    try:
        raw = json.loads(Path(configured).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("protected Omnigent execution support evidence is unavailable") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("protected Omnigent execution support evidence must be an object")
    entries = raw.get("entries")
    return list(entries) if isinstance(entries, list) else [raw]


def find_protected_evidence_entry(
    support_combination_key: str,
    *,
    path: str | Path | None = None,
) -> ProtectedExecutionSupportEvidence | None:
    """Return the recorded protected entry for one exact support combination.

    This is an observation probe, not admission authority. It applies the
    document's structural authority (closed schema, declared issuer, recomputed
    support key, secret scan) but not its freshness or its recorded outcome, so
    a caller reporting readiness can distinguish missing evidence from expired
    evidence and from a recorded ``failed``/``blocked``/``unavailable`` row
    instead of collapsing them all into "missing". Admission still runs through
    :func:`load_protected_execution_support_evidence`, which fails closed.
    """

    expected = str(support_combination_key or "").strip()
    if not expected:
        return None
    try:
        candidates = _protected_evidence_candidates(path)
    except ValueError:
        return None
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        if candidate.get("supportCombinationKey") != expected:
            continue
        try:
            return ProtectedExecutionSupportEvidence.model_validate(candidate)
        except Exception:
            continue
    return None


def load_protected_execution_support_evidence(
    plan_payload: Any,
    *,
    path: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Load one protected document selected by the exact plan support key."""

    candidates = _protected_evidence_candidates(path)
    matching = [
        value
        for value in candidates
        if isinstance(value, Mapping)
        and value.get("supportCombinationKey") == plan_payload.supportCombinationKey
    ]
    if len(matching) != 1:
        raise ValueError("exact protected execution support evidence is unavailable")
    expected_commit = os.getenv("MOONMIND_SOURCE_COMMIT", "").strip() or None
    parsed = validate_protected_execution_support_evidence(
        matching[0], now=now, expected_source_commit=expected_commit
    )
    assert_protected_evidence_matches_plan(parsed, plan_payload)
    return parsed.model_dump(mode="json", by_alias=True)


__all__ = [
    "EXECUTION_SUPPORT_EVIDENCE_VERSION",
    "EXECUTION_SUPPORT_EVIDENCE_ISSUER",
    "MAX_EXECUTION_SUPPORT_EVIDENCE_AGE",
    "ExecutionSupportRowStatus",
    "ProtectedExecutionSupportEvidence",
    "advertised_concurrency_ceiling",
    "assert_protected_evidence_matches_plan",
    "find_protected_evidence_entry",
    "protected_evidence_is_admissible",
    "load_protected_execution_support_evidence",
    "validate_protected_execution_support_evidence",
]
