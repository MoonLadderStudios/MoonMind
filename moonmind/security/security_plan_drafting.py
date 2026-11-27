"""Draft a ``security-plan/v1`` from a subject, a saved environment and standards.

A planning run has three steps. This module owns the deterministic parts: the
planning context given to the planner and the check of what it drafted.

1. ``planning_context`` projects the saved environment's target candidates and
   every requirement of the selected standards, with the evidence methods each
   allows. Credential, connection and network references stay out.
2. The planner (the ``security-review`` Skill) reads the repository and drafts a
   plan, proposing only the subject boundary, applicability, evidence paths,
   jobs, open points and regulatory facts, each with its source.
3. ``plan_from_draft`` fills derived content with trusted code, adds the open
   questions the run cannot answer, and validates the draft against the run's
   admitted subject type, standards and saved environment.

Repository and document content are untrusted data: a draft whose subject type,
standards, pack identities or targets differ from what the run admitted is
rejected, never adopted. Nothing here holds an assessment scope, credential,
egress grant or job reservation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from pydantic import TypeAdapter, ValidationError

from moonmind.security.assessment_projects import (
    AssessmentFramework,
    AssessmentProjectSnapshot,
)
from moonmind.security.assessments import content_key
from moonmind.security.framework_packs import (
    VersionedFrameworkPack,
    validate_pack_effectivity,
)
from moonmind.security.frameworks import (
    MAX_FRAMEWORK_PACKS,
    EvidencePeriod,
    FrameworkPackIdentity,
)
from moonmind.security.security_plan import (
    JOB_KINDS,
    OpenPoint,
    PlanStandard,
    SecurityPlan,
    SecurityPlanError,
    derive_security_plan,
    parse_security_plan,
    requirement_evidence,
    selected_for_subject,
    validate_security_plan,
)

CONTEXT_SCHEMA = "security-plan-context/v1"
# Subjects an installed planning journey honours. Network joins when its
# assessment owner lands.
SUBJECT_TYPES = ("application", "endpoint")
DRAFT_PATH = "artifacts/security-plan/draft.json"
WEB_API_PACK_ID = "web-api-methodology"

_SELECTION = TypeAdapter(AssessmentFramework | FrameworkPackIdentity)
# Operator decisions a planner cannot make on the operator's behalf.
_OPERATOR_FIELDS = {
    "requirement_choices": "requirementChoices",
    "evidence_refs": "evidenceRefs",
    "audit_ref": "auditRef",
    "previous_assessment_ref": "previousAssessmentRef",
}


def parse_standards(values: Any) -> list[AssessmentFramework | FrameworkPackIdentity]:
    """The catalog selections a run admitted: installed by ID, or a registration."""
    if not isinstance(values, list) or not values:
        raise SecurityPlanError(["standards: select at least one standard"])
    if len(values) > MAX_FRAMEWORK_PACKS:
        raise SecurityPlanError(
            [f"standards: select at most {MAX_FRAMEWORK_PACKS} standards"]
        )
    selections, errors, seen = [], [], set()
    for index, value in enumerate(values):
        try:
            selection = _SELECTION.validate_python(value)
        except ValidationError:
            errors.append(f"standards[{index}]: choose a standard from the catalog")
            continue
        key = (selection_id(selection), selection.edition)
        if key in seen:
            errors.append(f"standards[{index}]: repeats a selected standard")
        seen.add(key)
        selections.append(selection)
    if errors:
        raise SecurityPlanError(errors)
    return selections


def selection_id(selection) -> str:
    return (
        selection.id
        if isinstance(selection, AssessmentFramework)
        else selection.pack_id
    )


def require_subject_type(value: Any) -> str:
    if value not in SUBJECT_TYPES:
        raise SecurityPlanError(
            [
                "subjectType: no installed journey plans this subject; choose "
                + " or ".join(SUBJECT_TYPES)
            ]
        )
    return value


def plan_period(snapshot: AssessmentProjectSnapshot, now: datetime) -> EvidencePeriod:
    """The saved environment's period, or the planning date until one is chosen."""
    if snapshot.configuration.period is not None:
        return snapshot.configuration.period
    return EvidencePeriod(start=now.date(), end=now.date())


def check_available(
    index: int, pack: VersionedFrameworkPack, now: datetime, period: EvidencePeriod
) -> None:
    try:
        validate_pack_effectivity(pack, now, period)
    except ValueError as exc:
        raise SecurityPlanError(
            [f"standards[{index}]: {pack.id} {pack.edition} is unavailable: {exc}"]
        ) from None


def plan_standard(
    selection,
    pack: VersionedFrameworkPack,
    *,
    sha256: str,
    period: EvidencePeriod,
    artifact_ref: str | None,
) -> PlanStandard:
    """The plan's standard identity: exact pack, digest and period, no choices."""
    if selection.edition != pack.edition:
        raise SecurityPlanError(
            [
                f"standards: {pack.id} edition {selection.edition} is not the "
                f"available edition {pack.edition}"
            ]
        )
    return PlanStandard.model_validate(
        {
            "packId": pack.id,
            "edition": pack.edition,
            "revision": pack.revision,
            "sha256": sha256,
            "period": period.model_dump(mode="json"),
            **({"artifactRef": artifact_ref} if artifact_ref else {}),
        }
    )


