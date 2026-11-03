"""A pool remains dynamic until its existing manager grants the exact account."""

from contextlib import asynccontextmanager
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from api_service.api.routers.executions import router, _get_service
from api_service.db.base import get_async_session
from api_service.db.models import (
    AgentSkillDefinition,
    ManagedAgentProviderProfile,
    TemporalExecutionCanonicalRecord,
    ProviderProfileSlotLease,
    OmnigentExecutionPlanRecord,
)
from api_service.services.native_pool_execution_plan import prepare_selected_pool_plan
from api_service.services.omnigent_agent_bootstrap_service import (
    reconcile_bootstrap_agent_profile,
)
from moonmind.config.settings import settings
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.activities.omnigent_session_activities import (
    _plan_capacity_authority,
)
from moonmind.schemas.omnigent_session_models import OmnigentSessionAdmissionDecision
from moonmind.schemas.agent_skill_models import SkillSelector
from moonmind.workflows.agent_skills.agent_skills_activities import (
    AgentSkillsActivities,
)
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    TemporalArtifactService,
    TemporalArtifactRepository,
)
from moonmind.workflows.temporal.service import TemporalExecutionService
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
from moonmind.omnigent.settings import resolved_server_url
from tests.integration.omnigent import (
    test_generic_native_durable_journey as native_fixture,
)
from tests.unit.api.routers.test_execution_saved_profile_boundaries import (
    agent_requests,
)
from tests.unit.api.routers.test_executions import _override_user_dependencies

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
native_journey = native_fixture.native_journey
session_factory = native_fixture.session_factory


@pytest.mark.parametrize(
    "native_journey", ["codex-native", "claude-native"], indirect=True
)
@pytest.mark.parametrize("scenario", [
    "ordinary", "disabled", "publish", "model_alias", "tier_resolution", "native_tier_option",
    "tier_launch_defaults", "tier_launch_explicit", "tier_publish", "retained_handoff", "tier_environment", "tier_removed_default",
    "explicit_work_branch", "invalid_work_branch_origin", "missing_generated_work_branch", "explicit_native_option",
])
async def test_pool_selected_account_compiles_once_and_preserves_its_original_inputs(
    native_journey, monkeypatch, scenario
):
    await execute_selected_pool_source(native_journey, monkeypatch, scenario=scenario)


@pytest.mark.parametrize(
    "native_journey", ["codex-native-api-key", "claude-native-api-key"], indirect=True
)
async def test_pool_selected_api_key_settings_reach_the_shared_host(native_journey, monkeypatch):
    await execute_selected_pool_source(native_journey, monkeypatch)


@pytest.mark.parametrize(
    "native_journey", ["codex-native", "claude-native"], indirect=True,
)
async def test_fixed_serialized_account_uses_the_same_fenced_plan_owner(
    native_journey, monkeypatch,
):
    await execute_selected_pool_source(native_journey, monkeypatch, fixed_serialized=True)


def _submission(payload, *, fixed_serialized, runtime):
    if not fixed_serialized:
        return payload
    parameters = payload["payload"]
    selected = parameters["workflow"]["runtime"]
    selected.pop("profileSelector")
    selected["providerProfileRef"] = native_fixture.PROFILE
    if "workspace" in parameters:
        parameters["workspaceSpec"] = parameters.pop("workspace")
    return {
        "workflowType": "MoonMind.UserWorkflow",
        "initialParameters": {
            **parameters, "targetRuntime": runtime,
            "profileId": native_fixture.PROFILE,
            "operatorExtension": {"accepted": "preserve-original-input"},
        },
    }


