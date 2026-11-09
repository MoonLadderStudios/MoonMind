"""Selected web/API procedures and evidence coverage, never execution authority.

Plans use declared framework-pack delivery. Executable methods still use the
existing admitted ZAP/application-access jobs; this module launches nothing.
"""

from __future__ import annotations

import html
import json
import re
from typing import Annotated, Literal

from pydantic import Field, model_validator

from moonmind.security.frameworks import AssessmentText as Text
from moonmind.security.frameworks import FrameworkContract as Contract

Texts = Annotated[list[Text], Field(max_length=64)]
Refs = Annotated[list[Text], Field(max_length=64)]
Method = Literal[
    "zap",
    "application-access",
    "security-review",
    "security-investigate",
    "repository-observation",
    "cloud-observation",
]


class AssessmentProcedure(Contract):
    id: Text
    method: Method
    instructions: Text
    oracle: Text
    needs_source: bool = False
    needs_identity: bool = False
    protocols: Texts = Field(default_factory=lambda: ["http", "https"])


class TestInterface(Contract):
    id: Text
    asset: Text
    protocol: Text
    locator: Text


class TestIdentity(Contract):
    id: Text
    available: bool
    credential_ref: Text | None = None


class PlannedProcedure(Contract):
    id: Text
    assets: Annotated[Texts, Field(min_length=1)]
    interface_ids: Annotated[Texts, Field(min_length=1)]
    identity_ids: Texts = Field(default_factory=list)
    case_refs: Texts = Field(default_factory=list)
    case_spec_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None


class PlannedObjective(Contract):
    id: Text
    applicability: Literal["applicable", "not_applicable", "undetermined"]
    reason: Text | None = None
    procedures: Annotated[list[PlannedProcedure], Field(max_length=16)]

    @model_validator(mode="after")
    def applicability_reason(self):
        if self.applicability != "applicable" and not self.reason:
            raise ValueError("undetermined or inapplicable objectives require a reason")
        if self.applicability == "not_applicable" and self.procedures:
            raise ValueError("inapplicable objectives cannot select executable methods")
        return self


class MethodologyInput(Contract):
    plan_ref: Text
    results_ref: Text | None = None
    baseline_ref: Text | None = None


class ProcedureRun(Contract):
    objective_id: Text
    procedure_id: Text
    status: Literal["completed", "partial", "blocked", "untested"]
    evidence_refs: Refs = Field(default_factory=list)
    evidence_kind: Literal[
        "tool_match", "reproduction", "review_judgment", "hypothesis"
    ]
    outcome: Literal["finding", "clear", "inconclusive"]
    reason: Text | None = None
    native_report_ref: Text | None = None
    case_refs: Texts = Field(default_factory=list)
    configuration_ref: Text | None = Field(
        default=None, json_schema_extra={"readOnly": True}
    )

    @model_validator(mode="after")
    def evidence_and_limits(self):
        if self.status in {"completed", "partial"} and not self.evidence_refs:
            raise ValueError("performed procedures require supporting evidence")
        if self.status != "completed" and not self.reason:
            raise ValueError("incomplete procedures require a reason")
        return self


class ProcedureResults(Contract):
    schema_version: Literal["web-api-procedure-results/v1"] = (
        "web-api-procedure-results/v1"
    )
    plan_ref: Text
    runs: Annotated[list[ProcedureRun], Field(max_length=256)]
    limitations: Annotated[Texts, Field(min_length=1)]
    review_status: Literal["pending", "reviewed"] = "pending"
    reviewer: Text | None = None
    reviewer_notes: Texts = Field(default_factory=list)
    follow_up_tasks: Texts = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_runs_and_review(self):
        keys = [(r.objective_id, r.procedure_id) for r in self.runs]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate procedure result")
        if self.review_status == "reviewed" and not self.reviewer:
            raise ValueError("reviewed results require reviewer attribution")
        return self


def _unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {label}")


