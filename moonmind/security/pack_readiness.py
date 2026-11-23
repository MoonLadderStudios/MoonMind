"""Readiness evidence for packs that declare profile inputs.

One evaluator serves every pack with a profile. Requirement content, profile
fields, modules, applicability rules and external claims are pack data. A
judgement that cannot be data is a small registered behavior (see
``framework_registry.PACK_BEHAVIORS``). Recorded evidence is readiness context:
it never certifies a product or approves it for a jurisdiction.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, Field, create_model
from pydantic.alias_generators import to_snake

from moonmind.security.frameworks import (
    DeclaredRequirementChoice,
    EvidencePeriod,
    FrameworkContract,
    FrameworkEvidence,
    Strings,
    Text,
)

READINESS_STATUSES = (
    "evidence_recorded",
    "incomplete",
    "partner_owned",
    "exclusion_for_review",
    "excluded",
    "not_applicable",
    "not_selected",
    "applicability_missing",
    "Unverified",
)
APPLICABLE = {
    "evidence_recorded",
    "incomplete",
    "partner_owned",
    "exclusion_for_review",
}
UNDETERMINED = {"applicability_missing", "Unverified"}
UNRESOLVED = {"incomplete", "partner_owned", "applicability_missing"}
# Evidence kinds that may carry each declared evidence type.
EVIDENCE_KINDS = {
    "technical": {"technical_assessment", "control_evidence", "external_audit"},
    "organizational": {"control_evidence", "external_audit"},
    "organisational": {"control_evidence", "external_audit"},
    "game_math": {"technical_assessment", "control_evidence"},
    "rng": {"technical_assessment", "control_evidence"},
    "laboratory": {"external_audit"},
    "regulator": {"external_audit"},
    "external_audit": {"external_audit"},
}
INVALID_EVIDENCE_PROMPT = "Supply a scoped evidence record with provenance, period, systems, control IDs and enquiry/evidence/observation method; an uploaded document alone cannot establish control coverage."
PARTNER_PROMPT = "Identify the partner and agreed responsibility; partner ownership does not remove the evidence obligation."
EXCLUSION_REVIEW_PROMPT = "Review the recorded exclusion against the selected rule; partner ownership does not establish evidence coverage."
SCOPED_REASON = "Scoped readiness baseline; adoption is distinct from product approval."


class JurisdictionBasis(FrameworkContract):
    """Recorded adoption. Publication of an edition is never adoption."""

    jurisdiction: Text | None = None
    status: Literal["unknown", "adopted", "not_adopted"] = "unknown"
    edition: Text | None = None
    adopted_on: date | None = None
    effective_on: date | None = None
    record_version: Text | None = None
    source_ref: Text | None = None
    reason: Text | None = None

    def adopted(self, edition: str, start: date) -> bool:
        return bool(
            self.status == "adopted"
            and self.jurisdiction
            and self.source_ref
            and self.record_version
            and self.edition == edition
            and self.adopted_on
            and self.effective_on
            and self.adopted_on <= self.effective_on <= start
        )


class PackEvidence(FrameworkEvidence):
    """One scoped evidence record that every selected pack judges for itself.

    Typed fields serve packs whose requirements declare evidence types; the
    qualifiers serve a registered behavior. Requirement IDs, not the record,
    decide which pack a record can support, so no conclusion transfers.
    """

    evidence_type: (
        Literal[
            "technical",
            "organizational",
            "organisational",
            "game_math",
            "rng",
            "laboratory",
            "regulator",
            "external_audit",
        ]
        | None
    ) = None
    outcome: Literal["passed", "failed", "incomplete"] | None = None
    edition: Text | None = None
    jurisdiction: Text | None = None
    division_approval_ref: Text | None = None
    operator_evidence_ref: Text | None = None
    incident_disposition: (
        Literal["events_reported", "no_reportable_incidents"] | None
    ) = None
    notification_ref: Text | None = None


@dataclass(frozen=True)
class PackBehavior:
    """Registered judgement a pack cannot express as data. Every hook is optional."""

    module_applicability: Callable | None = None
    # Record fields this pack's evidence must carry to be a valid record.
    required_evidence_fields: tuple[str, ...] = ("evidence_type", "outcome", "edition")
    records_model: type[BaseModel] | None = None
    validate_records: Callable | None = None
    evaluate_evidence: Callable | None = None
    evidence_refs: Callable = lambda record: ()
    record_refs: Callable = lambda records: ()
    context: Callable | None = None


DEFAULT_BEHAVIOR = PackBehavior()


def parse_evidence(behavior: PackBehavior, payload, artifact_ref: str) -> PackEvidence:
    """A pack's view of one record; raises ValueError when it is invalid for it."""
    if not isinstance(payload, dict):
        raise ValueError("evidence record must be a JSON object")
    # The artifact owner supplies the identity, not authored JSON.
    record = PackEvidence.model_validate({**payload, "artifactRef": artifact_ref})
    if any(getattr(record, name) is None for name in behavior.required_evidence_fields):
        raise ValueError("evidence record lacks this pack's required fields")
    return record


