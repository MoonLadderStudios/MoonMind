"""``security.plan``: the deterministic steps of a security planning run.

``phase: context`` checks the admitted subject type and standards against the
saved environment snapshot and retains the planning context the planner reads.
``phase: retain`` reads the planner's draft, validates it with trusted code and
retains a ``security-plan/v1`` artifact with its readable plan and gap list. A
rejected draft stays retained with field-addressed diagnostics.

The tool holds no assessment scope, credential, egress grant or job
reservation, and launches no scanner, cloud, web or network job.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Annotated, Any, Literal, Mapping

from pydantic import Field, StringConstraints, ValidationError

from moonmind.security.assessment_projects import AssessmentProjectSnapshot
from moonmind.security.frameworks import FrameworkContract
from moonmind.security.security_plan import (
    SecurityPlanError,
    gap_list,
    plan_identity,
    render_security_plan,
)
from moonmind.security.security_plan_drafting import (
    WEB_API_PACK_ID,
    check_available,
    context_key,
    diagnostics_document,
    inline_context,
    parse_standards,
    plan_from_draft,
    plan_period,
    plan_standard,
    planning_context,
    render_diagnostics,
    require_subject_type,
    selection_id,
)
from moonmind.workflows.skills.security_assessment import AssessmentExecution, _json
from moonmind.workflows.skills.tool_plan_contracts import ToolFailure, ToolResult
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    ArtifactDependency,
    TemporalArtifactError,
)
from moonmind.workflows.temporal.report_artifacts import build_report_bundle_result

TOOL_NAME = "security.plan"
DRAFT_OUTPUT = "securityPlanDraftRef"
TRUSTED_SOURCE = "moonmind.security.plan_context"
ArtifactRef = Annotated[str, StringConstraints(pattern=r"^art_[A-Za-z0-9_-]+$")]


class SecurityPlanToolInput(FrameworkContract):
    """Inputs of one deterministic planning step; nothing here grants authority."""

    model_config = {
        **FrameworkContract.model_config,
        "json_schema_extra": {"x-assessment-project-defaults": True},
    }
    assessment_project: AssessmentProjectSnapshot | None = Field(
        default=None, json_schema_extra={"readOnly": True}
    )
    phase: Literal["context", "retain"]
    subject_type: str
    standards: Annotated[list[dict[str, Any]], Field(min_length=1, max_length=16)]
    # API callers may name a retained draft; a planning run carries the
    # planner's published draft in the previous step's outputs.
    draft_ref: ArtifactRef | None = None
    previous_outputs: dict[str, Any] | None = Field(
        default=None, json_schema_extra={"readOnly": True}
    )


class _Execution(AssessmentExecution):
    producer = TOOL_NAME


async def _saved_report(execution, key: str) -> str | None:
    """A completed readable report from this execution; reports are not JSON."""
    for item in await execution.service.list_for_execution(
        **execution.execution, link_type="report.primary", access=execution.access
    ):
        if (item.metadata_json or {}).get("subject") == key and getattr(
            item.status, "value", item.status
        ) == "complete":
            await execution.read(item.artifact_id)
            return item.artifact_id
    return None


def _failure(diagnostics) -> ToolFailure:
    text = "; ".join(list(dict.fromkeys(diagnostics))[:20])
    return ToolFailure("INVALID_INPUT", f"securityPlan: {text}", False)


def _web_api_pack_bytes() -> bytes:
    from moonmind.workflows.skills.resolver import resolve_skill_markdown_path

    skill = resolve_skill_markdown_path("security-review")
    if skill is None:
        raise ValueError("restore the installed security-review Skill's web/API pack")
    return (skill.parent / "references" / "web-api.json").read_bytes()


async def _resolve_standards(execution, selections, period, now):
    """Exact pack identity, bytes and availability for each admitted standard."""
    from moonmind.security.framework_registry import INSTALLED_PACK_IDS, installed_pack

    standards, packs, refs, errors = [], {}, [], []
    for index, selection in enumerate(selections):
        pack_id = selection_id(selection)
        declared = getattr(selection, "artifact_ref", None)
        try:
            if declared:
                chosen = selection
            elif pack_id in INSTALLED_PACK_IDS:
                _, installed = installed_pack(pack_id)
                chosen = SimpleNamespace(
                    pack_id=pack_id,
                    edition=selection.edition,
                    revision=installed.revision,
                    artifact_ref=None,
                    sha256=None,
                )
            elif pack_id == WEB_API_PACK_ID:
                # The Skill's pack is retained as bytes so the plan cites them.
                raw = _web_api_pack_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                saved = await execution.saved(
                    kind="security.framework-pack", key=digest
                )
                ref = (
                    saved[0]
                    if saved
                    else await execution.write(
                        raw, kind="security.framework-pack", key=digest
                    )
                )
                from moonmind.security.framework_packs import parse_declared_pack

                chosen = SimpleNamespace(
                    pack_id=pack_id,
                    edition=selection.edition,
                    revision=parse_declared_pack(raw).revision,
                    artifact_ref=ref,
                    sha256=digest,
                )
            else:
                errors.append(f"standards[{index}]: {pack_id} is not in the catalog")
                continue
            ref, digest, pack = await execution.load_selected_pack(chosen, index)
        except ToolFailure as exc:
            errors.append(
                f"standards[{index}]: {pack_id} is unavailable: {exc.message}"
            )
            continue
        except (OSError, ValueError, TemporalArtifactError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            errors.append(f"standards[{index}]: {pack_id} is unavailable: {reason}")
            continue
        try:
            check_available(index, pack, now, period)
            standard = plan_standard(
                selection,
                pack,
                sha256=digest,
                period=period,
                artifact_ref=chosen.artifact_ref,
            )
        except SecurityPlanError as exc:
            errors.extend(exc.diagnostics)
            continue
        standards.append(standard)
        packs[(pack.id, pack.edition)] = pack
        refs.append(ref)
    if errors:
        raise SecurityPlanError(errors)
    return standards, packs, refs


async def _context(execution, request, snapshot, subject_type, selections, now):
    """The retained planning context; an unchanged retry reuses it."""
    key = context_key(snapshot, subject_type, selections)
    saved = await execution.saved(kind="security.plan-context", key=key)
    period = plan_period(snapshot, now)
    if saved:
        document = saved[1]
        from moonmind.security.frameworks import EvidencePeriod

        period = EvidencePeriod.model_validate(document["standards"][0]["period"])
    standards, packs, pack_refs = await _resolve_standards(
        execution, selections, period, now
    )
    if saved:
        return saved[0], saved[1], standards, packs, pack_refs
    document = planning_context(
        snapshot, subject_type, [(s, packs[(s.pack_id, s.edition)]) for s in standards]
    )
    ref = await execution.write(_json(document), kind="security.plan-context", key=key)
    project_ref = await execution.project_snapshot_ref(snapshot)
    required = [*pack_refs, project_ref]

    async def dependencies():
        return [
            ArtifactDependency(
                item, await execution.digest(item), True, "security.plan-dependency"
            )
            for item in dict.fromkeys(required)
        ]

    await execution.seal(ref, dependencies)
    return ref, document, standards, packs, pack_refs


def _draft_ref(request: SecurityPlanToolInput) -> str | None:
    if request.draft_ref:
        return request.draft_ref
    previous = request.previous_outputs or {}
    value = previous.get(DRAFT_OUTPUT)
    return value if isinstance(value, str) and value.startswith("art_") else None


async def _retain(
    execution,
    request,
    snapshot,
    subject_type,
    context_ref,
    standards,
    packs,
    pack_refs,
    now,
):
    draft_ref = _draft_ref(request)
    if draft_ref is None:
        raise ToolFailure(
            "INVALID_INPUT",
            "securityPlan: the planning step produced no draft at "
            "artifacts/security-plan/draft.json; rerun planning to draft one.",
            False,
        )
    try:
        _, raw = await execution.read(draft_ref)
    except TemporalArtifactError as exc:
        raise ToolFailure(
            "INVALID_INPUT",
            f"securityPlan: the planner's draft is unreadable ({type(exc).__name__})",
            False,
        ) from None
    project_ref = await execution.project_snapshot_ref(snapshot)
    try:
        plan = plan_from_draft(
            raw,
            subject_type=subject_type,
            standards=standards,
            packs=packs,
            snapshot=snapshot,
            now=now,
        )
    except SecurityPlanError as exc:
        await _retain_rejection(execution, exc.diagnostics, draft_ref, raw)
        raise _failure(exc.diagnostics) from None
    identity = plan_identity(plan)
    saved = await execution.saved(kind="security.plan", key=identity)
    plan_ref = (
        saved[0]
        if saved
        else await execution.write(
            _json(plan.model_dump(mode="json", by_alias=True)),
            kind="security.plan",
            key=identity,
        )
    )
    required = [*pack_refs, project_ref, context_ref, draft_ref]

    async def dependencies():
        return [
            ArtifactDependency(
                item, await execution.digest(item), True, "security.plan-dependency"
            )
            for item in dict.fromkeys(required)
        ]

    await execution.seal(plan_ref, dependencies)
    gaps = gap_list(plan)
    report = (
        "# Security plan\n\n"
        f"Drafted for {snapshot.project_name} / {snapshot.environment_name}. "
        "No scanner, cloud, web or network job ran; launch planned work separately "
        "under a saved authorization.\n" + render_security_plan(plan, plan_ref=plan_ref)
    ).encode()
    report_key = hashlib.sha256(report).hexdigest()
    report_ref = await _saved_report(execution, report_key) or await execution.write(
        report, kind="report.primary", key=report_key, content_type="text/markdown"
    )

    async def report_dependencies():
        return [
            ArtifactDependency(
                plan_ref, await execution.digest(plan_ref), True, "security.plan"
            )
        ]

    await execution.seal(report_ref, report_dependencies)
    for ref, link_type in (
        (plan_ref, "report.structured"),
        (draft_ref, "report.evidence"),
    ):
        await execution.service.link_artifact(
            artifact_id=ref,
            principal=execution.principal,
            execution_ref={**execution.execution, "link_type": link_type},
        )
    counts = {
        "requirements": len(plan.requirements),
        "gaps": len(gaps),
        "jobs": len(plan.jobs),
        "openPoints": len(plan.open_points),
    }
    return {
        "securityPlanRef": plan_ref,
        "securityPlanIdentity": identity,
        "securityPlanGaps": len(gaps),
        "securityPlanDraftRef": draft_ref,
        "planningContextRef": context_ref,
        "assessmentProjectRef": project_ref,
        "counts": counts,
        "reportBundle": build_report_bundle_result(
            primary_report_ref={"artifact_id": report_ref},
            structured_ref={"artifact_id": plan_ref},
            evidence_refs=[{"artifact_id": draft_ref}],
            report_type="security_plan",
            report_scope="final",
            sensitivity="restricted",
            counts=counts,
        ),
        "summary": (
            f"Security plan retained: {counts['requirements']} requirements, "
            f"{counts['gaps']} gaps, {counts['jobs']} planned jobs, "
            f"{counts['openPoints']} open points. No assessment job ran."
        ),
    }


async def _retain_rejection(execution, diagnostics, draft_ref, raw):
    """Keep the rejected draft and why, so it can be diagnosed and corrected."""
    digest = hashlib.sha256(raw).hexdigest()
    document = diagnostics_document(
        diagnostics, draft_ref=draft_ref, draft_sha256=digest
    )
    payload = _json(document)
    key = hashlib.sha256(payload).hexdigest()
    saved = await execution.saved(kind="security.plan-diagnostics", key=key)
    ref = (
        saved[0]
        if saved
        else await execution.write(payload, kind="security.plan-diagnostics", key=key)
    )

    async def dependencies():
        return [
            ArtifactDependency(
                draft_ref,
                await execution.digest(draft_ref),
                True,
                "security.plan-draft",
            )
        ]

    await execution.seal(ref, dependencies)
    text = render_diagnostics(diagnostics, draft_ref=draft_ref).encode()
    text_key = hashlib.sha256(text).hexdigest()
    if not await _saved_report(execution, text_key):
        report = await execution.write(
            text, kind="report.primary", key=text_key, content_type="text/markdown"
        )
        await execution.seal(report, dependencies)
    for ref_id, link_type in (
        (ref, "report.structured"),
        (draft_ref, "report.evidence"),
    ):
        await execution.service.link_artifact(
            artifact_id=ref_id,
            principal=execution.principal,
            execution_ref={**execution.execution, "link_type": link_type},
        )


async def security_plan_handler(
    inputs: Mapping[str, Any], context: Mapping[str, Any] | None = None
) -> ToolResult:
    context = dict(context or {})
    try:
        request = SecurityPlanToolInput.model_validate(inputs)
    except ValidationError as exc:
        from moonmind.security.evidence_exports import validation_summary

        raise ToolFailure(
            "INVALID_INPUT",
            "Invalid security plan step. "
            + validation_summary(exc, SecurityPlanToolInput),
            False,
        ) from None
    required = ("namespace", "workflow_id", "run_id", "node_id")
    if any(not context.get(key) for key in required) or not context.get(
        "temporal_artifact_service"
    ):
        raise ToolFailure(
            "INVALID_INPUT",
            "Security planning requires its execution and artifact context.",
            False,
        )
    principal = context.get("deployment_evidence_principal")
    if not principal:
        raise ToolFailure(
            "POLICY_VIOLATION", "Security planning principal is unavailable.", False
        )
    snapshot = request.assessment_project
    if snapshot is None:
        raise ToolFailure(
            "INVALID_INPUT",
            "securityPlan: choose a saved assessment environment; a plan is drafted "
            "from its targets and exclusions.",
            False,
        )
    execution = _Execution(
        context["temporal_artifact_service"],
        principal=principal,
        context=context,
        access=ArtifactAccessContext.for_activity(
            principal="system:security-assessment"
        ),
    )
    now = datetime.now(UTC)
    try:
        subject_type = require_subject_type(request.subject_type)
        selections = parse_standards(request.standards)
        context_ref, document, standards, packs, pack_refs = await _context(
            execution, request, snapshot, subject_type, selections, now
        )
    except SecurityPlanError as exc:
        raise _failure(exc.diagnostics) from None
    if request.phase == "context":
        requirement_count = sum(len(s["requirements"]) for s in document["standards"])
        return ToolResult(
            status="COMPLETED",
            outputs={
                "trustedSource": TRUSTED_SOURCE,
                "planningContextRef": context_ref,
                "planningContext": inline_context(document),
                "summary": (
                    f"Planning context for {snapshot.project_name} / "
                    f"{snapshot.environment_name}: {len(document['standards'])} "
                    f"standards, {requirement_count} requirements."
                ),
            },
        )
    outputs = await _retain(
        execution,
        request,
        snapshot,
        subject_type,
        context_ref,
        standards,
        packs,
        pack_refs,
        now,
    )
    return ToolResult(status="COMPLETED", outputs=outputs)


def security_plan_tool_definition() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "Prepare a security plan's context from a saved assessment environment "
            "and selected standards, or validate, render and retain a drafted "
            "security-plan/v1. Holds no assessment scope, credential or egress."
        ),
        "inputs": {"schema": SecurityPlanToolInput.model_json_schema(by_alias=True)},
        "outputs": {"schema": {"type": "object", "additionalProperties": True}},
        "executor": {
            "activity_type": "mm.tool.execute",
            "selector": {"mode": "by_capability"},
        },
        "requirements": {"capabilities": ["sandbox"]},
        "policies": {
            "timeouts": {
                "start_to_close_seconds": 300,
                "schedule_to_close_seconds": 600,
            },
            "retries": {"max_attempts": 2},
        },
    }


def register_security_plan_tool_handler(dispatcher: Any) -> None:
    dispatcher.register_skill(skill_name=TOOL_NAME, handler=security_plan_handler)


__all__ = [
    "DRAFT_OUTPUT",
    "TOOL_NAME",
    "TRUSTED_SOURCE",
    "SecurityPlanToolInput",
    "register_security_plan_tool_handler",
    "security_plan_handler",
    "security_plan_tool_definition",
]
