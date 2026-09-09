"""Replay the schedule-to-runtime handoff after a deployment image update."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from api_service.services import omnigent_execution_plan_service
from api_service.services.recurring_workflows_service import (
    RecurringWorkflowConflictError,
    RecurringWorkflowsService,
)
from moonmind.omnigent import deployment_identity
from moonmind.omnigent.profile_bound_execution import _compile_persisted_effective_launch
from moonmind.schemas.agent_runtime_models import OmnigentExecutionPlanBinding
from tests.integration.reliability.helpers import load_replay
from tests.unit.services.test_schedule_deployment_refresh import deployment_session  # noqa: F401

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.reliability_journey]


@pytest.mark.parametrize("update_policy", [None, "pinned"])
@pytest.mark.parametrize("concurrent_update", [False, True])
async def test_scheduled_image_update_reaches_dispatch_with_matching_authority(
    deployment_session, monkeypatch, update_policy, concurrent_update,
):
    manifest = load_replay("scheduled-deployment-policy-drift", "manifest.json")
    session = deployment_session
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    parameters = session.parameters()
    binding = OmnigentExecutionPlanBinding(
        planRef="omnigent-execution-plan:sha256:" + "1" * 64,
        planDigest="sha256:" + "1" * 64,
        planArtifactRef="art_old_plan",
        taskInputSnapshotRef="art_original_task",
        taskInputSnapshotDigest="sha256:" + "a" * 64,
    )
    parameters["omnigentExecutionPlan"] = binding.model_dump(by_alias=True)
    target = {
        "workflowType": "MoonMind.UserWorkflow",
        "initialParameters": parameters,
        "agentProfileSnapshot": deepcopy(parameters["agentProfileSnapshot"]),
    }
    if update_policy is not None:
        target["runtimeProviderTargetUpdatePolicy"] = update_policy
    historical_input = deepcopy(target)
    definition = SimpleNamespace(
        id=uuid4(), target=target, version=1, owner_user_id=None,
        name="Scheduled deployment replay", policy={},
    )
    observed_launches = []
    current_digest = "sha256:" + "2" * 64
    current_host = "ghcr.io/example/host@" + current_digest
    monkeypatch.setattr(deployment_identity, "resolve_deployed_server_build_digest", lambda: current_digest)
    monkeypatch.setattr(deployment_identity, "_resolve_deployed_host_image_ref", lambda _: current_host)

    def dispatch_authority(snapshot):
        launch = _compile_persisted_effective_launch(
            session.policies[snapshot["launchPolicyRef"]],
            provider_profile_id=session.provider.profile_id,
            follow_up_retrieval={},
        )
        payload = SimpleNamespace(
            executionRealizerRef="generic-omnigent-host@1",
            harnessId=manifest["harness"], hostImageRef=launch["hostImageRef"],
            supportIdentity=SimpleNamespace(
                omnigentServerBuildRef="sha256:" + launch["serverImageRef"].rsplit(":", 1)[-1]
            ),
        )
        deployment_identity.assert_plan_matches_deployed_runtime(payload)
        return launch

    with pytest.raises(deployment_identity.OmnigentDeploymentIdentityConflict):
        dispatch_authority(parameters["agentProfileSnapshot"])

    async def persist_plan(**kwargs):
        # Execute the real policy compiler and dispatch identity gate. The
        # artifact transport is replaced by a compact persisted binding here.
        observed_launches.append(dispatch_authority(kwargs["agent_profile_snapshot"]))
        assert kwargs["task_input_snapshot_ref"] == binding.task_input_snapshot_ref
        assert kwargs["initial_parameters"]["model"] == historical_input["initialParameters"]["model"]
        return SimpleNamespace(
            binding=binding.model_copy(update={
                "plan_ref": "omnigent-execution-plan:" + current_digest,
                "plan_digest": current_digest, "plan_artifact_ref": "art_new_plan",
            }),
            artifact_refs=["art_new_plan"], resolved_skillset_ref="art_skills",
            runtime_provider_rollout=None,
        )

    monkeypatch.setattr(omnigent_execution_plan_service, "compile_and_persist_execution_plan", persist_plan)
    adapter = SimpleNamespace(
        describe_schedule=AsyncMock(return_value=SimpleNamespace(
            schedule=SimpleNamespace(action=None),
        )),
        update_schedule=AsyncMock(),
    )
    service = RecurringWorkflowsService(session, temporal_client_adapter=adapter, artifact_service=object())
    if concurrent_update:
        async def changed_definition(*_args, **_kwargs):
            definition.version += 1

        session.refresh.side_effect = changed_definition
        with pytest.raises(RecurringWorkflowConflictError, match="schedule changed"):
            await service._refresh_managed_bootstrap_target(definition)
        adapter.update_schedule.assert_not_awaited()
        assert session.usage.version == 1
        assert definition.target == historical_input
        return
    assert await service._refresh_managed_bootstrap_target(definition)
    await service._ensure_schedule_action_current(definition)
    new_input = adapter.update_schedule.await_args.kwargs["workflow_input"]["initial_parameters"]
    assert new_input["agentProfileSnapshot"]["version"] == 2
    assert new_input["omnigentExecutionPlan"]["planDigest"] == current_digest
    assert new_input["effort"] == historical_input["initialParameters"]["effort"]
    assert new_input["profileId"] == historical_input["initialParameters"]["profileId"]
    assert dispatch_authority(new_input["agentProfileSnapshot"])["hostImageRef"] == current_host
    assert historical_input["initialParameters"]["agentProfileSnapshot"]["version"] == 1
    assert session.usage.version == 2
    assert definition.version == 2
    assert len(observed_launches) == 1
