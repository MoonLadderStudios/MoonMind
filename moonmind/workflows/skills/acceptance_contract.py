"""Typed adapter for the portable verifier's declared acceptance evidence.

This validates the terminal contract and identity bindings, never collects or
classifies requirements. The resolved moonspec-verify bundle owns those semantics.
Existing gate artifacts carry this value under validatedRefs.acceptance.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Record(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, str_strip_whitespace=True
    )


class AcceptanceSubject(_Record):
    repository: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    content_digest: str = Field(alias="contentDigest", min_length=1)


class AcceptanceTarget(_Record):
    ref: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    content_digest: str = Field(alias="contentDigest", min_length=1)


class AcceptanceScope(_Record):
    source_ref: str = Field(alias="sourceRef", min_length=1)
    source_digest: str = Field(alias="sourceDigest", min_length=1)
    complete: Literal[True]
    requirement_ids: list[str] = Field(alias="requirementIds", min_length=1)


class RequirementEvidence(_Record):
    requirement_id: str = Field(alias="requirementId", min_length=1)
    evidence_refs: list[str] = Field(alias="evidenceRefs")
    status: (
        Literal[
            "VERIFIED",
            "PARTIAL",
            "MISSING",
            "CONFLICT",
            "UNVERIFIED",
            "NO_DETERMINATION",
            "FAILED",
        ]
        | None
    ) = None
    obligation_digest: str | None = Field(None, alias="obligationDigest", min_length=1)
    tested_subject: AcceptanceSubject | None = Field(None, alias="testedSubject")
    proof_method: str | None = Field(None, alias="proofMethod", min_length=1)
    target: str | None = Field(None, min_length=1)
    profile: str | None = Field(None, min_length=1)
    topology: str | None = Field(None, min_length=1)
    sampling_policy: str | None = Field(None, alias="samplingPolicy", min_length=1)


class EvidenceFreshness(_Record):
    policy: str = Field(min_length=1)
    valid_until: datetime | None = Field(None, alias="validUntil")

    @model_validator(mode="after")
    def timezone_required(self) -> EvidenceFreshness:
        if self.valid_until is not None and self.valid_until.tzinfo is None:
            raise ValueError("validUntil requires a timezone")
        return self


class SourceArtifactBinding(_Record):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, str_strip_whitespace=False
    )

    artifact_ref: str = Field(alias="artifactRef", min_length=1)
    pointer: str = ""


class AcceptanceEvidence(_Record):
    schema_version: Literal["acceptance/v1"] = Field(alias="schemaVersion")
    subject: AcceptanceSubject
    scope: AcceptanceScope
    completion_target: AcceptanceTarget = Field(alias="completionTarget")
    evidence: list[RequirementEvidence] = Field(min_length=1)
    freshness: EvidenceFreshness
    verification_map: list[dict[str, Any]] | None = Field(None, alias="verificationMap")
    evidence_set: dict[str, str] | None = Field(None, alias="evidenceSet")
    evidence_set_digest: str | None = Field(None, alias="evidenceSetDigest")
    source_binding: SourceArtifactBinding | None = Field(None, alias="sourceBinding")
    scope_reconciliation: dict[str, Any] | None = Field(
        None, alias="scopeReconciliation"
    )

    @model_validator(mode="after")
    def complete_binding(self) -> AcceptanceEvidence:
        required = self.scope.requirement_ids
        evidenced = [item.requirement_id for item in self.evidence]
        if (
            any(not item.strip() for item in required)
            or len(set(required)) != len(required)
            or len(set(evidenced)) != len(evidenced)
            or set(required) != set(evidenced)
            or any(
                not item.evidence_refs
                or any(not ref.strip() for ref in item.evidence_refs)
                for item in self.evidence
            )
        ):
            raise ValueError(
                "acceptance evidence must bind every mandatory requirement exactly once"
            )
        if any(row.status not in {None, "VERIFIED"} for row in self.evidence):
            raise ValueError(
                "objective acceptance cannot contain nonpassing requirement evidence"
            )
        if any(
            value is not None
            for value in (
                self.verification_map,
                self.evidence_set,
                self.evidence_set_digest,
                self.source_binding,
                self.scope_reconciliation,
            )
        ):
            self._validate_source_bound_evidence()
        return self

    def _validate_source_bound_evidence(self) -> None:
        """Validate the portable declaration without collecting/classifying work."""
        rows = self.verification_map
        inventory = _source_inventory(self.evidence_set, self.evidence_set_digest)
        plans = _source_plan(self.scope, rows)
        if self.scope_reconciliation is not None:
            _validate_scope_reconciliation(
                self.scope_reconciliation, self.scope.model_dump(by_alias=True)
            )
        for evidence in self.evidence:
            _validate_requirement_row(
                evidence,
                plans[evidence.requirement_id],
                self.subject,
                inventory,
                require_verified=True,
            )

    def matches_target(self, *, repository: str, ref: str, revision: str) -> bool:
        """Validate an independently observed target against declared evidence."""
        return (
            self.subject.repository == repository
            and self.completion_target.ref == ref
            and self.completion_target.revision == revision
            and self.subject.content_digest == self.completion_target.content_digest
        )

    def is_current(self, now: datetime) -> bool:
        expiry = self.freshness.valid_until
        return expiry is None or now < expiry


# Shared acceptance/v1 declaration fields. Transient readiness, advice and
# attempt accounting intentionally do not change an executed proof obligation.
_OBLIGATION_FIELDS = (
    "id",
    "sourceRef",
    "sourceDigest",
    "sourceLocator",
    "sourceQuote",
    "classification",
    "requiredBehavior",
    "executableChecks",
    "proofMethods",
    "owner",
    "target",
    "profile",
    "topology",
    "samplingPolicy",
)
_PROOF_FIELDS = ("target", "profile", "topology", "samplingPolicy")


def _source_plan(scope: AcceptanceScope, rows: Any) -> dict[str, dict[str, Any]]:
    if (
        not isinstance(rows, list)
        or not rows
        or any(not isinstance(row, Mapping) for row in rows)
    ):
        raise ValueError("source-bound verification map is missing or malformed")
    mandatory = set(scope.requirement_ids)
    plans = {}
    for row in rows:
        requirement_id = row.get("id")
        classification = row.get("classification")
        if (
            not isinstance(requirement_id, str)
            or not requirement_id.strip()
            or requirement_id in plans
            or row.get("sourceRef") != scope.source_ref
            or row.get("sourceDigest") != scope.source_digest
            or not all(
                isinstance(row.get(key), str) and row[key].strip()
                for key in ("sourceLocator", "requiredBehavior")
            )
            or classification not in {"mandatory", "optional", "separate_release"}
            or (classification == "mandatory") != (requirement_id in mandatory)
            or (
                "mandatory" in row
                and row["mandatory"] is not (classification == "mandatory")
            )
        ):
            raise ValueError(
                "verification map must preserve the original mandatory source scope"
            )
        if classification == "mandatory" and (
            not all(
                _nonblank_string_list(row.get(key))
                for key in ("executableChecks", "proofMethods")
            )
            or not isinstance(row.get("owner"), str)
            or not row["owner"].strip()
        ):
            raise ValueError(
                "mandatory source requirement needs executable proof and its owner"
            )
        if any(
            key in row and (not isinstance(row[key], str) or not row[key].strip())
            for key in _PROOF_FIELDS
        ):
            raise ValueError("source proof constraints must be nonblank strings")
        plans[requirement_id] = row
    if {
        key for key, value in plans.items() if value["classification"] == "mandatory"
    } != mandatory:
        raise ValueError("verification map omits a mandatory source requirement")
    return plans


def _binding_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _nonblank_string_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and item.strip() for item in value)
    )


def acceptance_evidence(payload: Mapping[str, Any]) -> AcceptanceEvidence | None:
    """Read a well-formed declaration; legacy verdicts grant no new authority."""
    refs = payload.get("validatedRefs")
    raw = refs.get("acceptance") if isinstance(refs, Mapping) else None
    if not isinstance(raw, Mapping):
        return None
    try:
        return AcceptanceEvidence.model_validate(raw)
    except (TypeError, ValueError):
        return None


def _scope_identity(value: Any) -> tuple[str, str, bool, tuple[str, ...]] | None:
    """Read a scope binding without trusting agent-authored container types."""
    if not isinstance(value, Mapping):
        return None
    source_ref = value.get("sourceRef")
    source_digest = value.get("sourceDigest")
    complete = value.get("complete")
    ids = value.get("requirementIds")
    if (
        not isinstance(source_ref, str)
        or not source_ref.strip()
        or not isinstance(source_digest, str)
        or not source_digest.strip()
        or not isinstance(complete, bool)
        or not isinstance(ids, list)
        or not ids
        or any(not isinstance(item, str) or not item.strip() for item in ids)
    ):
        return None
    normalized = tuple(sorted(item.strip() for item in ids))
    if len(set(normalized)) != len(normalized):
        return None
    return source_ref.strip(), source_digest.strip(), complete, normalized


def _validate_scope_reconciliation(
    record: Any, current: Mapping[str, Any], *, previous: Any = None
) -> None:
    if not isinstance(record, Mapping):
        raise ValueError("changed source scope needs its reconciliation record")
    old = _scope_identity(record.get("previousScope"))
    new = _scope_identity(record.get("currentScope"))
    current_identity = _scope_identity(current)
    if (
        old is None
        or new is None
        or new != current_identity
        or old[0] != new[0]
        or (previous is not None and old != _scope_identity(previous))
    ):
        raise ValueError(
            "scope reconciliation does not bind the original and current source scopes"
        )
    removed = record.get("removedRequirements")
    if not isinstance(removed, list):
        raise ValueError("scope reconciliation needs removed requirement explanations")
    ids = []
    for row in removed:
        if not isinstance(row, Mapping) or not all(
            isinstance(row.get(key), str) and row[key].strip()
            for key in ("id", "reason", "sourceLocator")
        ):
            raise ValueError(
                "removed historical requirement needs its reason and source citation"
            )
        ids.append(row["id"])
    if len(set(ids)) != len(ids) or set(ids) != set(old[3]) - set(new[3]):
        raise ValueError(
            "scope reconciliation must explain every removed "
            "historical requirement exactly once"
        )


def _source_inventory(value: Any, digest: Any) -> dict[str, str]:
    if (
        not isinstance(value, Mapping)
        or any(
            not isinstance(ref, str)
            or not ref.strip()
            or not isinstance(body_digest, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", body_digest)
            for ref, body_digest in value.items()
        )
        or digest != _binding_digest(value)
    ):
        raise ValueError(
            "source-bound acceptance requires a byte-bound evidence inventory"
        )
    return dict(value)


def _validate_requirement_row(
    row: RequirementEvidence,
    plan: Mapping[str, Any],
    subject: AcceptanceSubject,
    inventory: Mapping[str, str],
    *,
    require_verified: bool,
) -> None:
    tested = row.tested_subject or subject
    declared = row.model_dump(by_alias=True)
    if (
        row.status is None
        or (require_verified and row.status != "VERIFIED")
        or row.obligation_digest
        != _binding_digest(
            {key: plan[key] for key in _OBLIGATION_FIELDS if key in plan}
        )
        or any(ref not in inventory for ref in row.evidence_refs)
        or tested.repository != subject.repository
        or tested.content_digest != subject.content_digest
        or (
            row.status == "VERIFIED"
            and (
                not row.evidence_refs
                or row.proof_method not in plan["proofMethods"]
                or any(
                    declared[key] != plan[key] for key in _PROOF_FIELDS if key in plan
                )
            )
        )
    ):
        raise ValueError(
            "requirement evidence does not bind the declared source proof and candidate"
        )


def _remaining_requirement_ids(
    item: Mapping[str, Any],
    required: set[str],
    *,
    require_primary: bool = False,
) -> list[str]:
    primary = item.get("requirement")
    ids = item.get("requirementIds", [primary])
    if not _nonblank_string_list(ids):
        raise ValueError(
            "Blocking work must name a mandatory requirement "
            "in the original source scope"
        )
    normalized = [value.strip() for value in ids]
    if len(set(normalized)) != len(normalized) or not set(normalized) <= required:
        raise ValueError(
            "Blocking work must name a mandatory requirement "
            "in the original source scope"
        )
    if primary is not None or require_primary:
        if not isinstance(primary, str) or primary.strip() not in normalized:
            raise ValueError(
                "remaining work primary requirement conflicts with its requirementIds"
            )
    return normalized


def _nonpassing_coverage(
    gate: Mapping[str, Any], binding: Mapping[str, Any], required: set[str]
) -> None:
    evidence = binding.get("evidence")
    if not isinstance(evidence, list):
        raise ValueError("current judgment requires requirement evidence rows")
    represented, verified = set(), set()
    for row in evidence:
        if not isinstance(row, Mapping):
            raise ValueError("current judgment has malformed requirement evidence")
        requirement_id = row.get("requirementId")
        if (
            not isinstance(requirement_id, str)
            or requirement_id not in required
            or requirement_id in represented
        ):
            raise ValueError(
                "current judgment has duplicate or out-of-scope requirement evidence"
            )
        represented.add(requirement_id)
        if row.get("status") == "VERIFIED":
            verified.add(requirement_id)
    gaps = set()
    work = gate.get("remainingWork")
    if not isinstance(work, list) or not work:
        raise ValueError(
            "nonpassing current judgment needs inline mandatory remaining work"
        )
    for item in work:
        if not isinstance(item, Mapping):
            raise ValueError("nonpassing current judgment has malformed remaining work")
        gaps.update(_remaining_requirement_ids(item, required, require_primary=True))
    if verified & gaps:
        raise ValueError("verified requirement cannot simultaneously be remaining work")
    if represented | gaps != required:
        raise ValueError(
            "current judgment must account for every mandatory requirement"
        )


def source_scope_from_artifact(
    gate: Mapping[str, Any],
    *,
    source_bytes: bytes,
    source_ref: str,
    source_artifact_ref: str,
    assessment: Mapping[str, Any],
    require_source_bound: bool = False,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Reconcile a current source-cited map against independently retrieved bytes.

    The selected verifier owns requirement extraction and classification. This
    reader checks source identity, exact bytes, map completeness and a truthful
    explanation of changed historical IDs; it never reclassifies source prose.
    Whole original brief bytes are the host-owned scope. A verifier cannot
    select a field that excludes other acceptance criteria or constraints.
    Retained calls may omit the map; new admissions require the strict binding.
    """
    refs = gate.get("validatedRefs")
    binding = refs.get("acceptance") if isinstance(refs, Mapping) else None
    if not isinstance(binding, Mapping) or "verificationMap" not in binding:
        return None, (
            [
                "source-bound admission requires a complete acceptance binding "
                "and verification map"
            ]
            if require_source_bound
            else []
        )
    try:
        declared_source = SourceArtifactBinding.model_validate(
            binding.get(
                "sourceBinding",
                {
                    "artifactRef": source_artifact_ref,
                    "pointer": "",
                },
            )
        )
        if not source_artifact_ref or declared_source.artifact_ref.removeprefix(
            "artifact://"
        ) != source_artifact_ref.removeprefix("artifact://"):
            raise ValueError(
                "source binding does not reference the original trusted artifact"
            )
        if declared_source.pointer:
            raise ValueError(
                "issue brief verification requires the complete original source "
                "artifact; field selection is not admitted"
            )
        try:
            original = json.loads(source_bytes)
        except (ValueError, UnicodeError):
            original = None
        if isinstance(original, Mapping):
            resolution = original.get("source_resolution")
            if (
                original.get("truncated") is True
                or original.get("truncated_fields")
                or (
                    isinstance(resolution, Mapping)
                    and (
                        resolution.get("status") == "incomplete"
                        or resolution.get("unrecovered_fields")
                    )
                )
            ):
                raise ValueError(
                    "original source scope is incomplete; "
                    "recover its missing source fields"
                )
        selected = source_bytes
        scope = AcceptanceScope.model_validate(binding.get("scope"))
        if (
            not source_ref
            or scope.source_ref != source_ref
            or scope.source_digest != "sha256:" + hashlib.sha256(selected).hexdigest()
        ):
            raise ValueError(
                "current source scope does not match the original "
                "source identity and bytes"
            )
        plans = _source_plan(scope, binding.get("verificationMap"))
        if require_source_bound:
            if binding.get("schemaVersion") != "acceptance/v1":
                raise ValueError("source-bound admission requires acceptance/v1")
            subject = AcceptanceSubject.model_validate(binding.get("subject"))
            AcceptanceTarget.model_validate(binding.get("completionTarget"))
            EvidenceFreshness.model_validate(binding.get("freshness"))
            inventory = _source_inventory(
                binding.get("evidenceSet"), binding.get("evidenceSetDigest")
            )
            if gate.get("verdict") == "FULLY_IMPLEMENTED":
                AcceptanceEvidence.model_validate(binding)
            else:
                _nonpassing_coverage(gate, binding, set(scope.requirement_ids))
                for raw in binding["evidence"]:
                    row = RequirementEvidence.model_validate(raw)
                    _validate_requirement_row(
                        row,
                        plans[row.requirement_id],
                        subject,
                        inventory,
                        require_verified=False,
                    )
        elif gate.get("verdict") != "FULLY_IMPLEMENTED":
            _nonpassing_coverage(gate, binding, set(scope.requirement_ids))
        for row in plans.values():
            quote = row.get("sourceQuote")
            if quote is not None and (
                not isinstance(quote, str)
                or not quote.strip()
                or quote.encode("utf-8") not in selected
            ):
                raise ValueError(
                    "verification map source quote is absent from the original source"
                )
        current = scope.model_dump(by_alias=True)
        previous = assessment.get("scope")
        prior_identity = _scope_identity(previous)
        record = binding.get("scopeReconciliation")
        if record is not None or (
            prior_identity is not None
            and prior_identity[2]
            and prior_identity[0] == source_ref
            and prior_identity != _scope_identity(current)
        ):
            _validate_scope_reconciliation(record, current, previous=previous)
        return current, []
    except (KeyError, IndexError, TypeError, ValueError, UnicodeError) as exc:
        return None, [str(exc)]


