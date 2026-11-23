"""Running a retained ``security-plan/v1``: derivation and plan against actual.

One owner turns a sealed plan into workflow work and reports what ran. The
derivation maps each planned job to its existing technical preset with the
plan's typed input, binds only the launch fields an operator names from the
saved authorization, and ends with one ``security.assess`` over the selected
standards. Nothing here admits a target: the derived workflow still needs the
rules-of-engagement review, and each job keeps its own admission, readiness,
budget and native evidence.

The concluding assessment reports every planned job and procedure against
what actually happened. Agent-recorded outcomes are untrusted: trusted code
checks their evidence and can only lower a claimed state, and a requirement
whose planned evidence did not materialize stays inconclusive.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable, Mapping
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints

from moonmind.security.assessments import PlannedJobRun, PlanRunInput, SkippedPlannedJob
from moonmind.security.frameworks import AssessmentText as Text
from moonmind.security.frameworks import FrameworkContract as Contract
from moonmind.security.security_plan import (
    SecurityPlan,
    SecurityPlanError,
    _safe,
    plan_identity,
)

#: Planned job kinds a plan run executes, as their existing technical preset.
#: Other kinds have no preset yet and are reported as not run with that reason.
PLAN_JOB_PRESETS: dict[str, str] = {
    "repository-scan": "security-code-supply-chain",
    "image-assessment": "security-image-sbom",
    "web-assessment": "security-web-api",
    "cloud-assessment": "security-aws-posture",
}
SCOPED_KINDS = frozenset({"web-assessment", "cloud-assessment"})
JobState = Literal[
    "completed",
    "partial",
    "failed",
    "blocked_admission",
    "blocked_readiness",
    "not_run",
]
JOB_STATES: tuple[str, ...] = JobState.__args__
OUTCOME_SCHEMA = "security-plan-job-outcome/v1"
#: Where a planned job's step records its outcome; the agent runtime publishes
#: these exact bytes for the concluding assessment.
OUTCOME_PATH = "artifacts/security-plan-run/{job_id}.json"
OUTCOME_LINK = "input.security_plan_job"
CONCLUSION_TITLE = "Conclude security plan run"
MAX_REPORT_REFS = 32

ArtifactRef = Annotated[str, StringConstraints(pattern=r"^art_[A-Za-z0-9_-]+$")]
Identifier = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_REF = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9.-]{0,127}$")]
Reason = Annotated[str, StringConstraints(min_length=1, max_length=500)]


class PlanRunSelection(Contract):
    """A retained plan, named by its artifact and content identity."""

    plan_ref: ArtifactRef
    identity: Sha256
    readiness_ref: ArtifactRef | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class ExcludedJob(Contract):
    """Planned work the operator removed before launch, and why."""

    job_id: Identifier
    reason: Reason = "Removed before launch."


class JobOutcome(Contract):
    """An agent step's record of one planned job. Untrusted until checked."""

    schema_version: Literal["security-plan-job-outcome/v1"] = OUTCOME_SCHEMA
    job_id: Identifier
    status: JobState
    reason: Text | None = None
    report_refs: Annotated[list[ArtifactRef], Field(max_length=8)] = Field(
        default_factory=list
    )
    review_ref: ArtifactRef | None = None


def _bind(kind: str, value: Mapping, *, assessment_ref, credential_refs, job_id):
    """The plan's job input with the launch-bound fields the operator named."""
    value = copy.deepcopy(dict(value))
    missing = []
    if kind in SCOPED_KINDS:
        if not assessment_ref:
            missing.append("assessment_ref")
        value["assessmentRef"] = assessment_ref
    purposes = {
        "cloud-assessment": ["cloud_inventory"],
        "web-assessment": (
            ["application_auth"] if isinstance(value.get("identity"), Mapping) else []
        ),
    }.get(kind, [])
    for purpose in purposes:
        ref = (credential_refs or {}).get(purpose)
        if not ref:
            missing.append(f"credential_refs.{purpose}")
        elif kind == "cloud-assessment":
            value["credentialRef"] = ref
        else:
            value["identity"] = {**value["identity"], "credentialRef": ref}
    if missing:
        raise SecurityPlanError(
            [
                f"{name}: planned job {job_id} needs it from the saved authorization; "
                "name it or exclude the job"
                for name in missing
            ]
        )
    return value


