"""Deployment qualification evidence for locally-generated Omnigent execution.

This document is generated automatically by the running MoonMind installation
after it has proven the exact local deployment can safely run the combination.
It is distinct from protected release certification evidence.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.conformance import assert_secret_free
from moonmind.omnigent.harness_platform.support import (
    SupportKeyPayload,
    compute_deployment_qualification_key,
    compute_support_combination_key,
    deployment_excluded_fields_for,
)

DEPLOYMENT_EVIDENCE_VERSION = "moonmind.omnigent-deployment-execution-evidence/v1"
DEPLOYMENT_EVIDENCE_ISSUER = "moonmind-deployment-bootstrap@1"
DEPLOYMENT_EVIDENCE_KEY_ID = "moonmind-deployment-evidence-v1"
DEPLOYMENT_EVIDENCE_DEFAULT_TTL = timedelta(days=30)
MAX_DEPLOYMENT_EVIDENCE_AGE = timedelta(days=30)
_DEPLOYMENT_EVIDENCE_SIGNING_KEY_PATH = Path(
    os.getenv("MOONMIND_DEPLOYMENT_EVIDENCE_KEY_PATH", "var/secrets/deployment_evidence_key")
)
# Allow override for tests
_DEPLOYMENT_EVIDENCE_ENV = "MOONMIND_OMNIGENT_DEPLOYMENT_EVIDENCE"


class DeploymentEvidenceSignature(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)
    algorithm: Literal["hmac-sha256"] = "hmac-sha256"
    key_id: str = Field(alias="keyId")
    value: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _check_key(self) -> DeploymentEvidenceSignature:
        if self.key_id != DEPLOYMENT_EVIDENCE_KEY_ID:
            raise ValueError("invalid deployment evidence keyId")
        return self


class DeploymentExecutionEvidence(BaseModel):
    """Locally-generated deployment qualification evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    schema_version: Literal[DEPLOYMENT_EVIDENCE_VERSION] = Field(
        DEPLOYMENT_EVIDENCE_VERSION, alias="schemaVersion"
    )
    evidence_issuer: Literal[DEPLOYMENT_EVIDENCE_ISSUER] = Field(
        DEPLOYMENT_EVIDENCE_ISSUER, alias="evidenceIssuer"
    )
    deployment_id: str = Field(alias="deploymentId", min_length=1, max_length=255)
    compatibility_generation: str = Field(
        alias="compatibilityGeneration", min_length=1, max_length=255
    )
    generated_at: datetime = Field(alias="generatedAt")
    expires_at: datetime = Field(alias="expiresAt")
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
    provider: dict[str, Any] = Field()
    model: dict[str, Any] = Field()
    results: dict[str, str] = Field()
    evidence_refs: dict[str, str] = Field(alias="evidenceRefs")
    feature_generation: str = Field(alias="featureGeneration", min_length=1)
    replay_compatibility_version: str = Field(
        alias="replayCompatibilityVersion", min_length=1
    )
    rollback_policy_version: str = Field(alias="rollbackPolicyVersion", min_length=1)
    signature: DeploymentEvidenceSignature

    @model_validator(mode="after")
    def validate_exact_authority(self) -> DeploymentExecutionEvidence:
        if self.generated_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("deployment evidence timestamps require timezones")
        if self.expires_at <= self.generated_at:
            raise ValueError("deployment evidence validity interval is invalid")
        if self.support_combination_key != compute_support_combination_key(
            self.support_identity
        ):
            raise ValueError("deployment support combination key does not recompute")
        assert_secret_free(self.model_dump(mode="json", by_alias=True, exclude={"signature"}))
        # Verify HMAC with canonical payload
        payload = self._signing_payload()
        expected = hmac.new(_get_signing_key(), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, self.signature.value):
            raise ValueError("deployment evidence HMAC verification failed")
        return self

    def _signing_payload(self) -> bytes:
        # Canonical JSON without signature, sorted keys, compact separators, datetime as isoformat
        data = self.model_dump(mode="json", by_alias=True, exclude={"signature"})
        # Ensure datetime are isoformat strings already from pydantic json mode
        return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _compute_signature(payload: bytes) -> str:
    key = _get_signing_key()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _get_signing_key() -> bytes:
    path = Path(os.getenv("MOONMIND_DEPLOYMENT_EVIDENCE_KEY_PATH", str(_DEPLOYMENT_EVIDENCE_SIGNING_KEY_PATH)))
    # Also allow volume path /app/var/secrets/deployment_evidence_key
    candidates = [path, Path("/app/var/secrets/deployment_evidence_key"), Path("var/secrets/deployment_evidence_key")]
    for cand in candidates:
        try:
            if cand.exists():
                data = cand.read_bytes()
                if len(data) >= 32:
                    return data[:32] if len(data) == 32 else hashlib.sha256(data).digest()
                # If file contains hex, decode?
                text = data.decode("utf-8", errors="ignore").strip()
                if len(text) == 64 and all(c in "0123456789abcdefABCDEF" for c in text):
                    return bytes.fromhex(text)
                return hashlib.sha256(data).digest()
        except OSError:
            continue
    # Fallback: derive from encryption master key or generate deterministically
    fallback = os.getenv("MOONMIND_DEPLOYMENT_EVIDENCE_SIGNING_KEY", "")
    if fallback:
        return hashlib.sha256(fallback.encode()).digest()
    # Deterministic fallback: hash of fixed seed plus deployment id for stability across hosts
    # Use a fixed seed so tests and local dev generate the same key predictably
    seed = os.getenv("MOONMIND_DEPLOYMENT_EVIDENCE_SEED", "moonmind-deployment-evidence-fallback")
    deterministic = hashlib.sha256(seed.encode()).digest()
    # Attempt to persist deterministically derived key for future reads
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(deterministic)
        vol = Path("/app/var/secrets/deployment_evidence_key")
        if vol != path:
            try:
                vol.parent.mkdir(parents=True, exist_ok=True)
                vol.write_bytes(deterministic)
            except OSError:
                # Best-effort: volume may be read-only in some test environments
                pass
    except OSError:
        # Best-effort persistence failed; return deterministic key anyway
        pass
    return deterministic