def assessment_scope_violations(
    gate: Mapping[str, Any],
    assessment: Mapping[str, Any],
    *,
    source_scope: Mapping[str, Any] | None = None,
) -> list[str]:
    """Bind current judgments to source scope, using the assessment as context.

    An independently read source binding supersedes the historical assessment.
    An old artifact with no complete scope grants no acceptance authority and
    cannot invent a new prerequisite. Objective success still requires the full
    acceptance contract, checked separately at the same publication boundary.
    """
    scope = source_scope if source_scope is not None else assessment.get("scope")
    if source_scope is None and (
        not isinstance(scope, Mapping) or scope.get("complete") is not True
    ):
        return []
    original = _scope_identity(scope)
    if original is None:
        return ["Original source scope is malformed; recover its requirement binding"]
    success = str(gate.get("verdict") or "").strip().upper() == "FULLY_IMPLEMENTED"
    if success and not original[2]:
        return [
            "The original source scope is incomplete; objective success is unavailable"
        ]
    refs = gate.get("validatedRefs")
    acceptance = refs.get("acceptance") if isinstance(refs, Mapping) else None
    declared = (
        acceptance.get("scope")
        if isinstance(acceptance, Mapping)
        else None if success else gate.get("scope")
    )
    if _scope_identity(declared) != original:
        return ["Verifier scope differs from the original source scope"]
    if success:
        return []

    mandatory = set(original[3])
    work = gate.get("remainingWork")
    if not isinstance(work, list) or not work:
        return ["Blocking report needs inline work linked to a mandatory requirement"]
    violations = []
    for item in work:
        if not isinstance(item, Mapping):
            violations.append("Blocking report has malformed remaining work")
            continue
        try:
            _remaining_requirement_ids(item, mandatory)
        except ValueError as exc:
            violations.append(str(exc))
    return violations