def validate_plan(plan, pack, *, now):
    # Reuse the same edition, period, distribution and applicability validation
    # as the control evaluator. Its conclusions remain separate from coverage.
    from moonmind.security.framework_packs import compile_framework_plan

    compiled, _ = compile_framework_plan(
        pack,
        scope=plan.scope,
        period=plan.pack.period,
        choices=plan.pack.requirement_choices,
        now=now,
    )
    if not pack.methodology_editions:
        raise ValueError("methodology procedures require explicit selected editions")
    _unique([i.id for i in plan.interfaces], "interface")
    _unique([i.id for i in plan.identities], "test identity")
    _unique([o.id for o in plan.objectives], "selected objective")
    if set(o.id for o in plan.objectives) != set(r.id for r in pack.requirements):
        raise ValueError("plan must account for every selected pack objective")
    if any(
        i.asset not in plan.scope.assets
        or i.asset in plan.scope.exclusions
        or i.locator in plan.scope.exclusions
        for i in plan.interfaces
    ):
        raise ValueError("interface outside admitted scope")
    interfaces = {i.id: i for i in plan.interfaces}
    identities = {i.id for i in plan.identities}
    for objective in plan.objectives:
        requirement = next(r for r in pack.requirements if r.id == objective.id)
        requirement_scope = next(
            r.scope for r in compiled.requirements if r.id == objective.id
        )
        choice = plan.pack.requirement_choices.get(objective.id)
        if (
            choice
            and choice.applicability != "undetermined"
            and choice.applicability != objective.applicability
        ):
            raise ValueError(
                "plan applicability conflicts with the selected pack choice"
            )
        catalog = {p.id: p for p in requirement.procedures}
        _unique([p.id for p in objective.procedures], "planned procedure")
        if not catalog:
            raise ValueError("selected pack has no maintained procedures")
        if objective.applicability != "not_applicable" and set(
            p.id for p in objective.procedures
        ) != set(catalog):
            raise ValueError(
                "applicable objectives must retain their selected procedures"
            )
        for selected in objective.procedures:
            procedure = catalog.get(selected.id)
            if procedure is None or procedure.method not in plan.allowed_methods:
                raise ValueError("procedure or method is not admitted by the plan")
            if procedure.method == "application-access" and (
                not selected.case_refs or not selected.case_spec_digest
            ):
                raise ValueError(
                    "executable access procedures require planned cases and their original spec digest"
                )
            if set(selected.assets) - set(requirement_scope.assets) or set(
                selected.assets
            ) & set(plan.scope.exclusions):
                raise ValueError("procedure target outside admitted scope")
            if set(selected.interface_ids) - set(interfaces):
                raise ValueError("procedure names unknown interface")
            if any(
                interfaces[i].asset not in selected.assets
                for i in selected.interface_ids
            ):
                raise ValueError("procedure interface exceeds selected targets")
            if set(selected.identity_ids) - identities:
                raise ValueError("procedure names unknown identity")


def evidence_refs(plan, results=None):
    refs = [
        plan.pack.artifact_ref,
        plan.authority_ref,
        *plan.source_refs,
        *plan.api_context_refs,
    ]
    if results:
        refs.extend(ref for run in results.runs for ref in run.evidence_refs)
        refs.extend(
            run.native_report_ref for run in results.runs if run.native_report_ref
        )
    refs = list(dict.fromkeys(refs))
    if len(refs) > 64:
        raise ValueError("methodology evidence exceeds reference bound")
    return refs


def bind_native_runs(plan, pack, results, summaries):
    """Reuse already verified native summaries, including identity/case failures."""
    native = {s["summaryRef"]: s for s in summaries}
    runs = []
    for run in results.runs:
        objective = next((o for o in plan.objectives if o.id == run.objective_id), None)
        selected = (
            next((p for p in objective.procedures if p.id == run.procedure_id), None)
            if objective
            else None
        )
        requirement = next(
            (r for r in pack.requirements if r.id == run.objective_id), None
        )
        procedure = (
            next((p for p in requirement.procedures if p.id == run.procedure_id), None)
            if requirement
            else None
        )
        if procedure is None or selected is None:
            raise ValueError("result names an unplanned procedure")
        if procedure.method not in {"zap", "application-access"} or run.status in {
            "blocked",
            "untested",
        }:
            runs.append(run)
            continue
        summary = native.get(run.native_report_ref)
        reasons, outcome = [], "inconclusive"
        if (
            summary is None
            or summary["scanner"] != procedure.method
            or summary["target"] not in selected.assets
        ):
            reasons.append(
                "No verified native report for the selected method and target"
            )
        elif procedure.method == "application-access":
            cases = {c["caseRef"]: c for c in summary["cases"]}
            if summary.get("specDigest") != selected.case_spec_digest:
                reasons.append("Native cases differ from the pre-execution test oracle")
            elif (
                not selected.case_refs
                or set(selected.case_refs) != set(run.case_refs)
                or set(selected.case_refs) - set(cases)
            ):
                reasons.append(
                    "Planned authorization cases were removed or not executed"
                )
            else:
                covered = [cases[c] for c in selected.case_refs]
                locators = {
                    i.locator for i in plan.interfaces if i.id in selected.interface_ids
                }
                if any(
                    c["coverage"] != "complete"
                    or c["authenticationState"] != "verified"
                    or c["identityRef"] not in selected.identity_ids
                    or c["request"]["path"] not in locators
                    for c in covered
                ) or set(selected.identity_ids) - {c["identityRef"] for c in covered}:
                    reasons.append(
                        "Selected identities, endpoints or case coverage are incomplete"
                    )
                uncovered = locators - {c["request"]["path"] for c in covered}
                if uncovered:
                    reasons.append(
                        f"Selected endpoints were not covered by native cases: {sorted(uncovered)}"
                    )
                outcome = (
                    "finding"
                    if any(c["outcome"] == "finding" for c in covered)
                    else "clear"
                )
        else:
            outcome = summary["methodologyOutcome"]
            if summary["coverage"] != "complete":
                reasons.append(
                    "Native scanner route, authentication or execution coverage is incomplete"
                )
            uncovered = {
                i.locator for i in plan.interfaces if i.id in selected.interface_ids
            } - set(summary["methodologyRoutes"])
            if uncovered:
                reasons.append(
                    f"Selected endpoints were not covered by the scanner: {sorted(uncovered)}"
                )
        if summary is not None:
            uncovered_assets = set(selected.assets) - {summary["target"]}
            if uncovered_assets:
                reasons.append(
                    f"No verified native report for selected targets: {sorted(uncovered_assets)}"
                )
        if reasons:
            run = run.model_copy(
                update={
                    "status": "partial",
                    "outcome": "finding" if outcome == "finding" else "inconclusive",
                    "reason": "; ".join(
                        dict.fromkeys(item for item in (run.reason, *reasons) if item)
                    ),
                }
            )
        else:
            run = run.model_copy(
                update={
                    "outcome": outcome,
                    "configuration_ref": summary.get("specDigest")
                    or summary.get("configurationRef"),
                }
            )
        runs.append(run)
    return results.model_copy(update={"runs": runs})