def _choices_validator(choices):
    def check(values):
        if values is not None and set(values) - set(choices):
            raise ValueError("profile selection names an unknown choice")
        return values

    return AfterValidator(check)


def _enum(choices):
    def extra(schema):
        schema["items"]["enum"] = list(choices)

    return extra


@lru_cache(maxsize=32)
def _profile_model(profile_json: str) -> type[FrameworkContract]:
    fields: dict[str, Any] = {}
    for field in json.loads(profile_json)["fields"]:
        name = to_snake(field["name"])
        kind = field["type"]
        options = {"title": field["title"]} if field.get("title") else {}
        if kind == "text":
            fields[name] = (Text | None, Field(default=None, **options))
        elif kind == "boolean":
            fields[name] = (bool | None, Field(default=None, **options))
        elif kind == "choice":
            fields[name] = (
                Literal[tuple(field["choices"])] | None,
                Field(default=None, **options),
            )
        elif kind == "choices":
            fields[name] = (
                Annotated[
                    Strings,
                    Field(json_schema_extra=_enum(field["choices"])),
                    _choices_validator(field["choices"]),
                ]
                | None,
                Field(default=None, **options),
            )
        elif kind == "text_list":
            fields[name] = (Strings, Field(default_factory=list, **options))
        else:
            fields[name] = (
                JurisdictionBasis,
                Field(default_factory=JurisdictionBasis, **options),
            )
    return create_model("ProfileInputs", __base__=FrameworkContract, **fields)


def profile_model(pack) -> type[FrameworkContract]:
    """The typed profile a pack declares, as one validating model."""
    if pack.profile is None:
        raise ValueError("framework pack declares no profile inputs")
    return _profile_model(pack.profile.model_dump_json(by_alias=True))


def profile_schema(pack) -> dict:
    from moonmind.schemas.form_fields import form_fields_schema

    return form_fields_schema(
        profile_model(pack).model_json_schema(by_alias=True), include_defaults=False
    )


def _three_valued(conditions, profile):
    """Kleene AND over one condition set: False wins, then unknown, then True.

    A multi-choice input meets a condition when any recorded choice is expected.
    """
    unknown = []
    for name, expected in conditions.items():
        value = profile.get(name)
        allowed = expected if isinstance(expected, list) else [expected]
        if value is None:
            unknown.append(name)
        elif not (
            set(value) & set(allowed) if isinstance(value, list) else value in allowed
        ):
            return False, []
    return (None, unknown) if unknown else (True, [])


def _rule_applicability(pack, requirement, profile):
    text = pack.applicability_text
    selection = pack.profile.field("selection")
    if selection and requirement.id in selection.choices:
        chosen = profile.get(selection.name)
        if chosen is not None and requirement.id not in chosen:
            return "not_selected", text.not_selected
    if not requirement.applicable_when:
        return "applicable", requirement.applicability
    unknown, applies = set(), False
    for conditions in requirement.applicable_when:
        value, missing = _three_valued(conditions, profile)
        if value:
            applies = True
            break
        if value is None:
            unknown.update(missing)
    if not applies and unknown:
        field = next(f for f in pack.profile.fields if f.name in unknown)
        return "Unverified", field.unknown_reason or f"Record {field.name}."
    if not applies:
        return "not_applicable", (
            f"{text.not_applicable} {requirement.applicability}"
            if text.cite_rule
            else text.not_applicable
        )
    basis = pack.profile.field("applicability_basis")
    return "applicable", " ".join(
        (text.applicable, (basis and profile.get(basis.name)) or text.basis_missing)
    )


def accepts_typed(record, requirement, systems, period, pack, jurisdiction) -> bool:
    """Matching scoped evidence for one requirement; no record covers another's."""
    if record.evidence_type is None or record.outcome is None:
        return False
    module = next((m for m in pack.modules if m.id == requirement.module), None)
    kind = record.evidence_type
    return (
        record.outcome == "passed"
        and record.edition == pack.edition
        and (
            module is None
            or record.provenance.standard == f"{module.id} v{module.edition}"
        )
        and record.kind in EVIDENCE_KINDS[kind]
        and kind in requirement.evidence_types
        and (kind in {"organizational", "organisational"} or record.method != "enquiry")
        and requirement.id in record.requirement_ids
        and bool(systems)
        and set(systems) <= set(record.systems)
        and record.period.covers(period)
        and (kind != "regulator" or record.jurisdiction == jurisdiction)
        and (
            kind != "external_audit"
            or (
                record.reliance is not None and record.reliance.valid_for.covers(period)
            )
        )
    )


