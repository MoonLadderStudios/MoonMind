"""Independent step model choices receive their own immutable shared plan."""

from types import SimpleNamespace
from copy import deepcopy
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from api_service.api.routers.executions import (
    _build_recurring_target,
    _get_service,
    _resolve_recurring_runtime_metadata,
    router,
)
from api_service.db.base import get_async_session
from api_service.db.models import ManagedAgentProviderProfile, TemporalExecutionCanonicalRecord
from api_service.services.omnigent_agent_bootstrap_service import reconcile_bootstrap_agent_profile
from api_service.services.omnigent_agent_profile_service import synchronize_omnigent_harness_catalog
from api_service.services.omnigent_execution_plan_service import readmit_frozen_execution_plan
from api_service.services.recurring_workflows_service import RecurringWorkflowsService
from moonmind.config.settings import settings
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.harness_platform.execution_plan import bind_omnigent_model_selection
from moonmind.omnigent.settings import resolved_server_url
from moonmind.security.operator_admission import OperatorAdmission
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
from moonmind.workflows.temporal.service import TemporalExecutionService
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext, TemporalArtifactAuthorizationError, TemporalArtifactRepository,
    TemporalArtifactService,
)
from tests.integration.omnigent import test_generic_native_durable_journey as native_fixture
from tests.integration.omnigent.test_generic_native_durable_journey import _DURABLE_SKILL_NAME, PROFILE
from tests.unit.api.routers.test_execution_saved_profile_boundaries import agent_requests
from tests.helpers.execution_api import _override_user_dependencies

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
native_journey = native_fixture.native_journey
session_factory = native_fixture.session_factory


@pytest.mark.parametrize("native_journey", ["codex-native"], indirect=True)
@pytest.mark.parametrize("identity", [{}, {"stepId": "selected"}, {"id": "selected"}])
async def test_scheduled_specialized_step_uses_canonical_identity(
    native_journey, monkeypatch, identity
):
    journey = native_journey
    monkeypatch.setattr(settings.workflow, "default_runtime", "omnigent")
    monkeypatch.setattr(settings.workflow, "github_repository", "")
    inventory = await OmnigentHttpClient(base_url=resolved_server_url()).list_agents()
    async with journey.session_factory() as session:
        await reconcile_bootstrap_agent_profile(session, inventory=inventory)
        await synchronize_omnigent_harness_catalog(session)
        request = {
            "workflowType": "MoonMind.UserWorkflow",
            "initialParameters": {
                "workflow": {
                    "instructions": "Review a report on schedule.",
                    "runtime": {
                        "mode": "codex_cli",
                        "providerProfileRef": PROFILE,
                        "model": journey.server.model,
                    },
                    "steps": [
                        {
                            **identity,
                            "instructions": "Review the report.",
                            "type": "skill",
                            "skill": {"id": _DURABLE_SKILL_NAME},
                            "runtime": {"model": "example/independent-model"},
                        },
                    ],
                },
                "publishMode": "none",
                "workspace": {"workspaceSource": {"kind": "scratch"}},
            },
        }
        metadata = await _resolve_recurring_runtime_metadata(request, session=session)
        target = _build_recurring_target(request, runtime_metadata=metadata)
        adapter = SimpleNamespace(create_schedule=AsyncMock())
        service = RecurringWorkflowsService(session, temporal_client_adapter=adapter)
        definition = await service.create_definition(
            name="Scheduled step identity",
            description=None,
            enabled=True,
            schedule_type="cron",
            cron="0 * * * *",
            timezone="UTC",
            scope_type="personal",
            scope_ref=None,
            owner_user_id=None,
            target=target,
            policy={},
            actor=OperatorAdmission(via="schedule-step-fixture"),
        )
        parameters = definition.target["initialParameters"]
        step_id = "selected" if identity else "step-1"
        assert parameters["workflow"]["steps"][0]["id"] == step_id
        assert set(parameters["omnigentStepExecutionPlans"]) == {step_id}
        requests = agent_requests(parameters)
        binding = requests[step_id].omnigent_execution_plan
        assert binding is not None
        assert binding.plan_ref != parameters["omnigentExecutionPlan"]["planRef"]
        plan = await DbExecutionPlanStore(journey.session_factory).load(binding.plan_ref)
        assert plan.payload.modelConfig.qualifiedId == "example/independent-model"
        assert plan.payload.harnessId == "codex-native"
        adapter.create_schedule.assert_awaited_once()
        dispatched = adapter.create_schedule.await_args.kwargs["workflow_input"][
            "initial_parameters"
        ]
        assert (
            dispatched["omnigentStepExecutionPlans"]
            == parameters["omnigentStepExecutionPlans"]
        )
        assert dispatched["workflow"]["steps"][0]["id"] == step_id