def get_or_create_signing_key() -> bytes:
    return _get_signing_key()


def sign_deployment_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Sign a deployment evidence payload (without signature) and return full doc."""
    # Canonicalize datetime values exactly the way pydantic's JSON mode
    # renders them on the verification side (``_signing_payload``), otherwise
    # the two canonical forms disagree (e.g. "+00:00" vs "Z") and every
    # freshly signed document fails its own HMAC check.
    def _normalize(value: Any) -> Any:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
        if isinstance(value, str):
            # Datetimes already rendered as strings keep one canonical shape.
            return value.replace("+00:00", "Z") if "+00:00" in value else value
        if isinstance(value, dict):
            return {k: _normalize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_normalize(v) for v in value]
        return value

    normalized = _normalize(dict(payload))
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sig = _compute_signature(canonical)
    signed = dict(normalized)
    signed["signature"] = {
        "algorithm": "hmac-sha256",
        "keyId": DEPLOYMENT_EVIDENCE_KEY_ID,
        "value": sig,
    }
    # Validate through model to catch errors early (also verifies HMAC)
    DeploymentExecutionEvidence.model_validate(signed)
    return signed


class DeploymentEvidenceUnusable(ValueError):
    """One published document cannot back an admission, and why.

    ``reason`` is always one of this module's own fixed strings. A candidate is
    untrusted until its schema, secret scan, and HMAC all pass, and a parser
    error quotes the values it rejected, so no candidate-derived text may ever
    reach a diagnostic. Keeping the vocabulary here gives the reader one safe
    thing to report instead of choosing between silence and leaking evidence.
    """

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


#: The complete bounded vocabulary of :class:`DeploymentEvidenceUnusable`.
UNUSABLE_UNVERIFIED = "failed structural or signature verification"
UNUSABLE_FUTURE_DATED = "is future-dated"
UNUSABLE_EXPIRED = "is expired or past the maximum evidence age"
UNUSABLE_GENERATION = "targets a different compatibility generation"


def validate_deployment_evidence(
    evidence: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> DeploymentExecutionEvidence:
    try:
        parsed = DeploymentExecutionEvidence.model_validate(evidence)
    except Exception as exc:
        raise DeploymentEvidenceUnusable(
            "deployment evidence failed structural or signature verification",
            reason=UNUSABLE_UNVERIFIED,
        ) from exc
    observed_at = now or datetime.now(UTC)
    if parsed.generated_at > observed_at:
        raise DeploymentEvidenceUnusable(
            "deployment evidence is future-dated", reason=UNUSABLE_FUTURE_DATED
        )
    if (
        observed_at - parsed.generated_at > MAX_DEPLOYMENT_EVIDENCE_AGE
        or parsed.expires_at <= observed_at
    ):
        raise DeploymentEvidenceUnusable(
            "deployment evidence is stale or expired", reason=UNUSABLE_EXPIRED
        )
    # Enforce compatibility generations at validation time so stale-generation evidence
    # is rejected even before plan comparison (admission must be generation-aware).
    from moonmind.omnigent.session_supervisor_rollback import (
        SUPERVISOR_ROLLBACK_POLICY_VERSION,
    )
    from moonmind.schemas.omnigent_session_models import (
        OMNIGENT_SESSION_COMPATIBILITY_VERSION,
        OMNIGENT_SESSION_FEATURE_GENERATION,
    )

    if parsed.feature_generation != OMNIGENT_SESSION_FEATURE_GENERATION:
        raise DeploymentEvidenceUnusable(
            f"deployment evidence featureGeneration {parsed.feature_generation!r} "
            f"does not match current {OMNIGENT_SESSION_FEATURE_GENERATION!r}",
            reason=UNUSABLE_GENERATION,
        )
    if parsed.replay_compatibility_version != OMNIGENT_SESSION_COMPATIBILITY_VERSION:
        raise DeploymentEvidenceUnusable(
            f"deployment evidence replayCompatibilityVersion {parsed.replay_compatibility_version!r} "
            f"does not match current {OMNIGENT_SESSION_COMPATIBILITY_VERSION!r}",
            reason=UNUSABLE_GENERATION,
        )
    if parsed.rollback_policy_version != SUPERVISOR_ROLLBACK_POLICY_VERSION:
        raise DeploymentEvidenceUnusable(
            f"deployment evidence rollbackPolicyVersion {parsed.rollback_policy_version!r} "
            f"does not match current {SUPERVISOR_ROLLBACK_POLICY_VERSION!r}",
            reason=UNUSABLE_GENERATION,
        )
    return parsed


def assert_deployment_evidence_matches_plan(
    evidence: DeploymentExecutionEvidence,
    plan_payload: Any,
) -> None:
    """Verify one qualification document answers this exact plan.

    ``compute_deployment_qualification_key`` is the single owner of which
    identity fields a deployment qualifies, so comparing that key is the whole
    identity comparison. Re-projecting the identity here would be a second
    owner of the same rule, free to disagree with the key that admission and
    publication already select on — which is how a plan and its evidence drift
    apart in the first place.

    Everything outside that key is either per-run variance the qualification
    deliberately ignores (model, effort, required capabilities) or a
    deployment fact recorded beside the identity. The host image is the one
    such fact: it is substrate, not variance, so it is compared exactly.
    """

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

    # Verify compatibility generations match current deployment
    if evidence.feature_generation != OMNIGENT_SESSION_FEATURE_GENERATION:
        raise ValueError(
            f"deployment evidence featureGeneration {evidence.feature_generation!r} "
            f"does not match current {OMNIGENT_SESSION_FEATURE_GENERATION!r}"
        )
    if evidence.replay_compatibility_version != OMNIGENT_SESSION_COMPATIBILITY_VERSION:
        raise ValueError(
            f"deployment evidence replayCompatibilityVersion {evidence.replay_compatibility_version!r} "
            f"does not match current {OMNIGENT_SESSION_COMPATIBILITY_VERSION!r}"
        )
    if evidence.rollback_policy_version != SUPERVISOR_ROLLBACK_POLICY_VERSION:
        raise ValueError(
            f"deployment evidence rollbackPolicyVersion {evidence.rollback_policy_version!r} "
            f"does not match current {SUPERVISOR_ROLLBACK_POLICY_VERSION!r}"
        )
    if compute_deployment_qualification_key(
        evidence.support_identity
    ) != compute_deployment_qualification_key(support_identity):
        raise ValueError(
            "deployment evidence conflicts with the execution plan: it "
            "qualifies a different execution combination"
        )
    if evidence.host_image_ref != plan_payload.hostImageRef:
        raise ValueError(
            "deployment evidence conflicts with the execution plan: it "
            "qualifies a different host image"
        )


def _candidate_qualification_key(candidate: Mapping[str, Any]) -> str | None:
    """Return one published entry's deployment-scoped combination key."""

    identity = candidate.get("supportIdentity")
    if not isinstance(identity, Mapping):
        return None
    return compute_deployment_qualification_key(dict(identity))