def pack_selections(plan: SecurityPlan) -> list[dict[str, Any]]:
    """The plan's standards as the multi-pack request selects them."""
    selections = []
    for standard in plan.standards:
        row = standard.model_dump(
            mode="json", by_alias=True, exclude={"title", "distribution"}
        )
        if standard.artifact_ref is None:
            # An installed pack is named by identity; the assessment retains it.
            row.pop("sha256", None)
        selections.append(row)
    return selections


def derive_plan_run(
    plan: SecurityPlan,
    *,
    selection: PlanRunSelection,
    assessment_ref: str | None = None,
    credential_refs: Mapping[str, str] | None = None,
    excluded: Iterable[ExcludedJob] = (),
    previous_assessment_ref: str | None = None,
) -> dict[str, Any]:
    """Derive a plan run: one existing preset per runnable job, then a conclusion.

    Returns ``{"jobs": [{jobId, kind, slug, inputs}], "conclusion": {...}}``.
    The conclusion's ``planRun.jobs[].stepIds`` are filled by the preset
    compiler once each job's preset has expanded. Excluded and unsupported
    jobs are carried to the conclusion with their reason.
    """
    if plan_identity(plan) != selection.identity:
        raise SecurityPlanError(
            ["securityPlan.identity: differs from the retained plan's content"]
        )
    known = {job.id for job in plan.jobs}
    removed = {}
    for item in excluded:
        if item.job_id not in known:
            raise SecurityPlanError(
                [f"exclude_jobs: {item.job_id} is not a job of this plan"]
            )
        removed[item.job_id] = item.reason
    jobs, skipped = [], []
    for job in plan.jobs:
        if job.id in removed:
            skipped.append(
                {
                    "jobId": job.id,
                    "kind": job.kind,
                    "state": "blocked_admission",
                    "reason": removed[job.id],
                }
            )
            continue
        slug = PLAN_JOB_PRESETS.get(job.kind)
        if slug is None:
            skipped.append(
                {
                    "jobId": job.id,
                    "kind": job.kind,
                    "state": "not_run",
                    "reason": f"A plan run cannot launch {job.kind} jobs yet; run it separately.",
                }
            )
            continue
        assessment = _bind(
            job.kind,
            job.input,
            assessment_ref=assessment_ref,
            credential_refs=credential_refs,
            job_id=job.id,
        )
        jobs.append(
            {
                "jobId": job.id,
                "kind": job.kind,
                "slug": slug,
                "inputs": {
                    "goal": (
                        f"Planned job {job.id} of security plan {selection.identity[:12]}: "
                        f"{job.kind} of {', '.join(job.assets)}. Collect and interpret "
                        "only this job's evidence."
                    ),
                    "assessment": assessment,
                },
            }
        )
    conclusion = {
        "scope": plan.subject.scope.model_dump(mode="json", by_alias=True),
        "tools": [],
        "budget": {"baseSeconds": 120, "maxSeconds": 300, "progressStallSeconds": 120},
        "credentialRefs": [],
        "networkRefs": [],
        "securityPlan": {
            "planRef": selection.plan_ref,
            **(
                {"readinessRef": selection.readiness_ref}
                if selection.readiness_ref
                else {}
            ),
        },
        "frameworkPacks": pack_selections(plan),
        "planRun": {
            "identity": selection.identity,
            "jobs": [
                {"jobId": job["jobId"], "kind": job["kind"], "stepIds": []}
                for job in jobs
            ],
            "skipped": skipped,
        },
    }
    if previous_assessment_ref:
        conclusion["previousAssessmentRef"] = previous_assessment_ref
    return {"jobs": jobs, "conclusion": conclusion}


