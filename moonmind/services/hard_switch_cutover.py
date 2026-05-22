"""Validation rules for MM-730 hard-switch cutover readiness."""

from __future__ import annotations

from collections.abc import Iterable

from moonmind.schemas.hard_switch_cutover_models import (
    CompatibilityMode,
    CutoverFindingSeverity,
    CutoverValidationFinding,
    CutoverValidationResult,
    HardSwitchCutoverRecord,
    NewStartsBoundary,
)


def validate_hard_switch_cutover(
    record: HardSwitchCutoverRecord,
) -> CutoverValidationResult:
    """Return deterministic release-readiness findings for a cutover record."""

    findings: list[CutoverValidationFinding] = []

    findings.extend(_validate_contract_coverage(record))
    findings.extend(_validate_worker_routing(record))
    findings.extend(_validate_environment_decisions(record))
    findings.extend(_validate_release_notes(record))
    findings.extend(_validate_compatibility_mode(record))

    findings = sorted(findings, key=lambda finding: (finding.code, finding.subject))
    return CutoverValidationResult(
        ready=not any(
            finding.severity == CutoverFindingSeverity.ERROR for finding in findings
        ),
        findings=findings,
    )


def _validate_contract_coverage(
    record: HardSwitchCutoverRecord,
) -> Iterable[CutoverValidationFinding]:
    contracts = record.affected_contracts
    categories = (
        ("workflows", contracts.workflows, "CUTOVER_MISSING_WORKFLOW_STRATEGY"),
        (
            "activityPayloads",
            contracts.activity_payloads,
            "CUTOVER_MISSING_ACTIVITY_PAYLOAD_STRATEGY",
        ),
        ("signals", contracts.signals, "CUTOVER_MISSING_SIGNAL_STRATEGY"),
        ("updates", contracts.updates, "CUTOVER_MISSING_UPDATE_STRATEGY"),
    )
    for subject, values, code in categories:
        if not values:
            yield _finding(
                code=code,
                subject=f"affectedContracts.{subject}",
                message=f"{subject} must include at least one affected contract strategy.",
                source="DESIGN-REQ-019",
            )


def _validate_worker_routing(
    record: HardSwitchCutoverRecord,
) -> Iterable[CutoverValidationFinding]:
    routing = record.worker_routing
    if (
        routing.single_build_serves_both_shapes
        and not (routing.version_boundary or "").strip()
    ):
        yield _finding(
            code="CUTOVER_WORKER_SINGLE_BUILD_BOTH_SHAPES",
            subject="workerRouting",
            message=(
                "One worker build cannot silently serve both old and renamed "
                "payload shapes."
            ),
            source="DESIGN-REQ-020",
        )

    if routing.new_starts_boundary == NewStartsBoundary.SAME_WORKER:
        yield _finding(
            code="CUTOVER_NEW_STARTS_BOUNDARY_MISSING",
            subject="workerRouting.newStartsBoundary",
            message=(
                "New starts must route to a renamed-contract worker, new workflow "
                "type, or documented equivalent boundary."
            ),
            source="DESIGN-REQ-020",
        )


def _validate_environment_decisions(
    record: HardSwitchCutoverRecord,
) -> Iterable[CutoverValidationFinding]:
    if not record.coordinated_release:
        yield _finding(
            code="CUTOVER_NOT_COORDINATED_RELEASE",
            subject="coordinatedRelease",
            message="The hard switch must be represented as one coordinated release.",
            source="DESIGN-REQ-021",
        )

    if not record.environment_decisions:
        yield _finding(
            code="CUTOVER_ENVIRONMENT_DECISION_MISSING",
            subject="environmentDecisions",
            message="At least one environment cutover decision is required.",
            source="DESIGN-REQ-021",
        )
        return

    for decision in record.environment_decisions:
        if not (decision.record_ref or "").strip():
            yield _finding(
                code="CUTOVER_ENVIRONMENT_DECISION_RECORD_MISSING",
                subject=f"environmentDecisions.{decision.environment}",
                message=(
                    "Each environment decision must include an operator-visible "
                    "cutover record reference."
                ),
                source="DESIGN-REQ-021",
            )


def _validate_release_notes(
    record: HardSwitchCutoverRecord,
) -> Iterable[CutoverValidationFinding]:
    notes = record.release_notes
    normalized = " ".join(notes.text.casefold().split())

    if not (notes.record_ref or "").strip():
        yield _finding(
            code="CUTOVER_RELEASE_NOTES_RECORD_MISSING",
            subject="releaseNotes.recordRef",
            message="Release notes must include an operator-visible record reference.",
            source="DESIGN-REQ-022",
        )

    mentions_task_removal = (
        "no longer exposes tasks as a product/runtime concept" in normalized
        or "no longer exposes task as a product/runtime concept" in normalized
    )
    if not mentions_task_removal:
        yield _finding(
            code="CUTOVER_RELEASE_NOTES_TASK_REMOVAL_MISSING",
            subject="releaseNotes.text",
            message=(
                "Release notes must state that MoonMind no longer exposes Tasks "
                "as a product/runtime concept."
            ),
            source="DESIGN-REQ-022",
        )

    mentions_no_aliases = (
        "no compatibility redirects or aliases are kept" in normalized
        or "no compatibility redirect or alias is kept" in normalized
    )
    if not mentions_no_aliases:
        yield _finding(
            code="CUTOVER_RELEASE_NOTES_NO_ALIAS_MISSING",
            subject="releaseNotes.text",
            message=(
                "Release notes must state that no compatibility redirects or "
                "aliases are kept."
            ),
            source="DESIGN-REQ-022",
        )


def _validate_compatibility_mode(
    record: HardSwitchCutoverRecord,
) -> Iterable[CutoverValidationFinding]:
    if record.compatibility_mode in {
        CompatibilityMode.ALIAS,
        CompatibilityMode.REDIRECT,
        CompatibilityMode.TRANSLATION_LAYER,
    }:
        yield _finding(
            code="CUTOVER_HIDDEN_COMPATIBILITY_LAYER",
            subject="compatibilityMode",
            message=(
                "Hidden aliases, redirects, or translation layers are not allowed "
                "for the hard switch."
            ),
            source="DESIGN-REQ-022",
        )


def _finding(
    *,
    code: str,
    subject: str,
    message: str,
    source: str,
) -> CutoverValidationFinding:
    return CutoverValidationFinding(
        code=code,
        subject=subject,
        message=message,
        source=source,
    )