@pytest.mark.parametrize("native_journey", ["codex-native"], indirect=True)
@pytest.mark.parametrize(
    "change,current",
    [
        ("release", ("gpt-7-sol", "medium")),
        ("pin", ("gpt-6-sol", "medium")),
        ("effort", ("gpt-7-sol", "high")),
        ("runtime_default", None),
        ("adopted", ("gpt-7-sol", "medium")),
        ("default_tier", None),
    ],
    ids=["release", "pin", "effort", "runtime_default", "adopted", "default_tier"],
)
@pytest.mark.parametrize(
    "selection,source",
    [
        ({}, "profile_default_tier"),
        ({"modelTier": 2}, "requested_tier"),
        ({"model": "gpt-6.1-sol", "effort": "medium"}, "task_override"),
    ],
    ids=["omitted", "tier", "custom"],
)
async def test_fresh_occurrence_launches_its_current_family_release(
    native_journey, monkeypatch, selection, source, change, current
):
    """An occurrence launches the tier policy current when it is admitted.

    After the schedule is saved, Sol 7 is released and the operator may pin
    Tier 2 or change its effort. Custom selections keep their authored pair.
    """

    from api_service.db import base
    from moonmind.provider_profiles.model_families import (
        first_party_family_tier_template,
    )
    from moonmind.schemas.agent_runtime_models import OmnigentExecutionPlanBinding
    from moonmind.workflows.executions.model_resolver import resolve_model_effort
    from moonmind.workflows.temporal.activities import omnigent_activities
    from moonmind.workflows.temporal.activities.omnigent_session_activities import (
        _reconstruct_plan_bound_request,
    )

    journey = native_journey
    monkeypatch.setattr(settings.workflow, "default_runtime", "omnigent")
    monkeypatch.setattr(settings.workflow, "github_repository", "")
    monkeypatch.setattr(base, "async_session_maker", journey.session_factory)
    inventory = await OmnigentHttpClient(base_url=resolved_server_url()).list_agents()
    tiers, default_tier = first_party_family_tier_template("codex_cli", "openai")
    # Tier 2 set to Runtime default in the editor: no family, no model.
    runtime_default_tier = {
        "label": "Sol",
        "model": None,
        "effort": None,
        "parameters": {},
        "annotations": {},
    }
    async with journey.session_factory() as session:
        provider = await session.get(ManagedAgentProviderProfile, PROFILE)
        provider.model_tiers = (
            [tiers[0], runtime_default_tier, *tiers[2:]]
            if change == "adopted"
            else tiers
        )
        provider.default_model_tier = default_tier
        provider.model_catalog_evidence_json = {
            "models": [{"id": "gpt-6-sol"}, {"id": "gpt-6.1-sol"}]
        }
        await session.commit()
        await reconcile_bootstrap_agent_profile(session, inventory=inventory)
        await synchronize_omnigent_harness_catalog(session)
        request = {
            "workflowType": "MoonMind.UserWorkflow",
            "initialParameters": {
                "workflow": {
                    "instructions": "Review a report on schedule.",
                    "runtime": {
                        "mode": "codex_cli",
                        "providerProfileRef": PROFILE,
                        **selection,
                    },
                },
                "publishMode": "none",
                "workspace": {"workspaceSource": {"kind": "scratch"}},
            },
        }
        metadata = await _resolve_recurring_runtime_metadata(request, session=session)
        target = _build_recurring_target(request, runtime_metadata=metadata)
        adapter = SimpleNamespace(create_schedule=AsyncMock())
        definition = await RecurringWorkflowsService(
            session, temporal_client_adapter=adapter
        ).create_definition(
            name="Nightly family-tier report",
            description=None,
            enabled=True,
            schedule_type="cron",
            cron="0 * * * *",
            timezone="UTC",
            scope_type="personal",
            scope_ref=None,
            owner_user_id=None,
            target=target,
            policy={},
            actor=OperatorAdmission(via="schedule-family-tier-fixture"),
        )
        # Temporal starts each occurrence from the schedule action's input.
        saved = deepcopy(
            adapter.create_schedule.await_args.kwargs["workflow_input"][
                "initial_parameters"
            ]
        )
        # Saving records the resolution, never an omitted or tier selection
        # turned into Custom.
        if change == "adopted" and source != "task_override":
            assert saved["modelSource"] == "runtime_default"
        else:
            assert saved["model"] == "gpt-6.1-sol"
            assert saved["modelSource"] == source
            assert saved["effort"] == "medium"
        # Sol 7 is released after the definition was saved, and the operator
        # then edits Tier 2 without revising the schedule.
        provider = await session.get(ManagedAgentProviderProfile, PROFILE)
        provider.model_catalog_evidence_json = {
            "models": [{"id": "gpt-6-sol"}, {"id": "gpt-6.1-sol"}, {"id": "gpt-7-sol"}]
        }
        edited = [dict(tier) for tier in provider.model_tiers]
        if change == "pin":
            edited[1].update(model_mode="pinned", model="gpt-6-sol")
        elif change == "effort":
            edited[1]["effort"] = "high"
        elif change == "runtime_default":
            edited[1] = dict(runtime_default_tier)
        elif change == "adopted":
            edited = [dict(tier) for tier in tiers]
        elif change == "default_tier":
            provider.model_catalog_evidence_json = {
                "models": [
                    *provider.model_catalog_evidence_json["models"],
                    {"id": "gpt-6.1-terra"},
                ]
            }
            provider.default_model_tier = 3
        provider.model_tiers = edited
        await session.commit()
        # What a new admission of the same authored selection resolves now.
        admission = resolve_model_effort(
            runtime_id="codex_cli",
            profile=provider,
            authored_runtime=selection or None,
            require_launch_ready=False,
            workflow_settings=settings.workflow,
        )
    if source == "task_override":
        expected, expected_effort = ("gpt-6.1-sol", "medium")
    else:
        expected, expected_effort = admission.model, admission.effort
        if current is not None:
            assert (expected, expected_effort) == current
        if change == "runtime_default":
            assert admission.model_source == "runtime_default"
        if change == "default_tier":
            assert expected == (
                "gpt-6.1-terra" if source == "profile_default_tier" else "gpt-7-sol"
            )

    occurrence = "mm:family-tier-occurrence"
    monkeypatch.setattr(
        omnigent_activities.activity,
        "info",
        lambda: SimpleNamespace(
            workflow_id=occurrence,
            workflow_run_id="occurrence-run",
            namespace="default",
        ),
    )
    admit = omnigent_activities.omnigent_admit_scheduled_occurrence_plan_activity
    admitted = await admit(
        {"workflowId": occurrence, "initialParameters": deepcopy(saved)}
    )
    assert admitted["disposition"] == "admitted", admitted
    binding = OmnigentExecutionPlanBinding.model_validate(
        admitted["omnigentExecutionPlan"]
    )
    plan = await DbExecutionPlanStore(journey.session_factory).load(binding.plan_ref)
    assert plan.payload.modelConfig.qualifiedId == expected
    assert plan.payload.modelConfig.effort == expected_effort
    assert binding.task_input_snapshot_ref == (
        saved["omnigentExecutionPlan"]["taskInputSnapshotRef"]
    )

    launched = await _reconstruct_plan_bound_request(
        binding=binding,
        plan=plan,
        workflow_id=occurrence,
        step_execution_id=f"{occurrence}:run:execution:1",
        agent_run_id="agent-run-1",
    )

    assert launched.parameters["model"] == expected
    assert launched.parameters["effort"] == expected_effort
    assert launched.parameters["omnigent"]["session"]["modelOverride"] == expected
    assert launched.parameters["omnigent"]["session"]["reasoningEffort"] == (
        expected_effort
    )
    resolution = launched.parameters["modelTierResolution"]
    assert (resolution["resolvedModel"], resolution["resolvedEffort"]) == (
        expected,
        expected_effort,
    )
    if source != "task_override":
        # Tier, label, mode, family status and sources are the occurrence
        # admission's, not the ones recorded when the schedule was saved.
        assert resolution == {**admission.as_metadata(), "providerProfileId": PROFILE}
        assert launched.parameters["workflow"]["runtime"]["modelTierResolution"] == (
            resolution
        )
    # Sol 8 is released while the occurrence's Step runs.
    async with journey.session_factory() as session:
        provider = await session.get(ManagedAgentProviderProfile, PROFILE)
        provider.model_catalog_evidence_json = {
            "models": [
                *provider.model_catalog_evidence_json["models"],
                {"id": "gpt-8-sol"},
            ]
        }
        await session.commit()
        if change == "release" and source != "task_override":
            # A new admission would now resolve the newer release.
            assert (
                resolve_model_effort(
                    runtime_id="codex_cli",
                    profile=provider,
                    authored_runtime=selection or None,
                    require_launch_ready=False,
                    workflow_settings=settings.workflow,
                ).model
                == "gpt-8-sol"
            )
    # The interrupted Step's successor is admitted with the occurrence's pair
    # and resolution. The occurrence workflow carries its admitted plan into
    # its input; the rest is the schedule's saved selection.
    occurrence_input = {**deepcopy(saved), **admitted}
    occurrence_input.pop("disposition")
    successor = await omnigent_activities.omnigent_admit_step_successor_plan_activity(
        {"workflowId": occurrence, "initialParameters": occurrence_input}
    )
    assert successor["disposition"] == "admitted", successor
    successor_binding = OmnigentExecutionPlanBinding.model_validate(
        successor["omnigentExecutionPlan"]
    )
    assert successor_binding.plan_ref != binding.plan_ref
    successor_plan = await DbExecutionPlanStore(journey.session_factory).load(
        successor_binding.plan_ref
    )
    assert successor_plan.payload.modelConfig.qualifiedId == expected
    assert successor_plan.payload.modelConfig.effort == expected_effort
    relaunched = await _reconstruct_plan_bound_request(
        binding=successor_binding,
        plan=successor_plan,
        workflow_id=occurrence,
        step_execution_id=f"{occurrence}:run:execution:2",
        agent_run_id="agent-run-2",
    )
    assert relaunched.parameters["model"] == expected
    assert relaunched.parameters["effort"] == expected_effort
    assert relaunched.parameters["modelTierResolution"] == resolution
    # The definition keeps the selection it recorded when it was saved.
    async with journey.session_factory() as session:
        stored = await session.get(type(definition), definition.id)
        assert stored.target["initialParameters"]["model"] == saved["model"]
        assert stored.target["initialParameters"]["effort"] == saved["effort"]
        assert stored.target["initialParameters"]["omnigentExecutionPlan"] == (
            saved["omnigentExecutionPlan"]
        )


