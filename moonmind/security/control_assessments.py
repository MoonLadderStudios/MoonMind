"""Scoped requirement conclusions over retained native observations.

Only an explicit, complete objective checklist can establish satisfaction.
Context claims and mappings never substitute for executed checks or widen scope.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import cache, partial

from moonmind.security.assessments import (
    Comparison,
    ControlAssessment,
    ControlAssessmentPlan,
    ControlObjectiveResult,
    ControlRequirementResult,
)


def _window(window, now):
    """A scan is a point observation, not proof of continuous operation."""
    try:
        observed = datetime.fromisoformat(window["observedAt"])
        expires = datetime.fromisoformat(window["expiresAt"])
        valid_from = (
            datetime.fromisoformat(window["validFrom"])
            if window.get("validFrom")
            else observed
        )
        fresh = observed <= now < expires and valid_from <= now
    except (KeyError, TypeError, ValueError):
        return False, None
    return fresh, observed.date()


@dataclass
class CellObservation:
    """Native facts shared across requirements. Period and applicability stay local."""

    methods: set[str] = field(default_factory=set)
    configs: set[str] = field(default_factory=set)
    finding_ids: set[str] = field(default_factory=set)
    limitations: set[str] = field(default_factory=set)
    dates: set[date | None] = field(default_factory=set)
    deterministic_dates: set[date] = field(default_factory=set)
    stale: bool = False
    incomplete: bool = False
    blocked: bool = False


def _observe_cell(observation, windows, now, source, tool, asset, rule):
    cell = CellObservation()
    for index, row in enumerate(observation.coverage):
        if (
            (source, tool) != (row.source, row.tool)
            or asset not in row.assets
            or rule not in row.rule_ids
        ):
            continue
        cell.configs.add(row.config_ref)
        if row.evidence_method != "unclassified":
            cell.methods.add(row.evidence_method)
        fresh, observed_date = _window(
            windows[index] if index < len(windows) else None, now
        )
        cell.dates.add(observed_date)
        if not fresh:
            cell.stale = True
            cell.limitations.add(
                "Evidence is stale, future-dated, or lacks its observation window."
            )
        if row.evidence_method != "deterministic_check":
            cell.incomplete = True
            cell.limitations.add(
                "The source does not identify this evidence as an executed deterministic check."
            )
        elif fresh:
            cell.deterministic_dates.add(observed_date)
        if row.status != "complete":
            cell.incomplete = True
            cell.limitations.add(
                "Matching source evidence includes incomplete or blocked execution."
            )
        cell.blocked |= row.status in {"authentication_failed", "failed", "unsupported"}
    cell.finding_ids = {
        row.finding_id
        for row in observation.findings
        if (row.source, row.tool, row.asset, row.rule_id) == (source, tool, asset, rule)
    }
    return cell


def review_organisational_evidence(request, requirement, availability, now):
    """Review the current immutable revision for this requirement, never globally."""
    from moonmind.security.frameworks import OrganisationalEvidenceResult

    refs = [item.artifact_ref for item in request.revisions]
    result = OrganisationalEvidenceResult(
        request_id=request.id,
        state="requested",
        current_ref=refs[-1] if refs else None,
        superseded_refs=refs[:-1],
    )
    limits = result.limitations
    if not refs:
        limits.append("Requested evidence has not been supplied.")
        return result
    current = request.revisions[-1]
    review = current.review
    if availability.get(current.artifact_ref, "unavailable"):
        limits.append(
            "Current evidence is missing, denied, or unreadable; restore authorized access and retry."
        )
    if not current.collected_at <= now < current.expires_at:
        limits.append(
            "Evidence is expired, future-dated, or has conflicting freshness dates."
        )
    if (
        current.kind == "operational_record"
        and current.period.end > current.collected_at.date()
    ):
        limits.append("Operational records cannot establish events after collection.")
    if not current.period.covers(requirement.period):
        limits.append("Evidence does not cover the requirement period.")
    if not set(requirement.scope.assets) <= set(current.population):
        limits.append("Evidence does not cover the required population.")
    if current.kind in {"management_assertion", "generated_draft"}:
        limits.append(
            "Assertions and generated drafts cannot establish adoption or real occurrence, or replace documentary evidence."
        )
    elif request.purpose != "design" and current.kind != "operational_record":
        limits.append(
            "A design document does not establish adoption or real occurrence; supply operational records."
        )
    if review:
        if review.outcome != "accepted":
            limits.append("The attributed review found this evidence insufficient.")
        if review.purpose != request.purpose:
            limits.append("Review acceptance is for a different purpose.")
        if not current.collected_at <= review.reviewed_at <= now:
            limits.append(
                "Review dates conflict with collection or the assessment time."
            )
        if not review.period.covers(requirement.period) or not current.period.covers(
            review.period
        ):
            limits.append(
                "The review period is incomplete or exceeds the supplied evidence period."
            )
        if (
            not set(requirement.scope.assets)
            <= set(review.population)
            <= set(current.population)
        ):
            limits.append(
                "The review population is incomplete or exceeds the supplied evidence population."
            )
    else:
        limits.append(
            "Supplied evidence awaits a purpose-specific, evidence-citing review."
        )
    result.state = (
        "accepted"
        if not limits
        else "supplied"
        if not review and len(limits) == 1
        else "insufficient"
    )
    return result


READINESS_LIMITATION = "Recorded readiness evidence is not independently verified; laboratory, regulator and customer qualification remain Unverified."


def assess_controls(
    plan: ControlAssessmentPlan,
    comparison: Comparison,
    *,
    scan_ref: str,
    comparison_ref: str,
    coverage_windows: list[dict],
    now: datetime,
    evidence_availability: dict[str, str | None],
    review_ref: str | None = None,
    review_status: str = "not_requested",
) -> ControlAssessment:
    observe_cell = cache(
        partial(_observe_cell, comparison.observation, coverage_windows, now)
    )
    results = []
    for requirement in plan.requirements:
        applicable = requirement.applicability == "applicable"
        limits, actions, objectives = [], [], []
        stale = False
        methods = set()
        evidence_refs = [scan_ref, comparison_ref]
        unavailable = []
        contradiction = False
        supporting_incomplete = False
        if requirement.applicability == "undetermined":
            limits.append("Applicability has not been established.")
            actions.append("Determine applicability and record its rationale.")
        readiness = requirement.readiness
        if applicable and readiness is not None:
            # Readiness evidence is recorded context, not an executed check.
            limits.append(READINESS_LIMITATION)
            actions.append(
                "Obtain independent verification of the recorded evidence; recorded readiness is not a satisfied conclusion."
                if readiness.status == "evidence_recorded"
                else "Supply the readiness evidence requested for this requirement, then reassess."
            )
        if readiness is not None:
            if readiness.evidence_refs:
                methods.add("document_review")
            evidence_refs.extend(readiness.evidence_refs)
        if applicable and readiness is None and not requirement.objectives_complete:
            limits.append(
                "The declared objectives do not cover the complete requirement."
            )
            actions.append(
                "Identify the complete requirement objectives and their assessment basis."
            )
        if applicable and readiness is None and not requirement.objectives:
            limits.append("No executable objective checks were selected.")
            actions.append(
                "Select explicit checks and retain their scoped execution evidence."
            )
        for objective in requirement.objectives:
            cells = [
                (asset, rule)
                for asset in requirement.scope.assets
                for rule in objective.rule_ids
            ]
            checked, blocked, objective_limits = 0, False, []
            configs, matched_ids, failed = set(), set(), False
            if applicable:
                for asset, rule in cells:
                    cell = observe_cell(objective.source, objective.tool, asset, rule)
                    configs.update(cell.configs)
                    methods.update(cell.methods)
                    matched_ids.update(cell.finding_ids)
                    objective_limits.extend(cell.limitations)
                    stale |= cell.stale
                    blocked |= cell.blocked
                    point = (
                        requirement.period.start
                        if requirement.period.start == requirement.period.end
                        else None
                    )
                    if cell.dates and (point is None or cell.dates != {point}):
                        objective_limits.append(
                            "Point-in-time observations do not cover the selected assessment period."
                        )
                    valid = point in cell.deterministic_dates and not cell.stale
                    failed |= bool(cell.finding_ids) and valid
                    if valid and not cell.incomplete:
                        checked += 1
                    else:
                        objective_limits.append(
                            "The selected rule lacks complete current coverage for part of the required population."
                        )
            coverage = (
                "not_assessed"
                if not applicable
                else (
                    "complete"
                    if checked == len(cells)
                    else "blocked" if blocked and not checked else "partial"
                )
            )
            outcome = (
                "not_assessed"
                if not applicable
                else (
                    "failed"
                    if failed
                    else "passed" if coverage == "complete" else "inconclusive"
                )
            )
            objectives.append(
                ControlObjectiveResult(
                    id=objective.id,
                    outcome=outcome,
                    coverage=coverage,
                    checked_cells=checked,
                    required_cells=len(cells),
                    finding_ids=sorted(matched_ids),
                    configuration_refs=sorted(configs),
                    limitations=sorted(set(objective_limits)),
                )
            )
            limits.extend(objective_limits)
        for evidence in requirement.supporting_evidence:
            methods.add(evidence.method)
            error = evidence_availability.get(evidence.artifact_ref, "unavailable")
            if error:
                unavailable.append(evidence.artifact_ref)
                limits.append(
                    "Required supporting evidence is missing, denied, or invalid."
                )
                actions.append(
                    "Restore authorized access to the listed unavailable evidence, then reassess."
                )
                continue
            evidence_refs.append(evidence.artifact_ref)
            covers = (
                set(requirement.scope.assets) <= set(evidence.scope.assets)
                and set(evidence.scope.exclusions) <= set(requirement.scope.exclusions)
                and set(evidence.scope.repository_exceptions)
                <= set(requirement.scope.repository_exceptions)
                and evidence.period.covers(requirement.period)
            )
            fresh = evidence.expires_at is None or now < evidence.expires_at
            if not covers:
                supporting_incomplete = True
                limits.append(
                    "Supporting evidence does not cover the selected scope and period."
                )
            if not fresh:
                stale = True
                limits.append("Supporting evidence has expired.")
            if applicable and covers and fresh and evidence.position == "contradicts":
                contradiction = True
        organisation_results = []
        for request in requirement.organisational_evidence:
            reviewed = review_organisational_evidence(
                request, requirement, evidence_availability, now
            )
            organisation_results.append(reviewed)
            methods.add("document_review")
            for revision in request.revisions[-1:]:
                if evidence_availability.get(revision.artifact_ref, "unavailable"):
                    unavailable.append(revision.artifact_ref)
                else:
                    evidence_refs.append(revision.artifact_ref)
            if reviewed.superseded_refs:
                limits.append(
                    f"{request.id}: Superseded revisions are historical references, not current evidence; their availability is not evaluated."
                )
            if reviewed.state != "accepted":
                supporting_incomplete = True
                limits.extend(
                    f"{request.id}: {value}" for value in reviewed.limitations
                )
                actions.append(
                    f"Supply or review {request.id} for {request.purpose} within the selected scope and period, then reassess."
                )
        if review_ref:
            methods.add("agent_interpretation")
            if review_status == "completed":
                evidence_refs.append(review_ref)
            else:
                unavailable.append(review_ref)
                limits.append("The requested agent interpretation is unavailable.")
                actions.append(
                    "Restore the requested interpretation and its evidence, then reassess."
                )
        matched = {finding_id for row in objectives for finding_id in row.finding_ids}
        exceptions = [
            row for row in comparison.decisions if row.decision.finding_id in matched
        ]
        if exceptions:
            limits.append(
                "Risk acceptance and other triage decisions are recorded separately; they do not satisfy a requirement."
            )
        if any(row.status != "active" for row in exceptions):
            actions.append(
                "Review the expired, out-of-scope, or otherwise review-due triage decision."
            )
        if not applicable:
            conclusion, coverage, sufficiency = (
                "not_assessed",
                "not_assessed",
                "not_assessed",
            )
        else:
            coverage = (
                "blocked"
                if unavailable or any(row.coverage == "blocked" for row in objectives)
                else (
                    "complete"
                    if not supporting_incomplete
                    and requirement.objectives_complete
                    and objectives
                    and all(row.coverage == "complete" for row in objectives)
                    else "partial"
                )
            )
            sufficiency = (
                "contradictory"
                if contradiction
                else (
                    "stale"
                    if stale
                    else "sufficient" if coverage == "complete" else "insufficient"
                )
            )
            conclusion = (
                "inconclusive"
                if unavailable or stale or contradiction
                else (
                    "not_satisfied"
                    if any(row.outcome == "failed" for row in objectives)
                    else "satisfied" if coverage == "complete" else "inconclusive"
                )
            )
            if conclusion != "satisfied" and readiness is None:
                actions.append(
                    "Complete or repeat the listed objective checks within the admitted scope and assessment period."
                )
            if contradiction:
                limits.append(
                    "Attributed supporting evidence contradicts the objective conclusion; no satisfied conclusion is made."
                )
                actions.append(
                    "Resolve the contradictory evidence with an attributable review."
                )
        results.append(
            ControlRequirementResult(
                requirement=requirement,
                organisational_evidence=organisation_results,
                conclusion=conclusion,
                coverage=coverage,
                evidence_sufficiency=sufficiency,
                review_state=(
                    "not_assessed"
                    if not applicable
                    else "automated" if conclusion == "satisfied" else "review_required"
                ),
                methods=sorted(methods),
                objectives=objectives,
                exceptions=exceptions,
                evidence_refs=list(dict.fromkeys(evidence_refs)),
                unavailable_evidence_refs=list(dict.fromkeys(unavailable)),
                limitations=sorted(set(limits)),
                next_actions=list(dict.fromkeys(actions)),
            )
        )
    return ControlAssessment(
        evaluated_at=now,
        scan_ref=scan_ref,
        comparison_ref=comparison_ref,
        review_ref=review_ref,
        review_status=review_status,
        requirements=results,
    )


def _render_readiness(readiness, clean) -> list[str]:
    """Per-framework readiness headline, external claims and the full record."""
    import json

    from moonmind.utils.logging import redact_evidence_text

    applicability = readiness["applicability"]
    lines = [
        f"Readiness: {readiness['readiness']}. Certification and jurisdiction approval: not performed.",
        f"Applicability: {applicability['status']}. {clean(applicability['reason'])}",
    ]
    for module in readiness.get("modules", []):
        status = (
            f"; {module['status']}: {clean(module['reason'])}"
            if "status" in module
            else ""
        )
        lines.append(
            f"- Module {clean(module['module'])} {clean(module['edition'])}: source {module['sourceStatus']}{status}"
        )
    for name, claim in readiness.get("externalClaims", {}).items():
        lines.append(
            f"- External claim {clean(name)}: {claim['status']}. Verifiable by {clean(claim['verifiableBy'])}."
            + (f" Next: {clean(claim['nextStep'])}" if claim.get("nextStep") else "")
        )
    unresolved = readiness.get("unresolvedRequirements", [])
    lines.append(
        f"Evidence requests: {len(readiness.get('evidenceRequests', []))}. Unresolved requirements: {clean(', '.join(unresolved) or 'none')}."
    )
    # The complete scoped record keeps exclusions, partner ownership,
    # provenance and every request in the export, not just the headline.
    record = {k: v for k, v in readiness.items() if k != "requirementStatuses"}
    lines += [
        "",
        "```json",
        redact_evidence_text(json.dumps(record, indent=2, ensure_ascii=False)),
        "```",
        "",
    ]
    return lines


def render_control_assessment(
    report: ControlAssessment, *, structured_ref: str | None = None
) -> str:
    """Use the ordinary assessment report, with evidence refs in its bundle."""

    from moonmind.utils.logging import redact_evidence_text

    def clean(value):
        return html.escape(redact_evidence_text(str(value))).replace("\n", " ")

    lines = [
        "",
        "## Requirement assessments",
        "",
        (
            "Conclusions apply only to the declared objectives, population and period. "
            "A single-date scan is a point-in-time check, not proof of continuous operation. "
            "Independent assessor conclusions and certification remain Unverified."
        ),
        f"Structured native comparison: {clean(report.comparison_ref)}.",
    ]
    packs = report.pack_metadata
    record = (
        f"[structured control assessment](/api/artifacts/{structured_ref}/download)"
        if structured_ref
        else "structured control assessment"
    )
    for pack in packs:
        lines += [
            "",
            f"### Framework pack: {clean(pack['id'])} / {clean(pack['edition'])} / {clean(pack['revision'])}.",
            f"Pack artifact: {clean(pack['artifactRef'])}; SHA-256: {clean(pack['sha256'])}.",
            f"Publication: {clean(pack['publicationStatus'])}; published: {clean(pack.get('publishedOn') or 'not recorded')}; distribution: {clean(pack['distribution'])}.",
            f"Previous assessment: {clean(pack.get('previousAssessmentStatus', 'initial'))}.",
        ]
        if pack.get("previousReportSummary"):
            lines.append(clean(pack["previousReportSummary"]))
        if pack.get("readiness"):
            lines += _render_readiness(pack["readiness"], clean)
        for name, label in (
            ("conclusionCounts", "Conclusions"),
            ("coverageCounts", "Coverage"),
        ):
            counts = pack.get(name, {})
            if counts:
                lines.append(
                    f"{label}: "
                    + ", ".join(f"{clean(k)} {v}" for k, v in sorted(counts.items()))
                )
        for prior in pack.get("previousOnlyFrameworks", []):
            lines.append(
                f"- Prior pack {clean(prior['id'])} / {clean(prior['edition'])}: Unverified in this assessment."
            )
        changes = pack.get("intelligenceChanges", report.intelligence_changes)
        lines.extend(f"- Assessment intelligence: {clean(value)}" for value in changes)
        diagnostics = pack.get("diagnostics", report.diagnostics)
        lines.extend(f"- Pack limitation: {clean(value)}" for value in diagnostics[:4])
        if len(diagnostics) > 4:
            lines.append(
                f"{len(diagnostics) - 4} further pack limitations in the {record}."
            )
        for result in report.requirements:
            row = result.requirement
            if (row.framework_id, row.framework_edition) == (
                pack["id"],
                pack["edition"],
            ):
                lines.append(
                    f"- {clean(row.id)}: **{result.conclusion}** ({result.coverage})"
                    + (f"; readiness {row.readiness.status}." if row.readiness else ".")
                )

    # Keep all pack conclusions together. Additional detail is retained by
    # whole requirement so a cut never separates a conclusion from its basis.
    from moonmind.security.assessment_reports import MAX_REPORT_BYTES

    detail_bytes = omitted = 0
    for result in report.requirements:
        start = len(lines)
        row = result.requirement
        lines += [
            "",
            f"### {clean(row.framework_id)} {clean(row.framework_edition)} / {clean(row.id)}: {clean(row.title)}",
            f"Applicability: {row.applicability}. {clean(row.applicability_reason)}",
            f"Scope: {clean(', '.join(row.scope.assets))}. Period: {row.period.start} to {row.period.end}.",
            f"Conclusion: **{result.conclusion}**. Coverage: {result.coverage}. Evidence: {result.evidence_sufficiency}. Review: {result.review_state}.",
            *(
                [
                    f"Readiness status: {row.readiness.status}. Recorded evidence: {clean(', '.join(row.readiness.evidence_refs) or 'none')}."
                    + (
                        f" Exclusion: {clean(row.readiness.exclusion_reason)}."
                        if row.readiness.exclusion_reason
                        else ""
                    )
                ]
                if row.readiness
                else []
            ),
            f"Methods: {', '.join(result.methods) or 'not assessed'}. Objective basis: {clean(row.objective_basis)}",
            *[
                f"- {clean(item.id)}: {item.outcome}; {item.checked_cells}/{item.required_cells} asset/rule checks complete."
                for item in result.objectives
            ],
            f"Evidence: {clean(', '.join(result.evidence_refs))}.",
            *[f"- Limitation: {clean(value)}" for value in result.limitations],
            *[f"- Next: {clean(value)}" for value in result.next_actions],
        ]
        for request, reviewed in zip(
            row.organisational_evidence, result.organisational_evidence
        ):
            lines += [
                f"- Evidence request {clean(request.id)} ({request.category}, {request.purpose}): **{reviewed.state}**. Owner: {clean(request.owner)}. {clean(request.description)}",
                f"  Current reference: {clean(reviewed.current_ref or 'not supplied')}. Superseded references: {clean(', '.join(reviewed.superseded_refs) or 'none')}; prior reviews remain in the structured assessment.",
            ]
            if request.revisions:
                revision = request.revisions[-1]
                lines.append(
                    f"  Source: {clean(revision.source)}; type: {revision.kind}; sensitivity: {revision.sensitivity}; collected: {revision.collected_at.isoformat()}; expires: {revision.expires_at.isoformat()}; period: {revision.period.start} to {revision.period.end}; population: {clean(', '.join(revision.population))}."
                )
                if revision.review:
                    review = revision.review
                    lines.append(
                        f"  Attributed review by {clean(review.reviewer)} for {review.purpose}, {review.period.start} to {review.period.end}: {clean(review.reason)}. Acceptance is purpose-specific, not independent verification of real-world events."
                    )
            lines.extend(f"  Gap: {clean(value)}" for value in reviewed.limitations)
        if row.owner or row.reviewer:
            lines.append(
                f"Attributed owner: {clean(row.owner or 'unspecified')}; reviewer: {clean(row.reviewer or 'unspecified')}. These are evidence metadata, not platform roles."
            )
        if row.responsibility_reason:
            lines.append(f"Responsibility basis: {clean(row.responsibility_reason)}.")
        if result.unavailable_evidence_refs:
            lines.append(
                "Unavailable references: "
                + clean(", ".join(result.unavailable_evidence_refs))
            )
        for exception in result.exceptions:
            decision = exception.decision
            lines.append(
                f"- Exception: {decision.disposition}, {exception.status}; {clean(decision.author)}: {clean(decision.reason)}. Review due: {decision.review_at.isoformat()}."
            )
        if packs:
            size = len(("\n".join(lines[start:]) + "\n").encode())
            if detail_bytes + size > MAX_REPORT_BYTES // 8:
                del lines[start:]
                omitted += 1
            else:
                detail_bytes += size
    if omitted:
        lines.append(
            f"Details for {omitted} requirements omitted from this presentation. See the {record}."
        )
    return "\n".join(lines) + "\n"
