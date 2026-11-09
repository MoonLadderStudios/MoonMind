"""A replacement attempt preserves the configuration admitted for its step."""

from copy import deepcopy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from api_service.db.models import ManagedAgentProviderProfile, ProviderProfileSlotLease
from api_service.services.native_pool_execution_plan import prepare_selected_pool_plan
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.harness_platform.execution_plan import (
    create_execution_plan_envelope,
)
from moonmind.omnigent import credential_materializers
from moonmind.omnigent.host_services import docker_backend
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    ExecutionBudgetContext,
    resolve_execution_budget,
)
from moonmind.schemas.omnigent_session_models import OmnigentSessionAdmissionDecision
from moonmind.workflows.temporal.activities.omnigent_session_activities import (
    _plan_capacity_authority,
)
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactActivities,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from tests.integration.omnigent import test_dispatch_pool_shared_admission as pool
from tests.integration.omnigent import test_generic_native_durable_journey as native
from tests.unit.workflows.temporal.workflows.test_run_step_ledger import (
    _configure_workflow_runtime,
)

native_journey = native.native_journey
session_factory = native.session_factory
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


_CODEX = {
    "harness": "codex-native",
    "oauthPrivateConfiguration": True,
    "sessionSettings": {"developer_instructions": "Use the admitted instructions."},
}
_CLAUDE = {
    "harness": "claude-native",
    "oauthTuning": {"API_TIMEOUT_MS": "300000"},
    "claudeOauthFiles": True,
}