@pytest.mark.parametrize("native_journey", ["codex-native"], indirect=True)
async def test_default_tier_rereads_a_catalog_that_omitted_its_family(
    native_journey, monkeypatch
):
    """A cached catalog without Sol refuses Tier 2 only while it is current.

    After the observation's refresh interval, or once the credential is
    replaced, the default-tier submission itself is admitted and its launch's
    exact-host read records the route's catalog again.
    """

    from datetime import UTC, datetime, timedelta

    from api_service.db import base
    from moonmind.omnigent.host_services import attestation
    from moonmind.provider_profiles.model_families import (
        first_party_family_tier_template,
    )

    journey = native_journey
    monkeypatch.setattr(settings.workflow, "default_runtime", "omnigent")
    monkeypatch.setattr(settings.workflow, "github_repository", "")
    monkeypatch.setattr(base, "async_session_maker", journey.session_factory)
    inventory = await OmnigentHttpClient(base_url=resolved_server_url()).list_agents()
    tiers, default_tier = first_party_family_tier_template("codex_cli", "openai")

    def observed(generation, *, age):
        return {
            "source": "exact_host_model_options",
            "credentialGeneration": generation,
            "validatedAt": (datetime.now(UTC) - age).isoformat(),
            "models": [
                {"qualifiedId": model}
                for model in ("gpt-6-astra", "gpt-6.1-terra", "gpt-6-luna")
            ],
        }

    async def set_profile(**values):
        async with journey.session_factory() as session:
            provider = await session.get(ManagedAgentProviderProfile, PROFILE)
            for key, value in values.items():
                setattr(provider, key, value)
            await session.commit()
            return int(provider.credential_generation)

    generation = await set_profile(
        model_tiers=tiers,
        default_model_tier=default_tier,
    )
    await set_profile(
        model_catalog_evidence_json=observed(generation, age=timedelta(0))
    )
    async with journey.session_factory() as session:
        await reconcile_bootstrap_agent_profile(session, inventory=inventory)
        await synchronize_omnigent_harness_catalog(session)
    plans = DbExecutionPlanStore(journey.session_factory)

    async def submit():
        async with journey.session_factory() as session:
            provider = await session.get(ManagedAgentProviderProfile, PROFILE)
            app = FastAPI()
            app.include_router(router)
            service = TemporalExecutionService(session)
            service._client_adapter.start_workflow = AsyncMock(
                return_value=SimpleNamespace(run_id="catalog-refresh-run")
            )
            app.dependency_overrides[_get_service] = lambda: service
            app.dependency_overrides[get_async_session] = lambda: session
            _override_user_dependencies(app, is_superuser=True)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/executions",
                    json={
                        "type": "workflow",
                        "payload": {
                            "workflow": {
                                "instructions": "Summarize a report.",
                                "runtime": {
                                    "mode": provider.runtime_id,
                                    "providerProfileRef": PROFILE,
                                },
                            },
                            "publishMode": "none",
                            "workspace": {"workspaceSource": {"kind": "scratch"}},
                        },
                    },
                )
            if response.status_code != 201:
                return response, None, None
            record = await session.get(
                TemporalExecutionCanonicalRecord, response.json()["workflowId"]
            )
            binding = record.parameters["omnigentExecutionPlan"]
            plan = await plans.load(binding["planRef"])
            # The plan's model is the admission's resolution, which the
            # execution records with its family provenance.
            resolution = record.parameters["modelTierResolution"]
            assert plan.payload.modelConfig.qualifiedId == resolution["resolvedModel"]
            return response, plan, resolution

    refused, _, _ = await submit()
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "model_tier_family_unavailable"

    # An observation older than its refresh interval cannot prove Sol is gone.
    await set_profile(
        model_catalog_evidence_json=observed(generation, age=timedelta(days=7))
    )
    admitted, plan, resolution = await submit()
    assert admitted.status_code == 201, admitted.text
    assert resolution["resolvedModel"] == "gpt-6.1-sol"
    assert resolution["effectiveModelTier"] == 2
    assert resolution["familyResolutionStatus"] == "unverified"

    # That launch's exact-host read records the route's current catalog.
    await attestation._record_profile_catalog(
        plan=plan,
        credential_handles=[
            {"providerProfileRef": PROFILE, "credentialGeneration": generation}
        ],
        model_options={
            "models": [
                {"id": model, "model": model}
                for model in ("gpt-6-astra", "gpt-7-sol", "gpt-6.1-terra")
            ]
        },
    )
    refreshed, _, resolution = await submit()
    assert refreshed.status_code == 201, refreshed.text
    assert resolution["resolvedModel"] == "gpt-7-sol"
    assert resolution["familyResolutionStatus"] == "resolved"

    # A reconnected credential is another account until its own launch reports.
    await set_profile(
        credential_generation=generation + 1,
        model_catalog_evidence_json=observed(generation, age=timedelta(0)),
    )
    reconnected, _, resolution = await submit()
    assert reconnected.status_code == 201, reconnected.text
    assert resolution["resolvedModel"] == "gpt-6.1-sol"
    assert resolution["familyResolutionStatus"] == "unverified"