def evaluate_coverage(plan, pack, results, *, plan_ref):
    if results and results.plan_ref != plan_ref:
        raise ValueError("procedure results belong to a different plan")
    selected = {(o.id, p.id) for o in plan.objectives for p in o.procedures}
    runs = (
        {(r.objective_id, r.procedure_id): r for r in results.runs} if results else {}
    )
    if set(runs) - selected:
        raise ValueError("result names an unplanned procedure")
    interfaces = {i.id: i for i in plan.interfaces}
    identities = {i.id: i for i in plan.identities}
    objectives = []
    for objective in plan.objectives:
        requirement = next(r for r in pack.requirements if r.id == objective.id)
        methods, limitations = [], []
        for selected_method in objective.procedures:
            procedure = next(
                p for p in requirement.procedures if p.id == selected_method.id
            )
            run = runs.get((objective.id, procedure.id))
            gaps = []
            if procedure.needs_source and not plan.source_refs:
                gaps.append("Source is unavailable")
            if procedure.needs_identity and (
                not selected_method.identity_ids
                or any(
                    not identities[i].available for i in selected_method.identity_ids
                )
            ):
                gaps.append("Required test identity is unavailable")
            if any(
                interfaces[i].protocol not in procedure.protocols
                for i in selected_method.interface_ids
            ):
                gaps.append("Interface protocol is unsupported by this procedure")
            if gaps:
                status = "blocked"
            elif run is None:
                status = "untested"
                gaps.append("Procedure was not executed or reviewed")
            else:
                status = run.status
                supported_kinds = (
                    {"tool_match", "reproduction"}
                    if procedure.method == "zap"
                    else (
                        {"reproduction"}
                        if procedure.method == "application-access"
                        else {"review_judgment", "reproduction"}
                    )
                )
                if (
                    run.status in {"completed", "partial"}
                    and run.evidence_kind not in supported_kinds
                ):
                    status = "partial"
                    gaps.append("Evidence kind does not establish the selected method")
                if run.status in {"completed", "partial"} and (
                    run.evidence_kind == "hypothesis" or run.outcome == "inconclusive"
                ):
                    status = "partial" if run.evidence_refs else "untested"
                    gaps.append(
                        "Hypothesis or inconclusive evidence does not complete a procedure"
                    )
                if run.reason:
                    gaps.append(run.reason)
            limitations.extend(gaps)
            methods.append(
                {
                    "id": procedure.id,
                    "method": procedure.method,
                    "oracle": procedure.oracle,
                    "assets": selected_method.assets,
                    "interfaceIds": selected_method.interface_ids,
                    "interfaces": [
                        interfaces[i].model_dump(mode="json", by_alias=True)
                        for i in selected_method.interface_ids
                    ],
                    "identityIds": selected_method.identity_ids,
                    "caseRefs": selected_method.case_refs,
                    "caseSpecDigest": selected_method.case_spec_digest,
                    "configurationRef": run.configuration_ref if run else None,
                    "nativeReportRef": run.native_report_ref if run else None,
                    "status": status,
                    "evidenceRefs": run.evidence_refs if run else [],
                    "evidenceKind": run.evidence_kind if run else None,
                    "outcome": run.outcome if run else "inconclusive",
                    "limitations": gaps,
                }
            )
        states = {m["status"] for m in methods}
        if objective.applicability == "not_applicable":
            status = "not_applicable"
        elif objective.applicability == "undetermined":
            status = "blocked"
        elif states == {"completed"}:
            status = "tested"
        elif states & {"completed", "partial"}:
            status = "partially_tested"
        elif "blocked" in states:
            status = "blocked"
        else:
            status = "untested"
        objectives.append(
            {
                "id": objective.id,
                "status": status,
                "reason": objective.reason,
                "methods": methods,
                "limitations": limitations,
            }
        )
    return {
        "schemaVersion": "web-api-coverage/v1",
        "planRef": plan_ref,
        "candidateRef": plan.candidate_ref,
        "scope": plan.scope.model_dump(mode="json", by_alias=True),
        "systemBoundary": plan.system_boundary,
        "pack": {
            "id": pack.id,
            "edition": pack.edition,
            "revision": pack.revision,
            "sha256": plan.pack.sha256,
        },
        "methodologyEditions": pack.methodology_editions,
        "objectives": objectives,
        "complete": all(
            o["status"] in {"tested", "not_applicable"} for o in objectives
        ),
        "limitations": (
            results.limitations if results else ["Procedure results are unavailable"]
        ),
        "reviewStatus": results.review_status if results else "pending",
        "reviewer": results.reviewer if results else None,
        "reviewerNotes": results.reviewer_notes if results else [],
        "followUpTasks": results.follow_up_tasks if results else [],
        "assurance": "automated_assessment; independent_assessment_unverified",
    }


