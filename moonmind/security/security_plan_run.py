"""Run a retained ``security-plan/v1`` through the existing assessment owners.

A plan run is ordinary workflow content. The preset compiler derives it from a
retained, digest-identified plan: each launchable planned job becomes the
existing technical preset of its kind with the plan's job input, and a final
``security.assess`` step concludes per selected standard over the evidence
those jobs retained. Launch still needs a saved authorization and the
rules-of-engagement review; nothing in a plan admits a target.

This module owns that derivation, the job outcome record each job step writes,
what the review reports as not covered, and the plan-against-actual record.
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, ValidationError

from moonmind.security.assessments import MAX_REPORT_REFS, Contract
from moonmind.security.assessments import ExcludedPlanJob as ExcludedJob
from moonmind.security.assessments import PlanRunInput
from moonmind.security.frameworks import AssessmentText as Text
from moonmind.security.security_plan import (
    SecurityPlan,
    _safe,
    parse_security_plan,
    plan_identity,
)

SCHEMA_VERSION = "security-plan-run/v1"
JOB_RECORD_VERSION = "security-plan-job/v1"
#: Link type of the job records the agent runtime publishes for a plan run.
JOB_RECORD_LINK = "input.security_plan_job"
#: Workspace directory where each job step writes its outcome record.
JOB_RECORD_DIRECTORY = "artifacts/security-plan-run"
#: Planned job kinds launched as the existing technical preset of that kind.
#: Other kinds stay planned work the run reports as not run.
RUN_PRESETS = {
    "repository-scan": "security-code-supply-chain",
    "image-assessment": "security-image-sbom",
    "web-assessment": "security-web-api",
    "cloud-assessment": "security-aws-posture",
}
#: Presets whose jobs run inside the admitted assessment scope.
SCOPED_PRESET_KINDS = frozenset({"web-assessment", "cloud-assessment"})
#: Concluding budget: offline evaluation of retained evidence only.
CONCLUSION_BUDGET = {"baseSeconds": 120, "maxSeconds": 300, "progressStallSeconds": 120}
#: Retained reports one conclusion evaluates; readable reports beyond it are
#: reported as unevaluated, never silently dropped.
MAX_CONCLUSION_REPORTS = MAX_REPORT_REFS
#: Job report summaries the retained-report owner of security.assess reads.
NATIVE_SUMMARY_VERSIONS = frozenset(
    {
        "repository-scanner/v1",
        "image-assessment/v1",
        "web-assessment/v1",
        "cloud-posture/v1",
        "tls-assessment/v1",
        "dns-assessment/v1",
        "service-inventory/v1",
    }
)

Identifier = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ArtifactRef = Annotated[str, StringConstraints(pattern=r"^art_[A-Za-z0-9_-]+$")]
Ref = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9.-]{0,127}$")]

JobState = Literal[
    "completed",
    "partial",
    "failed",
    "blocked_admission",
    "blocked_readiness",
    "not_run",
]
JOB_STATES: tuple[str, ...] = JobState.__args__
RequirementState = Literal[
    "evidence_collected",
    "inconclusive",
    "not_assessed",
    "review",
    "organisational",
    "external_only",
    "not_applicable",
]
REQUIREMENT_STATES: tuple[str, ...] = RequirementState.__args__


class PlanRunError(ValueError):
    """A plan run cannot be derived; the message never echoes plan content."""


class LaunchBinding(Contract):
    """Launch-bound references for one job, chosen from the saved authorization.

    A plan names credential purposes only. These are authored at launch and
    still pass the rules-of-engagement review and job admission.
    """

    assessment_ref: Ref | None = None
    credential_ref: str | None = Field(default=None, min_length=4, max_length=1024)


class PlanJobRecord(Contract):
    """What one job step reports it did. Claims are checked, never trusted."""

    schema_version: Literal["security-plan-job/v1"] = JOB_RECORD_VERSION
    plan_identity: Sha256
    job_id: Identifier
    state: Literal[
        "completed",
        "partial",
        "failed",
        "blocked_admission",
        "blocked_readiness",
        "cancelled",
    ]
    reason: Text
    container_job_ids: Annotated[list[Text], Field(max_length=8)] = Field(
        default_factory=list
    )
    report_refs: Annotated[list[ArtifactRef], Field(max_length=8)] = Field(
        default_factory=list
    )
    review_ref: ArtifactRef | None = None


def read_plan(raw: bytes) -> tuple[SecurityPlan, str]:
    """A retained plan and its content identity."""
    plan = parse_security_plan(raw)
    return plan, plan_identity(plan)


def job_record_path(job_id: str) -> str:
    return f"{JOB_RECORD_DIRECTORY}/{job_id}.json"


def _bind(
    kind: str, value: dict, binding: LaunchBinding | None, assessment_ref: str | None
) -> dict:
    value = copy.deepcopy(value)
    binding = binding or LaunchBinding()
    reference = binding.assessment_ref or assessment_ref
    if reference and kind in SCOPED_PRESET_KINDS:
        value["assessmentRef"] = reference
    if binding.credential_ref:
        if kind == "cloud-assessment":
            value["credentialRef"] = binding.credential_ref
        elif kind == "image-assessment":
            # A private target registry; the image contract admits it for
            # registry targets only.
            value["targetCredentialRef"] = binding.credential_ref
        elif kind == "web-assessment" and isinstance(value.get("identity"), dict):
            value["identity"] = {
                **value["identity"],
                "credentialRef": binding.credential_ref,
            }
    return value


def _unbound(kind: str, value: Mapping) -> str | None:
    """Why a job still lacks the launch-bound references its contract needs."""
    if kind in SCOPED_PRESET_KINDS and not value.get("assessmentRef"):
        return "needs the saved authorization's assessment reference (Assessment reference)"
    if kind == "cloud-assessment" and not value.get("credentialRef"):
        return "needs a cloud credentialRef from the saved authorization under Launch bindings"
    identity = value.get("identity")
    if (
        kind == "web-assessment"
        and isinstance(identity, Mapping)
        and not identity.get("credentialRef")
    ):
        return "needs the test identity's credentialRef from the saved authorization under Launch bindings"
    return None


def parse_run_inputs(
    bindings: Any, excluded: Any
) -> tuple[dict[str, LaunchBinding], dict[str, str]]:
    """Per-job launch bindings and the jobs the operator leaves out."""
    try:
        bound = {
            str(job_id): LaunchBinding.model_validate(value)
            for job_id, value in dict(bindings or {}).items()
        }
    except (TypeError, ValueError, ValidationError):
        raise PlanRunError(
            "launch_bindings: map job IDs to assessmentRef and credentialRef"
        ) from None
    removed: dict[str, str] = {}
    for item in list(excluded or []):
        if isinstance(item, str):
            removed[item] = "Removed by the operator before launch."
        elif isinstance(item, Mapping) and isinstance(item.get("jobId"), str):
            removed[item["jobId"]] = str(
                item.get("reason") or "Removed by the operator before launch."
            )[:1024]
        else:
            raise PlanRunError("excluded_jobs: name job IDs, optionally with a reason")
    return bound, removed


def derive_plan_run(
    plan: SecurityPlan,
    *,
    assessment_ref: str | None = None,
    bindings: Mapping[str, LaunchBinding] | None = None,
    excluded: Mapping[str, str] | None = None,
) -> tuple[list[tuple[str, str, dict]], list[ExcludedJob]]:
    """Launchable ``(job_id, preset_slug, assessment_input)`` and excluded jobs.

    Every planned job is either launched through its technical preset or
    reported with the reason it is not. Nothing here widens a job input.
    """
    bindings = dict(bindings or {})
    excluded = dict(excluded or {})
    jobs = {job.id: job for job in plan.jobs}
    unknown = sorted((set(bindings) | set(excluded)) - set(jobs))
    if unknown:
        raise PlanRunError(f"unknown planned job IDs: {', '.join(unknown)}")
    launch, skipped = [], []
    for job in plan.jobs:
        preset = RUN_PRESETS.get(job.kind)
        if job.id in excluded:
            reason = excluded[job.id]
        elif preset is None:
            reason = f"No launchable job step exists for {job.kind}; launch it separately under its own review."
        else:
            value = _bind(job.kind, job.input, bindings.get(job.id), assessment_ref)
            missing = _unbound(job.kind, value)
            if missing is None:
                launch.append((job.id, preset, value))
                continue
            reason = f"{missing[0].upper()}{missing[1:]}."
        skipped.append(ExcludedJob(job_id=job.id, kind=job.kind, reason=reason))
    return launch, skipped


def job_step_overlay(job_id: str, identity: str) -> dict[str, Any]:
    """What a plan-run job step adds to its technical preset step."""
    path = job_record_path(job_id)
    return {
        "annotations": {"securityPlanJob": {"jobId": job_id, "planIdentity": identity}},
        "args": {
            "securityPlanJobPath": path,
            "securityPlanJob": {"jobId": job_id, "planIdentity": identity},
        },
        "instructions": (
            f"This is planned job {job_id} of security plan {identity}. Before finishing, "
            f"write its {JOB_RECORD_VERSION} outcome record to {path}: planIdentity, jobId, "
            "state (completed, partial, failed, blocked_admission, blocked_readiness or "
            "cancelled), reason, containerJobIds, reportRefs (the job's retained report "
            "summary artifacts) and reviewRef when an interpretation was saved. Record a "
            "refused admission or failed readiness as blocked with its diagnostic; never "
            "record completion without retained native evidence."
        ),
    }


def _framework_pack(standard) -> dict:
    exclude = {"title", "distribution"}
    if standard.artifact_ref is None:
        exclude.add("sha256")
    return standard.model_dump(mode="json", by_alias=True, exclude=exclude)


def conclusion_step(
    plan: SecurityPlan,
    *,
    plan_ref: str,
    identity: str,
    excluded: list[ExcludedJob],
    previous_assessment_ref: str | None = None,
) -> dict[str, Any]:
    """The final ``security.assess`` over the plan's standards and job evidence."""
    inputs = {
        "scope": plan.subject.scope.model_dump(mode="json", by_alias=True),
        "tools": [],
        "budget": dict(CONCLUSION_BUDGET),
        "credentialRefs": [],
        "networkRefs": [],
        "frameworkPacks": [_framework_pack(s) for s in plan.standards],
        "planRun": PlanRunInput(
            plan_ref=plan_ref, plan_identity=identity, excluded=excluded
        ).model_dump(mode="json", by_alias=True),
    }
    if previous_assessment_ref:
        inputs["previousAssessmentRef"] = previous_assessment_ref
    return {
        "title": "Conclude per selected standard",
        "type": "tool",
        "instructions": (
            "Conclude each selected standard once, from the evidence this run's planned "
            "jobs retained, and report every planned job and requirement against the plan. "
            "Missing evidence stays inconclusive; zero findings never mean a requirement "
            "is met. Runs no job and authorizes no remediation or publication."
        ),
        "tool": {
            "id": "security.assess",
            "requiredCapabilities": ["sandbox"],
            "inputs": inputs,
        },
        "annotations": {
            "securityPlanRun": {"planRef": plan_ref, "planIdentity": identity}
        },
    }


