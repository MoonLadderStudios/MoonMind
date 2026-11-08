"""Carry admitted assessment constraints into existing model-delivery owners.

This module selects no credentials and grants no runtime or artifact access.
Only canonical execution state supplies constraints; retrieved evidence cannot.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from moonmind.security.assessment_projects import AssessmentProjectSnapshot
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactAuthorizationError,
    TemporalArtifactError,
    TemporalArtifactStateError,
    TemporalArtifactUnavailableError,
)


class AssessmentProcessingSourceMissing(TemporalArtifactStateError):
    """No admitted source exists here; a verified parent may own this child."""

    code = "TemporalArtifactStateError"


def processing_from_source(source: Any) -> tuple[dict | None, dict | None]:
    return processing_from_parameters(source.parameters or {})


def processing_from_parameters(parameters: Mapping) -> tuple[dict | None, dict | None]:
    value = parameters.get("assessmentProject")
    if value is None:
        return None, None
    try:
        snapshot = AssessmentProjectSnapshot.model_validate(value)
    except (TypeError, ValueError):
        raise TemporalArtifactAuthorizationError(
            "Admitted assessment processing context is unavailable; evidence is preserved."
        ) from None
    policy = snapshot.configuration.evidence_processing
    return (
        policy.model_dump(mode="json", by_alias=True) if policy is not None else None,
        snapshot.model_dump(mode="json", by_alias=True),
    )


def _temporary_processing_source_failure(error: BaseException) -> bool:
    """Recognize transport failures without exposing their messages or payloads."""
    from sqlalchemy.exc import OperationalError
    from temporalio.service import RPCError, RPCStatusCode

    seen: set[int] = set()
    for _ in range(8):
        if id(error) in seen:
            break
        seen.add(id(error))
        if isinstance(error, (TimeoutError, ConnectionError, OperationalError)):
            return True
        if isinstance(error, RPCError) and error.status in {
            RPCStatusCode.UNAVAILABLE,
            RPCStatusCode.DEADLINE_EXCEEDED,
            RPCStatusCode.RESOURCE_EXHAUSTED,
            RPCStatusCode.ABORTED,
        }:
            return True
        if error.__cause__ is None:
            break
        error = error.__cause__
    return False


def processing_source_error(
    error: BaseException, *, workflow_id: str
) -> TemporalArtifactError:
    """Preserve bounded recovery without serializing source or transport payloads."""
    if _temporary_processing_source_failure(error):
        return TemporalArtifactUnavailableError(
            workflow_id, dependency="assessment_history", operation="model_delivery"
        )
    return TemporalArtifactStateError(
        "Admitted assessment processing context is unavailable; evidence is preserved."
    )


async def assessment_context_for_execution(
    session,
    workflow_id: str,
    run_id: str | None,
    *,
    service=None,
):
    """Read admitted processing and snapshot through the shared source owner."""
    from api_service.db.models import (
        TemporalExecutionCanonicalRecord,
        TemporalExecutionRecord,
    )
    from moonmind.workflows.temporal.client import TemporalClientAdapter
    from moonmind.workflows.temporal.schedule_mapping import scheduled_definition_id

    try:
        source = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
        if source is not None:
            if source.workflow_id != workflow_id or not source.run_id:
                raise ValueError("Canonical source identity is unavailable")
            if run_id is not None and source.run_id != run_id:
                await TemporalClientAdapter().verify_workflow_continuation(
                    workflow_id,
                    run_id=run_id,
                    source_run_id=source.run_id,
                )
            # Never replace the admitted snapshot with mutable settings or
            # continuation arguments, even when the API row predates this run.
            return processing_from_source(source)
        projection = None
        if run_id is None:
            projection = (
                await service.describe_execution(workflow_id)
                if service is not None
                else await session.get(TemporalExecutionRecord, workflow_id)
            )
            run_id = getattr(projection, "run_id", None)
            if not run_id:
                raise ValueError("Admitted source run is unavailable")
        if scheduled_definition_id(workflow_id) is not None:
            # Schedules have no API source row. The existing start-input owner
            # follows server first-run authority and rejects authored schedule
            # identities; continuation arguments cannot relax the original policy.
            admitted = (
                await TemporalClientAdapter().read_scheduled_workflow_start_input(
                    workflow_id,
                    run_id=run_id,
                )
            )
            parameters = admitted.get("initial_parameters")
            if not isinstance(parameters, Mapping):
                raise ValueError("Scheduled source parameters are unavailable")
            return processing_from_parameters(parameters)
        if projection is None:
            projection = (
                await service.describe_execution(workflow_id)
                if service is not None
                else await session.get(TemporalExecutionRecord, workflow_id)
            )
        if projection is None:
            raise AssessmentProcessingSourceMissing(
                "Admitted assessment processing context is unavailable; evidence is preserved."
            )
        if projection.run_id == run_id:
            from moonmind.workflows.temporal.service import TemporalExecutionService

            source = await (
                service or TemporalExecutionService(session)
            ).read_scheduled_execution_source(projection)
        if (
            source is None
            or source.workflow_id != workflow_id
            or source.run_id != run_id
        ):
            raise ValueError("Admitted source identity is unavailable")
        return processing_from_source(source)
    except TemporalArtifactError:
        raise
    except Exception as exc:  # noqa: BLE001 - Classify transport failure without serializing source data.
        raise processing_source_error(exc, workflow_id=workflow_id) from None


async def assessment_processing_for_execution(session, workflow_id: str, run_id: str):
    policy, _snapshot = await assessment_context_for_execution(
        session, workflow_id, run_id
    )
    return policy


def require_model_delivery(policy: Mapping | None, *, profile_ref: str | None) -> None:
    if policy is None:
        return
    mode = policy.get("modelDelivery", "allowed")
    if mode == "disabled":
        raise TemporalArtifactAuthorizationError(
            "Model delivery is disabled for this assessment; retained evidence remains available."
        )
    if mode == "local_only":
        raise TemporalArtifactAuthorizationError(
            "Local-only model delivery is unavailable on this runtime route; retained evidence remains available."
        )
    if mode != "allowed":
        raise TemporalArtifactAuthorizationError(
            "Assessment model delivery constraints are invalid."
        )
    allowed = policy.get("allowedProfileRefs") or []
    if allowed and profile_ref is None:
        raise TemporalArtifactAuthorizationError(
            "This model route cannot verify an admitted Provider Profile for assessment evidence; evidence is preserved."
        )
    if allowed and profile_ref not in allowed:
        raise TemporalArtifactAuthorizationError(
            "The selected recipient is outside this assessment's admitted Provider Profiles; evidence is preserved."
        )


def require_execution_model_delivery(
    parameters: Mapping, *, profile_ref: str | None
) -> None:
    """Apply saved constraints at the existing agent-plan admission boundary."""
    from moonmind.workflows.executions.execution_contract import (
        workflow_payload_from_parameters,
        workflow_requires_agent,
    )

    policy, _ = processing_from_parameters(parameters)
    if policy is not None and workflow_requires_agent(
        workflow_payload_from_parameters(parameters), parameters
    ):
        require_model_delivery(policy, profile_ref=profile_ref)


async def _source_plan(source, *, session, artifacts, access):
    from moonmind.omnigent.harness_platform.execution_plan import (
        OmnigentExecutionPlanEnvelope,
        verify_execution_plan_envelope,
    )
    from moonmind.omnigent.harness_platform.stores import SessionExecutionPlanStore
    from moonmind.schemas.agent_runtime_models import OmnigentExecutionPlanBinding

    value = (source.parameters or {}).get("omnigentExecutionPlan")
    if not isinstance(value, Mapping):
        raise TemporalArtifactStateError(
            "Choose an explicit reviewer Provider Profile: this assessment has no admitted model recipient."
        )
    try:
        binding = OmnigentExecutionPlanBinding.model_validate(value)
        plan = await SessionExecutionPlanStore(session).load(binding.plan_ref)
        if plan is None:
            raise ValueError("missing plan")
        verify_execution_plan_envelope(plan)
        if (
            plan.planRef != binding.plan_ref
            or binding.plan_digest != "sha256:" + plan.planRef.rsplit(":", 1)[-1]
        ):
            raise ValueError("plan mismatch")
        _artifact, body = await artifacts.read(
            artifact_id=binding.plan_artifact_ref.removeprefix(
                "artifact://"
            ).removeprefix("artifact:"),
            access=access,
        )
        if OmnigentExecutionPlanEnvelope.model_validate_json(body) != plan:
            raise ValueError("plan artifact mismatch")
        authority = plan.payload.authority
        if authority is None or (
            authority.taskInputSnapshotRef != binding.task_input_snapshot_ref
            or authority.taskInputSnapshotDigest != binding.task_input_snapshot_digest
        ):
            raise ValueError("task authority mismatch")
        import hashlib
        import json

        from moonmind.workflows.executions.execution_contract import (
            original_task_snapshot_parameters,
        )

        _artifact, task_body = await artifacts.read(
            artifact_id=binding.task_input_snapshot_ref.removeprefix(
                "artifact://"
            ).removeprefix("artifact:"),
            access=access,
        )
        if (
            "sha256:" + hashlib.sha256(task_body).hexdigest()
            != binding.task_input_snapshot_digest
        ):
            raise ValueError("task-input body mismatch")
        frozen_parameters = original_task_snapshot_parameters(json.loads(task_body))
        if (
            processing_from_parameters(frozen_parameters)[1]
            != processing_from_source(source)[1]
        ):
            raise ValueError("source assessment differs from frozen task input")
        return plan
    except Exception:  # noqa: BLE001 - Fail closed without payload-bearing failure causes.
        raise TemporalArtifactStateError(
            "The source model selection is unavailable or inconsistent; no alternate recipient was selected."
        ) from None


async def resolve_review_delivery(*, source, runtime, session, artifacts, access):
    """Resolve recipient intent before evidence reads; ordinary admission still owns launch."""
    from api_service.services.provider_profile_runtime import (
        provider_profile_ref_from_mapping,
    )
    from moonmind.omnigent.harness_platform.credential_bindings import model_bindings_of

    policy, snapshot = processing_from_source(source)
    # Reject unsupported processing modes before looking for an alternate route.
    if policy and policy.get("modelDelivery") != "allowed":
        require_model_delivery(policy, profile_ref=None)
    runtime = dict(runtime)
    inherited = not runtime
    source_plan = None
    launch = {}
    profile_selection = None
    if inherited:
        source_plan = await _source_plan(
            source, session=session, artifacts=artifacts, access=access
        )
        plan = source_plan.payload
        profiles = {
            item.providerProfileRef
            for item in model_bindings_of(plan.credentialBindings).values()
        }
        if len(profiles) != 1:
            raise TemporalArtifactStateError(
                "Source model recipient is ambiguous; choose an explicit reviewer."
            )
        runtime = {
            "mode": "omnigent",
            "providerProfileRef": profiles.pop(),
            "model": plan.modelConfig.qualifiedId,
            "effort": plan.modelConfig.effort,
        }
        launch = {"launchPolicyRef": plan.launchPolicyRef}
        # Reuse the selected Profile configuration, never its repository or
        # publication grants. Capture overrides preserve source privacy intent.
        import json

        try:
            profile_ref = str(plan.agentProfileSnapshotRef or "")
            if not profile_ref.startswith("artifact:"):
                raise ValueError("profile authority unavailable")
            _artifact, profile_body = await artifacts.read(
                artifact_id=profile_ref.removeprefix("artifact:"),
                access=access,
            )
            selected = json.loads(profile_body)
            if not isinstance(selected, Mapping) or not selected.get("profileId"):
                raise ValueError("profile authority unavailable")
        except Exception:  # noqa: BLE001 - Fail closed without payload-bearing failure causes.
            raise TemporalArtifactStateError(
                "Source reviewer configuration is unavailable; choose an explicit reviewer."
            ) from None
        profile_selection = {
            "profileId": selected.get("profileId"),
            "version": selected.get("version"),
            "providerProfileRef": runtime["providerProfileRef"],
            "launchPolicyRef": plan.launchPolicyRef,
            "overrides": {"capture": dict(plan.capturePolicy)},
        }
    profile = provider_profile_ref_from_mapping(runtime)
    if not profile:
        raise TemporalArtifactStateError(
            "Choose an explicit reviewer Provider Profile; no ambient recipient was selected."
        )
    nested = runtime.get("agentProfile")
    if isinstance(nested, Mapping) and nested.get("providerProfileRef") not in (
        None,
        profile,
    ):
        raise TemporalArtifactAuthorizationError(
            "Reviewer Provider Profile selections conflict."
        )
    require_model_delivery(policy, profile_ref=profile)
    return {
        "runtime": runtime,
        "assessmentProject": snapshot,
        "omnigent": launch,
        "agentProfile": profile_selection,
        "receipt": {
            "schemaVersion": "assessment-model-delivery/v1",
            "status": "pending_admission",
            "selection": "inherited" if inherited else "explicit",
            "recipient": {
                "providerProfileRef": profile,
                "model": runtime.get("model"),
                "effort": runtime.get("effort"),
            },
            "sourcePlanRef": source_plan.planRef if source_plan else None,
            "sourceProcessing": policy,
            "outputDestination": "instance_artifacts",
            "publicationMode": "none",
            "limitations": [
                "Recipient readiness and actual model are resolved by ordinary execution admission; no delivery is claimed before execution."
            ],
        },
    }


async def persist_review_delivery_receipt(
    *, execution, receipt, session, artifacts, access, principal
):
    """Record actual admitted recipient without claiming a provider send occurred.

    Failure here never retries an accepted create or removes the selected source
    evidence. The authored review report also retains the transformation receipt.
    """
    import json
    import logging

    from api_service.db.models import (
        TemporalArtifactRedactionLevel,
        TemporalExecutionCanonicalRecord,
    )
    from moonmind.omnigent.harness_platform.credential_bindings import model_bindings_of

    try:
        canonical = await session.get(
            TemporalExecutionCanonicalRecord, execution.workflow_id
        )
        if (
            canonical is None
            or canonical.workflow_id != execution.workflow_id
            or canonical.run_id != execution.run_id
        ):
            raise ValueError("admitted execution unavailable")
        plan = await _source_plan(
            canonical, session=session, artifacts=artifacts, access=access
        )
        payload = plan.payload
        admitted = {
            **receipt,
            "status": "admitted",
            "execution": {
                "workflowId": canonical.workflow_id,
                "runId": canonical.run_id,
            },
            "recipient": {
                "providerProfileRefs": sorted(
                    {
                        item.providerProfileRef
                        for item in model_bindings_of(
                            payload.credentialBindings
                        ).values()
                    }
                ),
                "provider": payload.modelConfig.routeRef,
                "model": payload.modelConfig.qualifiedId,
                "effort": payload.modelConfig.effort,
            },
            "executionPlanRef": plan.planRef,
            "launchPolicyRef": payload.launchPolicyRef,
            "capturePolicyRef": payload.capturePolicyRef,
            "configurationSnapshot": payload.providerConfigurationSnapshot.model_dump(
                mode="json"
            )
            if payload.providerConfigurationSnapshot
            else None,
            "limitations": [
                *receipt.get("limitations", []),
                "Admission is recorded; provider execution/delivery outcome remains owned by the review execution.",
            ],
        }
        artifact, _created = await artifacts.put_content_addressed_payload_complete(
            principal=principal,
            payload=json.dumps(
                admitted, sort_keys=True, separators=(",", ":")
            ).encode(),
            content_type="application/json",
            scope="assessment-deliveries",
            link={
                "namespace": canonical.namespace,
                "workflow_id": canonical.workflow_id,
                "run_id": canonical.run_id,
                "link_type": "report.evidence",
            },
            metadata_json={"artifact_type": "assessment_evidence_delivery"},
            redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
        )
        return artifact.artifact_id
    except Exception:  # noqa: BLE001 - Fail closed without payload-bearing failure causes.
        logging.getLogger(__name__).warning(
            "Assessment delivery receipt unavailable after accepted review admission"
        )
        return None