async def execute_selected_pool_source(
    native_journey, monkeypatch, *, scenario="ordinary", include_successor=False,
    fixed_serialized=False, explicit_scratch=False,
    runtime_selection=None, step_runtime=None,
):
    """Exercise the first admitted pool turn, optionally retaining a second step."""
    journey = native_journey
    publish_scenario = scenario in {"publish", "tier_publish"}
    tier_launch = scenario in {"tier_launch_defaults", "tier_launch_explicit", "tier_publish"}
    work_branch_scenario = scenario in {"explicit_work_branch", "invalid_work_branch_origin", "missing_generated_work_branch"}
    runtime = "codex_cli" if journey.harness == "codex-native" else "claude_code"
    monkeypatch.setattr(settings.workflow, "default_runtime", runtime)
    monkeypatch.setattr(settings.workflow, "github_repository", "")
    inventory = await OmnigentHttpClient(base_url=resolved_server_url()).list_agents()
    async with journey.session_factory() as session:
        repository = None
        if publish_scenario or work_branch_scenario:
            from api_service.services.repository_connections import (
                RepositoryConnectionService,
            )
            from tests.helpers.repository_connections import (
                github_pat_connection,
                github_repository_assignment,
            )

            await RepositoryConnectionService(session).create_connection(
                github_pat_connection("pool-repository", "POOL_TEST_REPOSITORY_TOKEN"),
                actor_ref="system:deployment",
                request_id="pool-publishing-connection",
                principal_ref="system:deployment",
                principal_scope=("system", None),
            )
            await RepositoryConnectionService(session).set_assignment(
                github_repository_assignment(
                    "pool-repository", "MoonLadderStudios/MoonMind"
                ),
                actor_ref="system:deployment",
                request_id="pool-publishing-assignment",
                principal_ref="system:deployment",
                principal_scope=("system", None),
            )
            repository = {
                "provider": "git",
                "connectionRef": "pool-repository",
                "repository": {"name": "MoonLadderStudios/MoonMind"},
                "branch": {"name": "main"},
            }
        provider = await session.get(
            ManagedAgentProviderProfile, native_fixture.PROFILE
        )
        provider.tags = ["selected-pool"]
        if scenario == "model_alias":
            provider.model_overrides = {"selected-pool-alias": journey.server.model}
        if scenario in {"tier_resolution", "native_tier_option", "tier_environment"} or tier_launch:
            provider.model_tiers = [{
                "model": journey.server.model,
                "parameters": (
                    {"metadata": {"operatorTrace": "accepted-tier-default"}}
                    if scenario == "tier_resolution" else (
                        {"commitMessage": "Tier-selected commit", "publishBaseBranch": "tier-selected-target"}
                        if tier_launch else (
                            {"envOverrides": {"MODE": "preserve-native-environment"}}
                            if scenario == "tier_environment" else {"native_option": "preserve-direct-setting"}
                        )
                    )
                ),
            }]
        await session.commit()
        await reconcile_bootstrap_agent_profile(session, inventory=inventory)
        app = FastAPI()
        app.include_router(router)
        service = TemporalExecutionService(session)
        service._client_adapter.start_workflow = AsyncMock(
            return_value=SimpleNamespace(run_id="pool-parent-run")
        )
        app.dependency_overrides[_get_service] = lambda: service
        app.dependency_overrides[get_async_session] = lambda: session
        _override_user_dependencies(app, is_superuser=True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/executions",
                json=_submission({
                    "type": "workflow",
                    "payload": {
                        "workflow": {
                            "instructions": "Generate a report using the selected pool.",
                            "runtime": {
                                "mode": runtime,
                                "profileSelector": {"tagsAll": ["selected-pool"]},
                                **({"parameters": {"commitMessage": "Explicit runtime commit"}}
                                   if scenario == "tier_launch_explicit" else {}),
                                **({"parameters": {
                                    "workBranch": "" if scenario == "missing_generated_work_branch" else "accepted-work-branch",
                                    "workBranchId": "refs/heads/accepted-work-branch",
                                    "workBranchOrigin": "invalid" if scenario == "invalid_work_branch_origin" else "generated" if scenario == "missing_generated_work_branch" else "review_mapping",
                                }} if work_branch_scenario else {}),
                                **({"parameters": {"native_option": "accepted-explicit-native-option"}}
                                   if scenario == "explicit_native_option" else {}),
                                **(runtime_selection if runtime_selection is not None else (
                                    {"modelTier": 1}
                                    if scenario in {"tier_resolution", "native_tier_option", "tier_environment"} or tier_launch
                                    else {"model": "selected-pool-alias" if scenario == "model_alias" else journey.server.model}
                                )),
                            },
                            "steps": [
                                {
                                    "id": "work",
                                    "instructions": "Generate a report.",
                                    "type": "skill",
                                    "skill": {"id": native_fixture._DURABLE_SKILL_NAME},
                                    **({"runtime": step_runtime} if step_runtime is not None else {}),
                                    **(
                                        {
                                            "publishMode": "pr",
                                            **({
                                                "publishBaseBranch": "selected-target",
                                                "commitMessage": "Publish selected pool work",
                                            } if scenario == "publish" else {}),
                                        }
                                        if publish_scenario else {}
                                    ),
                                },
                                *(
                                    [
                                        {
                                            "id": "second",
                                            "instructions": "Continue from the accepted report.",
                                            "type": "skill",
                                            "skill": {
                                                "id": native_fixture._DURABLE_SKILL_NAME
                                            },
                                        }
                                    ]
                                    if include_successor
                                    else []
                                ),
                            ],
                        },
                        "publishMode": "none",
                        **({"repository": repository} if repository else {}),
                        **(
                            {"workspace": {"workspaceSource": {"kind": "scratch"}}}
                            if explicit_scratch else {}
                        ),
                    },
                }, fixed_serialized=fixed_serialized, runtime=runtime),
            )
        assert response.status_code == 201, response.text
        record = await session.get(
            TemporalExecutionCanonicalRecord, response.json()["workflowId"]
        )
        assert record.parameters["targetRuntime"] == runtime
        assert not record.parameters.get("omnigentExecutionPlan")
        request = agent_requests(
            record.parameters, workflow_id=record.workflow_id, run_id=record.run_id
        )["work"]
        assert request.execution_profile_ref == (native_fixture.PROFILE if fixed_serialized else None)
        assert request.profile_selector.tags_all == ([] if fixed_serialized else ["selected-pool"])
        if scenario == "explicit_work_branch":
            assert request.parameters["workBranch"] == "accepted-work-branch"
        if scenario == "explicit_native_option":
            assert request.parameters["native_option"] == "accepted-explicit-native-option"
        if scenario == "tier_removed_default":
            # A recorded request may carry defaults from a previous native
            # resolution. The selected Profile no longer supplies this value.
            request.parameters["commitMessage"] = "Previous injected tier message"
            metadata = request.parameters.setdefault("metadata", {})
            metadata.setdefault("moonmind", {})["modelEffortResolution"] = {
                "tierParameterDefaults": {"commitMessage": "Previous injected tier message"},
            }
        selected = request.model_copy(
            update={"execution_profile_ref": native_fixture.PROFILE}
        )
        if publish_scenario:
            assert record.parameters["publishMode"] == "none"
            assert request.parameters["publishMode"] == "pr"
        original_parameters, original_memo = dict(record.parameters), dict(record.memo)
        parent_id = record.workflow_id
        parent_run = record.run_id
        monkeypatch.setattr(
            "temporalio.activity.info",
            lambda: SimpleNamespace(
                namespace="default",
                workflow_id=parent_id,
                workflow_run_id=parent_run,
                activity_id="resolve-work-skills",
                attempt=1,
            ),
        )
        skills = await AgentSkillsActivities(
            artifact_service=TemporalArtifactService(
                TemporalArtifactRepository(session)
            ),
            async_session_maker=journey.session_factory,
        ).resolve_skills(
            SkillSelector(include=[{"name": native_fixture._DURABLE_SKILL_NAME}]),
            run_id=parent_run,
        )
        selected.resolved_skillset_ref = skills.manifest_ref
        selected.step_execution.resolved_skillset_ref = skills.manifest_ref
        # A queued task keeps its admitted Skill even after catalog removal.
        definition = (
            await session.scalars(
                select(AgentSkillDefinition).where(
                    AgentSkillDefinition.slug == native_fixture._DURABLE_SKILL_NAME
                )
            )
        ).one()
        await session.delete(definition)
        await session.commit()

    @asynccontextmanager
    async def database():
        async with journey.session_factory() as session:
            yield session

    monkeypatch.setattr("api_service.db.base.get_async_session_context", database)
    owner, owner_run = "pool-agent-run", "pool-agent-run-1"
    async with journey.session_factory() as session:
        existing_leases = set(
            await session.scalars(select(ProviderProfileSlotLease.lease_id))
        )
    # Use the existing incremental persistence owner to commit the one grant.
    granted = await TemporalArtifactActivities(None).provider_profile_sync_slot_leases(
        runtime_id=runtime,
        action="grant",
        leases=[
            {
                "lease_id": owner,
                "workflow_id": owner,
                "owner_id": owner,
                "profile_id": native_fixture.PROFILE,
                "owner_kind": "workflow",
                "ownerIsWorkflow": True,
                "purpose": "execution_direct",
                "compatibility_class": "shared_execution",
                "fencing_generation": 7,
                "scope_generation": 3,
                "capacity_scope_ref": f"provider-profile:{native_fixture.PROFILE}",
                "credential_generation": 3,
                "expiresAt": (
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
                "safe_metadata": {"runId": owner_run},
            }
        ],
    )
    assert granted.get("granted") is True
    description = SimpleNamespace(
        raw_description=SimpleNamespace(
            workflow_execution_info=SimpleNamespace(
                execution=SimpleNamespace(workflow_id=owner, run_id=owner_run),
                status=1,
                type=SimpleNamespace(name="MoonMind.AgentRun"),
                parent_execution=SimpleNamespace(
                    workflow_id=parent_id, run_id=parent_run
                ),
            )
        )
    )
    transport = SimpleNamespace(describe_workflow=AsyncMock(return_value=description))
    payload = {
        "owner_id": owner,
        "profile_id": native_fixture.PROFILE,
        "runId": owner_run,
        "fencing_generation": 7,
        "originalProfileRef": native_fixture.PROFILE if fixed_serialized else None,
        "request": selected.model_dump(mode="json", by_alias=True),
    }
    if scenario == "disabled":
        flag = (
            "MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED"
            if runtime == "codex_cli"
            else "MOONMIND_OMNIGENT_GENERIC_CLAUDE_QUALIFIED"
        )
        monkeypatch.setenv(flag, "false")
    async with journey.session_factory() as session:
        previous_plan_count = await session.scalar(
            select(func.count(OmnigentExecutionPlanRecord.plan_ref))
        )
        expected_selection = selected.model_copy(deep=True)
        if scenario in {"tier_resolution", "tier_removed_default"} or tier_launch:
            from moonmind.workflows.temporal.runtime.launcher import ManagedRuntimeLauncher
            current_provider = await session.get(ManagedAgentProviderProfile, native_fixture.PROFILE)
            ManagedRuntimeLauncher._apply_resolved_tier_policy(
                request=expected_selection, profile=current_provider, strategy=None,
            )
        result = await prepare_selected_pool_plan(
            session, runtime_id=runtime, payload=payload, client=transport
        )
        if scenario in {"disabled", "native_tier_option", "tier_environment", "explicit_native_option", "invalid_work_branch_origin", "missing_generated_work_branch"}:
            assert result["disposition"] == "native", result
            if scenario == "native_tier_option":
                assert result["reason"] == "native_parameter_defaults_require_an_equivalent_handoff"
            if scenario == "tier_environment":
                assert result["reason"] == "native_environment_overrides_require_their_current_adapter"
            if scenario == "explicit_native_option":
                assert result["reason"] == "native_runtime_parameters_require_an_equivalent_handoff"
            if scenario in {"invalid_work_branch_origin", "missing_generated_work_branch"}:
                assert result["reason"] == "native_repository_metadata_requires_its_current_adapter"
            assert (
                await session.scalar(
                    select(func.count(OmnigentExecutionPlanRecord.plan_ref))
                )
                == previous_plan_count
            )
            row = (
                await session.scalars(
                    select(ProviderProfileSlotLease).where(
                        ProviderProfileSlotLease.lease_id == owner
                    )
                )
            ).one()
            assert row.lease_state == "held" and row.execution_plan_ref is None
            return None
        assert result["disposition"] == "shared", result
        shared = AgentExecutionRequest.model_validate(result["request"])
        assert shared.agent_kind == "external" and shared.agent_id == "omnigent"
        assert shared.execution_profile_ref == native_fixture.PROFILE
        assert shared.idempotency_key == request.idempotency_key
        assert shared.instruction_ref == request.instruction_ref
        assert shared.runtime_command == request.runtime_command
        assert shared.execution_budget == request.execution_budget
        assert shared.terminal_contract == request.terminal_contract
        assert shared.profile_selector == request.profile_selector
        if scenario == "explicit_work_branch":
            for key in ("workBranch", "workBranchId", "workBranchOrigin"):
                assert shared.parameters[key] == request.parameters[key]
            assert shared.workspace_spec.get("targetBranch") == request.workspace_spec.get("targetBranch")
        if scenario in {"tier_resolution", "tier_removed_default"} or tier_launch:
            if scenario == "tier_resolution":
                assert "operatorTrace" not in expected_selection.parameters["metadata"]
            if tier_launch:
                for key in ("commitMessage", "publishBaseBranch"):
                    assert shared.parameters[key] == expected_selection.parameters[key]
                assert shared.parameters["commitMessage"] == (
                    "Explicit runtime commit" if scenario == "tier_launch_explicit" else "Tier-selected commit"
                )
            assert shared.parameters["metadata"] == expected_selection.parameters["metadata"]
            assert ("modelTierResolution" in shared.parameters) == ("modelTierResolution" in expected_selection.parameters)
            assert shared.parameters.get("modelTierResolution") == expected_selection.parameters.get("modelTierResolution")
        if scenario == "tier_removed_default":
            assert "commitMessage" not in expected_selection.parameters
            assert "commitMessage" not in shared.parameters
        if fixed_serialized:
            assert record.parameters["operatorExtension"] == {"accepted": "preserve-original-input"}
            assert shared.parameters.get("operatorExtension") == request.parameters.get("operatorExtension")
        artifacts = TemporalArtifactService(TemporalArtifactRepository(session))
        access = ArtifactAccessContext.for_execution(parent_id)
        _, frozen_skill_bytes = await artifacts.read(
            artifact_id=skills.manifest_ref, access=access
        )
        _, shared_skill_bytes = await artifacts.read(
            artifact_id=shared.resolved_skillset_ref, access=access
        )
        assert json.loads(shared_skill_bytes) == json.loads(frozen_skill_bytes)
        plan = await DbExecutionPlanStore(journey.session_factory).load(
            shared.omnigent_execution_plan.plan_ref
        )
        if scenario == "explicit_work_branch":
            from moonmind.omnigent.harness_platform.credential_bindings import repository_bindings_of

            bindings = repository_bindings_of(plan.payload.credentialBindings)
            assert bindings and all(binding.connectionRef == "pool-repository" for binding in bindings.values())
            assert all(binding.repositoryRole != "destination_write" for binding in bindings.values())
        if publish_scenario:
            from moonmind.omnigent.harness_platform.credential_bindings import (
                repository_bindings_of,
            )

            bindings = repository_bindings_of(plan.payload.credentialBindings)
            assert any(
                binding.repositoryRole == "destination_write"
                for binding in bindings.values()
            )
            assert shared.parameters["publishMode"] == "pr"
            assert shared.parameters["publishBaseBranch"] == (
                "tier-selected-target" if tier_launch else "selected-target"
            )
            assert shared.parameters["commitMessage"] == (
                "Tier-selected commit" if tier_launch else "Publish selected pool work"
            )
        assert plan.payload.harnessId == journey.harness
        if scenario == "model_alias":
            assert request.parameters["model"] == "selected-pool-alias"
            assert shared.parameters["model"] == journey.server.model
            assert plan.payload.modelConfig.qualifiedId == journey.server.model
        assert (
            plan.payload.credentialBindings["primary-model"].providerProfileRef
            == native_fixture.PROFILE
        )
        assert (
            shared.omnigent_execution_plan.task_input_snapshot_ref
            == original_memo["task_input_snapshot_ref"]
        )
        row = (
            await session.scalars(
                select(ProviderProfileSlotLease).where(
                    ProviderProfileSlotLease.lease_id == owner
                )
            )
        ).one()
        if scenario == "retained_handoff":
            # Retained grants from the previous writer have model evidence,
            # but no frozen launch-parameter field. Keep their interpretation.
            metadata = dict(row.safe_metadata_json)
            metadata["preparedPoolPlan"] = {
                key: value for key, value in metadata["preparedPoolPlan"].items()
                if key != "launchParameters"
            }
            row.safe_metadata_json = metadata
        selected_lease_id = row.id
        assert row.fencing_generation == 7 and row.scope_generation == 3
        assert row.execution_plan_ref == plan.planRef
        if scenario == "explicit_work_branch":
            for key in ("workBranch", "workBranchId", "workBranchOrigin"):
                assert row.safe_metadata_json["preparedPoolPlan"]["launchParameters"][key] == request.parameters[key]
        assert set(
            await session.scalars(select(ProviderProfileSlotLease.lease_id))
        ) == existing_leases | {owner}
        plan_count = await session.scalar(
            select(func.count(OmnigentExecutionPlanRecord.plan_ref))
        )
        provider = await session.get(
            ManagedAgentProviderProfile, native_fixture.PROFILE
        )
        provider.enabled = False
        if scenario in {"tier_resolution", "tier_removed_default"} or tier_launch:
            provider.model_tiers = [{
                "model": journey.server.model,
                "parameters": {"metadata": {"operatorTrace": "later-profile-edit"},
                               "commitMessage": "A later Profile edit", "publishBaseBranch": "later-target"},
            }]
        await session.commit()
        repeated = await prepare_selected_pool_plan(
            session, runtime_id=runtime, payload=payload, client=transport
        )
        assert (
            repeated["request"]["omnigentExecutionPlan"]
            == result["request"]["omnigentExecutionPlan"]
        )
        if scenario in {"retained_handoff", "tier_removed_default"}:
            assert repeated["request"]["parameters"] == result["request"]["parameters"]
        if scenario == "explicit_work_branch":
            for key in ("workBranch", "workBranchId", "workBranchOrigin"):
                assert repeated["request"]["parameters"][key] == result["request"]["parameters"][key]
        if scenario in {"tier_resolution", "tier_removed_default"} or tier_launch:
            assert repeated["request"]["parameters"]["metadata"] == result["request"]["parameters"]["metadata"]
            assert ("modelTierResolution" in repeated["request"]["parameters"]) == ("modelTierResolution" in result["request"]["parameters"])
            assert repeated["request"]["parameters"].get("modelTierResolution") == result["request"]["parameters"].get("modelTierResolution")
            if tier_launch:
                for key in ("commitMessage", "publishBaseBranch"):
                    assert repeated["request"]["parameters"][key] == result["request"]["parameters"][key]
        assert (
            await session.scalar(
                select(func.count(OmnigentExecutionPlanRecord.plan_ref))
            )
            == plan_count
        )
        record = await session.get(TemporalExecutionCanonicalRecord, parent_id)
        assert record.parameters == original_parameters and record.memo == original_memo
        changed = {
            **payload,
            "request": {**payload["request"], "instructionRef": "A different task."},
        }
        with pytest.raises(ValueError, match="recorded request"):
            await prepare_selected_pool_plan(
                session, runtime_id=runtime, payload=changed, client=transport
            )
        with pytest.raises(ValueError, match="fenced"):
            await prepare_selected_pool_plan(
                session,
                runtime_id=runtime,
                payload={**payload, "fencing_generation": 6},
                client=transport,
            )
        assert (
            await session.scalar(
                select(func.count(OmnigentExecutionPlanRecord.plan_ref))
            )
            == plan_count
        )
        provider.enabled = True
        await session.commit()

    if publish_scenario or scenario == "explicit_work_branch":
        return None

    # The real AgentRun capacity handoff reuses the selected grant. Only
    # Temporal signal delivery and host-capacity observation are transports.
    monkeypatch.setattr(
        "temporalio.workflow.info",
        lambda: SimpleNamespace(
            workflow_id=owner,
            run_id=owner_run,
            namespace="default",
            task_queue="test",
            parent=None,
        ),
    )
    monkeypatch.setattr("temporalio.workflow.patched", lambda marker: True)
    monkeypatch.setattr("temporalio.workflow.now", lambda: datetime.now(timezone.utc))
    run = MoonMindAgentRun()
    requests = []

    async def ensure(manager_id, runtime_id, *, request_slot=True, **kwargs):
        if request_slot:
            requests.append(kwargs)
            run.slot_assigned(
                {"profile_id": native_fixture.PROFILE, "fencing_generation": 7}
            )
        return SimpleNamespace(signal=AsyncMock())

    run._ensure_manager_and_signal = ensure
    run._sync_manager_profiles = AsyncMock()
    run._await_omnigent_host_capacity = AsyncMock()
    monkeypatch.setattr(
        "api_service.db.base.async_session_maker", journey.session_factory
    )
    authority = await _plan_capacity_authority(
        plan, execution_profile_ref=native_fixture.PROFILE
    )
    admission = OmnigentSessionAdmissionDecision(
        admitted=False,
        reasonCode="realizer_managed_lifecycle",
        admissionMode="enabled",
        admittedFeatureGeneration="omnigent-session-v1",
        executionRealizerRef=plan.payload.executionRealizerRef,
        **authority,
    )
    admitted = await run._admit_omnigent_capacity_before_execution(
        request=shared, admission=admission, parent_info=None
    )
    assert len(requests) == 1
    if fixed_serialized:
        assert requests[0]["execution_profile_ref"] == native_fixture.PROFILE
        assert "profile_selector" not in requests[0]
    else:
        assert requests[0]["profile_selector"] == shared.profile_selector.model_dump(
            by_alias=True, exclude_none=True
        )
    assert admitted.admitted_provider_capacity.lease_owner_id == owner
    assert admitted.admitted_provider_capacity.execution_plan_ref == plan.planRef
    journey.plan, journey.request, journey.provider_lease_id = (
        plan,
        admitted,
        selected_lease_id,
    )
    journey.manager.plan, journey.manager.request = plan, admitted
    await journey.gateway.admit_execution_plan_inputs(request=admitted, plan=plan)
    journey.docker.host_class, _ = await journey.services.planned_host_resolver(
        plan, request=admitted
    )
    terminal = await native_fixture.assert_native_turn_saved_and_cleaned(journey)
    return SimpleNamespace(
        result=terminal,
        original=request,
        selected=selected,
        parameters=original_parameters,
        parent_id=parent_id,
        parent_run=parent_run,
        owner=owner,
        owner_run=owner_run,
        lease_payload=payload,
    )
