"""Bounded operator-supplied framework intelligence on the control evaluator.

Mappings describe directional relationships, never evidence or objective coverage.
No registry or network retrieval is involved. Artifact access and byte integrity
are the caller's responsibility.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import TYPE_CHECKING, Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, StringConstraints, model_validator

from moonmind.security.assessments import ControlObjective
from moonmind.security.frameworks import (
    DeclaredRequirementChoice,
    EvidencePeriod,
    FrameworkContract,
    FrameworkRequirement,
    FrameworkSource,
    Text,
)

if TYPE_CHECKING:
    from moonmind.security.assessments import AssessmentScope, ControlAssessmentPlan

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$",
    ),
]
Identifiers = Annotated[list[Identifier], Field(min_length=1, max_length=64)]


class PackSource(FrameworkSource):
    @model_validator(mode="after")
    def validate_url(self):
        parsed = urlsplit(self.url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError(
                "framework source requires an HTTP(S) URL without credentials"
            )
        return self


class PackObjective(ControlObjective):
    """Existing evaluator objective with bounded machine identifiers."""

    id: Identifier
    source: Identifier
    tool: Identifier
    rule_ids: Annotated[list[Identifier], Field(min_length=1, max_length=32)]


class PackRequirement(FrameworkRequirement):
    id: Identifier
    summary: Text
    sources: Annotated[list[PackSource], Field(max_length=16)] = Field(
        default_factory=list
    )
    required_evidence_methods: Annotated[
        list[Literal["deterministic_check", "enquiry", "evidence", "observation"]],
        Field(max_length=4),
    ] = Field(default_factory=list)
    objectives: Annotated[list[PackObjective], Field(max_length=32)] = Field(
        default_factory=list
    )
    objectives_complete: bool = False
    objective_basis: Annotated[str, StringConstraints(min_length=1, max_length=1024)]

    @model_validator(mode="after")
    def unique_objectives(self):
        _unique([item.id for item in self.objectives], "objective")
        if self.objectives_complete and not self.objectives:
            raise ValueError("complete objectives require explicit checks")
        return self


class MappingTarget(FrameworkContract):
    id: Identifier
    framework_id: Identifier
    edition: Identifier
    requirement_ids: Identifiers

    @model_validator(mode="after")
    def unique_requirements(self):
        _edition(self.edition)
        _unique(self.requirement_ids, "mapping target requirement")
        return self


class FrameworkMapping(FrameworkContract):
    id: Identifier
    from_requirement_ids: Identifiers
    target_id: Identifier
    to_requirement_ids: Identifiers
    relation: Literal["equivalent", "partial", "subset", "superset", "related"]
    rationale: Text
    source: PackSource
    basis: Literal["upstream", "evaluated"]
    reviewed_by: Text | None = None
    reviewed_on: date | None = None

    @model_validator(mode="after")
    def review_and_uniqueness(self):
        _unique(self.from_requirement_ids, "mapping source")
        _unique(self.to_requirement_ids, "mapping destination")
        if self.basis == "evaluated" and (not self.reviewed_by or not self.reviewed_on):
            raise ValueError("evaluated mapping requires review attribution")
        return self


def _edition(value):
    if value.casefold() in {"latest", "current", "default", "any"}:
        raise ValueError(
            "framework edition/revision must be explicit, not a mutable selector"
        )


def _unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {label} identity")


class VersionedFrameworkPack(FrameworkContract):
    schema_version: Literal["declared-framework-pack/v1"] = "declared-framework-pack/v1"
    id: Identifier
    edition: Identifier
    revision: Identifier
    amendments: Annotated[list[Identifier], Field(max_length=32)] = Field(
        default_factory=list
    )
    title: Text
    source: PackSource
    reviewed_by: Text
    reviewed_on: date
    publication_status: Literal["published", "draft", "superseded"]
    published_on: date | None = None
    effective_from: date | None = None
    effective_until: date | None = None
    provenance: Text
    license: Text
    distribution: Literal["redistributable", "restricted", "prohibited"]
    requirements: Annotated[list[PackRequirement], Field(min_length=1, max_length=64)]
    mapping_targets: Annotated[list[MappingTarget], Field(max_length=64)] = Field(
        default_factory=list
    )
    mappings: Annotated[list[FrameworkMapping], Field(max_length=256)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_references(self):
        _edition(self.edition)
        _edition(self.revision)
        _unique(self.amendments, "amendment")
        ids = {row.id for row in self.requirements}
        _unique([row.id for row in self.requirements], "requirement")
        _unique([row.id for row in self.mapping_targets], "mapping target")
        _unique([row.id for row in self.mappings], "mapping")
        # A framework/edition pair has exactly one catalog. Different editions
        # need explicit targets; no lookup guesses an edition from a framework ID.
        _unique(
            [(row.framework_id, row.edition) for row in self.mapping_targets],
            "mapping edition",
        )
        targets = {row.id: row for row in self.mapping_targets}
        for mapping in self.mappings:
            if set(mapping.from_requirement_ids) - ids:
                raise ValueError("mapping names unknown source requirement")
            target = targets.get(mapping.target_id)
            if target is None or set(mapping.to_requirement_ids) - set(
                target.requirement_ids
            ):
                raise ValueError("mapping names unknown target or requirement")
        if self.publication_status == "published" and (
            not self.published_on or not self.effective_from
        ):
            raise ValueError("published pack requires publication and effective dates")
        if (
            self.effective_until
            and self.effective_from
            and self.effective_until <= self.effective_from
        ):
            raise ValueError("effective interval must be ordered")
        return self


def load_declared_pack(
    payload: bytes, *, pack_id: str, edition: str, revision: str
) -> VersionedFrameworkPack:
    if len(payload) > 2 * 1024 * 1024:
        raise ValueError("framework pack exceeds byte bound")

    # Reject duplicate JSON keys rather than silently admitting the last edition.
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate framework pack JSON key")
            result[key] = value
        return result

    pack = VersionedFrameworkPack.model_validate(
        json.loads(payload, object_pairs_hook=pairs)
    )
    if (pack.id, pack.edition, pack.revision) != (pack_id, edition, revision):
        raise ValueError(
            "framework pack selection does not match declared identity/edition/revision"
        )
    return pack


def _operative(
    pack: VersionedFrameworkPack, now: datetime, period: EvidencePeriod | None = None
):
    if pack.distribution == "prohibited":
        raise ValueError("framework pack distribution prohibited; assessment refused")
    if pack.publication_status != "published":
        raise ValueError(f"framework pack is {pack.publication_status}, not operative")
    today = now.date()
    if pack.published_on > today or pack.effective_from > today:
        raise ValueError("framework pack publication/effectivity is in the future")
    if pack.reviewed_on > today or pack.source.reviewed_on > today:
        raise ValueError("framework pack review is in the future")
    if pack.effective_until and today >= pack.effective_until:
        raise ValueError("framework pack is no longer effective")
    if period and (
        period.start < pack.effective_from
        or (pack.effective_until and period.end >= pack.effective_until)
    ):
        raise ValueError("assessment period falls outside pack effective interval")


def compile_framework_plan(
    pack: VersionedFrameworkPack,
    *,
    scope: AssessmentScope,
    period: EvidencePeriod,
    choices: dict,
    now: datetime,
) -> tuple[ControlAssessmentPlan, list[str]]:
    from moonmind.security.assessments import (
        AssessmentScope,
        ControlAssessmentPlan,
        ControlObjective,
        ControlRequirement,
    )

    _operative(pack, now, period)
    if set(choices) - {row.id for row in pack.requirements}:
        raise ValueError("choices name unknown framework requirement")
    restricted = pack.distribution == "restricted"
    diagnostics = (
        ["Restricted pack text omitted; retained identity and content digests only."]
        if restricted
        else []
    )
    rows = []
    for requirement in pack.requirements:
        choice = DeclaredRequirementChoice.model_validate(
            choices.get(requirement.id, {})
        )
        applicability = choice.applicability
        reason = choice.applicability_reason
        if choice.not_applicable_reason:
            if applicability == "applicable":
                raise ValueError("conflicting requirement applicability choices")
            applicability = "not_applicable"
            reason = choice.not_applicable_reason
        if applicability != "undetermined" and not reason:
            raise ValueError("explicit requirement applicability requires a reason")
        assets = scope.assets if choice.systems is None else choice.systems
        if (
            not assets
            or set(assets) - set(scope.assets)
            or set(assets) & set(scope.exclusions)
        ):
            raise ValueError("requirement systems outside admitted scope")
        selected_period = choice.period or period
        if not period.covers(selected_period):
            raise ValueError("requirement period outside admitted period")
        if choice.responsible_entity == "partner" and not choice.responsibility_reason:
            raise ValueError("partner responsibility requires a reason")
        methods_unfulfilled = bool(
            set(requirement.required_evidence_methods) - {"deterministic_check"}
        ) or bool(set(requirement.evidence_types) - {"deterministic_check"})
        if methods_unfulfilled:
            diagnostics.append(
                f"{requirement.id}: declared evidence methods/types require evidence beyond deterministic checks; completeness withheld."
            )
        rows.append(
            ControlRequirement(
                id=requirement.id,
                framework_id=pack.id,
                framework_edition=pack.edition,
                title=requirement.id if restricted else requirement.title,
                scope=AssessmentScope(
                    assets=assets,
                    exclusions=scope.exclusions,
                    repository_exceptions=scope.repository_exceptions,
                ),
                period=selected_period,
                applicability=applicability,
                applicability_reason=reason
                or "Requirement applicability has not been determined for the admitted scope.",
                objectives_complete=requirement.objectives_complete
                and not methods_unfulfilled,
                objective_basis=(
                    "Restricted pack objective basis omitted."
                    if restricted
                    else requirement.objective_basis
                ),
                objectives=[
                    ControlObjective(
                        **dict(
                            item.model_dump(),
                            description=item.id if restricted else item.description,
                        )
                    )
                    for item in requirement.objectives
                ],
                owner=choice.responsible_entity,
                responsibility_reason=choice.responsibility_reason,
            )
        )
    if pack.mappings:
        diagnostics.append(
            "Directional mappings are reference intelligence only; they provide no executed checks or requirement coverage."
        )
    return ControlAssessmentPlan(requirements=rows), diagnostics


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def retained_pack_metadata(
    pack: VersionedFrameworkPack, *, artifact_ref: str, sha256: str, now: datetime
) -> dict:
    _operative(pack, now)
    # Restricted source text stays in its authorized artifact. Digests preserve
    # comparison without copying that text into portable report metadata.
    data = pack.model_dump(mode="json", by_alias=True)
    result = {
        "schemaVersion": pack.schema_version,
        "id": pack.id,
        "edition": pack.edition,
        "revision": pack.revision,
        "amendments": pack.amendments,
        "artifactRef": artifact_ref,
        "sha256": sha256,
        "distribution": pack.distribution,
        "publicationStatus": pack.publication_status,
        "publishedOn": data["publishedOn"],
        "effectiveFrom": data["effectiveFrom"],
        "effectiveUntil": data["effectiveUntil"],
        "reviewedOn": data["reviewedOn"],
        "requirementsDigest": _digest(data["requirements"]),
        "mappingsDigest": _digest([data["mappingTargets"], data["mappings"]]),
        "provenanceDigest": _digest(
            [data["source"], data["provenance"], data["reviewedBy"], data["license"]]
        ),
        "textOmitted": pack.distribution == "restricted",
    }

    if pack.distribution == "redistributable":
        result.update(
            {
                key: data[key]
                for key in (
                    "title",
                    "source",
                    "reviewedBy",
                    "provenance",
                    "license",
                    "requirements",
                    "mappingTargets",
                    "mappings",
                )
            }
        )
    else:
        result["omissionReason"] = (
            "Licensed content omitted under restricted distribution; original artifact access remains required."
        )
        result["licenseDigest"] = _digest(data["license"])
    return result


def compare_pack_metadata(current: dict, previous: dict | None) -> list[str]:
    if previous is None:
        return []
    keys = (
        "id",
        "edition",
        "revision",
        "amendments",
        "requirementsDigest",
        "mappingsDigest",
        "provenanceDigest",
        "distribution",
        "publicationStatus",
        "publishedOn",
        "effectiveFrom",
        "effectiveUntil",
        "reviewedOn",
        "sha256",
    )
    return [
        f"Framework assessment intelligence changed: {key}; this is not remediation evidence."
        for key in keys
        if current.get(key) != previous.get(key)
    ]