@dataclass
class Readiness:
    rows: dict[str, dict]
    context: dict


def evaluate_readiness(
    pack,
    behavior: PackBehavior,
    *,
    profile: dict | None,
    period: EvidencePeriod,
    choices: dict,
    records: list,
    audit: BaseModel | None,
    invalid_refs: list[str] = (),
    admitted_assets: list[str],
    shared_requirement_ids: frozenset[str] = frozenset(),
) -> Readiness:
    """Requirement readiness from profile inputs and evidence collected once.

    ``shared_requirement_ids`` are the other selected packs' requirements: a
    shared record may name them, and this pack ignores them.
    """
    model = profile_model(pack)
    inputs = model.model_validate(profile or {})
    values = inputs.model_dump(by_alias=True)
    ids = {row.id for row in pack.requirements}
    unknown = set(choices) - ids
    unknown |= (
        {key for row in records for key in row.requirement_ids}
        - ids
        - set(shared_requirement_ids)
    )
    if unknown:
        raise ValueError("Unknown framework requirement: " + ", ".join(sorted(unknown)))
    systems_field = pack.profile.field("systems")
    systems = values[systems_field.name] if systems_field else list(admitted_assets)
    if not set(systems) <= set(admitted_assets):
        raise ValueError("Framework systems exceed admitted assessment scope")
    if audit is not None and behavior.validate_records:
        behavior.validate_records(pack, audit, values)
    source = {"source": pack.profile.request_source}
    if pack.profile.request_section:
        source["section"] = pack.profile.request_section
    requests: list[dict] = []
    missing_profile = False
    for field in pack.profile.fields:
        if field.required and values.get(field.name) in (None, "", []):
            missing_profile = True
            requests.append({"id": field.request_id, "prompt": field.prompt, **source})

    jurisdiction_field = pack.profile.field("jurisdiction_basis")
    basis = (
        getattr(inputs, to_snake(jurisdiction_field.name))
        if jurisdiction_field
        else None
    )
    adoption_known = basis is None or basis.adopted(pack.edition, period.start)
    if not adoption_known:
        requests.append(
            {
                "id": "jurisdiction.adoption",
                "prompt": jurisdiction_field.prompt,
                **source,
            }
        )

    modules = []
    for module in pack.modules:
        row = {
            "module": module.id,
            "edition": module.edition,
            "sourceStatus": module.source_status,
        }
        if module.status_reason:
            row["statusReason"] = module.status_reason
        if behavior.module_applicability:
            status, reason = behavior.module_applicability(module, values)
            row.update(status=status, reason=reason)
            if status == "incomplete":
                requests.append(
                    {"id": "module." + module.id, "prompt": reason, **source}
                )
        modules.append(row)
    by_module = {row["module"]: row for row in modules}

    rows = {}
    for requirement in pack.requirements:
        choice = DeclaredRequirementChoice.model_validate(
            choices.get(requirement.id, {})
        )
        if choice.applicability != "undetermined" or choice.applicability_reason:
            raise ValueError(
                "This pack derives applicability from profile inputs; record exclusions with notApplicableReason"
            )
        selected = systems if choice.systems is None else choice.systems
        if not set(selected) <= set(systems):
            raise ValueError("Requirement systems exceed assessed scope")
        selected_period = choice.period or period
        cited = requirement.sources[0] if requirement.sources else None
        scoped = {
            "requirementId": requirement.id,
            "systems": selected,
            "period": selected_period.model_dump(mode="json"),
        }
        cite = (
            {"source": cited.url or cited.artifact_ref, "section": cited.section}
            if cited
            else source
        )
        module = by_module.get(requirement.module)
        if module and "status" in module:
            applicability, reason = module["status"], module["reason"]
            if applicability == "incomplete":
                applicability = "applicability_missing"
        else:
            applicability, reason = _rule_applicability(pack, requirement, values)
        typed = behavior.evaluate_evidence is None
        jurisdiction = basis.jurisdiction if basis else None
        # Typed evidence references stay visible on every row, applicable or not.
        matched: list[str] = (
            [
                r.artifact_ref
                for r in records
                if accepts_typed(
                    r, requirement, selected, selected_period, pack, jurisdiction
                )
            ]
            if typed
            else []
        )
        status = applicability
        if applicability == "Unverified":
            requests.append(
                {
                    "id": "applicability." + requirement.id,
                    "prompt": reason,
                    **cite,
                    **scoped,
                }
            )
        elif applicability == "applicable" and choice.not_applicable_reason:
            if pack.exclusion_review:
                status = "exclusion_for_review"
                requests.append(
                    {
                        "id": "requirement." + requirement.id,
                        "prompt": EXCLUSION_REVIEW_PROMPT,
                        **cite,
                        **scoped,
                    }
                )
            else:
                status, reason = "excluded", choice.not_applicable_reason
        elif applicability == "applicable":
            if typed:
                gap = not matched
            else:
                matched, gaps = behavior.evaluate_evidence(
                    pack, requirement, selected, selected_period, records, audit
                )
                requests.extend(
                    {"id": key, "prompt": prompt, **cite, **scoped}
                    for key, prompt in gaps
                )
                gap = bool(gaps)
            status = (
                (
                    "partner_owned"
                    if choice.responsible_entity == "partner"
                    else "incomplete"
                )
                if gap
                else "evidence_recorded"
            )
        if status in UNRESOLVED and (typed or status == "applicability_missing"):
            requests.append(
                {
                    "id": requirement.id,
                    "prompt": requirement.evidence_prompt,
                    **source,
                    **scoped,
                    "evidenceTypes": requirement.evidence_types,
                    "responsibleEntity": choice.responsible_entity,
                }
            )
        if choice.responsible_entity == "partner" and not choice.responsibility_reason:
            requests.append(
                {
                    "id": "responsibility." + requirement.id,
                    "prompt": PARTNER_PROMPT,
                    "requirementId": requirement.id,
                }
            )
        rows[requirement.id] = {
            "status": status,
            "applicabilityStatus": applicability,
            "reason": reason,
            "systems": selected,
            "period": selected_period,
            "evidenceRefs": matched,
            "responsibleEntity": choice.responsible_entity,
            "responsibilityReason": choice.responsibility_reason,
            "exclusionReason": choice.not_applicable_reason,
        }
    for ref in invalid_refs:
        requests.append(
            {
                "id": "evidence." + ref,
                "artifactRef": ref,
                "prompt": INVALID_EVIDENCE_PROMPT + (pack.evidence_guidance or ""),
            }
        )
    incomplete_modules = [
        m["reason"] for m in modules if m.get("status") == "incomplete"
    ]
    undetermined = any(row["status"] in UNDETERMINED for row in rows.values())
    context = {
        "readiness": "incomplete" if requests else "prepared",
        "profile": inputs.model_dump(mode="json", by_alias=True),
        "period": period.model_dump(mode="json"),
        "applicability": {
            "status": (
                "incomplete"
                if missing_profile
                or not adoption_known
                or incomplete_modules
                or undetermined
                else "scoped"
            ),
            "reason": "; ".join(dict.fromkeys(incomplete_modules)) or SCOPED_REASON,
        },
        "modules": modules,
        "evidence": [r.model_dump(mode="json", by_alias=True) for r in records],
        "evidenceRequests": requests,
        "unresolvedRequirements": [
            key
            for key, row in rows.items()
            if row["status"] in UNRESOLVED or row["status"] == "Unverified"
        ],
        "externalClaims": {
            claim.id: {
                "status": "Unverified",
                "gap": claim.gap,
                "verifiableBy": claim.verifiable_by,
                "nextStep": claim.next_step,
                "evidenceRefs": [
                    r.artifact_ref
                    for r in records
                    if claim.evidence_type
                    and getattr(r, "evidence_type", None) == claim.evidence_type
                ],
            }
            for claim in pack.external_claims
        },
        "certification": "not_performed",
        "jurisdictionApproval": "not_performed",
        "limitations": list(pack.limitations),
    }
    if basis is not None:
        context["jurisdictionBasis"] = basis.model_dump(mode="json", by_alias=True)
    if behavior.context:
        context.update(behavior.context(pack, rows, audit))
    return Readiness(rows=rows, context=context)


def input_refs(pack, behavior: PackBehavior, *, profile, records, audit) -> list[str]:
    """Artifacts a readiness conclusion cites, for access checks and sealing.

    A source citation may name something other than an artifact, so only its
    artifact IDs are read. Reliance reviews, approvals and assessment records are
    mandatory artifacts: a malformed one must fail the read, not be dropped.
    """
    from moonmind.workflows.temporal.artifacts import ARTIFACT_ID_PATTERN

    citations = [record.provenance.source_ref for record in records]
    field = pack.profile.field("jurisdiction_basis") if pack.profile else None
    if field:
        basis = JurisdictionBasis.model_validate((profile or {}).get(field.name) or {})
        citations.insert(0, basis.source_ref)
    refs = [ref for ref in citations if ref and ARTIFACT_ID_PATTERN.fullmatch(ref)]
    for record in records:
        if record.reliance:
            refs.append(record.reliance.review_ref)
        refs.extend(behavior.evidence_refs(record))
    if audit is not None:
        refs.extend(behavior.record_refs(audit))
    return list(dict.fromkeys(ref for ref in refs if ref))