def _support_identity_drift(
    plan_payload: Any, candidate: Mapping[str, Any]
) -> list[str]:
    """Name fields that separate a plan from untrusted candidate evidence.

    Candidate values have not passed schema validation, the secret scan, or
    HMAC verification. Diagnostics therefore expose bounded field names only.
    For none@1 plans, volatile build fields are ignored here exactly as in
    admission so diagnostics don't report churn that wouldn't block.
    """

    requested = plan_payload.supportIdentity.model_dump(mode="json", by_alias=True)
    attested = candidate.get("supportIdentity")
    if not isinstance(attested, Mapping):
        return ["supportIdentity"]
    excluded = deployment_excluded_fields_for(plan_payload.supportIdentity)
    drift: list[str] = []
    for field in sorted(SupportKeyPayload.model_fields):
        if field in excluded:
            continue
        if requested.get(field) != attested.get(field):
            drift.append(f"{field} differs")
    return drift


def _unqualified_combination_message(
    plan_payload: Any, candidates: list[Any]
) -> str:
    """Explain which exact combination is missing and how to re-qualify.

    Candidates are untrusted until schema, secret, and signature validation,
    so only bounded field names may appear in this pre-validation diagnostic.
    """

    differences = [
        _support_identity_drift(plan_payload, candidate)
        for candidate in candidates
        if isinstance(candidate, Mapping)
    ]
    # Historical rows are diagnostic hints, never admission authority. Report
    # the closest row so one current source change is not buried underneath
    # every image, policy, and credential class this deployment ever used.
    if differences:
        closest = min(differences, key=lambda fields: (len(fields), fields))
        detail = (
            "closest published identity: " + "; ".join(closest)
            if closest
            else "published identity does not resolve to unique deployment evidence"
        )
    else:
        detail = "no deployment evidence is published"
    # Name the requested credential and policy classes so a default
    # OpenCode Go + muse-spark + xhigh failure points at the exact stale
    # materializer/launch-policy combination instead of only the opaque
    # support key. Values are bounded refs, never secret bodies.
    try:
        _identity = plan_payload.supportIdentity
        _materializers = ",".join(sorted(map(str, getattr(_identity, "materializerRefs", ()) or ())))
        _launch = str(getattr(_identity, "launchPolicyRef", "") or "")
    except Exception:
        _materializers = ""
        _launch = ""
    _requested = ""
    if _materializers or _launch:
        _requested = f" requested materializerRefs=[{_materializers}] launchPolicyRef={_launch}."
    return (
        "this deployment is not qualified for the requested execution "
        f"combination {plan_payload.supportCombinationKey} ({detail}).{_requested} "
        "Qualification follows the current launch-ready Provider Profile "
        "materializer classes and Agent Profile launch policy; revalidate the "
        "requested profile and retry deployment qualification."
    )


