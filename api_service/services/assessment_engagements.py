"""Compile readable assessment proposals at the existing operator boundary."""

from __future__ import annotations

import copy
import hashlib
import html
import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from moonmind.security.assessment_authorizations import (
    AssessmentAuthorization,
    authorization_scope,
    scope_digest,
)
from moonmind.security.operator_admission import OperatorAdmission

AUTHORIZATION_KEY = "assessmentAuthorizationRef"
REVIEW_KEY = "assessmentReview"
AUTHORITY_KEYS = (AUTHORIZATION_KEY, REVIEW_KEY, "assessmentScope")


def _denied(message: str, *, code="assessment_review_required", status=409):
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _reject_parameter_envelope(parameters: Mapping[str, Any]) -> None:
    if "initialParameters" in parameters or "initial_parameters" in parameters:
        raise _denied(
            "Workflow parameters cannot contain a nested direct execution envelope.",
            status=422,
        )


def _parameter_document(payload: Mapping[str, Any]) -> dict[str, Any]:
    plan = copy.deepcopy(dict(payload))
    if "initialParameters" in plan or "initial_parameters" in plan:
        if "workflow" in plan or "task" in plan:
            raise _denied(
                "Provide a direct execution envelope or workflow parameters, not both.",
                status=422,
            )
        from moonmind.schemas.temporal_models import CreateExecutionRequest

        try:
            request = CreateExecutionRequest.model_validate(plan)
        except (TypeError, ValueError) as exc:
            raise _denied(
                "Provide a valid direct execution request to review.", status=422
            ) from exc
        plan = request.model_dump(
            mode="json", by_alias=True, exclude_none=True, exclude_unset=True
        )
        initial = plan.pop("initialParameters", {})
        _reject_parameter_envelope(initial)
        plan = {**dict(initial), **plan}
    return plan


def _plan(payload: Mapping[str, Any]) -> dict[str, Any]:
    plan = _parameter_document(payload)
    plan.setdefault("workflowType", "MoonMind.UserWorkflow")
    for key in AUTHORITY_KEYS:
        plan.pop(key, None)
    if "task" in plan:
        if "workflow" in plan:
            raise _denied("Provide only one workflow plan.", status=422)
        plan["workflow"] = plan.pop("task")
    # Transport identity does not change the proposed actions. Artifact IDs,
    # targets, instructions, runtime choices and authored inputs do.
    plan.pop("idempotencyKey", None)
    if isinstance(plan.get("workflow"), dict):
        plan["workflow"].pop("idempotencyKey", None)
    if plan.get("assessmentProject") is None:
        plan.pop("assessmentProject", None)
    return plan


def assessment_edit_material(
    record, patch, *, input_artifact_ref=None, plan_artifact_ref=None
):
    """One effective input owner for scoped preview and changed-plan admission.

    Explicit patches replace canonical parameter fields. Omitted artifacts and
    source parameters remain part of what will execute, so they are reviewed too.
    Workspace admission may subsequently add workspaceSpec; it does not change
    this authored proposal, and cannot introduce target authority.
    """
    material = copy.deepcopy(dict(getattr(record, "parameters", None) or {}))
    material.update(copy.deepcopy(dict(patch or {})))
    _reject_parameter_envelope(material)
    from moonmind.workflows.executions.execution_contract import (
        workflow_payload_from_parameters,
    )

    selected = workflow_payload_from_parameters(material)
    if selected:
        material["workflow"] = copy.deepcopy(selected)
        material.pop("task", None)
    for key, explicit, attr in (
        ("inputArtifactRef", input_artifact_ref, "input_ref"),
        ("planArtifactRef", plan_artifact_ref, "plan_ref"),
    ):
        value = explicit or getattr(record, attr, None)
        if value:
            material[key] = value
        else:
            material.pop(key, None)
    manifest = getattr(record, "manifest_ref", None)
    if manifest:
        material["manifestArtifactRef"] = manifest
    else:
        material.pop("manifestArtifactRef", None)
    workflow_type = getattr(record, "workflow_type", "MoonMind.UserWorkflow")
    material["workflowType"] = getattr(workflow_type, "value", workflow_type)
    material["assessmentSourceExecution"] = record.workflow_id
    if getattr(record, "run_id", None):
        material["assessmentSourceRun"] = record.run_id
    return material