def _environment(snapshot: AssessmentProjectSnapshot) -> dict[str, Any]:
    """Target candidates only: no credential, connection or network references."""
    config = snapshot.configuration
    return {
        "assets": list(config.assets),
        "exclusions": list(config.exclusions),
        "originCandidates": list(config.origin_candidates),
        "repositories": [item.repository for item in config.repositories],
        "artifactRefs": list(config.artifact_refs),
        "cloudAccountCandidates": [
            {
                "provider": item.provider,
                "account": item.account,
                "regions": list(item.regions),
            }
            for item in config.cloud_account_candidates
        ],
        "jurisdictions": list(config.jurisdictions),
        "dataClassification": config.data_classification,
    }


def planning_context(
    snapshot: AssessmentProjectSnapshot,
    subject_type: str,
    standards: Sequence[tuple[PlanStandard, VersionedFrameworkPack]],
) -> dict[str, Any]:
    """What the planner needs and nothing more; restricted packs omit titles."""
    rows = []
    for standard, pack in standards:
        restricted = pack.distribution == "restricted"
        requirements = []
        for requirement in pack.requirements:
            allowed, party, _ = requirement_evidence(pack, requirement, _no_choice())
            requirements.append(
                {
                    "id": requirement.id,
                    **({} if restricted else {"title": requirement.title}),
                    "allowedMethods": allowed,
                    **({"externalParty": party} if party else {}),
                    **(
                        {"notAssessedReason": requirement.not_assessed_reason}
                        if requirement.not_assessed_reason
                        else {}
                    ),
                    **(
                        {"subjectTypes": list(requirement.subject_types)}
                        if requirement.subject_types
                        else {}
                    ),
                    **(
                        {
                            "selectedForSubject": selected_for_subject(
                                requirement, subject_type
                            )
                        }
                        if subject_type != "application"
                        else {}
                    ),
                }
            )
        identity = standard.model_dump(
            mode="json",
            by_alias=True,
            include={
                "pack_id",
                "edition",
                "revision",
                "artifact_ref",
                "sha256",
                "period",
            },
        )
        rows.append(
            {
                **identity,
                **({} if restricted else {"title": pack.title}),
                "distribution": pack.distribution,
                "requirements": requirements,
            }
        )
    document = {
        "schemaVersion": CONTEXT_SCHEMA,
        "subjectType": subject_type,
        "project": {
            "projectId": snapshot.project_id,
            "environmentId": snapshot.environment_id,
            "sourceVersion": snapshot.source_version,
            "digest": snapshot.digest,
            "projectName": snapshot.project_name,
            "environmentName": snapshot.environment_name,
            "purpose": snapshot.purpose,
            "boundaries": snapshot.boundaries,
        },
        "environment": _environment(snapshot),
        "standards": rows,
        "jobKinds": list(JOB_KINDS),
        "draftPath": DRAFT_PATH,
    }
    if subject_type == "endpoint":
        from moonmind.security.endpoint_assessments import (
            GENERAL_PROFILE,
            PLAN_REQUIRED_INPUTS,
            SHIPPED_DATASTREAMS,
            UNSUPPORTED_PLATFORMS,
        )

        document["endpoint"] = {
            "jobKind": "endpoint-assessment",
            "targets": ["rootfs", "image", "results"],
            "datastreams": list(SHIPPED_DATASTREAMS),
            "generalProfile": GENERAL_PROFILE,
            "unsupportedPlatforms": list(UNSUPPORTED_PLATFORMS),
            "requiredInputs": list(PLAN_REQUIRED_INPUTS),
        }
    return document


# Inline context rides workflow history and the planner's instructions; the
# retained artifact keeps every field.
MAX_INLINE_CONTEXT_BYTES = 48 * 1024


def inline_context(document: Mapping[str, Any]) -> dict[str, Any]:
    """The context the planner reads inline, without titles when it is large."""
    if len(json.dumps(document, separators=(",", ":"))) <= MAX_INLINE_CONTEXT_BYTES:
        return dict(document)
    compact = json.loads(json.dumps(document))
    for standard in compact["standards"]:
        for requirement in standard["requirements"]:
            requirement.pop("title", None)
    compact["titlesOmitted"] = True
    return compact


def context_key(
    snapshot: AssessmentProjectSnapshot, subject_type: str, selections: Sequence
) -> str:
    """One planning context per environment snapshot, subject and selection."""
    return content_key(
        {
            "snapshot": snapshot.digest,
            "subjectType": subject_type,
            "standards": [
                _SELECTION.dump_python(s, mode="json", by_alias=True)
                for s in selections
            ],
        }
    )


def _no_choice():
    from moonmind.security.frameworks import DeclaredRequirementChoice

    return DeclaredRequirementChoice.model_validate({})