# --- Rules-of-engagement review ------------------------------------------


def _scoped_step_gap(kind: str, admission: Mapping, scope) -> str | None:
    """Why the selected authorization would refuse this job, or None."""
    from moonmind.schemas.container_job_models import ContainerJobSpec
    from moonmind.security.assessment_preset_inputs import assessment_job_spec
    from moonmind.security.workload_profiles import resolved_job_within_scope

    value = dict(admission)
    if kind == "web-assessment" and not value.get("assessmentRef"):
        value["assessmentRef"] = scope.assessment_ref
    try:
        spec = ContainerJobSpec.model_validate(assessment_job_spec(kind, value))
    except (TypeError, ValueError):
        return "its job input is incomplete or invalid"
    try:
        resolved_job_within_scope(spec, scope, None)
    except (PermissionError, ValueError) as exc:
        return str(exc)
    needed = []
    if kind == "cloud-assessment":
        needed.append(("cloud_inventory", value.get("credentialRef")))
    identity = value.get("identity")
    if kind == "web-assessment" and isinstance(identity, Mapping):
        needed.append(("application_auth", identity.get("credentialRef")))
    for purpose, ref in needed:
        if not any(g.ref == ref and g.purpose == purpose for g in scope.grants):
            return f"no {purpose} credential grant names the selected reference"
    return None