@pytest.mark.parametrize("native_journey", ["codex-native", "claude-native", "opencode-native"], indirect=True)
@pytest.mark.parametrize("specialization", ["model", "effort", "clear_effort", "tier"])
async def test_independent_step_model_uses_its_own_shared_plan(native_journey, monkeypatch, specialization):
    journey = native_journey
    monkeypatch.setattr(settings.workflow, "default_runtime", "omnigent")
    monkeypatch.setattr(settings.workflow, "github_repository", "")
    inventory = await OmnigentHttpClient(base_url=resolved_server_url()).list_agents()
    async with journey.session_factory() as session:
        provider = await session.get(ManagedAgentProviderProfile, PROFILE)
        provider.model_tiers = [
            {"model": journey.server.model, "effort": "medium"},
            {"model": "example/independent-model", "effort": "high"},
        ]
        await session.commit()
        root_runtime = {"mode": provider.runtime_id, "providerProfileRef": PROFILE, "modelTier": 1}
        step_runtime, expected_model, expected_effort = {
            "model": ({"model": "example/independent-model", "effort": "medium"}, "example/independent-model", "medium"),
            "effort": ({"model": journey.server.model, "effort": "high"}, journey.server.model, "high"),
            "clear_effort": ({"model": journey.server.model, "effort": None}, journey.server.model, "high" if journey.harness == "codex-native" else None),
            "tier": ({"modelTier": 2}, "example/independent-model", "high"),
        }[specialization]
        await reconcile_bootstrap_agent_profile(session, inventory=inventory)
        await synchronize_omnigent_harness_catalog(session)
        app = FastAPI()
        app.include_router(router)
        service = TemporalExecutionService(session)
        service._client_adapter.start_workflow = AsyncMock(return_value=SimpleNamespace(run_id="step-plans-run"))
        app.dependency_overrides[_get_service] = lambda: service
        app.dependency_overrides[get_async_session] = lambda: session
        _override_user_dependencies(app, is_superuser=True)
        backend = provider.runtime_id if journey.harness != "opencode-native" else "omnigent"
        root_runtime["mode"] = backend
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/executions", json={
                "type": "workflow",
                "payload": {
                    "workflow": {
                        "instructions": "Generate and check a report.",
                        "runtime": root_runtime,
                        "steps": [
                            {"id": "generate", "instructions": "Generate a report.", "type": "skill", "skill": {"id": _DURABLE_SKILL_NAME}},
                            {"id": "verify", "instructions": "Check a report.", "type": "skill", "skill": {"id": _DURABLE_SKILL_NAME}, "runtime": step_runtime},
                        ],
                    },
                    "publishMode": "none",
                    "workspace": {"workspaceSource": {"kind": "scratch"}},
                },
            })
        assert response.status_code == 201, response.text
        record = await session.get(TemporalExecutionCanonicalRecord, response.json()["workflowId"])
        assert record.parameters["targetRuntime"] == "omnigent"
        requests = agent_requests(record.parameters)
        assert set(requests) == {"generate", "verify"}
        assert all(request.agent_id == "omnigent" for request in requests.values())
        root = requests["generate"].omnigent_execution_plan
        specialized = requests["verify"].omnigent_execution_plan
        assert root is not None and specialized is not None
        assert root.plan_ref != specialized.plan_ref
        plans = DbExecutionPlanStore(journey.session_factory)
        root_plan, step_plan = await plans.load(root.plan_ref), await plans.load(specialized.plan_ref)
        assert root_plan.payload.modelConfig.qualifiedId == journey.server.model
        assert step_plan.payload.modelConfig.qualifiedId == expected_model
        assert step_plan.payload.modelConfig.effort == expected_effort
        assert root_plan.payload.harnessId == step_plan.payload.harnessId == journey.harness
        assert root_plan.payload.credentialBindings == step_plan.payload.credentialBindings
        bound = bind_omnigent_model_selection(requests["verify"], model=expected_model, effort=expected_effort)
        assert bound.parameters["model"] == expected_model
        assert bound.parameters["effort"] == expected_effort
        assert service._client_adapter.start_workflow.await_count == 1
        artifacts = TemporalArtifactService(TemporalArtifactRepository(session))
        for ref in (specialized.plan_artifact_ref, requests["verify"].resolved_skillset_ref):
            await artifacts.read(artifact_id=ref, access=ArtifactAccessContext.for_execution(record.workflow_id))
            with pytest.raises(TemporalArtifactAuthorizationError):
                await artifacts.read(artifact_id=ref, access=ArtifactAccessContext.for_execution("unrelated-workflow"))
        original_binding = dict(record.parameters["omnigentExecutionPlan"])
        original_step_plans = dict(record.parameters["omnigentStepExecutionPlans"])
        successor = await readmit_frozen_execution_plan(
            db_session=session, artifact_service=None, workflow_id=record.workflow_id,
            initial_parameters=record.parameters, subject="Step plan test",
            preserve_recorded_intent=True,
        )
        assert successor["disposition"] == "admitted", successor
        successor_binding = successor["omnigentStepExecutionPlans"]["verify"]["omnigentExecutionPlan"]
        successor_plan = await plans.load(successor_binding["planRef"])
        assert successor_plan.payload.modelConfig == step_plan.payload.modelConfig
        # The same transaction/grant owner used by scheduled occurrences and
        # interrupted attempts must authorize every new step artifact.
        from api_service.db import base
        from moonmind.workflows.temporal.activities.omnigent_activities import _link_execution_plan_inputs

        monkeypatch.setattr(base, "async_session_maker", journey.session_factory)
        await _link_execution_plan_inputs(
            successor["omnigentExecutionPlan"],
            execution=SimpleNamespace(workflow_id=record.workflow_id, workflow_run_id=record.run_id, namespace="default"),
            admitted_step_plans=successor["omnigentStepExecutionPlans"],
        )
        successor_skillset = successor["omnigentStepExecutionPlans"]["verify"]["resolvedSkillsetRef"]
        await artifacts.read(artifact_id=successor_skillset, access=ArtifactAccessContext.for_execution(record.workflow_id))
        with pytest.raises(TemporalArtifactAuthorizationError):
            await artifacts.read(artifact_id=successor_skillset, access=ArtifactAccessContext.for_execution("unrelated-workflow"))
        assert record.parameters["omnigentExecutionPlan"] == original_binding
        assert record.parameters["omnigentStepExecutionPlans"] == original_step_plans
        if specialization == "model":
            from tests.integration.omnigent.test_native_default_admission import _execute_admitted_generic_plan

            journey.server.additional_models = [expected_model]
            await _execute_admitted_generic_plan(journey, step_plan, SimpleNamespace(
                workflow_id=record.workflow_id, run_id=record.run_id,
                parameters={**record.parameters, **record.parameters["omnigentStepExecutionPlans"]["verify"]},
            ))
        if specialization == "model" and journey.harness == "codex-native":
            from temporalio import activity
            from moonmind.workflows.temporal.activity_runtime import TemporalActivityRuntimeError, TemporalPlanActivities

            workflow_id = record.workflow_id
            run_id = record.run_id
            monkeypatch.setattr(activity, "in_activity", lambda: True)
            monkeypatch.setattr(activity, "info", lambda: SimpleNamespace(workflow_id=workflow_id, workflow_run_id=run_id, namespace="default"))
            planner = TemporalPlanActivities(artifact_service=artifacts)
            admitted_parameters = deepcopy(record.parameters)
            for tamper in ("replace", "remove"):
                parameters = deepcopy(admitted_parameters)
                if tamper == "replace":
                    parameters["omnigentStepExecutionPlans"]["verify"]["omnigentExecutionPlan"] = parameters["omnigentExecutionPlan"]
                else:
                    parameters.pop("omnigentStepExecutionPlans")
                refused = await readmit_frozen_execution_plan(
                    db_session=session, artifact_service=None, workflow_id=workflow_id,
                    initial_parameters=parameters, subject="Step handoff tamper test",
                    preserve_recorded_intent=True,
                )
                assert refused["disposition"] == "recorded", refused
                assert "step" in refused["reason"] and "authority" in refused["reason"]
                with pytest.raises(TemporalActivityRuntimeError, match="step.*authority"):
                    await planner.plan_validate(
                        plan_ref="unused-agent-plan", principal=f"workflow:{workflow_id}",
                        omnigent_execution_plan=admitted_parameters["omnigentExecutionPlan"],
                        execution_parameters=parameters,
                    )
        if specialization == "clear_effort" and journey.harness == "codex-native":
            monkeypatch.setattr(settings.workflow, "codex_effort", "low")
            refused = await readmit_frozen_execution_plan(
                db_session=session, artifact_service=None, workflow_id=record.workflow_id,
                initial_parameters=record.parameters, subject="Step plan drift test",
                preserve_recorded_intent=True,
            )
            assert refused["disposition"] == "recorded"
            assert "step verify" in refused["reason"] and "model" in refused["reason"]


