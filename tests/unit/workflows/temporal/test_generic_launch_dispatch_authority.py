"""Generic dispatch admits precompiled launch inputs before the actual reader."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from moonmind.omnigent.bridge_artifacts import (
    OmnigentArtifactError,
    TemporalOmnigentArtifactGateway,
)
from moonmind.omnigent.harness_platform.planning_service import (
    OmnigentPlannedHostResolver,
)
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.activities.omnigent_activities import (
    _try_generic_realizer_dispatch,
)
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
)
from tests.unit.omnigent.test_admitted_input_authority import (  # noqa: F401
    AUTH_MODES,
    _admitted_launch,
    artifacts,  # noqa: F811
    auth_mode,  # noqa: F811
)


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_mode", AUTH_MODES)
@pytest.mark.parametrize(
    "binding_shape", ["typed", "retained-plan-ref", "freshly-planned", "step-retained"]
)
async def test_dispatch_admits_launch_before_real_host_reader(
    artifacts,  # noqa: F811
    auth_mode,  # noqa: F811
    binding_shape,
    monkeypatch,
):
    plan, request, launch, launch_id = await _admitted_launch(
        artifacts, workflow_id="launch-owner"
    )
    assert not plan.payload.resolvedTools.get("repositoryAccess")
    if binding_shape == "step-retained":
        payload = request.model_dump(mode="json", by_alias=True)
        payload["stepExecution"]["omnigentExecutionPlan"] = payload[
            "omnigentExecutionPlan"
        ]
        payload["omnigentExecutionPlan"] = None
        with pytest.raises(ValidationError, match="must match request authority"):
            AgentExecutionRequest.model_validate(payload)
        return
    payload = request.model_dump(mode="json", by_alias=True)
    if binding_shape in {"retained-plan-ref", "freshly-planned"}:
        payload["omnigentExecutionPlan"] = None
        payload["resolvedSkillsetRef"] = plan.payload.resolvedSkills[
            "resolvedSkillSetRef"
        ]
        if binding_shape == "freshly-planned":
            payload["parameters"].pop("executionPlanRef", None)
            payload["parameters"]["omnigent"] = {
                "agentProfileRef": {"id": "test-profile"}
            }
            from moonmind.omnigent import production, settings as omnigent_settings

            monkeypatch.setattr(omnigent_settings, "generic_host_enabled", lambda: True)
            monkeypatch.setattr(
                production,
                "build_generic_omnigent_execution_services",
                lambda **kwargs: SimpleNamespace(
                    planning_service=SimpleNamespace(plan=AsyncMock(return_value=plan)),
                ),
            )
    request = AgentExecutionRequest.model_validate(payload)
    gateway = TemporalOmnigentArtifactGateway(artifacts)
    resolver = OmnigentPlannedHostResolver(
        catalog_repository=SimpleNamespace(), artifact_gateway=gateway
    )
    reads = []
    dispatched_requests = []

    class Realizer:
        async def execute(self, dispatched, persisted):
            dispatched_requests.append(dispatched)
            reads.append(
                await resolver._load_exact_launch(persisted, request=dispatched)
            )
            return AgentRunResult(summary="launch inputs resolved")

    result = await _try_generic_realizer_dispatch(
        request,
        plan_store=DbExecutionPlanStore(artifacts),
        artifact_gateway=gateway,
        realizer_registry=SimpleNamespace(require=lambda _ref: Realizer()),
    )
    assert result.failure_class is None, result
    assert reads == [launch]
    async with artifacts() as session:
        repository = TemporalArtifactRepository(session)
        assert (
            await repository.get_artifact(launch_id)
        ).created_by_principal == "system"
        links = await repository.list_links(launch_id)
        assert any(
            link.workflow_id == "launch-owner"
            and link.run_id == request.step_execution.run_id
            and link.link_type == "input.execution_plan"
            for link in links
        )
    if auth_mode != "disabled":
        foreign = dispatched_requests[0].model_copy(
            update={
                "step_execution": request.step_execution.model_copy(
                    update={"workflow_id": "foreign"}
                )
            }
        )
        with pytest.raises(TemporalArtifactAuthorizationError):
            await resolver._load_exact_launch(plan, request=foreign)


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_mode", AUTH_MODES)
@pytest.mark.parametrize(
    "mismatch", ["different-plan", "missing-ref", "missing-step", "unpersisted", "tampered"]
)
async def test_noncompact_admission_rejects_unselected_plan_before_any_grant(
    artifacts,  # noqa: F811
    auth_mode,  # noqa: F811
    mismatch,  # noqa: F811
):
    plan, request, _launch, launch_id = await _admitted_launch(
        artifacts, workflow_id="request-owner"
    )
    other_plan, _other_request, _other_launch, other_launch_id = await _admitted_launch(
        artifacts, workflow_id="other-workflow"
    )
    payload = request.model_dump(mode="json", by_alias=True)
    payload["omnigentExecutionPlan"] = None
    supplied = plan
    if mismatch == "different-plan":
        supplied = other_plan
    elif mismatch == "missing-ref":
        payload["parameters"].pop("executionPlanRef")
    elif mismatch == "missing-step":
        payload["stepExecution"] = None
    elif mismatch == "unpersisted":
        payload["parameters"]["executionPlanRef"] = "omnigent-plan:sha256:" + "f" * 64
    else:
        supplied = plan.model_copy(
            update={
                "payload": plan.payload.model_copy(
                    update={
                        "effectiveLaunchSnapshotRef": other_plan.payload.effectiveLaunchSnapshotRef,
                    }
                ),
            }
        )
    request = AgentExecutionRequest.model_validate(payload)
    gateway = TemporalOmnigentArtifactGateway(artifacts)
    with pytest.raises(OmnigentArtifactError):
        await gateway.admit_execution_plan_inputs(request=request, plan=supplied)
    async with artifacts() as session:
        repository = TemporalArtifactRepository(session)
        for artifact_id in (launch_id, other_launch_id):
            assert await repository.list_links(artifact_id) == []