def review_items(plan_document: Mapping[str, Any], scope) -> list[str] | None:
    """The review's "Not covered by this authorization" items, or None.

    Lists each scoped job step the authorization would refuse and each planned
    job the run does not launch. None when the workflow has neither a plan run
    nor an uncovered step, so other proposals are unchanged.
    """
    workflow = plan_document.get("workflow")
    steps = workflow.get("steps") if isinstance(workflow, Mapping) else None
    items: list[str] = []
    plan_run = False
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, Mapping):
            continue
        title = str(step.get("title") or step.get("id") or "Workflow step")
        annotations = step.get("annotations")
        hint = (
            annotations.get("securityAssessment")
            if isinstance(annotations, Mapping)
            else None
        )
        if (
            isinstance(hint, Mapping)
            and hint.get("kind") in {"web-assessment", "cloud-assessment"}
            and isinstance(hint.get("admission"), Mapping)
        ):
            gap = _scoped_step_gap(hint["kind"], hint["admission"], scope)
            if gap:
                items.append(
                    f"{title}: not covered ({gap}). Remove it under Excluded jobs, or "
                    "select an authorization that covers it, and review again; its "
                    "requirements are then reported as not assessed."
                )
        tool = step.get("tool")
        inputs = tool.get("inputs") if isinstance(tool, Mapping) else None
        run = inputs.get("planRun") if isinstance(inputs, Mapping) else None
        if isinstance(run, Mapping):
            plan_run = True
            items.extend(
                f"Planned job {item.get('jobId')} ({item.get('kind')}) is not launched: "
                f"{item.get('reason')} Its requirements are reported as not assessed."
                for item in run.get("excluded") or ()
                if isinstance(item, Mapping)
            )
    if not items and not plan_run:
        return None
    return items or ["Every launched planned job is within this authorization."]