def _review_text(value: str) -> str:
    """Keep untrusted case content literal in text and Markdown review surfaces."""
    value = html.escape(value, quote=False).replace("\n", "\\n").replace("\r", "\\r")
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", value)


def build_assessment_proposal(
    payload: Mapping[str, Any], record: AssessmentAuthorization
) -> dict[str, Any]:
    scope = authorization_scope(record)
    scope_json = scope.model_dump(mode="json", by_alias=True, exclude_none=True)
    digest = scope_digest(scope)
    plan = _plan(payload)
    fingerprint = hashlib.sha256(
        json.dumps(
            {"scopeDigest": digest, "plan": plan},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    targets, exclusions = [], list(scope.exclusions)
    realtime_operations = []
    for target in scope.targets:
        if target.web_origin:
            targets.extend(
                f'{target.web_origin}{route.path}: {", ".join(route.methods)}'
                for route in target.web_routes
            )
            exclusions.extend(
                f"{target.web_origin}{path}" for path in target.web_exclusions
            )
            for path, messages in target.web_messages.items():
                targets.append(
                    f'{target.web_origin}{path}: messages {", ".join(messages)}'
                )
            if target.realtime is not None:
                for case in target.realtime.cases:
                    identity = case.credential_ref or "anonymous"
                    negotiation = (
                        f"; negotiation POST {case.negotiation_path}"
                        if case.negotiation_path
                        else ""
                    )
                    realtime_operations.append(
                        _review_text(
                            f"Case {case.case_ref}: {case.protocol} GET "
                            f"{target.web_origin}{case.path}{negotiation}; "
                            f"identity {identity}; effect {case.effect}; "
                            f"message type {case.message_type}; exact payload "
                            + json.dumps(case.payload, sort_keys=True, ensure_ascii=True)
                        )
                    )
                caps = target.realtime.model_dump(
                    mode="json", by_alias=True, exclude={"cases"}
                )
                realtime_operations.append(
                    _review_text(
                        f"{target.web_origin}: transport caps "
                        + json.dumps(caps, sort_keys=True)
                    )
                )
        elif target.native_network is not None:
            targets.append(_review_text(
                "Native TCP CONNECT authority (IPv4 only): "
                + json.dumps(target.native_network.model_dump(mode="json", by_alias=True), sort_keys=True)
                + "; connections, bytes, connection rate and concurrency are conservatively reserved across all jobs without refunds; duration is per job."
            ))
        elif target.cloud_account:
            targets.append(
                f'{target.cloud_provider} account {target.cloud_account}; regions {", ".join(target.cloud_regions)}'
            )
        elif target.artifact_ref:
            targets.append(f"Retained input artifact {target.artifact_ref}")
        else:
            targets.append(f"Captured workspace path {target.workspace_path}")
    project = plan.get("assessmentProject")
    config = project.get("configuration", {}) if isinstance(project, dict) else {}
    evidence = [scope.evidence_intent]
    if config.get("dataClassification"):
        evidence.append(f'Classification: {config["dataClassification"]}')
    if config.get("retentionDays"):
        evidence.append(
            f'Requested retention: {config["retentionDays"]} days; the artifact owner enforces retention.'
        )
    workflow = plan.get("workflow") or {}
    plan_items = []
    if isinstance(workflow, Mapping):
        if workflow.get("title"):
            plan_items.append(str(workflow["title"]))
        for step in workflow.get("steps") or ():
            if isinstance(step, Mapping):
                tool = step.get("tool") or step.get("skill") or {}
                plan_items.append(
                    str(
                        step.get("title")
                        or (
                            tool.get("id") or tool.get("name")
                            if isinstance(tool, Mapping)
                            else tool
                        )
                        or step.get("id")
                        or "Workflow step"
                    )
                )
    plan_items.extend(
        f"{key}: {plan[key]}"
        for key in (
            "inputArtifactRef",
            "planArtifactRef",
            "manifestArtifactRef",
            "assessmentSourceExecution",
            "assessmentSourceRun",
        )
        if plan.get(key)
    )
    sections = [
        (
            "Workflow plan",
            plan_items
            or ["The exact submitted workflow inputs are bound to this proposal."],
        ),
        (
            "Authority",
            [
                f"{record.authority_kind}: {record.authority_source}",
                f"Authorization {record.ref}; assessment {scope.assessment_ref}",
            ],
        ),
        ("Targets", targets),
        ("Exclusions", exclusions or ["No additional exclusions recorded."]),
        (
            "Actions",
            [
                f'Profiles: {", ".join(scope.profile_refs)}',
                f'Action classes: {", ".join(scope.action_classes)}',
                f'Configuration exceptions: {", ".join(scope.configuration_exceptions) or "None"}',
            ],
        ),
        (
            "Identities",
            [
                f"{grant.ref}: {grant.purpose}, account/identity {grant.account}, expires {grant.expires_at.isoformat()}"
                for grant in scope.grants
            ]
            or ["No workload credential grants."],
        ),
        (
            "Network reach",
            list(scope.network_refs) or ["No network policy is admitted."],
        ),
        (
            "Rate and resource limits",
            [
                f"At most {scope.max_jobs} jobs across sessions and reruns.",
                f"Each job: {scope.max_job_seconds} seconds, {scope.max_cpu_millis} millicores, {scope.max_memory_mib} MiB.",
                f"Cumulative reserved job time: {record.max_total_job_seconds} seconds; retries do not renew reservations.",
                (
                    f"Web/TLS request rate per job: {scope.max_requests_per_second}/second."
                    if scope.max_requests_per_second
                    else "No separate web/TLS request rate is admitted."
                ),
            ],
        ),
        (
            "Validity",
            [
                f"{record.valid_from.isoformat()} through {record.valid_until.isoformat()}; retries and scheduled occurrences retain this window."
            ],
        ),
        ("Evidence handling", evidence),
        (
            "Stop conditions",
            [
                "Authorization is revoked, removed or materially changed.",
                "The testing window ends, the original job deadline passes, or jobs/seconds are exhausted.",
                "The operator cancels the workflow or job; completed evidence remains available.",
            ],
        ),
    ]
    if realtime_operations:
        sections.insert(3, ("Real-time operations", realtime_operations))
    return {
        "schemaVersion": "assessment-engagement/v1",
        "authorizationRef": record.ref,
        "authorizationGeneration": record.generation,
        "scopeDigest": digest,
        "proposalDigest": fingerprint,
        "scope": scope_json,
        "sections": [{"label": label, "items": items} for label, items in sections],
        "limitations": [
            "This authorization covers assessment only. It does not authorize publishing fixes or changing infrastructure.",
            "DNS/file challenges show technical control and cloud caller identity shows a principal; neither proves every testing permission.",
            "Saved or discovered assets remain candidates unless included in these admitted targets. Network reach does not itself authorize an action.",
            "The authority source is operator-recorded, not independently verified by MoonMind. Per-job rate and resource ceilings do not promise an aggregate traffic rate.",
        ],
    }


def validate_reviewed_proposal(
    payload: Mapping[str, Any], record: AssessmentAuthorization
) -> dict[str, Any]:
    expected = build_assessment_proposal(payload, record)
    supplied = payload.get(REVIEW_KEY)
    if not isinstance(supplied, Mapping) or json.dumps(
        dict(supplied), sort_keys=True, separators=(",", ":")
    ) != json.dumps(expected, sort_keys=True, separators=(",", ":")):
        raise _denied(
            "The assessment plan or authorization changed. Review the current proposal before starting."
        )
    selected = payload.get(AUTHORIZATION_KEY)
    if selected is not None and selected != record.ref:
        raise _denied("The selected assessment authorization changed. Review again.")
    return expected


async def compile_assessment_proposal(
    payload, authorization_ref, *, session, user, access
):
    from api_service.services.assessment_authorizations import (
        AssessmentAuthorizationDenied,
        load_assessment_authorization,
    )
    from api_service.services.assessment_projects import (
        validate_assessment_project_parameter,
    )
    from api_service.auth_providers import instance_operator_principal

    if not isinstance(user, OperatorAdmission):
        raise _denied("Only the operator can review assessment authority.", status=403)
    if not isinstance(payload, Mapping):
        raise _denied("Provide a workflow payload to review.", status=422)
    parameters = _parameter_document(payload)
    material = _plan(parameters)
    if not isinstance(material.get("workflow"), Mapping) and not any(
        material.get(key)
        for key in ("inputArtifactRef", "planArtifactRef", "manifestArtifactRef")
    ):
        raise _denied(
            "Provide the complete workflow or immutable input artifact to review.",
            status=422,
        )
    selected = parameters.get(AUTHORIZATION_KEY)
    if selected is not None and selected != authorization_ref:
        raise _denied(
            "The selected assessment authorization is inconsistent.", status=422
        )
    try:
        record = await load_assessment_authorization(session, authorization_ref)
    except AssessmentAuthorizationDenied as exc:
        raise _denied(
            str(exc), code="assessment_authorization_denied", status=403
        ) from exc
    # The same reference owner validates candidate context on preview and start.
    await validate_assessment_project_parameter(
        parameters,
        session=session,
        principal=instance_operator_principal(),
        access=access,
    )
    return build_assessment_proposal(parameters, record)


async def admit_assessment_parameters(
    parameters, *, session, user, access, principal=None, plan_overrides=None
):
    """New authority requires an exact review and the real operator boundary."""
    if not any(parameters.get(key) is not None for key in AUTHORITY_KEYS):
        return None
    if not isinstance(user, OperatorAdmission) or getattr(
        principal, "is_workflow_principal", False
    ):
        raise _denied(
            "Only the operator can admit an assessment.",
            code="assessment_scope_denied",
            status=403,
        )
    ref = parameters.get(AUTHORIZATION_KEY)
    if not isinstance(ref, str) or not ref:
        raise _denied(
            "Select a saved assessment authorization and review its proposal before starting. Existing admitted executions retain their recorded authority."
        )
    # Callers pass actual parameters separately from transport metadata. A
    # recurring target may supply a complete direct envelope as its override;
    # normalize that owner before merging instead of creating a mixed shape.
    _reject_parameter_envelope(parameters)
    material = {**parameters, **_parameter_document(plan_overrides or {})}
    proposal = await compile_assessment_proposal(
        material, ref, session=session, user=user, access=access
    )
    if parameters.get(REVIEW_KEY) != proposal:
        raise _denied(
            "The assessment plan or authorization changed. Review the current proposal before starting."
        )
    scope = proposal["scope"]
    if datetime.now(timezone.utc) >= datetime.fromisoformat(
        scope["engagement"]["validUntil"].replace("Z", "+00:00")
    ):
        raise _denied(
            "The assessment authorization window has expired.",
            code="assessment_authorization_denied",
            status=403,
        )
    parameters["assessmentScope"] = scope
    parameters[REVIEW_KEY] = {
        **proposal,
        "admission": {
            "via": user.via,
            "admittedAt": datetime.now(timezone.utc).isoformat(),
        },
    }
    return scope


async def validate_retained_assessment(parameters, *, session):
    """Preserve original scope/window on equivalent reruns; never re-admit it."""
    from api_service.services.assessment_authorizations import (
        AssessmentAuthorizationDenied,
        validate_current_assessment_authorization,
    )
    from moonmind.security.workload_profiles import AdmittedAssessmentScope

    value = parameters.get("assessmentScope")
    if value is None:
        return
    scope = AdmittedAssessmentScope.model_validate(value)
    if scope.engagement is not None:
        try:
            await validate_current_assessment_authorization(session, scope)
        except AssessmentAuthorizationDenied as exc:
            raise _denied(
                str(exc), code="assessment_authorization_denied", status=403
            ) from exc
