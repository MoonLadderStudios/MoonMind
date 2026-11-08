"""Resolve reusable context using existing Settings and current reference owners."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from pydantic import Field

from api_service.services.repository_connections import RepositoryConnectionService
from api_service.services.secrets import SecretsService
from api_service.services.settings_catalog import SettingsCatalogService
from moonmind.security.assessment_projects import (
    SETTINGS_KEY,
    AssessmentProjectSnapshot,
    Identifier,
    resolve_assessment_project,
)
from moonmind.security.frameworks import FrameworkContract
from moonmind.workflows.executions.execution_contract import (
    snapshot_workflow_from_artifact_payload,
)
from moonmind.workflows.executions.repository_contract import RepositoryRouteError
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    TemporalArtifactError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)


class AssessmentProjectResolveRequest(FrameworkContract):
    project_id: Identifier
    environment_id: Identifier
    expected_version: int = Field(ge=0)
    overrides: dict[str, Any] = Field(default_factory=dict)


def _validate_snapshot(value: Any) -> AssessmentProjectSnapshot:
    try:
        snapshot = AssessmentProjectSnapshot.model_validate(value)
        # Reuse the typed Settings value and secret guards for standalone
        # authored snapshots too. A hash proves intact data, not permission.
        document = {
            "projects": [
                {
                    "id": snapshot.project_id,
                    "name": snapshot.project_name,
                    "purpose": snapshot.purpose,
                    "boundaries": snapshot.boundaries,
                    "environments": [
                        snapshot.configuration.model_dump(mode="json", by_alias=True)
                    ],
                }
            ]
        }
        service = SettingsCatalogService()
        issues = service._validation_issues_for_value(
            service._entries_by_key[SETTINGS_KEY], document, boundary="write_request"
        )
        if issues:
            raise ValueError("invalid project data")
        return snapshot
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_assessment_project",
                "message": "Assessment context is invalid or changed. Resolve the selected environment and overrides again.",
            },
        ) from exc


def _references(snapshot: AssessmentProjectSnapshot):
    config = snapshot.configuration
    refs = [
        (f"artifactRefs[{index}]", "artifact", ref)
        for index, ref in enumerate(config.artifact_refs)
    ]
    refs += [
        (f"credentialRefs[{index}]", "credential", ref)
        for index, ref in enumerate(config.credential_refs)
    ]
    refs += [
        (f"connectionRefs[{index}]", "connection", ref)
        for index, ref in enumerate(config.connection_refs)
    ]
    refs += [
        (f"repositories[{index}].connectionRef", "connection", repo.connection_ref)
        for index, repo in enumerate(config.repositories)
        if repo.connection_ref
    ]
    refs += [
        (
            f"cloudAccountCandidates[{index}].credentialRef",
            "credential",
            account.credential_ref,
        )
        for index, account in enumerate(config.cloud_account_candidates)
        if account.credential_ref
    ]
    return refs


async def assessment_project_gaps(
    snapshot: AssessmentProjectSnapshot,
    *,
    session,
    access: ArtifactAccessContext,
    principal: str,
) -> list[dict[str, str]]:
    """Metadata readiness only; no credentials, remote probes or new grants.

    Actual operations still resolve their own current access. The snapshot is
    a candidate configuration, not permission or a promise of future access.
    """
    gaps = []
    refs = _references(snapshot)
    if refs and session is None:
        return [
            {
                "field": field,
                "code": "reference_check_unavailable",
                "message": "Current reference access could not be checked. Retry when Settings storage is available.",
            }
            for field, _, _ in refs
        ]
    artifacts = None
    connections = None
    checked: dict[tuple[str, str], bool] = {}
    for field, kind, ref in refs:
        key = (kind, ref)
        if key not in checked:
            valid = False
            if kind == "artifact":
                try:
                    artifacts = artifacts or TemporalArtifactService(
                        TemporalArtifactRepository(session)
                    )
                    artifact, _, _, policy = await artifacts.get_metadata(
                        artifact_id=ref, access=access
                    )
                    valid = policy.raw_access_allowed
                    if valid:
                        await artifacts.validate_dependency_availability(
                            artifact, access=access
                        )
                except TemporalArtifactError:
                    valid = False
            elif kind == "credential":
                result = await SecretsService.validate_secret_ref(
                    session, ref.removeprefix("db://")
                )
                valid = result.get("valid") is True
            else:
                try:
                    connections = connections or RepositoryConnectionService(session)
                    selected = await connections.get_connection(
                        ref, principal_ref=principal, principal_scope=("system", None)
                    )
                    valid = selected is not None
                except RepositoryRouteError:
                    valid = False
            checked[key] = valid
        if not checked[key]:
            messages = {
                "artifact": "Selected evidence is unavailable or not readable. Choose accessible retained evidence.",
                "credential": "Selected credential is missing or inactive. Repair that reference in Secrets; no alternative was selected.",
                "connection": "Selected connection is unavailable or not permitted. Repair that connection in Settings; no alternative was selected.",
            }
            gaps.append(
                {
                    "field": field,
                    "code": f"{kind}_unavailable",
                    "message": messages[kind],
                }
            )
    return gaps


async def validate_assessment_project_snapshot(
    value: Any, *, session, access: ArtifactAccessContext, principal: str
) -> dict[str, Any]:
    snapshot = _validate_snapshot(value)
    gaps = await assessment_project_gaps(
        snapshot, session=session, access=access, principal=principal
    )
    if gaps:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "assessment_project_setup_gaps",
                "message": "Resolve the selected assessment reference gaps before starting work.",
                "gaps": gaps,
            },
        )
    return snapshot.model_dump(mode="json", by_alias=True)


async def resolve_saved_assessment_project(
    request: AssessmentProjectResolveRequest,
    *,
    session,
    access: ArtifactAccessContext,
    principal: str,
) -> dict[str, Any]:
    current = await SettingsCatalogService(session=session).effective_value_async(
        SETTINGS_KEY
    )
    if current.value_version != request.expected_version:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "assessment_project_changed",
                "message": "Saved assessment projects changed. Reload and review the selected environment again.",
            },
        )
    try:
        snapshot = resolve_assessment_project(
            current.value,
            project_id=request.project_id,
            environment_id=request.environment_id,
            version=current.value_version,
            overrides=request.overrides,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_assessment_selection",
                "message": "Select an existing project and environment with valid overrides.",
            },
        ) from exc
    snapshot = _validate_snapshot(snapshot)
    gaps = await assessment_project_gaps(
        snapshot, session=session, access=access, principal=principal
    )
    return {"snapshot": snapshot.model_dump(mode="json", by_alias=True), "gaps": gaps}


def assessment_project_input_metadata(workflow: Mapping[str, Any]) -> list[Any]:
    from moonmind.security.assessment_projects import (
        ASSESSMENT_PROJECT_INPUT_KEYS,
        assessment_input_selectors,
    )

    values = []
    try:
        for step, tool in assessment_input_selectors(workflow):
            for inputs in (step.get("inputs"), tool.get("inputs"), tool.get("args")):
                if isinstance(inputs, Mapping):
                    for key in ASSESSMENT_PROJECT_INPUT_KEYS:
                        if inputs.get(key) is not None:
                            values.append(inputs[key])
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_assessment_project", "message": str(exc)},
        ) from exc
    return values


def _authored_workflow_metadata(parameters: Mapping[str, Any]) -> list[Any]:
    # Inspect both aliases, even an empty preferred workflow. Consumers may
    # fall back to the retained task spelling, so it cannot hide context.
    metadata = []
    for key in ("workflow", "task"):
        workflow = parameters.get(key)
        if not isinstance(workflow, Mapping):
            continue
        if workflow.get("steps") is not None and not isinstance(
            workflow["steps"], list
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_execution_request",
                    "message": f"payload.{key}.steps must be a JSON array.",
                },
            )
        metadata.extend(assessment_project_input_metadata(workflow))
    return metadata


def validate_assessment_project_input_artifact(
    artifact_payload: Any,
    snapshot: dict[str, Any] | None,
) -> None:
    if not isinstance(artifact_payload, Mapping):
        return
    payload, _workflow = snapshot_workflow_from_artifact_payload(artifact_payload)
    draft = artifact_payload.get("draft")
    source = draft if isinstance(draft, Mapping) else artifact_payload
    if payload.get("assessmentProject") != snapshot or any(
        value != snapshot for value in _authored_workflow_metadata(source)
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_assessment_project",
                "message": (
                    "Input artifact assessment context differs from the admitted "
                    "payload.assessmentProject (or initialParameters.assessmentProject). "
                    "Rebuild the input artifact from the reviewed draft."
                ),
            },
        )


async def validate_assessment_project_parameter(
    parameters: Mapping[str, Any],
    *,
    session,
    principal: str,
    access: ArtifactAccessContext,
) -> dict[str, Any] | None:
    """Check frozen context and current references, never resolve saved defaults."""
    metadata = _authored_workflow_metadata(parameters)
    if "assessmentProject" not in parameters and metadata:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_assessment_project",
                "message": (
                    "Assessment context must be admitted at the execution boundary. "
                    "Supply payload.assessmentProject (or initialParameters.assessmentProject "
                    "for serialized requests), not Skill input metadata."
                ),
            },
        )
    value = parameters.get("assessmentProject")
    if value is None:
        return None
    return await validate_assessment_project_snapshot(
        value, session=session, access=access, principal=principal
    )


def apply_assessment_project_defaults(
    workflow: dict[str, Any], snapshot: dict[str, Any] | None
) -> None:
    from moonmind.security.assessment_projects import (
        AssessmentProjectSnapshot,
        apply_assessment_defaults,
    )

    steps = workflow.get("steps")
    if steps is not None and not isinstance(steps, list):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_execution_request",
                "message": "payload.workflow.steps must be a JSON array.",
            },
        )
    resolved = (
        AssessmentProjectSnapshot.model_validate(snapshot)
        if snapshot is not None
        else None
    )
    try:
        apply_assessment_defaults(workflow, resolved)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_assessment_project", "message": str(exc)},
        ) from exc


def apply_assessment_project_parameter_defaults(
    parameters: dict[str, Any], snapshot: dict[str, Any] | None
) -> None:
    """Canonicalize every retained workflow alias before any consumer selects it."""
    for key in ("workflow", "task"):
        workflow = parameters.get(key)
        if isinstance(workflow, dict):
            apply_assessment_project_defaults(workflow, snapshot)