# --- Plan against actual ---------------------------------------------------


#: Why a cited report is not evidence, by the status the conclusion recorded.
_UNUSABLE = {
    "malformed": "malformed",
    "unavailable": "unavailable",
    "rejected": "rejected by the retained-report check",
    "unevaluated": (
        f"not evaluated (beyond the {MAX_CONCLUSION_REPORTS} reports one "
        "conclusion evaluates)"
    ),
}


def _unusable(refs: Iterable[str], reports: Mapping[str, str]) -> str:
    return "; ".join(
        f"{ref} {_UNUSABLE.get(reports.get(ref, ''), 'unavailable')}" for ref in refs
    )


def job_outcome(
    job_id: str,
    *,
    excluded: Mapping[str, str],
    record: PlanJobRecord | None,
    reports: Mapping[str, str],
) -> tuple[str, str, list[str]]:
    """``(state, reason, readable report refs)`` for one planned job.

    ``reports`` maps each cited report (and review) to ``readable``,
    ``malformed`` or ``unavailable`` as the artifact owner read it,
    ``rejected`` when the retained-report owner refused it, or
    ``unevaluated`` when it exceeds the conclusion's report bound.
    """
    if job_id in excluded:
        return "not_run", f"Not launched: {excluded[job_id]}", []
    if record is None:
        return (
            "not_run",
            "No outcome was recorded for this planned job; its step did not run or did not finish.",
            [],
        )
    if record.state == "cancelled":
        return "not_run", f"Cancelled: {record.reason}", []
    readable = [ref for ref in record.report_refs if reports.get(ref) == "readable"]
    if record.state in {"completed", "partial"}:
        unusable = [ref for ref in record.report_refs if ref not in readable]
        if not readable:
            return (
                "failed",
                f"Reported {record.state} without readable native evidence"
                + (f" ({_unusable(unusable, reports)})" if unusable else "")
                + f": {record.reason}",
                [],
            )
        if unusable:
            return (
                "partial",
                f"Native evidence not usable ({_unusable(unusable, reports)}): "
                f"{record.reason}",
                readable,
            )
    return record.state, record.reason, readable