def compare_methodology(baseline, current):
    """Removed work or unavailable identities cannot count as a repaired finding."""
    current_rows = {r["id"]: r for r in current["objectives"]}
    results = {}
    same_context = baseline["pack"]["id"] == current["pack"]["id"] and all(
        baseline[k] == current[k]
        for k in ("methodologyEditions", "scope", "systemBoundary")
    )

    def signature(row):
        fields = (
            "id",
            "method",
            "oracle",
            "assets",
            "interfaces",
            "identityIds",
            "caseRefs",
            "caseSpecDigest",
            "configurationRef",
        )
        return sorted(
            json.dumps({k: m[k] for k in fields}, sort_keys=True)
            for m in row["methods"]
        )

    for previous in baseline["objectives"]:
        row = current_rows.get(previous["id"])
        if (
            not same_context
            or row is None
            or previous["status"] != "tested"
            or row["status"] != "tested"
            or signature(previous) != signature(row)
        ):
            state = "coverage_changed"
        elif any(m["outcome"] == "finding" for m in row["methods"]):
            state = "still_observed"
        elif any(m["outcome"] == "finding" for m in previous["methods"]):
            # Comparable scanner absence or a changed judgment alone is not a
            # passing reproduction of protected behavior.
            state = (
                "fixed_behavior"
                if any(
                    m["outcome"] == "finding"
                    and m["evidenceKind"] == "reproduction"
                    and m["nativeReportRef"]
                    and m["configurationRef"]
                    for m in previous["methods"]
                )
                else "absent_comparable"
            )
        else:
            state = "no_finding_observed"
        results[previous["id"]] = state
    return results


def render_methodology(report):
    def safe(value):
        value = html.escape(str(value).replace("\n", " ").replace("\r", " "))
        return re.sub(r"([\\`*_[\]{}()#!|>])", r"\\\1", value)

    lines = [
        "",
        "## Web and API methodology",
        "",
        "Selected editions: " + safe(report["methodologyEditions"]),
        "Review status: " + safe(report["reviewStatus"]),
        "Reviewer: " + safe(report["reviewer"] or "pending"),
        "Automated assessment; independent professional assessment Unverified.",
        "Plan: " + safe(report["planRef"]),
        "Exclusions: " + safe(report["scope"]["exclusions"]),
    ]
    for row in report["objectives"]:
        lines.append(
            f"- {safe(row['id'])} — {row['status']}; {safe(row['reason'] or '')}"
        )
        for method in row["methods"]:
            lines.append(
                f"  - {safe(method['id'])} / {method['method']}: {method['status']}; {method['outcome']} ({method['evidenceKind']}). Evidence: {safe(method['evidenceRefs'])}. Oracle: {safe(method['oracle'])}."
            )
        lines.extend("  - Limitation: " + safe(item) for item in row["limitations"])
    for key in ("limitations", "reviewerNotes", "followUpTasks"):
        lines.extend(f"- {key}: {safe(item)}" for item in report[key])
    if report.get("retest"):
        lines.append("Retest: " + safe(report["retest"]))
    return "\n".join(lines) + "\n"
