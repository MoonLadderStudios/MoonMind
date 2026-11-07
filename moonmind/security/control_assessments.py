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

    def clean(value):
        return html.escape(str(value)).replace("\n", " ")

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