@pytest.mark.parametrize("native_journey", ["codex-native"], indirect=True)
async def test_explicit_shared_step_rejects_uncarried_native_tier_options(native_journey, monkeypatch):
    journey = native_journey
    monkeypatch.setattr(settings.workflow, "github_repository", "")
    inventory = await OmnigentHttpClient(base_url=resolved_server_url()).list_agents()
    async with journey.session_factory() as session:
        provider = await session.get(ManagedAgentProviderProfile, PROFILE)
        provider.model_tiers = [
            {"model": journey.server.model},
            {"model": "example/selected", "parameters": {"native_option": "required"}},
        ]
        await session.commit()
        await reconcile_bootstrap_agent_profile(session, inventory=inventory)
        await synchronize_omnigent_harness_catalog(session)
        app = FastAPI()
        app.include_router(router)
        service = TemporalExecutionService(session)
        service._client_adapter.start_workflow = AsyncMock(return_value=SimpleNamespace(run_id="must-not-start"))
        app.dependency_overrides[_get_service] = lambda: service
        app.dependency_overrides[get_async_session] = lambda: session
        _override_user_dependencies(app, is_superuser=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/executions", json={"type": "workflow", "payload": {
                "workflow": {"instructions": "Preserve every requested native option.",
                    "runtime": {"mode": "omnigent", "providerProfileRef": PROFILE, "model": journey.server.model},
                    "steps": [{"id": "selected", "instructions": "Use the selected tier.", "type": "skill", "skill": {"id": _DURABLE_SKILL_NAME}, "runtime": {"modelTier": 2}}]},
                "publishMode": "none", "workspace": {"workspaceSource": {"kind": "scratch"}},
            }})
        assert response.status_code == 422, response.text
        assert "native tier options" in response.text
        service._client_adapter.start_workflow.assert_not_awaited()