async def validate_completion_target(
    payload: Mapping[str, Any],
    *,
    repository: str,
    source_ref: str,
    expected_ref: str = "",
    read_target: Callable[[str, str], Awaitable[Mapping[str, str]]],
) -> str | None:
    """Validate the terminal declaration against the owning live ref reader.

    Return an actionable missing-evidence reason; do not reinterpret requirements,
    rerun tests, publish code, or derive acceptance from an initial assessment.
    """
    evidence = acceptance_evidence(payload)
    if (
        payload.get("verdict") != "FULLY_IMPLEMENTED"
        or payload.get("invalid")
        or payload.get("degraded")
        or evidence is None
    ):
        return (
            "Run objective verification with complete acceptance/v1 evidence "
            "before issue completion"
        )
    if not source_ref or evidence.scope.source_ref != source_ref:
        return (
            "Verify the original issue scope; "
            "the acceptance source identity does not match"
        )
    if not evidence.is_current(datetime.now(timezone.utc)):
        return "Required acceptance evidence expired; repeat the affected verification"
    try:
        target = await read_target(repository, expected_ref)
    except Exception:
        return (
            "Completion target is unreadable through the authorized repository "
            "reader; retry that evidence read"
        )
    if (
        not evidence.matches_target(
            repository=repository,
            ref=target.get("ref", ""),
            revision=target.get("revision", ""),
        )
        or target.get("contentDigest") != evidence.subject.content_digest
    ):
        return (
            "Verify the current completion target or publish the candidate "
            "through its required review path"
        )
    return None