def _admission_errors(
    plan: SecurityPlan, subject_type: str, admitted: Sequence[PlanStandard]
) -> list[str]:
    errors = []
    if plan.subject.type != subject_type:
        errors.append(
            "subject.type: differs from the run's admitted subject type; "
            "repository content cannot change it"
        )
    identity = ("pack_id", "edition", "revision", "artifact_ref", "sha256", "period")
    expected = {
        (s.pack_id, s.edition): {f: getattr(s, f) for f in identity} for s in admitted
    }
    drafted = set()
    for index, standard in enumerate(plan.standards):
        key = (standard.pack_id, standard.edition)
        drafted.add(key)
        if key not in expected:
            errors.append(
                f"standards[{index}]: not a standard this run admitted; "
                "repository content cannot add standards"
            )
            continue
        if {f: getattr(standard, f) for f in identity} != expected[key]:
            errors.append(
                f"standards[{index}]: differs from the admitted pack identity or period"
            )
        for field, alias in _OPERATOR_FIELDS.items():
            if getattr(standard, field):
                errors.append(
                    f"standards[{index}].{alias}: an operator decision, not a "
                    "planner proposal"
                )
    for pack_id, edition in expected.keys() - drafted:
        errors.append(
            f"standards: omits the admitted standard {pack_id} {edition}; "
            "repository content cannot remove standards"
        )
    return errors


def _profile_errors(plan: SecurityPlan, packs) -> list[str]:
    """Proposed typed inputs must use the pack's own input fields."""
    from moonmind.security.pack_readiness import profile_schema

    errors = []
    for index, standard in enumerate(plan.standards):
        if standard.profile is None:
            continue
        pack = packs.get((standard.pack_id, standard.edition))
        schema = profile_schema(pack) if pack is not None and pack.profile else None
        if schema is None:
            errors.append(f"standards[{index}].profile: this standard takes no inputs")
            continue
        known = set((schema.get("properties") or {}).keys())
        if set(standard.profile) - known:
            errors.append(
                f"standards[{index}].profile: names a field outside the standard's inputs"
            )
    return errors


def operator_points(
    plan: SecurityPlan, snapshot: AssessmentProjectSnapshot
) -> list[OpenPoint]:
    """Questions only the operator can answer, raised instead of waiting."""
    points = []
    if plan.subject.type == "endpoint" and not any(
        job.kind == "endpoint-assessment" for job in plan.jobs
    ):
        points.append(
            OpenPoint(
                kind="operator_question",
                text=(
                    "Which root filesystem, image archive or OpenSCAP results does "
                    "this endpoint plan assess? Upload it as an artifact of the saved "
                    "environment; MoonMind does not reach live hosts."
                ),
            )
        )
    if snapshot.configuration.period is None:
        points.append(
            OpenPoint(
                kind="operator_question",
                text=(
                    "Which evidence period will the assessment cover? The plan uses "
                    "the planning date until the saved environment records a period."
                ),
            )
        )
    for standard in plan.standards:
        if standard.profile is not None:
            points.append(
                OpenPoint(
                    kind="operator_question",
                    text=(
                        f"Review the proposed {standard.pack_id} inputs; they are drafts "
                        "from repository evidence, not confirmed facts."
                    ),
                )
            )
    present = {(p.kind, p.text) for p in plan.open_points}
    return [p for p in points if (p.kind, p.text) not in present]


def plan_from_draft(
    raw: bytes | str | Mapping,
    *,
    subject_type: str,
    standards: Sequence[PlanStandard],
    packs: Mapping[tuple[str, str], VersionedFrameworkPack],
    snapshot: AssessmentProjectSnapshot,
    now: datetime,
) -> SecurityPlan:
    """Derive and validate a drafted plan, or raise field-addressed diagnostics."""
    draft = parse_security_plan(raw)
    errors = _admission_errors(draft, subject_type, standards)
    errors += _profile_errors(draft, packs)
    plan = derive_security_plan(draft, packs=packs)
    plan = plan.model_copy(
        update={"open_points": [*plan.open_points, *operator_points(plan, snapshot)]}
    )
    try:
        plan = validate_security_plan(plan, packs=packs, snapshot=snapshot, now=now)
    except SecurityPlanError as exc:
        errors += exc.diagnostics
    if errors:
        raise SecurityPlanError(errors)
    return plan


def draft_digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def diagnostics_document(
    diagnostics: Sequence[str], *, draft_ref: str, draft_sha256: str
) -> dict[str, Any]:
    return {
        "schemaVersion": "security-plan-diagnostics/v1",
        "outcome": "rejected",
        "draftRef": draft_ref,
        "draftSha256": draft_sha256,
        "diagnostics": list(diagnostics),
    }


def render_diagnostics(diagnostics: Sequence[str], *, draft_ref: str) -> str:
    lines = [
        "# Security plan rejected",
        "",
        f"The planner's draft ({draft_ref}) failed deterministic validation and was "
        "not retained as a plan. The draft is kept for diagnosis. Correct the "
        "environment or standards and rerun, or rerun planning.",
        "",
        *[f"- {json.dumps(item)[1:-1]}" for item in diagnostics],
    ]
    return "\n".join(lines) + "\n"