def _load_deployment_evidence_candidates(
    path: str | Path | None,
) -> list[Any]:
    configured = str(
        path
        or os.getenv(
            _DEPLOYMENT_EVIDENCE_ENV,
            "/workspace/omnigent-evidence/deployment-execution-evidence.json",
        )
    ).strip()
    if not configured:
        raise ValueError("deployment execution evidence is not configured")
    try:
        raw = json.loads(Path(configured).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("deployment execution evidence is unavailable") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("deployment evidence must be an object")
    entries = raw.get("entries")
    return list(entries) if isinstance(entries, list) else [raw]


def load_deployment_evidence_entries(
    *,
    path: str | Path | None = None,
    now: datetime | None = None,
) -> tuple[DeploymentExecutionEvidence, ...]:
    """Load every independently valid local qualification entry.

    Invalid unrelated entries do not make a valid exact combination
    inadmissible. Callers receive only HMAC-verified, current evidence and can
    decide which deployment qualification class they own.
    """

    validated: list[DeploymentExecutionEvidence] = []
    for candidate in _load_deployment_evidence_candidates(path):
        if not isinstance(candidate, Mapping):
            continue
        try:
            validated.append(validate_deployment_evidence(candidate, now=now))
        except ValueError:
            continue
    if not validated:
        raise ValueError("deployment execution evidence is unavailable")
    return tuple(validated)


def load_deployment_evidence_for_support_combination(
    support_combination_key: str,
    *,
    path: str | Path | None = None,
    now: datetime | None = None,
) -> DeploymentExecutionEvidence:
    """Load one exact, recorded deployment qualification document.

    Bootstrap reconciliation owns the support combination it most recently
    published. It uses this read boundary to distinguish a still-current
    qualification from missing, expired, or superseded evidence without
    compiling a caller-owned execution plan.
    """

    expected_key = str(support_combination_key or "").strip()
    if not expected_key.startswith("omnigent-support:sha256:"):
        raise ValueError("deployment support combination key is unavailable")
    candidates = _load_deployment_evidence_candidates(path)
    matching = [
        value
        for value in candidates
        if isinstance(value, Mapping)
        and value.get("supportCombinationKey") == expected_key
    ]
    if len(matching) != 1:
        raise ValueError(
            "exact deployment execution evidence is unavailable for the "
            "recorded support combination"
        )
    return validate_deployment_evidence(matching[0], now=now)


def find_deployment_evidence_entry(
    support_identity: SupportKeyPayload,
    *,
    path: str | Path | None = None,
) -> DeploymentExecutionEvidence | None:
    """Return the recorded deployment entry qualifying one exact combination.

    This is an observation probe, not admission authority. It selects on the
    same deployment qualification key admission uses and applies the document's
    structural authority (closed schema, secret scan, HMAC), but not its
    freshness, so a caller reporting readiness can distinguish missing evidence
    from expired evidence instead of collapsing both into "missing". Admission
    still runs through :func:`load_deployment_evidence`, which fails closed.
    """

    expected = compute_deployment_qualification_key(support_identity)
    try:
        candidates = _load_deployment_evidence_candidates(path)
    except ValueError:
        return None
    matching: list[DeploymentExecutionEvidence] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        if _candidate_qualification_key(candidate) != expected:
            continue
        try:
            matching.append(DeploymentExecutionEvidence.model_validate(candidate))
        except Exception:
            continue
    if not matching:
        return None
    # One class can hold several published documents, so the probe has to
    # select the one admission would. Reporting an older document's freshness
    # would let a rollout gate call a class expired that the loader admits.
    return max(matching, key=lambda entry: entry.generated_at)


def load_deployment_evidence(
    plan_payload: Any,
    *,
    path: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Admit the current qualification this deployment published for one class.

    The publisher appends one signed document per qualification run and
    replaces only entries sharing the *current* projection, so a projection
    change leaves several documents describing one deployment class. That is
    publication history, not ambiguity: every candidate is schema-checked,
    secret-scanned, HMAC-verified, and freshness-checked, and the admitted one
    must still match the plan. Admission therefore takes the most recently
    generated admissible document and still fails closed when none is.
    """

    candidates = _load_deployment_evidence_candidates(path)
    requested_qualification_key = compute_deployment_qualification_key(
        plan_payload.supportIdentity
    )
    matching = [
        value
        for value in candidates
        if isinstance(value, Mapping)
        and _candidate_qualification_key(value) == requested_qualification_key
    ]
    admissible: list[DeploymentExecutionEvidence] = []
    conflict: ValueError | None = None
    unusable: list[str] = []
    for candidate in matching:
        try:
            parsed = validate_deployment_evidence(candidate, now=now)
        except DeploymentEvidenceUnusable as exc:
            # Invalid history never blocks a current document, but if nothing
            # current remains the operator needs the real reason: routine
            # expiry and evidence corruption are different problems.
            if exc.reason not in unusable:
                unusable.append(exc.reason)
            continue
        try:
            assert_deployment_evidence_matches_plan(parsed, plan_payload)
        except ValueError as exc:
            # A verified document for this class that the plan contradicts is
            # the actionable failure; keep it rather than reporting the class
            # as simply unqualified.
            conflict = conflict or exc
            continue
        admissible.append(parsed)
    if not admissible:
        if conflict is not None:
            raise conflict
        if unusable:
            raise ValueError(
                "this deployment published a qualification for the requested "
                f"execution combination {plan_payload.supportCombinationKey}, "
                "but no published document is usable: "
                + "; ".join(f"evidence {reason}" for reason in sorted(unusable))
                + ". Requalify this combination to publish current evidence."
            )
        raise ValueError(_unqualified_combination_message(plan_payload, candidates))
    current = max(admissible, key=lambda entry: entry.generated_at)
    return current.model_dump(mode="json", by_alias=True)


__all__ = [
    "DEPLOYMENT_EVIDENCE_DEFAULT_TTL",
    "DEPLOYMENT_EVIDENCE_ISSUER",
    "DEPLOYMENT_EVIDENCE_KEY_ID",
    "DEPLOYMENT_EVIDENCE_VERSION",
    "DeploymentEvidenceSignature",
    "DeploymentEvidenceUnusable",
    "DeploymentExecutionEvidence",
    "assert_deployment_evidence_matches_plan",
    "find_deployment_evidence_entry",
    "get_or_create_signing_key",
    "load_deployment_evidence",
    "load_deployment_evidence_entries",
    "load_deployment_evidence_for_support_combination",
    "sign_deployment_evidence",
    "validate_deployment_evidence",
]