@pytest.mark.parametrize(
    ("native_journey", "same_step", "edit_profile", "tamper"),
    [
        pytest.param(_CODEX, True, True, None, id="codex-replacement-profile-edit"),
        pytest.param(_CODEX, False, True, None, id="codex-fresh-step-profile-edit"),
        pytest.param(_CODEX, True, False, None, id="codex-replacement-unchanged"),
        pytest.param(_CODEX, True, True, "foreign-step", id="foreign-step-checkpoint"),
        pytest.param(_CODEX, True, True, "plan-digest", id="plan-digest-mismatch"),
        pytest.param(_CODEX, True, True, "unadmitted-plan", id="unadmitted-plan"),
        pytest.param(_CLAUDE, True, True, None, id="claude-replacement-profile-edit"),
        pytest.param(_CLAUDE, False, True, None, id="claude-fresh-step-profile-edit"),
        pytest.param(_CLAUDE, True, False, None, id="claude-replacement-unchanged"),
    ],
    indirect=["native_journey"],
)
async def test_selected_pool_replacement_uses_its_admitted_configuration(
    native_journey,
    monkeypatch,
    same_step,
    edit_profile,
    tamper,
):
    journey = native_journey
    codex = journey.harness == "codex-native"
    runtime = "codex_cli" if codex else "claude_code"
    original_setting = "Use the admitted instructions." if codex else "300000"
    edited_setting = "A later edit." if codex else "100000"

    def observed_configuration():
        if codex:
            return json.loads(
                journey.docker.launch_env["MOONMIND_CODEX_PROFILE_CONFIGURATION"]
            )["document"]["developer_instructions"]
        assert json.loads(journey.docker.claude_preferences["settings.json"])[
            "language"
        ] == (
            "Later language"
            if journey.docker.launch_env["API_TIMEOUT_MS"] == "100000"
            else "English"
        )
        assert (
            journey.docker.claude_preferences["CLAUDE.md"]
            == "Admitted instructions for /workspaces/run.\n"
        )
        assert journey.docker.volumes == {"journey_oauth": {}}
        return journey.docker.launch_env["API_TIMEOUT_MS"]

    first_now = datetime.now(timezone.utc) - timedelta(seconds=10)
    build_first = pool.agent_requests
    execute_first = native.assert_native_turn_saved_and_cleaned

    def budgeted_requests(*args, **kwargs):
        requests = build_first(*args, **kwargs)
        for step, request in requests.items():
            request.execution_budget = ExecutionBudgetContext(
                budget=resolve_execution_budget(
                    agent_kind=request.agent_kind, timeout_policy=request.timeout_policy
                ),
                logicalStepId=step,
                attemptId="pool-agent-run",
                admittedAt=first_now,
            )
        return requests

    async def interrupted_first(journey):
        return await execute_first(journey, "failed")

    with monkeypatch.context() as first_transport:
        first_transport.setattr(pool, "agent_requests", budgeted_requests)
        first_transport.setattr(
            native, "assert_native_turn_saved_and_cleaned", interrupted_first
        )
        first = await pool.execute_selected_pool_source(
            journey, monkeypatch, include_successor=True
        )
    assert first.result.failure_class is not None
    assert observed_configuration() == original_setting
    assert not journey.server.workspace.exists()

    _configure_workflow_runtime(monkeypatch)
    owner = run_module.MoonMindUserWorkflow()
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id=first.parent_id,
            run_id=first.parent_run,
            namespace="default",
            task_queue="test",
            parent=None,
            start_time=first_now,
            search_attributes={},
            typed_search_attributes={},
        ),
    )
    monkeypatch.setattr(run_module.workflow, "now", lambda: now)
    monkeypatch.setattr(run_module.workflow, "patched", lambda marker: True)
    planner = _build_runtime_planner()(
        inputs={},
        parameters=first.parameters,
        snapshot=SimpleNamespace(
            digest="reg:sha256:test", artifact_ref="art_registry_test"
        ),
    )
    node_id = "work" if same_step else "second"
    node = next(node for node in planner["nodes"] if node["id"] == node_id)
    owner._initialize_step_ledger(
        ordered_nodes=planner["nodes"],
        dependency_map={node["id"]: [] for node in planner["nodes"]},
        updated_at=first_now,
    )
    if same_step:
        owner._mark_step_running("work", updated_at=first_now)
    restore_ref = None
    if same_step:

        async def read_saved(activity, payload, **kwargs):
            assert activity == "artifact.read", "source worker is already cleaned"
            # Controlled Activity transport; bytes and all checkpoint identities
            # still come from the real artifact owner and are validated by Run.
            return await journey.gateway.read_bytes(payload.artifact_ref)

        monkeypatch.setattr(run_module, "execute_typed_activity", read_saved)
        assert await owner._adopt_shared_child_saved_checkpoint(
            "work", first.selected, first.result
        )
        owner._carry_interrupted_omnigent_step_workspace("work", next_attempt=2)
        restore_ref = owner._interrupted_step_restore_refs[
            owner._step_execution_launch_block_key("work", 2)
        ]
    owner._mark_step_running(node_id, updated_at=now)
    successor = owner._build_agent_execution_request(
        node_inputs=node["inputs"],
        node_id=node_id,
        tool_name=node["tool"]["name"],
        workflow_parameters=first.parameters,
        step_execution=2 if same_step else 1,
        resolved_skillset_ref=first.selected.resolved_skillset_ref,
        attempt_reason="interrupted_step" if same_step else "initial_execution",
        trusted_remediation_checkpoint_restore_ref=restore_ref,
    )
    successor = owner._bind_shared_runtime_context(successor)
    successor.execution_profile_ref = native.PROFILE
    successor.execution_budget = (
        first.selected.execution_budget.successor(
            now=now, attempt_id="pool-replacement"
        )
        if same_step
        else ExecutionBudgetContext(
            budget=resolve_execution_budget(
                agent_kind=successor.agent_kind, timeout_policy=successor.timeout_policy
            ),
            logicalStepId=node_id,
            attemptId="pool-replacement",
            admittedAt=now,
        )
    )
    if same_step:
        assert (
            successor.step_execution.logical_step_id
            == first.selected.step_execution.logical_step_id
        )
        assert successor.step_execution.execution_ordinal == 2
        assert successor.idempotency_key != first.selected.idempotency_key
        assert (
            successor.execution_budget.budget == first.selected.execution_budget.budget
        )
        assert successor.execution_budget.remaining_seconds(
            now
        ) == first.selected.execution_budget.remaining_seconds(now)
        assert successor.execution_budget.consumed_seconds >= 10
        assert (
            successor.execution_budget.recovery_count
            == first.selected.execution_budget.recovery_count + 1
        )
        assert (
            successor.execution_budget.remaining_seconds(now)
            < successor.execution_budget.budget.max_seconds
        )
        assert (
            successor.workspace_spec["workspaceCheckpointRestoreRef"]
            == first.result.metadata["savedWorkspaceCheckpoint"]["archiveRef"]
        )
    async with journey.session_factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, native.PROFILE)
        if edit_profile:
            if codex:
                templates = deepcopy(profile.file_templates)
                templates[0]["content_template"]["developer_instructions"] = (
                    edited_setting
                )
                profile.file_templates = templates
            else:
                templates = deepcopy(profile.file_templates)
                templates[0]["content_template"]["language"] = "Later language"
                profile.file_templates = templates
                profile.env_template = {
                    **profile.env_template,
                    "API_TIMEOUT_MS": edited_setting,
                }
        await session.commit()

    activities = TemporalArtifactActivities(None)
    released = await activities.provider_profile_sync_slot_leases(
        runtime_id=runtime,
        action="release_one",
        leases=[{"lease_id": first.owner, "fencing_generation": 7}],
    )
    assert released["released"] is True
    child_id, child_run = "pool-replacement", "pool-replacement-run"
    granted = await activities.provider_profile_sync_slot_leases(
        runtime_id=runtime,
        action="grant",
        leases=[
            {
                "lease_id": child_id,
                "workflow_id": child_id,
                "owner_id": child_id,
                "profile_id": native.PROFILE,
                "owner_kind": "workflow",
                "ownerIsWorkflow": True,
                "purpose": "execution_direct",
                "compatibility_class": "shared_execution",
                "fencing_generation": 8,
                "scope_generation": 3,
                "capacity_scope_ref": f"provider-profile:{native.PROFILE}",
                "credential_generation": 3,
                "expiresAt": (now + timedelta(hours=1)).isoformat(),
                "safe_metadata": {"runId": child_run},
            }
        ],
    )
    assert granted["granted"] is True
    transport = SimpleNamespace(
        describe_workflow=AsyncMock(
            return_value=SimpleNamespace(
                raw_description=SimpleNamespace(
                    workflow_execution_info=SimpleNamespace(
                        execution=SimpleNamespace(
                            workflow_id=child_id, run_id=child_run
                        ),
                        status=1,
                        type=SimpleNamespace(name="MoonMind.AgentRun"),
                        parent_execution=SimpleNamespace(
                            workflow_id=first.parent_id, run_id=first.parent_run
                        ),
                    )
                ),
            )
        )
    )
    if tamper:
        hints = successor.parameters["metadata"]["moonmind"]
        original_ref = hints["sharedStepCheckpointRef"]
        original_bytes = await journey.gateway.read_bytes(original_ref)
        checkpoint = json.loads(original_bytes)
        if tamper == "foreign-step":
            checkpoint["source"]["logicalStepId"] = "another-step"
        elif tamper == "plan-digest":
            checkpoint["planDigest"] = "sha256:" + "f" * 64
        async with journey.session_factory() as session:
            artifacts = TemporalArtifactService(TemporalArtifactRepository(session))
            if tamper == "unadmitted-plan":
                unadmitted = create_execution_plan_envelope(
                    journey.plan.payload.model_copy(
                        update={
                            "modelConfig": journey.plan.payload.modelConfig.model_copy(
                                update={"qualifiedId": "unadmitted-model"}
                            ),
                        }
                    )
                )
                plan_artifact, _ = await artifacts.create(
                    principal=f"workflow:{first.parent_id}",
                    content_type="application/json",
                    link={
                        "namespace": "default",
                        "workflow_id": first.parent_id,
                        "run_id": first.parent_run,
                        "link_type": "output.checkpoint",
                    },
                )
                await artifacts.write_complete(
                    artifact_id=plan_artifact.artifact_id,
                    principal=f"workflow:{first.parent_id}",
                    payload=unadmitted.model_dump_json(by_alias=True).encode(),
                    content_type="application/json",
                )
                checkpoint["planRef"] = "artifact://" + plan_artifact.artifact_id
                checkpoint["planDigest"] = (
                    "sha256:" + unadmitted.planRef.rsplit(":", 1)[-1]
                )
            artifact, _ = await artifacts.create(
                principal=f"workflow:{first.parent_id}",
                content_type="application/json",
                link={
                    "namespace": "default",
                    "workflow_id": first.parent_id,
                    "run_id": first.parent_run,
                    "link_type": "output.checkpoint",
                },
            )
            await artifacts.write_complete(
                artifact_id=artifact.artifact_id,
                principal=f"workflow:{first.parent_id}",
                payload=json.dumps(checkpoint).encode(),
                content_type="application/json",
            )
            hints["sharedStepCheckpointRef"] = "artifact://" + artifact.artifact_id
            await session.commit()
    async with journey.session_factory() as session:
        payload = {
            "owner_id": child_id,
            "profile_id": native.PROFILE,
            "runId": child_run,
            "fencing_generation": 8,
            "originalProfileRef": successor.execution_profile_ref,
            "request": successor.model_dump(mode="json", by_alias=True),
        }
        if tamper:
            expected_refusal = (
                "not admitted" if tamper == "unadmitted-plan" else "checkpoint|plan"
            )
            with pytest.raises(ValueError, match=expected_refusal):
                await prepare_selected_pool_plan(
                    session, runtime_id=runtime, payload=payload, client=transport
                )
            lease = (
                await session.scalars(
                    select(ProviderProfileSlotLease).where(
                        ProviderProfileSlotLease.lease_id == child_id,
                    )
                )
            ).one()
            assert lease.execution_plan_ref is None and lease.lease_state == "held"
            assert await journey.gateway.read_bytes(original_ref) == original_bytes
            assert journey.docker.create_count == 1
            return
        compiled = await prepare_selected_pool_plan(
            session, runtime_id=runtime, payload=payload, client=transport
        )
        assert compiled["disposition"] == "shared", compiled
        lease = (
            await session.scalars(
                select(ProviderProfileSlotLease).where(
                    ProviderProfileSlotLease.lease_id == child_id,
                )
            )
        ).one()
        lease_id = lease.id
    shared = AgentExecutionRequest.model_validate(compiled["request"])
    plan = await DbExecutionPlanStore(journey.session_factory).load(
        shared.omnigent_execution_plan.plan_ref
    )
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id=child_id,
            run_id=child_run,
            namespace="default",
            task_queue="test",
            parent=None,
        ),
    )
    child = MoonMindAgentRun()

    async def ensure(manager_id, runtime_id, *, request_slot=True, **kwargs):
        if request_slot:
            child.slot_assigned({"profile_id": native.PROFILE, "fencing_generation": 8})
        return SimpleNamespace(signal=AsyncMock())

    child._ensure_manager_and_signal = ensure
    child._sync_manager_profiles = AsyncMock()
    child._await_omnigent_host_capacity = AsyncMock()
    authority = await _plan_capacity_authority(
        plan, execution_profile_ref=native.PROFILE
    )
    admitted = await child._admit_omnigent_capacity_before_execution(
        request=shared,
        parent_info=None,
        admission=OmnigentSessionAdmissionDecision(
            admitted=False,
            reasonCode="realizer_managed_lifecycle",
            admissionMode="enabled",
            admittedFeatureGeneration="omnigent-session-v1",
            executionRealizerRef=plan.payload.executionRealizerRef,
            **authority,
        ),
    )
    assert admitted.execution_budget == successor.execution_budget
    journey.plan, journey.request, journey.provider_lease_id = plan, admitted, lease_id
    journey.manager.plan, journey.manager.request = plan, admitted
    await journey.gateway.admit_execution_plan_inputs(request=admitted, plan=plan)
    journey.docker.host_class, _ = await journey.services.planned_host_resolver(
        plan, request=admitted
    )
    journey.server.terminal_status = journey.server.status = "idle"
    journey.server.items, journey.server.events = [], []
    observed_saved_bytes = []
    command = journey.docker.command

    async def observe_launch(argv, **kwargs):
        if list(argv[:2]) == ["docker", "create"]:
            root = Path(native._mounts(list(argv))["/workspaces/run"]["src"])
            report = root / native.RESULT_PATH
            observed_saved_bytes.append(
                report.read_bytes() if report.exists() else None
            )
        return await command(argv, **kwargs)

    monkeypatch.setattr(docker_backend, "run_runtime_command", observe_launch)
    monkeypatch.setattr(credential_materializers, "run_runtime_command", observe_launch)
    result = await journey.realizer.execute(admitted, plan)
    assert result.failure_class is None, result.summary
    expected = edited_setting if edit_profile and not same_step else original_setting
    assert observed_configuration() == expected
    assert observed_saved_bytes == ([native.GENERATED] if same_step else [None])
    assert journey.docker.create_count == 2


@pytest.mark.parametrize("native_journey", [_CLAUDE], indirect=True)
async def test_fixed_serialized_claude_oauth_files_use_the_shared_lifecycle(
    native_journey,
    monkeypatch,
):
    journey = native_journey
    await pool.execute_selected_pool_source(journey, monkeypatch, fixed_serialized=True)
    assert json.loads(journey.docker.claude_preferences["settings.json"]) == {
        "language": "English"
    }
    assert journey.docker.claude_preferences["CLAUDE.md"] == (
        "Admitted instructions for /workspaces/run.\n"
    )
    assert journey.docker.volumes == {"journey_oauth": {}}