def plan_against_actual(
    plan: SecurityPlan,
    *,
    plan_ref: str,
    identity: str,
    excluded: Iterable[ExcludedJob],
    records: Mapping[str, PlanJobRecord],
    reports: Mapping[str, str],
) -> dict[str, Any]:
    """Every planned job and requirement against what the run retained.

    A requirement is never ``met`` here: per-standard conclusions come from the
    framework evaluation, and planned evidence that did not materialize leaves
    the requirement inconclusive or not assessed.
    """
    removed = {item.job_id: item.reason for item in excluded}
    jobs = []
    states = {}
    for job in plan.jobs:
        state, reason, refs = job_outcome(
            job.id, excluded=removed, record=records.get(job.id), reports=reports
        )
        states[job.id] = (state, reason)
        record = records.get(job.id)
        row = {
            "jobId": job.id,
            "kind": job.kind,
            "state": state,
            "reason": reason,
            "reportRefs": refs,
        }
        if (
            record is not None
            and record.review_ref
            and state != "not_run"
            and reports.get(record.review_ref) == "readable"
        ):
            row["reviewRef"] = record.review_ref
        jobs.append(row)
    requirements = []
    for row in plan.requirements:
        closable_by = None
        if row.applicability == "not_applicable":
            state, reason = "not_applicable", row.reason
        elif any(job in removed for job in row.job_ids):
            state = "not_assessed"
            reason = "; ".join(
                f"job {job} not launched: {removed[job]}"
                for job in row.job_ids
                if job in removed
            )
        elif row.job_ids:
            pending = [
                f"job {job} {states[job][0]}: {states[job][1]}"
                for job in row.job_ids
                if job in states and states[job][0] != "completed"
            ]
            state = "inconclusive" if pending else "evidence_collected"
            reason = (
                "; ".join(pending)
                if pending
                else "Every planned job retained native evidence; the standard's conclusion is reported above."
            )
        elif any(gap.kind == "no_evidence_path" for gap in row.gaps):
            state = "not_assessed"
            reason = "; ".join(gap.detail for gap in row.gaps)
        elif row.gaps:
            state = "inconclusive"
            reason = "; ".join(
                gap.detail
                + (f" Next action: {gap.next_action}" if gap.next_action else "")
                for gap in row.gaps
            )
        elif "external_only" in row.methods and len(row.methods) == 1:
            state = "external_only"
            closable_by = row.external_party or "An outside party"
            reason = "Only an outside party can evidence this requirement."
        elif "document_review" in row.methods:
            state = "organisational"
            requests = ", ".join(row.evidence_request_ids)
            closable_by = "The operator" + (
                f", supplying {requests}" if requests else ""
            )
            reason = (
                "Organisational evidence decides this requirement; no job collects it."
            )
        elif row.methods or row.procedures:
            state = "review"
            reason = "The agent review and procedure results decide this requirement."
        else:
            state = "inconclusive"
            reason = "The plan records no evidence path for this requirement."
        item = {
            "packId": row.pack_id,
            "edition": row.edition,
            "requirementId": row.id,
            "state": state,
            "reason": reason[:2048],
            "jobIds": list(row.job_ids),
        }
        if closable_by:
            item["closableBy"] = closable_by
        requirements.append(item)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "planRef": plan_ref,
        "planIdentity": identity,
        "jobs": jobs,
        "requirements": requirements,
        "counts": {
            "jobs": dict(Counter(row["state"] for row in jobs)),
            "requirements": dict(Counter(row["state"] for row in requirements)),
        },
    }


def conclusion_reports(
    record: Mapping[str, Any], reports: Mapping[str, str]
) -> tuple[list[str], dict[str, str]]:
    """The reports one conclusion evaluates, and statuses naming any overflow.

    Readable reports are taken in plan job order up to the conclusion's bound;
    each one beyond it becomes ``unevaluated``, so its job and requirements are
    reported as not fully assessed instead of being silently truncated.
    """
    refs = list(
        dict.fromkeys(ref for job in record["jobs"] for ref in job["reportRefs"])
    )
    statuses = dict(reports)
    statuses.update({ref: "unevaluated" for ref in refs[MAX_CONCLUSION_REPORTS:]})
    return refs[:MAX_CONCLUSION_REPORTS], statuses


def render_plan_against_actual(record: Mapping[str, Any], *, ref: str | None) -> str:
    lines = [
        "",
        "## Plan against actual",
        "",
        f"Plan {_safe(record['planRef'])} (identity {record['planIdentity']}); "
        f"record {_safe(ref or 'not retained')}. Planned evidence that did not "
        "materialize leaves its requirement inconclusive or not assessed; zero findings "
        "never mean a requirement is met.",
        "",
        "### Planned jobs",
    ]
    lines += [
        f"- {_safe(row['jobId'])} ({_safe(row['kind'])}): {row['state']} — {_safe(row['reason'])}"
        + (f" Evidence: {', '.join(row['reportRefs'])}." if row["reportRefs"] else "")
        + (f" Review: {row['reviewRef']}." if row.get("reviewRef") else "")
        for row in record["jobs"]
    ] or ["- No planned jobs."]
    lines += ["", "### Requirements"]
    lines += [
        f"- {_safe(row['packId'])} {_safe(row['requirementId'])}: {row['state']} — "
        f"{_safe(row['reason'])}"
        + (
            f" Closable by: {_safe(row['closableBy'])}."
            if row.get("closableBy")
            else ""
        )
        for row in record["requirements"]
    ]
    return "\n".join(lines) + "\n"


def parse_job_record(raw: bytes) -> PlanJobRecord:
    return PlanJobRecord.model_validate(json.loads(raw))
