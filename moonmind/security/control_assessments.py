"""Scoped requirement conclusions over retained native observations.

Only an explicit, complete objective checklist can establish satisfaction.
Context claims and mappings never substitute for executed checks or widen scope.
"""

from __future__ import annotations

import html
from datetime import datetime

from moonmind.security.assessments import (
    Comparison,
    ControlAssessment,
    ControlAssessmentPlan,
    ControlObjectiveResult,
    ControlRequirementResult,
)


def _in_period(window, period, now):
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
        return False, False
    return fresh, period.start == period.end == observed.date()


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
    observation = comparison.observation
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
        if applicable and not requirement.objectives_complete:
            limits.append(
                "The declared objectives do not cover the complete requirement."
            )
            actions.append(
                "Identify the complete requirement objectives and their assessment basis."
            )
        if applicable and not requirement.objectives:
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
                    matching = [
                        (index, row)
                        for index, row in enumerate(observation.coverage)
                        if row.source == objective.source
                        and row.tool == objective.tool
                        and asset in row.assets
                        and rule in row.rule_ids
                    ]
                    # allRules describes a source's configuration, not proof an
                    # arbitrary mapped rule was installed or actually selected.
                    complete, valid = False, False
                    cell_stale = False
                    cell_incomplete = False
                    for index, row in matching:
                        configs.add(row.config_ref)
                        deterministic = row.evidence_method == "deterministic_check"
                        if row.evidence_method != "unclassified":
                            methods.add(row.evidence_method)
                        cell_incomplete |= not deterministic or row.status != "complete"
                        if not deterministic:
                            objective_limits.append(
                                "The source does not identify this evidence as an executed deterministic check."
                            )
                        if row.status != "complete":
                            objective_limits.append(
                                "Matching source evidence includes incomplete or blocked execution."
                            )
                        window = (
                            coverage_windows[index]
                            if index < len(coverage_windows)
                            else {}
                        )
                        fresh, in_period = _in_period(window, requirement.period, now)
                        cell_stale |= not fresh
                        if not fresh:
                            objective_limits.append(
                                "Evidence is stale, future-dated, or lacks its observation window."
                            )
                        if not in_period:
                            objective_limits.append(
                                "Point-in-time observations do not cover the selected assessment period."
                            )
                        valid |= deterministic and fresh and in_period
                        complete |= (
                            deterministic
                            and row.status == "complete"
                            and fresh
                            and in_period
                        )
                        blocked |= row.status in {
                            "authentication_failed",
                            "failed",
                            "unsupported",
                        }
                    stale |= cell_stale
                    ids = {
                        row.finding_id
                        for row in observation.findings
                        if row.source == objective.source
                        and row.tool == objective.tool
                        and row.asset == asset
                        and row.rule_id == rule
                    }
                    matched_ids.update(ids)
                    # An older observation cannot borrow a newer one's timestamp.
                    # Mixed unavailable/stale windows remain inconclusive.
                    failed |= bool(ids) and valid and not cell_stale
                    if complete and not cell_stale and not cell_incomplete:
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
            for revision in request.revisions:
                if evidence_availability.get(revision.artifact_ref, "unavailable"):
                    unavailable.append(revision.artifact_ref)
                else:
                    evidence_refs.append(revision.artifact_ref)
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
            if conclusion != "satisfied":
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


def render_control_assessment(report: ControlAssessment) -> str:
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
    if report.framework_pack:
        pack = report.framework_pack
        lines += [
            f"Framework pack: {clean(pack['id'])} / {clean(pack['edition'])} / {clean(pack['revision'])}.",
            f"Pack artifact: {clean(pack['artifactRef'])}; SHA-256: {clean(pack['sha256'])}.",
            f"Publication: {clean(pack['publicationStatus'])}; distribution: {clean(pack['distribution'])}.",
            *[
                f"- Assessment intelligence: {clean(value)}"
                for value in report.intelligence_changes
            ],
            *[f"- Pack limitation: {clean(value)}" for value in report.diagnostics],
        ]
    for result in report.requirements:
        row = result.requirement
        lines += [
            "",
            f"### {clean(row.framework_id)} {clean(row.framework_edition)} / {clean(row.id)}: {clean(row.title)}",
            f"Applicability: {row.applicability}. {clean(row.applicability_reason)}",
            f"Scope: {clean(', '.join(row.scope.assets))}. Period: {row.period.start} to {row.period.end}.",
            f"Conclusion: **{result.conclusion}**. Coverage: {result.coverage}. Evidence: {result.evidence_sufficiency}. Review: {result.review_state}.",
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
    return "\n".join(lines) + "\n"