def outcome_instructions(job_id: str) -> str:
    """Appended to a planned job's step so the conclusion can find its outcome."""
    path = OUTCOME_PATH.format(job_id=job_id)
    return (
        f"\n\nThis step runs planned job {job_id} of a security plan run. Before "
        f"finishing, write {path} as JSON: schemaVersion {OUTCOME_SCHEMA}, jobId "
        f"{job_id}, status (completed, partial, failed, blocked_admission or "
        "blocked_readiness), a reason unless completed, reportRefs naming this "
        "job's retained native summary artifacts and reviewRef naming its "
        "interpretation. A refused admission or blocked readiness is an outcome to "
        "record, not a reason to retry differently. Never record work that did not "
        "run as completed; the concluding assessment checks the evidence."
    )


# ---------------------------------------------------------------------------
# Plan against actual
# ---------------------------------------------------------------------------


def _job_state(job, outcome: JobOutcome | None, checked: Mapping[str, bool]):
    """The actual state of one planned job, never higher than its evidence."""
    if outcome is None:
        return {
            "state": "not_run",
            "reason": "No outcome was recorded: the step failed before recording, "
            "was cancelled or removed, or did not run.",
            "reportRefs": [],
        }
    valid = [ref for ref in outcome.report_refs if checked.get(ref)]
    invalid = [ref for ref in outcome.report_refs if not checked.get(ref)]
    state, reason = outcome.status, outcome.reason
    if state in {"completed", "partial"} and not valid:
        state = "failed"
        reason = (
            "No readable native summary of the expected kind was retained"
            + (f" ({len(invalid)} unusable)" if invalid else "")
            + "."
        )
    elif state == "completed" and invalid:
        state = "partial"
        reason = f"{len(invalid)} recorded native report(s) are unreadable or of another kind."
    if state != "completed" and not reason:
        reason = "The step recorded no reason."
    return {
        "state": state,
        **({"reason": reason} if state != "completed" else {}),
        "reportRefs": valid,
        **({"reviewRef": outcome.review_ref} if outcome.review_ref else {}),
    }


def plan_against_actual(
    plan: SecurityPlan,
    run: PlanRunInput,
    outcomes: Mapping[str, JobOutcome],
    checked_refs: Mapping[str, bool],
) -> dict[str, Any]:
    """Every planned job, procedure and requirement against what ran.

    ``outcomes`` maps a planned job ID to the outcome its own step recorded;
    ``checked_refs`` says which report refs are readable native summaries of
    the job's kind. A requirement is never concluded here: it is
    ``evidence_collected`` only when all its planned jobs completed, and
    inconclusive otherwise. Pack evaluation owns conclusions per standard.
    """
    jobs = []
    states = {}
    skipped = {item.job_id: item for item in run.skipped}
    launched = {item.job_id: item for item in run.jobs}
    for job in plan.jobs:
        row = {"jobId": job.id, "kind": job.kind}
        if job.id in skipped:
            item = skipped[job.id]
            row.update(state=item.state, reason=item.reason, reportRefs=[])
        elif job.id in launched:
            row.update(
                stepIds=list(launched[job.id].step_ids),
                **_job_state(job, outcomes.get(job.id), checked_refs),
            )
        else:
            row.update(
                state="not_run",
                reason="The reviewed workflow does not include this job.",
                reportRefs=[],
            )
        states[job.id] = row
        jobs.append(row)
    requirements, procedures, gaps = [], [], []
    for req in plan.requirements:
        key = {"packId": req.pack_id, "edition": req.edition, "requirementId": req.id}
        for procedure in req.procedures:
            procedures.append(
                {
                    **key,
                    "procedureId": procedure.id,
                    "state": "not_run",
                    "reason": "No procedure results are recorded by a plan run; "
                    "run the web/API methodology to evidence it.",
                }
            )
        if req.applicability == "not_applicable":
            requirements.append(
                {**key, "status": "not_applicable", "reason": req.reason}
            )
            continue
        planned = [states[j] for j in req.job_ids if j in states]
        reasons = [
            f"job {row['jobId']} {row['state']}: {row.get('reason', '')}".rstrip(": ")
            for row in planned
            if row["state"] != "completed"
        ]
        if req.procedures:
            reasons.append("planned procedures did not run")
        external = "external_only" in req.methods
        organisational = "document_review" in req.methods
        if planned and not reasons:
            status = "evidence_collected"
        elif planned or req.procedures:
            status = "inconclusive"
        elif external:
            status = "external_only"
        elif organisational:
            status = "organisational_evidence"
        else:
            status = "inconclusive"
            reasons.append("no planned evidence path")
        row = {**key, "status": status}
        if reasons:
            row["reason"] = "; ".join(reasons)
        requirements.append(row)
        if external:
            gaps.append(
                {
                    **key,
                    "kind": "external_only",
                    "closableBy": req.external_party or "An external assessor",
                }
            )
        if organisational and req.evidence_request_ids:
            gaps.append(
                {
                    **key,
                    "kind": "organisational_evidence",
                    "closableBy": "The operator, by supplying the requested records",
                    "evidenceRequestIds": list(req.evidence_request_ids),
                }
            )
        gaps.extend(
            {
                **key,
                "kind": gap.kind,
                "detail": gap.detail,
                **({"nextAction": gap.next_action} if gap.next_action else {}),
            }
            for gap in req.gaps
        )
    counts = {
        state: sum(1 for row in jobs if row["state"] == state) for state in JOB_STATES
    }
    return {
        "schemaVersion": "security-plan-run/v1",
        "identity": run.identity,
        "jobs": jobs,
        "procedures": procedures,
        "requirements": requirements,
        "gaps": gaps,
        "counts": {key: value for key, value in counts.items() if value},
    }


