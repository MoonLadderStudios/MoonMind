"""Replay the schedule-to-runtime handoff after a deployment image update."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio

from api_service.db.models import (
    OmnigentExecutionPlanRecord,
    OmnigentPolicyVersion,
    TemporalArtifactDependency,
)
from api_service.services import omnigent_execution_plan_service
from api_service.services.omnigent_policies import PolicyConflict
from api_service.services.recurring_workflows_service import (
    RecurringWorkflowConflictError,
    RecurringWorkflowsService,
)
from moonmind.omnigent import deployment_identity
from moonmind.omnigent.harness_platform.stores import InMemoryExecutionPlanStore
from moonmind.omnigent.profile_bound_execution import (
    _compile_persisted_effective_launch,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from tests.artifact_access import access_for_test
from tests.integration.reliability.helpers import load_replay
from tests.unit.services.test_omnigent_execution_plan_service import (
    _compile_opencode_plan,
    _configure_ready_host_image_pair,
)
from tests.unit.services.test_schedule_deployment_refresh import (
    deployment_session,  # noqa: F401 - pytest fixture
)
from tests.unit.workflows.temporal.test_artifacts import temporal_db

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@pytest_asyncio.fixture
async def original_plan_artifacts(tmp_path):
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        yield TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "plan-artifacts"),
        ), sessions


@pytest.mark.parametrize("update_policy", [None, "pinned"])
@pytest.mark.parametrize(
    "concurrent_update", [None, "schedule", "profile", "policy", "server", "host"]
)
async def test_scheduled_image_update_reaches_dispatch_with_matching_authority(
    deployment_session,
    original_plan_artifacts,
    monkeypatch,
    update_policy,
    concurrent_update,
):
    manifest = load_replay("scheduled-deployment-policy-drift", "manifest.json")
    session = deployment_session
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    scalar = session.scalar

    async def locked_scalar(statement):
        if statement.column_descriptions[0].get("entity") is OmnigentPolicyVersion:
            assert statement._for_update_arg is not None
            assert statement.get_execution_options()["populate_existing"] is True
            return SimpleNamespace(state=session.policy_states["omnigent-on-demand@2"])
        return await scalar(statement)

    session.scalar = locked_scalar
    parameters = session.parameters()
    definition_id = uuid4()
    # The retained input and its closure use real metadata/blob storage. The
    # profile/policy race below deliberately isolates synthetic policy rows.
    artifacts, sessions = original_plan_artifacts
    execute_policy_query = session.execute

    async def execute_with_retained_dependencies(statement):
        if statement.column_descriptions[0].get("entity") is TemporalArtifactDependency:
            return await artifacts._repository._session.execute(statement)
        return await execute_policy_query(statement)

    session.execute = execute_with_retained_dependencies
    snapshot_ref, snapshot_digest = (
        await omnigent_execution_plan_service.persist_json_artifact(
            artifact_service=artifacts,
            principal="user-1",
            artifact_class="original_task_input_snapshot",
            payload={
                "snapshotVersion": "original-task-input/v1",
                "source": {"kind": "schedule", "definitionId": str(definition_id)},
                "target": {"initialParameters": parameters},
            },
        )
    )
    with monkeypatch.context() as plan_patch:
        _configure_ready_host_image_pair(plan_patch)
        plan_patch.setattr(
            omnigent_execution_plan_service,
            "_try_load_real_harness_config",
            AsyncMock(return_value=None),
        )
        plan_patch.setattr(
            omnigent_execution_plan_service,
            "resolve_execution_evidence",
            lambda *_args, **_kwargs: (None, "uncertified"),
        )
        original_plan = await _compile_opencode_plan(
            plan_patch,
            artifacts=artifacts,
            session_factory=sessions,
            workflow_id=f"mm-schedule:{definition_id}",
            launch_policy_ref="omnigent-on-demand@1",
            plan_store=InMemoryExecutionPlanStore(),
            task_input_snapshot_ref=snapshot_ref,
            task_input_snapshot_digest=snapshot_digest,
        )
    get_record = session.get

    async def persisted_record(model, key):
        if model is OmnigentExecutionPlanRecord:
            envelope = original_plan.envelope
            if key != envelope.planRef:
                return None
            return OmnigentExecutionPlanRecord(
                plan_ref=envelope.planRef,
                schema_version=envelope.schemaVersion,
                payload_json=envelope.payload.model_dump(mode="json", by_alias=True),
            )
        return await get_record(model, key)

    session.get = persisted_record
    binding = original_plan.binding
    for artifact_id in (binding.plan_artifact_ref, binding.task_input_snapshot_ref):
        metadata, *_ = await artifacts.get_metadata(
            artifact_id=artifact_id, access=access_for_test("user-1")
        )
        session.task_inputs[artifact_id] = metadata
    closure = await artifacts.verified_dependencies(
        root_artifact_id=binding.plan_artifact_ref, access=access_for_test("user-1")
    )
    assert closure is not None
    assert binding.task_input_snapshot_ref in {
        item.artifact_id for item in closure.dependencies
    }
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
        id=definition_id,
        target=target,
        version=1,
        owner_user_id=None,
        name="Scheduled deployment replay",
        policy={},
    )
    observed_launches = []
    plan_payloads = []
    current_digest = "sha256:" + "2" * 64
    current_host = "ghcr.io/example/host@" + current_digest
    monkeypatch.setattr(
        deployment_identity,
        "resolve_deployed_server_build_digest",
        lambda: current_digest,
    )
    monkeypatch.setattr(
        deployment_identity, "_resolve_deployed_host_image_ref", lambda _: current_host
    )

    async def dispatch_authority(snapshot):
        launch = _compile_persisted_effective_launch(
            session.policies[snapshot["launchPolicyRef"]],
            provider_profile_id=session.provider.profile_id,
            follow_up_retrieval={},
        )
        payload = SimpleNamespace(
            executionRealizerRef="generic-omnigent-host@1",
            harnessId=manifest["harness"],
            hostImageRef=launch["hostImageRef"],
            supportIdentity=SimpleNamespace(
                omnigentServerBuildRef="sha256:"
                + launch["serverImageRef"].rsplit(":", 1)[-1]
            ),
        )
        await deployment_identity.assert_plan_matches_deployed_runtime(payload)
        plan_payloads.append(payload)
        return launch

    with pytest.raises(deployment_identity.OmnigentDeploymentIdentityConflict):
        await dispatch_authority(parameters["agentProfileSnapshot"])

    async def persist_plan(**kwargs):
        # Execute the real policy compiler and dispatch identity gate. The
        # artifact transport is replaced by a compact persisted binding here.
        observed_launches.append(
            await dispatch_authority(kwargs["agent_profile_snapshot"])
        )
        assert kwargs["principal"] == "user-1"
        assert kwargs["task_input_snapshot_ref"] == binding.task_input_snapshot_ref
        assert (
            kwargs["task_input_snapshot_digest"] == binding.task_input_snapshot_digest
        )
        assert (
            kwargs["initial_parameters"]["model"]
            == historical_input["initialParameters"]["model"]
        )
        # Reproduce a second deployment during artifact persistence, after
        # compilation validated its inputs and released its initial locks.
        if concurrent_update == "profile":
            session.profile.active_version += 1
        elif concurrent_update == "policy":
            session.policy_states["omnigent-on-demand@2"] = "superseded"
        elif concurrent_update == "server":
            monkeypatch.setattr(
                deployment_identity,
                "resolve_deployed_server_build_digest",
                lambda: "sha256:" + "3" * 64,
            )
        elif concurrent_update == "host":
            monkeypatch.setattr(
                deployment_identity,
                "_resolve_deployed_host_image_ref",
                lambda _: current_host.replace("2" * 64, "3" * 64),
            )
        return SimpleNamespace(
            envelope=SimpleNamespace(payload=plan_payloads[-1]),
            binding=binding.model_copy(
                update={
                    "plan_ref": "omnigent-execution-plan:" + current_digest,
                    "plan_digest": current_digest,
                    "plan_artifact_ref": "art_new_plan",
                }
            ),
            artifact_refs=["art_new_plan"],
            resolved_skillset_ref="art_skills",
            runtime_provider_rollout=None,
        )

    monkeypatch.setattr(
        omnigent_execution_plan_service,
        "compile_and_persist_execution_plan",
        persist_plan,
    )
    adapter = SimpleNamespace(
        describe_schedule=AsyncMock(
            return_value=SimpleNamespace(
                schedule=SimpleNamespace(action=None),
            )
        ),
        update_schedule=AsyncMock(),
    )
    service = RecurringWorkflowsService(
        session, temporal_client_adapter=adapter, artifact_service=object()
    )

    async def retain_compiled_target(target, **_kwargs):
        # persist_plan intentionally substitutes the new compiler result. Its
        # dependency publication is covered through real services separately.
        assert (
            target["initialParameters"]["omnigentExecutionPlan"]["planArtifactRef"]
            == "art_new_plan"
        )
        return "art_new_plan"

    service._retain_target_artifacts = AsyncMock(side_effect=retain_compiled_target)
    if concurrent_update == "schedule":
        session.commit = AsyncMock()
        # Compilation now retains the definition lock. A newer saved edit
        # can win only before the separate post-commit dispatch lock is taken.
        # The PostgreSQL dispatch-fence tests exercise the actual overlap.
        winner = SimpleNamespace(
            **{**vars(definition), "version": definition.version + 1}
        )
        service._lock_definition_for_update = AsyncMock(return_value=winner)
        async with service._dispatch_committed_definition(
            definition.id, definition.version
        ) as current:
            assert current is None
        service._lock_definition_for_update.assert_awaited_once_with(definition.id)
        adapter.update_schedule.assert_not_awaited()
        service._retain_target_artifacts.assert_not_awaited()
        session.commit.assert_awaited_once()
        assert winner.version == 2
        assert winner.target == historical_input
        assert session.usage.version == 1
        assert definition.target == historical_input
        return
    if concurrent_update:
        expected_error = {
            "profile": RecurringWorkflowConflictError,
            "policy": PolicyConflict,
            "server": deployment_identity.OmnigentDeploymentIdentityConflict,
            "host": deployment_identity.OmnigentDeploymentIdentityConflict,
        }[concurrent_update]
        with pytest.raises(expected_error):
            await service._refresh_managed_bootstrap_target(definition)
        adapter.update_schedule.assert_not_awaited()
        service._retain_target_artifacts.assert_not_awaited()
        assert session.usage.version == 1
        assert definition.target == historical_input
        return
    assert await service._refresh_managed_bootstrap_target(definition)
    await service._ensure_schedule_action_current(definition)
    new_input = adapter.update_schedule.await_args.kwargs["workflow_input"][
        "initial_parameters"
    ]
    assert new_input["agentProfileSnapshot"]["version"] == 2
    assert new_input["omnigentExecutionPlan"]["planDigest"] == current_digest
    assert new_input["effort"] == historical_input["initialParameters"]["effort"]
    assert new_input["profileId"] == historical_input["initialParameters"]["profileId"]
    assert (await dispatch_authority(new_input["agentProfileSnapshot"]))[
        "hostImageRef"
    ] == current_host
    assert historical_input["initialParameters"]["agentProfileSnapshot"]["version"] == 1
    assert session.usage.version == 2
    assert definition.version == 2
    assert len(observed_launches) == 1
    service._retain_target_artifacts.assert_awaited_once()
    assert definition.input_artifact_root_id == "art_new_plan"