def render_plan_against_actual(result: Mapping[str, Any]) -> str:
    """The readable plan-against-actual section of the primary report."""
    lines = [
        "",
        "## Plan against actual",
        "",
        "Every planned job and procedure, and whether its planned evidence materialized. "
        "Zero findings never means a requirement is met; a requirement whose planned "
        "evidence is missing stays inconclusive.",
        "",
        "### Planned jobs",
    ]
    for row in result["jobs"]:
        reason = f" — {_safe(row['reason'])}" if row.get("reason") else ""
        lines.append(
            f"- {_safe(row['jobId'])} ({_safe(row['kind'])}): {row['state']}{reason}"
        )
    if result["procedures"]:
        lines += ["", "### Planned procedures"]
        lines += [
            f"- {_safe(row['packId'])} {_safe(row['requirementId'])} "
            f"{_safe(row['procedureId'])}: {row['state']} — {_safe(row['reason'])}"
            for row in result["procedures"]
        ]
    lines += ["", "### Requirements"]
    for row in result["requirements"]:
        reason = f" — {_safe(row['reason'])}" if row.get("reason") else ""
        lines.append(
            f"- {_safe(row['packId'])} {_safe(row['requirementId'])}: {row['status']}{reason}"
        )
    if result.get("limitations"):
        lines += ["", "### Limitations"]
        lines += [f"- {_safe(item)}" for item in result["limitations"]]
    if result["gaps"]:
        lines += ["", "### Gaps carried into this report"]
        for row in result["gaps"]:
            who = (
                f"; closable by {_safe(row['closableBy'])}"
                if row.get("closableBy")
                else ""
            )
            detail = f": {_safe(row['detail'])}" if row.get("detail") else ""
            lines.append(
                f"- {_safe(row['packId'])} {_safe(row['requirementId'])} "
                f"{row['kind']}{detail}{who}"
            )
    return "\n".join(lines) + "\n"


def parse_outcome(raw: bytes) -> JobOutcome | None:
    """An agent-recorded outcome, or ``None`` when it is not one."""
    try:
        return JobOutcome.model_validate(json.loads(raw))
    except (ValueError, TypeError):
        return None


__all__ = [
    "CONCLUSION_TITLE",
    "JOB_STATES",
    "OUTCOME_LINK",
    "OUTCOME_PATH",
    "OUTCOME_SCHEMA",
    "PLAN_JOB_PRESETS",
    "ExcludedJob",
    "JobOutcome",
    "PlanRunInput",
    "PlanRunSelection",
    "PlannedJobRun",
    "SkippedPlannedJob",
    "derive_plan_run",
    "outcome_instructions",
    "pack_selections",
    "parse_outcome",
    "plan_against_actual",
    "render_plan_against_actual",
]
